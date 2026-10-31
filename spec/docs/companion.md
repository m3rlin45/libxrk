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
IEEE 754 half-precision (binary16) and promoted to float32. This is the most
common encoding for analog sensor channels.

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
In the spec this is the `NonFiniteToNaN` adapter wrapping the float codecs in
`SAMPLE_CODECS` (`spec/xrk_format.py`); `channel_sample_codec(chs)` is the
complete per-sample decoder for a channel and `decode_channel_values()` its
vectorised form.

**Sample streams:** a data message's payload is structured in the spec, not
opaque: an `(M` burst's `samples` are `Array(count, Bytes(CHS.data_size))`,
and a `(G` payload's `samples` are a `Struct` of its member channels'
samples in GRP order, each `CHS.data_size` wide, built from the GRP message
and selected by `group_index` at parse time (`_GroupLayout`). Both are
`RestreamData` views over the raw `data`, so byte-identical rebuilds are
unaffected. Because messages interleave channels and may repeat, a channel's
values exist only after an accumulation pass, which is why both backends
accumulate first and decode after. The spec does it in the parse hook
(`_accumulate`): one accumulator per message family (G per group; S, c and M
per channel), a message accepted only when its timecode is strictly greater
than the accumulator's last accepted one, and an M burst overlapping the
accepted stream contributing only its samples past the overlap (section 4).
The result is `ParseResult.streams`, contiguous bytes per channel in
`data_size` slots. A stream's grammar is `channel_stream(chs)`:
`GreedyRange` of the channel's codec padded to the slot; `channel_values()`
decodes every stream with the vectorised equivalent, and `test_spec.py`
compares every value of every fixture against both backends.
`0.0` would be a plausible-looking lie for a lambda or a brake pressure; `NaN`
is what pandas/pyarrow consumers already treat as missing.

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
in the spec), including the "out" lap and the final partial lap below. The
table is then finished by the shared GPS pipeline (`libxrk-gps` `laps.rs`,
`finish_laps`): the last lap becomes **"in"** when the GPS position at its end
is more than 30 m from the TRK start/finish (an out-lap is never
reclassified), and a file whose table is empty gets the GPS fallback below.

#### Final partial lap

When the recording runs past the last beacon crossing, the official AIM DLL
reports one more lap, from the last LAP message's end to the **end of data**.
Observed on 9 corpus files plus the SFJ 0101 and Suzuka 0090 fixtures:

- it is added whenever at least one lap was completed (7 of 7 files with two
  or more laps), and never when the out-lap is the only lap (2 of 2);
- its end is the **header timecode of the last data message in file order**
  (`(S`/`(G`/`(M`/`(c`), taken even when that message is truncated. Suzuka
  0090 ends with an `(S` whose payload is cut off by the trailing RCR/VEH
  frames; both backends discard the sample, but its header timecode is still
  where the DLL ends the lap. This is not the maximum timecode over channels:
  loggers buffer per channel, so a channel's final sample can sit earlier in
  the file than another's and be up to tens of milliseconds later in time
  (58 ms on Suzuka 0090). Only the file-order rule matches the DLL to the
  millisecond. For an `(M` burst the header (first-sample) timecode is used;
  no file in hand ends with one, so that detail is unverified.

libxrk implements the same rule. The synthesized lap is typed `"in"`; the lap
before it ended at a LAP message, so it is `"full"` even if the GPS-distance
heuristic would have called it `"in"`.

In the spec: `XRKMessage` falls back to a header-only parse (`_SMessageHeader`
and friends, `Peek`ed so bad-byte recovery still advances one byte) when a data
message's body does not parse, and both whole messages and bare headers set
`ParseResult.last_data_timecode`. `ParseResult.end_of_data()` makes it
session-relative and `ParseResult.lap_table()` builds the LAP-message table;
`test_spec.py` runs the shared GPS pipeline over it and compares the result
against both backends, and the table itself against the DLL.

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
