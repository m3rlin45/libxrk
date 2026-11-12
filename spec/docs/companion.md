# AIM XRK Format — Companion Documentation

This document covers application-level algorithms and behaviors that go beyond
the raw binary format specification in `spec/xrk_format.py`. These are needed
to fully interpret XRK data but aren't part of the wire format itself.

## 1. XRZ Decompression

XRZ files are XRK files compressed with standard zlib.

**Detection:** First two bytes are `0x78` followed by `0x01`, `0x9C`, or `0xDA`.

**Implementation:** Apply `zlib.decompress()` to the entire file contents. The
decompressed result is a standard XRK byte stream. Some XRZ files may be
truncated (incomplete logging sessions); use `decompressobj()` with `flush()`
to recover partial data.

Reference: `_aim_xrk_cy.pyx:1165-1186` (`_decompress_if_zlib`)

## 2. Time Offset Computation

All timecodes in the XRK file are absolute logger timestamps (milliseconds since
logger power-on). To get relative session time, subtract a `time_offset`:

```
time_offset = first_LAP_message.end_time - first_LAP_message.duration
```

This gives the session start time. Subtracting it from all timecodes yields
milliseconds since session start.

If no LAP messages exist, `time_offset` is the minimum of the first timecodes
across all channels.

Precisely, both backends take the **minimum** of all three candidates: the first
LAP message's start, the first timecode of every data stream (S/G/M/c, per
channel or group), and the first GPS record's timecode. The spec implements
this as `ParseResult.time_offset()`.

Reference: `_aim_xrk_cy.pyx:530-533`, `_aim_xrk_cy.pyx:662-690`

## 3. Decoder Dispatch Table

Each CHS channel definition includes a `decoder_type` byte at offset [20].
This determines how to interpret the raw data bytes:

| decoder_type | struct fmt | Size | Interpolate | Notes |
|-------------|-----------|------|-------------|-------|
| 0  | `i` (int32)   | 4 | No  | Master Clock on M4GT4 |
| 1  | `H` (uint16)  | 2 | Yes | Plain uint16 (e.g. StartRec) |
| 3  | `i` (int32)   | 4 | No  | Master Clock on ScottE46 |
| 4  | `h` (int16)   | 2 | No  | |
| 6  | `f` (float32) | 4 | Yes | Standard float |
| 8  | `i` (int32)   | 4 | No  | iGPS reference |
| 11 | `h` (int16)   | 2 | No  | |
| 12 | `i` (int32)   | 4 | No  | Predictive Time |
| 13 | `B` (uint8)   | 1 | No  | Status field |
| 15 | `H` (uint16)  | 2 | No  | **Gear lookup** — map ASCII 'N'→0, '1'→1, ..., '6'→6 |
| 20 | `H` (uint16)  | 2 | Yes | **float16 encoded** — decode uint16 as IEEE 754 half-precision |
| 22 | `i` (int32)   | 4 | No  | Lap Time (variant) |
| 24 | `i` (int32)   | 4 | No  | Best Run Diff |
| 26 | `i` (int32)   | 4 | No  | Total Odometer |
| 27 | `i` (int32)   | 4 | No  | Reset Odometer |
| 31 | `i` (int32)   | 4 | No  | Lap Time |
| 32 | `i` (int32)   | 4 | No  | Roll Time |
| 33 | `i` (int32)   | 4 | No  | Best Time |
| 37 | `i` (int32)   | 4 | No  | GPS_Hours |
| 38 | `i` (int32)   | 4 | No  | GPS_Date |
| 39 | `i` (int32)   | 4 | No  | GPS_Time |

**Float16 encoding (decoder 20):** The raw uint16 value is reinterpreted as
IEEE 754 half-precision (binary16) and returned as float16 (Arrow
`halffloat`), not widened. One exception: a channel in volts is stored in
millivolts and divided by 1000 (see "Special fixups"), and the quotient is not
a float16 value, so it is widened to float32 first and returned as float32
(see "Value types"). This is the most common encoding for analog sensor
channels.

**Gear lookup (decoder 15):** The raw uint16 value is an ASCII character code.
Map `'N'`(0x4E)→0, `'1'`(0x31)→1, ..., `'6'`(0x36)→6.

**Non-finite samples:** loggers write IEEE infinities (and occasionally NaN)
into float channels as "no value" sentinels — float16 `0x7C00` (+inf) on every
sample of the official AIM sample's `External Voltage` channel (no external
supply connected), float32 `0xFF800000` (−inf) on CAN channels such as
`Lambda` and `P_Brake` during bus dropouts. The official AIM DLL returns `0.0`
for those samples, and hides a channel whose every sample is non-finite.
libxrk instead reports them as **`NaN`** on both backends: after decoding and
the mV→V conversion, any non-finite value in a decoder-6 (float32) or
decoder-20 (float16) channel becomes `NaN`. Integer channels are untouched.
`0.0` would be a plausible-looking lie for a lambda or a brake pressure; `NaN`
is what pandas/pyarrow consumers already treat as missing. In the spec this is
`NonFiniteToNaN`, an `ExprAdapter` wrapping the float codecs in `SAMPLE_CODECS`
(`spec/xrk_format.py`), next to `Gear` (decoder 15) and `Volts` (the mV→V
conversion). Each adapter's decoder is a numpy expression, so the same function
decodes one sample (`channel_sample_codec(chs).parse(...)`) and a whole stream
(`decode_channel_values()`).

**Value types:** each channel's table has an int64 `timecodes` column
(milliseconds since session start; int32 on the wire, section 2) and a value
column whose type the spec states as `channel_value_dtype(chs)`
(`spec/xrk_format.py`, next to `SAMPLE_CODECS`). Both backends give the
column the matching Arrow type, and `decode_channel_values()` returns its
arrays in it. The type is derived from the codec rather than tabulated:

1. A channel named `Calculated_Gear` or `PreCalcGear` is **uint32**, whatever
   its `decoder_type` (`NAMED_VALUE_DTYPES`; see "Special fixups").
2. Otherwise the value has the type of the base field of the decoder's codec
   in `SAMPLE_CODECS`: `Int32sl` → int32, `Int16ul` → uint16, `Int16sl` →
   int16, `Int8ul` → uint8, `Float32l` → float32, `Float16l` → float16. A
   float16 sample is returned as float16, not widened: the widening carried
   no information and doubled the largest channels.
3. The mV→V conversion (unit `V`) then divides by 1000 (`VOLTS_DTYPES`): any
   float type becomes **float32**, any integer type **float64**. A float16
   widens here because the quotient is not a float16 value (11264 mV is one,
   11.264 V is not). Both backends widen the sample to float32 first and
   divide in float32.

Which gives:

| Channel | Value type |
|---------|-----------|
| decoder 1, 15 | uint16 |
| decoder 4, 11 | int16 |
| decoder 13 | uint8 |
| decoder 6 | float32 |
| decoder 20 | float16 |
| every other decoder (0, 3, 8, 12, 22, 24, 26, 27, 31–33, 37–39) | int32 |
| `Calculated_Gear`, `PreCalcGear` | uint32 |
| unit `V`, decoder 6 or 20 | float32 |
| unit `V`, any integer type (gear channels included) | float64 |

`TestChannelValueTypes` (`spec/tests/test_spec.py`) holds both backends to
this: the Arrow type of every channel of every fixture, and of every decoder
with and without the `V` conversion on a patched fixture, since the shipped
files only carry data for decoders 1, 4, 6, 11, 12, 15, 20 and 24. One
combination is left open: a gear-named channel with unit `V` whose
`decoder_type` byte is 6 or 20. Cython gives float64 (rule 3 applied to
uint32), Rust float32 (it picks the `V` type from `decoder_type` before
looking at the name). No fixture has such a channel.

The 14 GPS channels have no CHS and no codec; their types (float64 for
`GPS Speed`, `GPS Latitude`, `GPS Longitude`, `GPS Altitude` and `GPS_ITOW`,
float32 for the rest) are set by the shared GPS pipeline (section 5) and
tested with it (`tests/test_gps_processing.py`).

**Sample streams:** a data message's payload is structured in the spec, not
opaque: an `(M` burst's `samples` are `Array(count, Bytes(CHS.data_size))`,
and a `(G` payload's `samples` are an `Array` with one sample per GRP member
that has a CHS (`members`), in GRP order, each `CHS.data_size` wide. Both are
`RestreamData` views over the raw `data`, so byte-identical rebuilds are
unaffected. Because messages interleave channels and may repeat, a channel's
values exist only after an accumulation pass, which is why both backends
accumulate first and decode after. Every data message is a list of samples,
each with a time: S and V1 `(c` carry one at the message timecode, a `(G` one
per member at the timecode, an `(M` burst `count` samples `Mms` apart from the
timecode (section 4). The rule is: **a sample is kept iff its time is strictly
later than the last kept sample of its accumulator since the last CNF**, one
accumulator per message family and channel (per group member for G). This is
what lets an M burst that overlaps the accepted stream contribute only its
samples past the overlap. A `CNF` header starts every accumulator afresh (next
paragraph). The spec applies the rule in the parse hook (`_accumulate`); the
result is `ParseResult.streams`, contiguous bytes per channel in `data_size`
slots. A stream's grammar is `channel_stream(chs, width)`,
`GreedyRange(Padded(width, codec))`; `channel_values()` decodes every stream
with its vectorised form, in the channel's value type, and `test_spec.py`
compares them against both backends sample by sample: same type, identical
values.

**CNF resets the accumulators:** a `CNF` header frame makes every accumulator
forget its last kept time, so the next sample of each is kept whatever came
before, and a channel's timecodes may step back at that point (they are
exposed in file order, not sorted). Precisely:

- All four families reset (G, S, V1 `(c`, M), every accumulator of each.
- Only the accumulators of the sequence the `CNF` frame sits in. A CNF's own
  payload is parsed as a separate sequence with its own accumulators, so a
  `CNF` nested in another frame's payload resets nothing outside it.
- An `ENF` frame resets nothing.
- V2/V3 `(c` samples are resolved and placed over the whole file after the
  parse (`_resolve_c_variants`, `_place_c_variants`) and keyed by time, so
  no CNF comes between them. They are cut at the channel's last V1 `(c`
  sample kept since the last CNF ("Expansion samples" below).
- A stream ends on its last kept sample, which after a step back is not its
  latest one. `ParseResult.stream_end_times()` gives that time per channel
  (the timecode of the channel's last row in both backends), and
  `non_gps_end_time()`, the input of the GPS timing fix's indirect method
  (section 6), is the latest of them. A reset alone does not lower it: a
  stream with no sample after the CNF keeps the end it had.

The spec's `_reset_accumulators` states this and both backends conform (the
`CNF` branch of `_decode_sequence` in Cython, of `handle_header_message` in
Rust). Loggers do write a CNF after data has started: a short one that
re-defines a single channel (every SFJ, 86 and issue68/84 fixture has one), or
the whole configuration again (the official sample, twice). But in no shipped
fixture do timecodes step back across one, so the reset changes nothing there,
and `TestCnfResetsAccumulators` builds its inputs by replaying, right after
such a CNF, the data messages that precede it.

**The official AIM DLL does not reset.** It applies the acceptance rule over
the whole file, as if the CNF were not there (Wine harness, patched copies of
the fixtures):

| Input | libxrk (spec and both backends) | AIM DLL |
|---|---|---|
| SFJ, every data message before the CNF at byte 32032 (930 S, 117 M) replayed after it | 2,490 samples kept a second time, over 18 streams; e.g. `RPM` 33,930 → 34,033 rows, timecodes 5584 → 484 ms at the replay | all 18 channels unchanged (735,453 samples), no step back |
| 86, same at byte 129125 (3,582 G, 1,270 M, 133 S) | 18,799 samples kept a second time, over 84 streams | all 92 channels unchanged (7,097,497 samples) |
| Official sample, same at its third CNF, byte 56223, a full configuration (1,717 S, 382 M) | every replayed sample kept a second time | all 11 channels unchanged (418,566 samples) |
| SFJ, the 103 `RPM` S messages before the CNF (484…5584 ms) replayed with a marker value | 103 marker rows | none; the original values stay |
| Same, replayed 7 ms later (491…5591 ms) | 103 marker rows | one, at 5591 ms, the only one later than 5584 ms |

So on a file whose timecodes do step back across a CNF, libxrk returns samples
the DLL drops. Whether real loggers ever write such a file is not known (the
backends' reset was written on the assumption that timecodes may restart or
overlap after a new configuration block); the difference is recorded here,
tracked in issue #158, and left as is.

**Sample times:** the time of a sample is the one the accumulation rule
compares, defined in one place (`_samples`): the message timecode for S, V1
`(c` and every member of a `(G`, and `timecode + k * Mms` for the k-th sample
of an `(M` burst (section 4). It is an int32 on the logger clock, like the
wire timecode. `_accumulate` stores the time of each kept sample next to its
bytes: `ParseResult.stream_times[channel]` has one entry per slot of
`ParseResult.streams[channel]`. `ParseResult.channel_timecodes()` subtracts
`time_offset()` (section 2) and returns int64 milliseconds since session
start per channel, index for index with `channel_values()`. That array is the
`timecodes` column of the channel's table in both backends: they apply the
same rule and the same subtraction, with no sort, resample or further dedup
pass, so the timecodes of a channel fed by one accumulator (every channel of
every fixture) strictly increase.
`TestExhaustiveChannelTimecodes` in `test_spec.py` compares every timecode of
every channel of every fixture against both backends for exact equality.
Samples of V2/V3 `(c` expansion messages are not in `streams` and so have no
entry here; their times are described in `unknown_regions.md`.

**Expansion samples (V2/V3 `(c`):** a V2 `(c` message carries two fp16
samples and a V3 one (`unknown_regions.md`, "(c) Expansion Data Messages").
Their channel is only known once the whole file has been read, so they are
placed after the parse, by `_place_c_variants`, from one table,
`C_SAMPLE_PLACEMENT`. It gives, for each variant and `channel_field` side, the
samples the message contributes: which payload sample, its time relative to
the message timecode `tc` (embedded in a V2, synthesized for a V3), and its
priority.

| Message | `channel_field` side | Samples | Priority |
|---|---|---|---|
| V2 | base (low nibble 0 or 8) | `[0]` at `tc`, `[1]` at `tc − 4` | 3 |
| V2 | +4 (any other low nibble) | `[0]` at `tc − 2`, `[1]` at `tc − 4` | 1 |
| V3 | +4 | `[0]` at `tc` | 2 |
| V3 | base | none | |

Per channel (both fields of a pair feed the same channel), the rule is:

1. Every V2/V3 message whose `channel_field` resolved to a channel
   contributes the samples of its row. The side of the field decides, not
   whether the field is paired: an orphan field follows the row of its own
   side. Every orphan observed so far is on the +4 side, so the accelerometer
   channels of the KK-SII fixtures get `[0]` at `tc − 2` and `[1]` at `tc − 4`.
2. When several samples land on the same time, the highest priority is kept;
   between equal priorities, the one from the earliest message in the file.
3. The survivors are accumulated in ascending time order, exactly as V1 `(c`
   messages of that channel at those times would be (the rule above: kept iff
   strictly later than the last kept `(c` sample of the channel since the
   last CNF). The channel's V1 messages were accumulated during the parse, so
   in the stream the V2/V3 samples follow all V1 samples, and any placed at
   or before the last V1 timecode kept since the last CNF are dropped. So is
   a sample placed before time 0, which covers a V3 that found no V2 to
   anchor its timecode on.

The first kept time is the channel's first `(c` timecode for the time offset
(section 2) when the channel has no V1 samples: a V2(base) at `tc` makes
`tc − 4` a candidate, not `tc`.

**The 2 ms and 4 ms offsets are fixed constants** (`C_SAMPLE_STEP_MS = 2` and
twice that), and so is the ±2 ms step of the V3 timecode synthesis. They are
the sample period of the 500 Hz shock potentiometers the format was
reverse-engineered on, and are *not* derived from `CHS.sample_period_us`: both
backends hard-code them. The consequence is visible on the 100 Hz orphan
channels, whose two samples per 20 ms message are exposed 2 ms apart (session
times 3298, 3300, 3318, 3320, ... on issue68's `LateralAcc`) instead of 10 ms
apart. Deriving the step from the channel period is an open question, not
something a backend may do on its own.

The result is in `ParseResult.streams` like any other channel, so
`channel_values()` decodes it through the channel's codec (non-finite samples
become NaN as for every decoder-20 channel) and `channel_timecodes()` gives
each kept sample's time. Both backends conform:
`spec/tests/test_c_placement.py` checks each row, each collision and the V1
mix on hand-built streams, and `TestIssue68SpecVsBackends` and
`TestExhaustiveChannelTimecodes` every value and every timecode of the
issue68 and issue84 fixtures.

**Special fixups:**
- Channels named `Calculated_Gear` or `PreCalcGear` use decoder `Q` (uint64)
  with bit extraction: `(value >> 16) & 7` (returns 0 if bit 19 is set).
- Unit type 21 (`mV`): when the calibrated flag (high bit of `unit_type_byte`) is set,
  the display unit becomes `V` and values are divided by 1000.

Reference: `_aim_xrk_cy.pyx:95-137`

## 4. M-Message Timing

`(M` messages carry burst data: multiple consecutive samples for a single
channel. The timing for each sample within the burst is:

```
sample[i].timecode = base_timecode + i * Mms
```

Where `Mms = CHS.sample_period_us // 1000` (the channel's sample period in ms).

This means M-messages are used for high-frequency channels (e.g., 500 Hz shock
potentiometers with Mms=2). The base timecode is the message timecode.

In the spec the formula is the `(M` case of `_samples`. Each sample of the
burst is then kept or dropped on its own time by the accumulation rule of
section 3, so a burst that starts at or before the last kept sample of its
channel contributes only the samples whose time is later, each still at
`base_timecode + i * Mms` of its own burst (both backends compute the number
of leading samples to skip, `(last - base_timecode) // Mms + 1`, which is the
same thing). The kept times are exposed, session-relative, by
`ParseResult.channel_timecodes()` and checked against both backends sample by
sample.

Reference: `_aim_xrk_cy.pyx:314-326`

## 5. GPS Data (u-blox NAV-SOL)

GPS messages contain a 4-byte AIM timecode followed by a 52-byte u-blox NAV-SOL
(Navigation Solution Information) payload. All GPS-derived channels are computed
from these raw ECEF coordinates and velocities.

**Where this lives.** Sections 5, 6 and the GPS parts of 7 are implemented
once, in the Rust crate `crates/libxrk-gps` (`processing.rs`, `timing.rs`,
`laps.rs`, `utils.rs`; `process()` runs the whole pipeline). The parsers only
*collect* the material -- the raw GPS and GNFI records, the time offset, the
lap table from LAP messages, the TRK start/finish -- and hand it over: the
Rust backend links the crate (`libxrk::process_gps`), and the Cython backend
and the spec call the same code through the Rust extension module
(`libxrk._aim_xrk_rs.process_gps`; `ParseResult.gps_input()` gathers the
spec's material). The pipeline is tested on
its own in `tests/test_gps_processing.py`; `spec/tests/test_spec.py` checks
that the spec's material and each backend's give identical results.

### ECEF to LLA Conversion

The ECEF (Earth-Centered, Earth-Fixed) coordinates from NAV-SOL are converted
to latitude/longitude/altitude using the **Vermeille 2003** algorithm, which
provides high accuracy without iteration.

Reference: `crates/libxrk-gps/src/utils.rs` (`ecef2lla_vermeille2003`)

### No-fix records

A NAV-SOL record written before the receiver has a fix carries an all-zero
ECEF position (and `gpsFix = 0`, `numSV = 0`). Loggers write one at power-on
and, rarely, as a mid-stream dropout (the all-zero record discussed under
section 6). Fed through `ecef2lla`, zeros come out as latitude 0, longitude 0
(a real place in the Gulf of Guinea) and a division by zero for altitude.

The pipeline treats an all-zero ECEF position as **position unknown**: `GPS
Latitude`, `GPS Longitude` and `GPS Altitude` are `NaN` for that record. The
record is kept (its timecode still anchors the GPS timebase), and the other
GPS channels decode normally: speed from the velocity fields, `GPS_Fix` and
`GPS_Satellites` as written. The heading-derived channels (`GPS_Yaw_Rate`,
`GPS_LateralAcc`) are `NaN` for that record and the one after it, since they
are differences. An unknown position at a lap's end is no evidence the car is
away from the line, so it never makes that lap an in-lap.

Reference: `crates/libxrk-gps/src/processing.rs` (`decode_gps`).

### Computed GPS Channels

From the raw NAV-SOL fields, the following channels are derived:

- **GPS Speed** = `sqrt(ecefVX² + ecefVY² + ecefVZ²) / 100.0` [m/s]
- **GPS Latitude/Longitude/Altitude** = `ecef2lla(ecefX/100, ecefY/100, ecefZ/100)`
- **GPS_Satellites** = `numSV`
- **GPS_Fix** = `gpsFix`
- **GPS_pDOP** = `pDOP / 100.0`
- **GPS_Position_Accuracy** = `pAcc / 100.0` [m]
- **GPS_Velocity_Accuracy** = `sAcc / 100.0` [m/s]
- **GPS_ITOW** = `iTOW` [ms], the receiver's GPS time of week, unmodified (wraps every 604,800,000 ms)
- **GPS_Week** = `week`, the GPS week number

### Derived Acceleration/Yaw Channels

These require velocity transformation to ENU (East-North-Up) coordinates.
An intermediate `heading = atan2(V_east, V_north)` is computed but not emitted
as a channel.

- **GPS_InlineAcc** = `d(speed)/dt / 9.81` [g]
- **GPS_Yaw_Rate** = `d(heading)/dt` [deg/s] (with ±180° wrap handling)
- **GPS_LateralAcc** = `speed × yaw_rate × π/180 / 9.81` [g]

Reference: `crates/libxrk-gps/src/processing.rs` (`decode_gps`)

## 6. GPS Timing Bug

Some AIM MXP firmware versions have a bug where GPS timecodes periodically
have their upper 16 bits corrupted, causing 65533ms jumps in the timestamp
stream.

### Detection

When GPS timecodes are not monotonically increasing (`timecodes[i+1] < timecodes[i]`),
the upper 16 bits are assumed to be unreliable.

### Correction (Timecode Reconstruction)

Reconstruction is a **phase unwrap**. Anchor on `timecodes[0]`, then place each
subsequent sample at the multiple of 65536 that puts it *closest* to its
predecessor — i.e. fold the low-16 delta into `[-32768, +32767]`:

```
out[0] = timecodes[0]
out[i] = out[i-1] + fold16(timecodes[i] - out[i-1])
    where fold16(d) = (((d & 0xFFFF) ^ 0x8000) - 0x8000)
```

A backwards step is therefore only interpreted as a 16-bit rollover when it is
*close to 65536*. This matters because backwards steps have several causes and
only one of them is a wrap:

| Cause | Backwards step | Correct handling |
|---|---|---|
| 16-bit rollover | ~65536 ms | advance one 65536 ms band |
| Replayed record block | block duration | reproduce the original times |
| Out-of-order record at a buffer seam | tens of ms | keep its true time |
| All-zero dropout record | current elapsed time | absorb; next record re-locks |

The earlier rule ("accumulate +65536 whenever `tc[i+1] < tc[i]`") treated *all*
of these as rollovers, adding a spurious 65536 ms to every later sample.

Only applied when the raw stream is non-monotonic (the corruption signature).
Clean streams are left untouched, which preserves legitimate forward gaps larger
than the 32768 ms half-range.

Reference: `crates/libxrk-gps/src/processing.rs` (`fix_timecodes`), exposed
as `libxrk._aim_xrk_rs.reconstruct_gps_timecodes`; applied inside `decode_gps`.

### GNFI-Based Detection

A more robust approach uses GNFI (Logger Internal Clock) messages as ground
truth. GNFI runs on the logger's internal clock, not GPS, so it's immune to
the GPS timing bug. Large discrepancies between GNFI and GPS timecodes indicate
the bug is present.

Reference: `crates/libxrk-gps/src/timing.rs` (`detect_gap_corrections`: GNFI,
then a direct 60-70 s gap, then the GPS stream outliving the non-GPS channels
by 60-70 s; `apply_corrections`). Laps detected from GPS (section 7) are
computed on the repaired timecodes, so they never need a correction of their
own; LAP-message laps run on the logger clock and are never shifted.

## 7. Lap Detection

### Primary: LAP Messages

When LAP messages exist, they define lap boundaries directly:
- Filter to `segment == 0` (ignore segment markers)
- Deduplicate by `lap_num`
- Compute `start_time = end_time - duration`
- Normalize to 0-based indexing

This much is the parsers' job (`_get_laps` in `_aim_xrk_cy.pyx`,
`parse_lap_messages` in `crates/libxrk/src/parser.rs`, `ParseResult.lap_table()`
in the spec), including the "out" lap. The table is then finished by the
shared GPS pipeline (`libxrk-gps` `laps.rs`, `finish_laps`): the last lap
becomes **"in"** when the GPS position at its end is more than 30 m from the
TRK start/finish (an out-lap is never reclassified), and a file whose table is
empty gets the GPS fallback below.

`test_spec.py` runs the shared GPS pipeline over the spec's `lap_table()` and
compares the result against both backends.

### Fallback: GPS Cross-Track

When no LAP messages exist, laps are detected from GPS by computing the
cross-track distance to the start/finish line:

1. Get S/F coordinates from TRK message (`sf_lat`, `sf_long`)
2. Convert GPS positions to ECEF
3. Find positions closest to the S/F line (minimum cross-track distance)
4. Use these crossing points as lap boundaries

The crossings are found in two passes: candidate crossings within 30 m of the
line while moving at least 4 m/s, whose velocity-weighted normals are averaged
into one line normal for a second pass with consistent crossing times. Each
crossing starts a lap; the last lap ends at the final GPS sample and is typed
"in". On the SFJ fixture every detected crossing lands within 1 ms of the
beacon time the LAP messages recorded.

Reference: `crates/libxrk-gps/src/utils.rs` (`find_laps`), `crates/libxrk-gps/src/laps.rs` (`finish_laps`)

## 8. Channel Merging

Different channels have different sample rates (1 Hz to 500 Hz). To merge
them into a single table:

1. Compute a merged timebase via full outer join on timecodes
2. For each channel, interpolate (if `interpolate=True`) or forward-fill
   values at the merged timecodes

Reference: `base.py` (`get_channels_as_table`)

### Duplicate channel names

A CHS table may define the same long_name at several channel indices — each a
genuinely distinct channel with its own `source_channel_id` and data stream
(the issue68/issue84 fixtures define `RotaryLeft/Middle/Right_led` three times
each and `Right_Btn_4_led` twice, all data-bearing). The official AIM DLL
disambiguates positionally: the first occurrence keeps the plain name, and the
k-th occurrence is exposed as `"<name> dup <k>"` (observed on the issue84
fixture: `Right_Btn_4_led dup 2`). Numbering follows ascending channel index
over all CHS entries, whether or not they carry data.

Both backends implement this convention; the reference mapping is
`ParseResult.channel_display_names()` in `spec/xrk_format.py`. No duplicate is
ever collapsed or dropped.

### CNF re-definitions (channel renames)

A file may carry several CNF configuration blocks, and a later CNF may
re-define an existing channel index under a **new name**: the AIM official
sample renames index 11 `Temperature 1` → `Exhaust Temp` and index 12
`Temperature 2` → `Water Temp 2` in its final CNFs. The official DLL applies
last-write-wins: it exposes only the new names, carrying the channel's full
data stream (verified via the Wine harness — `Exhaust Temp` / `Water Temp`
with all 11,971 samples).

The spec (`_merge_cnf_result`) and both backends implement the same rule: a
re-definition refreshes the channel's names; the data stream is continuous
across the rename, and other CHS fields plus the accumulator layout are
assumed stable across re-definitions.

## 9. Unit Type Map

The CHS `unit_type_byte` (offset [12], lower 7 bits) maps to display units and
decimal precision. The high bit indicates whether the channel has been calibrated.

| unit_type | Units | Decimal Points |
|-----------|-------|----------------|
| 1  | %      | 2 |
| 3  | g      | 2 |
| 4  | deg    | 1 |
| 5  | deg/s  | 1 |
| 6  | (none) | 0 |
| 8  | m      | 0 |
| 9  | Hz     | 0 |
| 11 | #      | 0 |
| 12 | mm     | 0 |
| 14 | bar    | 2 |
| 15 | rpm    | 0 |
| 16 | km/h   | 0 |
| 17 | C      | 1 |
| 18 | ms     | 0 |
| 19 | Nm     | 0 |
| 20 | km/h   | 0 |
| 21 | mV     | 1 |
| 22 | l      | 1 |
| 24 | l/s    | 0 |
| 26 | time?  | 0 |
| 27 | A      | 0 |
| 28 | %      | 2 |
| 30 | lambda | 2 |
| 31 | gear   | 0 |
| 33 | %      | 2 |
| 43 | kg     | 3 |

Reference: `_aim_xrk_cy.pyx:144-169`

## 10. Unparsed Byte Runs: Messages Written Over One Another

The scanner tries each opcode at the current offset (`(S`, `(M`, `(G`, `(c`,
`<h`) and, when none parses, advances one byte and tries again (the `Bytes(1)`
fallback in `XRKStream`). A run of such skipped bytes is almost never
corruption. The logger writes messages over one another:

* the trailer (`RCR`, `VEH`, `CMP`, `VTY`, `NTE`) is appended wherever recording
  stopped, so the last message is usually cut short — its **head** (opcode,
  timecode, index, some data) sits immediately before the trailer. A `<hGPS`
  record can be cut the same way: a frame declaring a 56-byte payload with only
  part of it present;
* the `ODO` record that closes the header block lands on the first GPS record or
  sample message of the data region, leaving its **tail** — the end of the
  payload and the closing `)` or `<GPS..>` footer — right after the record;
* mid-stream, a message is occasionally written over the head of the one
  before it, leaving a tail of a few bytes (`.. af 29`, `.. c3 29`) or of a
  whole GPS record between two complete messages.

In a survey of 812 files (406 sessions as .xrk and .xrz, MXP/MXm/Solo 2 and
others, 2025-2026) the scanner skipped 255 runs in 166 files. All but two were
one of these fragments. The two exceptions are messages that straddle a 64 KiB
boundary of the file (ending at 0x10005 and 0x10008) with a few junk bytes
inserted at the boundary; they are framed like messages but do not parse.

### Where fragments occur, and why

Counted over the 255 skipped runs in the survey's .xrk files:

| Position in file | Runs | What is left behind |
|---|---|---|
| End of data, immediately before the trailer | 122 | Head of the last message (or of a `<hGPS` record) |
| Start of data, immediately after the `ODO` record | 121 | Tail of the first GPS record or sample message |
| Mid-file, immediately after an inserted `<hGPS` record | 5 | Tail of the sample message the record landed on |
| Mid-file, between two complete sample messages | 7 | Tail of a message whose head the next message overwrote |

The firmware is not available to read, so the mechanisms below are inferred
from the byte patterns; the shapes are consistent enough to state them.

* **Trailer.** Recording stops at an arbitrary point in the sample stream and
  the trailer records are appended there. The message being written is simply
  never finished.
* **ODO record.** What follows the ODO record is always a *tail*, so sample data
  was already on disk when the header block was written over the front of the
  file. The logger streams samples from the moment recording starts and writes
  the header block afterwards, at an offset computed from an earlier, shorter
  header length; the ODO record's size varies with the number of odometer
  entries, so the block overruns the first data message by a variable amount
  (1 to 71 bytes seen).
* **Inserted GPS records.** GPS records come from a different producer than the
  sample stream. One that reserves its slot in the shared write buffer after
  the sample writer has already passed it lands on the head of a message that
  was already written.
* **Message over message.** The same race without a header record involved:
  two producers handed overlapping positions, the later writer takes the head,
  the earlier writer's tail survives.

Each fragment costs one sample of one channel, and nothing can be recovered
from the fragment itself. The pattern is a fingerprint of the writer design
rather than of a damaged file, which is why fragments are counted, not
reported.

The two 64 KiB-boundary runs are different in kind: a few junk bytes inserted
at exactly 0x10000 inside a message, which points at the block-flush path and
corrupts rather than truncates. They do not match either fragment shape and
stay reported.

### Classification rule

A skipped run is a *fragment* — expected, carrying no recoverable data, and not
worth reporting — when either:

* **head**: it begins with a message opcode (`(S`, `(M`, `(G`, `(c` or `<h`, or
  is a lone `(` / `<`) and is immediately followed by a header record or by the
  end of the stream; or
* **tail**: it does not begin with an opcode and ends with a message terminator
  (`)` or `>`). A tail can begin with a stray `(` or `<` byte (`3c 29` is seen
  after the ODO record), which is why the full opcode pair is required above.

Every other skipped run is reported as bad bytes with its offset and content.
In particular a run that both begins with an opcode and ends with a terminator
is a framed message the parser could not interpret: an unhandled message
variant looks exactly like that (issue #77 was found from these lines), as do
the boundary-junk messages above and any message with a damaged body.

Reference: `crates/libxrk/src/parser.rs::record_unparsed_run`. The Cython
backend counts skipped bytes the same way behind its `show_bad` switch.
