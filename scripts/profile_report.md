# Rust Backend CPU Profile Report (Post-Pre-Allocation)

**Date:** 2026-02-17
**Platform:** Linux 6.6.87.2-microsoft-standard-WSL2 (x86_64)
**Rust:** release profile (LTO, codegen-units=1); profiling profile (+ debug=1, strip=none) for sampling
**Tools:** samply 0.13.1 (perf-based) + addr2line, py-spy 0.4.1 (native mode)

## 1. Current Timing

### End-to-end (Python → Rust → Arrow → LogFile)

| File | Cython median | Rust median | Speedup |
|------|--------------|-------------|---------|
| SFJ (7.9 MB, 34 ch) | 50 ms | 19 ms | ~2.5x |
| 86 (40 MB, 102 ch) | 230 ms | 109 ms | ~2.1x |

### Pure Rust parsing + GPS decode (no PyO3/Arrow)

| File | Median | Min | Max |
|------|--------|-----|-----|
| SFJ (7.9 MB) | 18.6 ms | 16.6 ms | 22.1 ms |
| 86 (39.8 MB) | 87.1 ms | 82.8 ms | 105.3 ms |

### Implied overhead breakdown (86 file)

| Phase | Time | % of total |
|-------|------|-----------|
| Pure Rust parsing + GPS decode | ~87 ms | 80% |
| File I/O + Arrow conversion + PyO3 + Python | ~22 ms | 20% |
| **End-to-end Rust backend** | **~109 ms** | **100%** |

## 2. samply Results — Pure Rust Parsing (86 file, 5485 samples)

### Self Time (where CPU cycles are actually spent)

| Function | Self % | Samples | Category |
|----------|--------|---------|----------|
| `decode_all_channels` data write (movq/mov) | 26.6% | 1461 | Data writing: decoded values + timecodes |
| libc `memmove` (rep movsb) | 8.2% | 451 | Memory: extend_from_slice copies |
| `parser::scan_stream` | 6.3% | 345 | Parsing: byte scanning + dispatch |
| libc `brk` / `sbrk` | 5.2% | 287 | Memory: heap expansion |
| `decode_all_channels` other (increment, bounds) | 5.7% | 313 | Data writing: loop overhead |
| `decoders::decode_sample` | 3.8% | 208 | Parsing: sample decode |
| `prescan` (inlined into parse_xrk) | 3.0% | 164 | Prescan: byte scanning |
| `core::hash::BuildHasher::hash_one` | 2.4% | 133 | Hashing: prescan HashMap |
| `decoders::half_to_f32` / `as_f64` | ~2% | ~110 | Parsing: type conversion |
| `gps_utils::ecef2lla_vermeille2003` | ~1% | ~55 | GPS: coordinate conversion |

### Total Time (inclusive, call tree)

| Function | Total % | Notes |
|----------|---------|-------|
| `bench_parse::main` | 84.9% | Full benchmark loop |
| `parser::parse_xrk` (finalize) | 35.8% | = decode_all_channels + finalize |
| `parser::parse_xrk` (prescan) | 18.1% | Pre-scan pass |
| `parser::scan_stream` | 13.0% | Main byte scanning |
| `try_parse_m_message` | 8.4% | M-message parsing (via scan_stream) |
| `System::dealloc` (free) | 3.8% | glibc `free()` calls |
| `decoders::decode_sample` | 3.8% | Per-sample decode |
| `core::hash::BuildHasher::hash_one` | 2.4% | Hashing in prescan |

### Category Summary

| Category | Self % |
|----------|--------|
| **Data writing** (decoded values/timecodes to output Vecs) | ~32% |
| **Hashing** (prescan HashMap entry/lookup) | ~5% |
| **Memory: memmove** (extend_from_slice in scan_stream) | ~8% |
| **Memory: brk/alloc** | ~5% |
| Parsing logic (scan + decode + messages) | ~15% |
| Prescan scanning | ~3% |
| GPS processing | ~1% |
| Other | ~5% |

## 3. py-spy Results — Full Python+Rust Stack (86 file, 21393 samples)

### Total Time (inclusive, parsing loop only)

| Function | Total % | Notes |
|----------|---------|-------|
| Parsing loop | 81.5% | Excludes one-time import overhead |
| `parser::parse_xrk` (finalize) | 35.8% | decode_all_channels + finalize |
| `parser::parse_xrk` (prescan) | 18.1% | Pre-scan pass |
| `decode_all_channels` | 18.0% | Channel data decode |
| `Vec::push` (f64 values) | 18.0% | Values push in decode |
| `Vec::push` (i64 timecodes) | 15.8% | Timecodes push in decode |
| `HashMap::entry` | 13.8% | **All from prescan** — hashing overhead |
| `scan_stream` | 13.0% | Main byte scanning |
| `Vec::push` (other) | 8.6% | Vec growth in scan_stream accums |
| `try_parse_m_message` | 8.4% | M-message parsing |
| `gps_processing::decode_gps` | 1.6% | GPS channel decode |

### Self Time Categories

| Category | Self % | Samples |
|----------|--------|---------|
| Data writing: ptr::write / copy | 40.4% | 8642 |
| **HashMap/hashing (prescan)** | **19.0%** | **4064** |
| libc (memmove/brk/alloc) | 9.1% | 1951 |
| Python/import overhead | 7.6% | 1616 |
| Prescan scanning | 3.9% | 831 |
| Vec growth | 2.4% | 506 |
| Arrow bridge | 2.1% | 456 |
| GPS processing | 1.6% | 342 |
| Parsing: decode_sample | 1.3% | 269 |
| Parsing: scan_stream | 0.5% | 112 |
| Other | 12.2% | 2604 |

## 4. Top Hotspots (ranked)

1. **HashMap hashing in `prescan`** — 19% self-time, 13.8% total in py-spy
   `PrescanResult` uses `HashMap<usize, usize>` to count bytes per channel index.
   Every G/S/M/c message triggers a `hints.data_bytes[cat].entry(index).or_insert(0)`
   call with SipHash hashing. Channel indices are small integers (0–101), so a
   `Vec<usize>` indexed directly would eliminate all hashing overhead.

2. **Data writing in `decode_all_channels`** — 40% self-time (unavoidable)
   `core::ptr::write` for pushing decoded f64 values and i64 timecodes into output
   Vecs. These Vecs are already pre-sized via `Vec::with_capacity(n_rows)`, so there
   are no reallocations. This is the irreducible cost of decoding.

3. **libc `memmove` in `scan_stream`** — 8.2% self in samply
   `extend_from_slice` copies raw bytes from the input buffer into pre-allocated
   accumulator Vecs. The copies are unavoidable (data must be staged for decode),
   but the `memmove` is now into pre-allocated buffers (no reallocation).

4. **GPS decode** (gps_processing.rs) — 1.6% total in py-spy
   Small and well-optimized. Not worth further effort.

5. ~~**Vec growth in `scan_stream` accumulators** — was 69% self-time~~
   **FIXED.** Pre-allocation reduced memmove from 21.8% to 8.2%, brk from 6.9%
   to 0.7%, Vec::push_mut from 7.7% to 0.5%.

6. ~~**Vec cloning in Arrow bridge** — was 17.9% of end-to-end time~~
   **FIXED.** Arrow bridge now takes ownership via `std::mem::take` and
   `into_iter()`. Arrow bridge self-time is now ~2%.

## 5. Observations

- **HashMap hashing is the new #1 bottleneck.** The prescan pass uses
  `HashMap<usize, usize>` for byte counting, causing ~19% of self-time in
  SipHash hashing and hashbrown probing. Replacing with `Vec<usize>` (indexed
  by channel index) would eliminate this entirely since indices are small integers.

- **Pre-allocation worked but introduced prescan overhead.** The prescan pass
  takes 18.1% of total time, of which ~14% is HashMap hashing overhead. With
  the HashMap→Vec fix, prescan overhead would drop to ~4% (just byte scanning),
  making it a clear net win.

- **Data writing dominates (40% self-time).** After eliminating reallocation
  and hashing overhead, the irreducible cost is writing decoded values to Vecs.
  `decode_all_channels` already uses `Vec::with_capacity(n_rows)`, so these
  writes go into pre-sized buffers without reallocation.

- **Memory allocation nearly eliminated.** brk/sbrk dropped from 6.9% to 0.7%,
  memmove from 21.8% to 8.2% (remaining is unavoidable extend_from_slice copies),
  Vec::push growth from 7.7% to 0.5%.

- **Arrow bridge and PyO3 remain negligible.** ~2% self-time for Arrow conversion.

## 6. Applied Optimizations

### Eliminate unnecessary clones (completed)

**Changes:**
- `build_channel_table`: takes owned `ChannelData` instead of `&ChannelData`, eliminates `.clone()` on timecodes and values
- `build_all_channel_tables`: takes owned `HashMap<u16, ChannelData>` instead of `&ParseResult`, uses `into_iter()` to move data
- `build_gps_channel`: takes owned `Vec<i64>` / `Vec<f64>` / `Vec<f32>` instead of slices, eliminates `.to_vec()`
- `build_gps_channel_tables`: consumes `GpsDecodeResult` by value, destructures to move all fields
- `aim_xrk` in lib.rs: uses `std::mem::take` to move `channel_data` out of `ParseResult` before Arrow conversion; reorders GPS Arrow table building after lap detection so `gps_result` can be consumed by value

**Improvement vs pre-optimization baseline:**
- SFJ: 23 ms → 18 ms (22% faster, speedup 2.43x → 2.93x)
- 86: 127 ms → 100 ms (21% faster, speedup 1.96x → 2.34x)

### Pre-allocate accumulator Vecs (completed)

**Changes:**
- Added `prescan()` function: fast first pass through the byte stream that processes
  header messages (CHS/GRP/CNF) to learn channel structure, then counts data bytes
  for each data message type (G/S/M/c) per channel index. Also counts GPS/GNFI bytes.
  Over-counts slightly (ignores timecode dedup) which is intentional — extra capacity
  is harmless.
- Added `PrescanResult` struct: holds `data_bytes` per category/index, `m_timecode_count`
  per index, `gps_bytes`, and `gnfi_bytes`.
- Added `ParserState::apply_prescan_hints()`: calls `reserve()` on all accumulator Vecs
  (`Accum.data`, `Accum.timecodes`, `gps_data`, `gnfi_data`) using prescan counts.
- Modified `parse_xrk()`: calls `prescan()` then `apply_prescan_hints()` before `scan_stream()`.

**Improvement (pure Rust, no PyO3/Arrow):**
- SFJ: 17.6 ms → 18.6 ms (within noise — 34 channels, little realloc to eliminate)
- 86: 104.0 ms → 87.1 ms (16% faster — 102 channels, eliminated ~22% realloc overhead)

## 7. Remaining Optimization Priorities

| Priority | Target | Expected gain | Effort |
|----------|--------|---------------|--------|
| 1 | Replace `HashMap` with `Vec` in `PrescanResult` | ~14% total time | Low |
| 2 | Track exact sample counts during scan for `decode_all_channels` capacity | ~3-5% pure Rust | Low-Medium |
| 3 | Arena allocator for decode_all_channels | ~2-3% pure Rust | High |

### Priority 1 detail: Replace HashMap with Vec in PrescanResult

`PrescanResult.data_bytes` uses `[HashMap<usize, usize>; 4]` but channel indices
are small integers (0–101 for the 86 file). Replace with `[Vec<usize>; 4]` and
index directly. Same for `m_timecode_count`. This eliminates all SipHash hashing
and hashbrown probing, which accounts for ~14% of total time in py-spy.

```rust
// Before (HashMap — 14% overhead):
*hints.data_bytes[0].entry(index).or_insert(0) += total_size - 3;

// After (Vec — O(1) direct indexing):
hints.data_bytes[0][index] += total_size - 3;
```
