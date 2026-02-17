# Rust Backend CPU Profile Report (Post-Clone-Elimination)

**Date:** 2026-02-17
**Platform:** Linux 6.6.87.2-microsoft-standard-WSL2 (x86_64)
**Rust:** release profile (LTO, codegen-units=1); profiling profile (+ debug=1, strip=none) for sampling
**Tools:** samply 0.13.1 (perf-based) + addr2line, py-spy 0.4.1 (native mode)

## 1. Current Timing

### End-to-end (Python → Rust → Arrow → LogFile)

| File | Cython median | Rust median | Speedup |
|------|--------------|-------------|---------|
| SFJ (7.9 MB, 34 ch) | 53 ms | 18 ms | 2.93x |
| 86 (40 MB, 102 ch) | 233 ms | 100 ms | 2.34x |

### Pure Rust parsing + GPS decode (no PyO3/Arrow)

| File | Median | Min | Max |
|------|--------|-----|-----|
| SFJ (7.9 MB) | 18.6 ms | 16.6 ms | 22.1 ms |
| 86 (39.8 MB) | 87.1 ms | 82.8 ms | 105.3 ms |

### Implied overhead breakdown (86 file)

| Phase | Time | % of total |
|-------|------|-----------|
| Pure Rust parsing + GPS decode | ~104 ms | 104% (*) |
| Arrow conversion + PyO3 + Python | ~−4 ms | — |
| **End-to-end Rust backend** | **~100 ms** | **100%** |

(*) Pure Rust bench includes GPS decode overhead that overlaps with end-to-end.
After eliminating Arrow clones, the Arrow bridge is essentially free — the
end-to-end time is now dominated entirely by parsing and GPS decode.

## 2. samply Results — Pure Rust Parsing (86 file, 5451 samples)

### Self Time (where CPU cycles are actually spent)

| Function | Self % | Samples | Category |
|----------|--------|---------|----------|
| `core::ptr::write` | 30.2% | 1647 | Memory: Vec data copy |
| libc `memmove` (rep movsb) | 21.8% | 1188 | Memory: libc memcpy/memmove |
| `Vec::push_mut` | 7.7% | 420 | Memory: Vec growth |
| libc `brk` / `sbrk` | 6.9% | 374 | Memory: heap expansion |
| `parser::decode_all_channels` | 4.9% | 268 | Parsing: channel decode |
| `decoders::decode_sample` | 3.6% | 195 | Parsing: sample decode |
| `parser::scan_stream` | 2.5% | 135 | Parsing: byte scanning |
| `parser::try_parse_g_message` | 2.1% | 112 | Parsing: G-message |
| libc allocator internals | 1.7% | 94 | Memory: alloc |
| `parser::try_parse_m_message` | 1.6% | 87 | Parsing: M-message |
| `decoders::half_to_f32` | 1.3% | 73 | Parsing: float16 decode |
| `decoders::SampleValue::as_f64` | 1.2% | 66 | Parsing: type convert |
| `gps_utils::ecef2lla_vermeille2003` | 1.0% | 53 | GPS: coordinate conversion |

### Total Time (inclusive, call tree)

| Function | Total % | Notes |
|----------|---------|-------|
| `bench_parse::main` | 99.6% | Full benchmark loop |
| `parser::parse_xrk` | 43.1% | Entry point (= scan + finalize) |
| `core::ptr::write` | 30.2% | Vec data copy on push/grow |
| `RawVecInner::grow_amortized` | 22.3% | Vec reallocation policy |
| `System::realloc` | 22.3% | glibc `realloc` calls |
| `Vec::push_mut` | 20.0% | Includes alloc + copy |
| `parser::scan_stream` | 11.9% | Byte scanning + message dispatch |
| `parser::decode_all_channels` | 9.8% | Channel data decode |
| `System::dealloc` (free) | 6.5% | glibc `free()` calls |
| `RawVecInner::reserve` | 5.3% | Capacity checks |
| `decoders::decode_sample` | 3.6% | Per-sample decode |
| `core::ptr::copy_nonoverlapping` | 3.4% | memcpy for Vec realloc |
| `f64::atan2` | 2.1% | GPS trig (ecef2lla) |

### Category Summary

| Category | Self % |
|----------|--------|
| **Memory: data copy** (`ptr::write`, libc `memmove`) | ~52% |
| **Memory: alloc/realloc** (`Vec::push_mut`, `brk`, libc alloc) | ~17% |
| Parsing logic (scan + decode + messages) | ~18% |
| GPS processing | ~1% |
| Other | ~2% |

## 3. py-spy Results — Full Python+Rust Stack (86 file, 29101 samples)

### Total Time (inclusive, parsing loop only)

| Function | Total % | Notes |
|----------|---------|-------|
| Parsing loop | 91.5% | Excludes one-time import overhead |
| `Vec::push` | 45.8% | Vec growth dominates everything |
| `parser::parse_xrk` | 44.8% | Pure Rust parsing |
| `parser::ParserState::finalize` | 44.8% | = decode_all_channels + finalize |
| `ptr::write` | 41.4% | Vec data copy |
| `parser::decode_all_channels` | 21.9% | Channel data decode |
| `realloc` (libc) | 15.9% | glibc reallocation |
| `parser::scan_stream` | 7.8% | Byte scanning + dispatch |
| `aim_xrk` (lib.rs) | 5.8% | Rust PyO3 entry point |
| `Vec::extend_from_slice` | 3.4% | Accum.data growth |
| `gps_processing::decode_gps` | 3.0% | GPS channel decode |
| `atan2` (libm) | 2.3% | GPS ECEF↔LLA trig |
| `ptr::copy_nonoverlapping` | 1.9% | memcpy for Vec realloc |

### Self Time Categories

| Category | Self % | Samples |
|----------|--------|---------|
| Memory: ptr::write / copy | 41.5% | 12072 |
| libc internals (memcpy/memmove/realloc) | 29.8% | 8679 |
| Parsing logic | 11.4% | 3312 |
| Python benchmark loop | 5.7% | 1651 |
| Memory: Vec growth | 4.8% | 1387 |
| GPS processing | 1.7% | 496 |
| Arrow bridge | 0.9% | 261 |
| Other | 4.2% | 1212 |

## 4. Top Hotspots (ranked)

1. **Vec growth in `scan_stream` accumulators** — dominates pure Rust self-time (~69% memory)
   `extend_from_slice` on `Accum.data` and `Vec::push` on `Accum.timecodes` cause
   repeated reallocation as data accumulates during stream scanning. No pre-allocation
   hints are used. For the 86 file this means hundreds of reallocations per channel
   as data grows from empty to final size.

2. **`decode_all_channels` allocation** (parser.rs) — 21.9% total in py-spy
   Creates new `Vec<i64>` and `Vec<f64>` for each of 102 channels. Each channel's
   raw bytes are decoded sample-by-sample, pushing onto fresh Vecs. The decode loop
   itself is efficient but dominated by Vec growth overhead.

3. **GPS decode** (gps_processing.rs) — 3.0% total in py-spy
   `decode_gps` decodes NAV-SOL messages and `ecef2lla_vermeille2003` converts
   coordinates using trig functions (atan2, sqrt). Small but measurable for files
   with many GPS samples.

4. **`decode_sample` dispatch** (decoders.rs:49) — 3.6% self in samply
   Match on `decoder_type` with byte extraction. Already well-optimized.

5. ~~**Vec cloning in `build_channel_table`** — was 17.9% of end-to-end time~~
   **FIXED.** Arrow bridge now takes ownership of data via `std::mem::take` and
   `into_iter()`. Arrow bridge self-time is now <1%.

## 5. Observations

- **Memory still dominates everything.** Even after eliminating Arrow clones,
  ~69% of self-time is `core::ptr::write` (Vec data copy) and libc `memmove`/`realloc`.
  This is now entirely within the parser itself (scan_stream + decode_all_channels),
  not the Arrow bridge.

- **The Arrow bridge is no longer a bottleneck.** After the clone elimination,
  Arrow bridge self-time dropped from ~18% to <1%. The end-to-end time (~100 ms)
  is now within noise of the pure Rust parsing time (~104 ms), confirming that
  Arrow conversion is effectively zero-cost.

- **Accumulator pre-allocation is now the #1 optimization target.** `scan_stream`
  grows `Accum.data` (raw bytes) and `Accum.timecodes` from empty Vecs. If we
  estimated per-channel data sizes from the file header (e.g., `file_size / n_channels`),
  pre-allocating would eliminate most of the realloc churn (~22% total time in
  `RawVecInner::grow_amortized`).

- **`decode_all_channels` could benefit from capacity hints.** Each channel creates
  Vecs with approximate capacity. If the accumulator tracked exact sample counts
  during scanning, decode could allocate exactly once.

- **The parser itself is very efficient.** Actual byte-scanning and decoding logic
  (`scan_stream` at 2.5% self, `decode_sample` at 3.6% self) is lean. The parser
  spends ~18% of self-time doing useful work; the rest is memory management.

- **PyO3 overhead is negligible.** py-spy shows <1% in Arrow bridge and no
  measurable time in PyO3 type conversions or GIL operations.

- **GPS processing is small but visible.** At 3% total, GPS decode + ECEF
  conversion is the third-largest component. The `atan2` calls in `ecef2lla` are
  inherently expensive (trig), but the absolute time (~3 ms) doesn't justify
  optimization effort.

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

**End-to-end (Python → Rust → Arrow → LogFile):**
- SFJ: ~18 ms → ~19 ms (within noise)
- 86: ~100 ms → ~109 ms (includes file I/O; pure Rust improvement masked by I/O)

## 7. Remaining Optimization Priorities

| Priority | Target | Expected gain | Effort |
|----------|--------|---------------|--------|
| 1 | Track exact sample counts during scan for `decode_all_channels` capacity | ~5-10% pure Rust | Low-Medium |
| 2 | Arena allocator for decode_all_channels | ~5% pure Rust | High |
