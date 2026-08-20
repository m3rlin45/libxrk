# AIM XRK Format — Unknown Regions Catalog

This document catalogs every byte range in the XRK format that has not been
fully reverse-engineered. For each unknown region, we document:
- **Location**: Message type and byte offset
- **Observed values**: What we've seen across test files
- **Hypothesis**: Best guess at the field's purpose
- **Status**: Whether it's actively investigated

## CHS (Channel Definition, 112 bytes)

The CHS payload is the most complex message type. Of 112 bytes, the following
regions have known purpose but unknown exact semantics:

### [2:4] Padding (2 bytes)
- **Observed**: Always `00 00` across all test files (SFJ, 86, aim_official)
- **Hypothesis**: Reserved/padding
- **Status**: Validated as zero in all 8 test files

### [4:6] Hardware ID (uint16)
- **Observed**: 0 for most channels; non-zero only for GPS-related channels
- **Hypothesis**: Hardware reference ID, used for GPS module identification
- **Status**: Partially understood; non-GPS values always 0

### [8:12] Hardware Reference (uint32)
- **Observed**: 0 for most channels; non-zero only when hardware_id is non-zero
- **Hypothesis**: Extended hardware reference for GPS module
- **Status**: Correlates with hardware_id

### [13] `maybe_display_format` (uint8)
- **Observed values**: 0, 1, 6, 9, 11, 13, 14, 17, 18, 19, 26, 128, 130, 169
- **Role**: Part of the channel function lookup key.
  When combined with `unit_type_byte` [12], this pair determines the RS3
  "Function" label (e.g., "Temperature", "Engine RPM", "Lateral Acceleration").
  Value 0 is the default for generic CAN/ECU channels; non-zero values are
  AIM-assigned function categories (IMU axes, shock pots, etc.).
- **Status**: Partially decoded — used in `_function_map` / `resolve_function()`
  lookup table alongside `unit_type_byte` and `maybe_config_flags`.

### [14:16] `maybe_config_flags` (uint16)
- **Observed values**: Various bit patterns (0, 1, 4, 6, 128, 519, 1024, 1286, 4096)
- **Role**: Acts as a tiebreaker for the channel function lookup in the single
  ambiguous case `(display_format=0, unit_type_byte=0x11)`:
  - `config_flags=0` → "Temperature"
  - `config_flags=1` → "Device Temperature" (internal logger sensor)
- **Status**: Partially decoded for function disambiguation

### [17:20] Padding (3 bytes)
- **Observed**: Always `00 00 00`
- **Status**: Validated as zero

### [21:24] Padding (3 bytes)
- **Observed**: Always `00 00 00`
- **Status**: Validated as zero

### [56:64] Padding (8 bytes)
- **Observed**: Always all zeros
- **Status**: Validated as zero

### [68:70] Data Offset (uint16)
- **Observed**: Sequential offsets within group
- **Hypothesis**: Byte offset of this channel's data within a group's packed payload
- **Status**: Understood; used by group message unpacking

### [70:72] Padding (2 bytes)
- **Observed**: Always `00 00`
- **Status**: Validated as zero

### [73:76] Padding (3 bytes)
- **Observed**: Always `00 00 00`
- **Status**: Validated as zero

### [80] Device Node ID (uint8)
- **Observed values**: 0, 1, 2
- **Hypothesis**: CAN bus node address or expansion device index
- **Status**: Correlates with expansion device presence

### [81] `maybe_device_flags` (uint8)
- **Observed values**: 0x00, 0x02, 0x10
- **Hypothesis**: Device capability flags
- **Status**: Three distinct values seen; meaning unknown

### [82:84] Padding (2 bytes)
- **Observed**: Always `00 00`
- **Status**: Validated as zero

### [84] `maybe_output_type` (uint8)
- **Observed values**: 1, 4, 6, 0xFF
- **Hypothesis**: Output format or display mode selector
- **Status**: Four distinct values; not correlated with other fields

### [85:88] Padding (3 bytes)
- **Observed**: Always `00 00 00`
- **Status**: Validated as zero

### [88:92] Display Index (uint32)
- **Observed values**: 0-based sequential, or 0xFFFFFFFF
- **Hypothesis**: Index for display ordering; 0xFFFFFFFF = virtual/hidden channel
- **Status**: Partially understood

### [92] `maybe_output_size` (uint8)
- **Observed values**: 0, 2, 4, 8
- **Hypothesis**: Output data size (for display or CAN transmission?)
- **Status**: Values correlate loosely with data_size

### [93:96] Padding (3 bytes)
- **Observed**: Always `00 00 00`
- **Status**: Validated as zero

## CAL (Calibration, variable size)

### [0:8] Prefix (8 bytes)
- **Observed**: Various patterns, often starts with `FF FF`
- **Hypothesis**: Channel reference or calibration ID
- **Status**: Not decoded

### [8:12] `u32_8` (uint32)
- **Observed**: Always 1
- **Hypothesis**: Calibration version or flag
- **Status**: Constant across all test files; expected to be 1

### [12:20] Padding (8 bytes)
- **Observed**: Various patterns
- **Hypothesis**: May contain additional calibration parameters
- **Status**: Not decoded

## GNFI (32 bytes)

### [4:32] Unknown (28 bytes)
- **Observed**: Various patterns, appears to contain logger state data
- **Hypothesis**: Internal logger diagnostics (temperature, voltage, etc.)
- **Status**: Only timecode at [0:4] is used; rest is unexplored

## GPSR (GPS Receiver, 36 bytes)

### [0:4] Prefix (4 bytes)
- **Observed**: Various patterns
- **Hypothesis**: GPS module configuration
- **Status**: Not decoded

### [8:22] Middle section (14 bytes)
- **Observed**: Various patterns
- **Hypothesis**: GPS configuration parameters
- **Status**: Not decoded

### [24:32] Post section (8 bytes)
- **Observed**: Various patterns
- **Status**: Not decoded

### [32:36] `u32_32` (uint32)
- **Observed**: Always 410
- **Hypothesis**: GPS message rate or protocol version
- **Status**: Constant across all test files; unexpected values trigger a warning

## idn (Logger Identity, 56 bytes)

### [2:6] Padding (4 bytes)
- **Observed**: Various patterns
- **Hypothesis**: Additional hardware identifiers
- **Status**: Not decoded

### [10:56] Remaining (46 bytes)
- **Observed**: Various patterns
- **Hypothesis**: Firmware version, configuration, serial data
- **Status**: Only model_id [0:2] and logger_id [6:10] are extracted

## SRC (Source, variable)

The SRC message wraps an embedded idn with a 6-byte header:
- [0:3]: Token string "idn"
- [3]: Version byte
- [4:6]: Length (uint16)
- [6:62]: idn payload

### [62:end] Remaining bytes
- **Status**: Not used

## LAP version 2 (32 bytes)

The spec's LAPPayload models the 20-byte version-0/1 layout. The AIM
official sample (`tests/test_data/aim_official/test.xrk`) carries LAP
messages with `version=2` and a **32-byte** payload. Observations from
that file (33 messages, 3 segments × 11 laps):

- [0:20]: Same field positions as v1, **except** [16:20] is *not* the
  absolute lap end time — its value tracks the lap duration [4:8]
  (always a few ms smaller), purpose unknown.
- [20:24]: uint32, always 0. **Status**: not decoded.
- [24:28]: uint32, small bit patterns (`0x0103`, `0x0203`, `0x010203`,
  `0x0306`). **Hypothesis**: per-segment flags. **Status**: not decoded.
- [28:32]: uint32, **absolute lap end time [ms]** on the logger clock.
  `[28:32] - duration` of the first LAP message equals the minimum data
  timecode of the file (verified: 46957 − 46946 = 11 = min data tc),
  i.e. this field plays the role that [16:20] plays in v1.
  **Status**: decoded; implemented by the spec (`LAPPayload.end_time`
  Computed field) and by both backends, discriminated by payload length.
  Round-trip coverage: `TestLAPRoundTrip.test_lap_v2_round_trip`.

## ODO (Odometer, n×64 bytes)

Each 64-byte record:
- [0:16]: Name (null-terminated ASCII)
- [16:20]: Time (uint32, seconds)
- [20:24]: Distance (uint32, meters)
- [24:64]: **Unknown** (40 bytes)
  - **Observed**: Contains non-zero data for Fuel records
  - **Hypothesis**: Fuel-specific metrics (consumption rate, remaining fuel)
  - **Status**: Fuel records are excluded from parsing due to unknown encoding

## TRK (Track, 44+ bytes)

### [32:36] Padding (4 bytes)
- **Observed**: Various patterns
- **Hypothesis**: Track configuration flags
- **Status**: Not decoded

### [44:end] Remaining bytes
- **Observed**: Present in some files
- **Hypothesis**: Additional track geometry (sector markers?)
- **Status**: Not decoded

## (c) Expansion Data Messages

Three variants coexist. The `(unk1, unk4)` pair at offsets [2] and [6] acts
as a variant tag; `unk3` at [5] is `0x84` in all known variants. See also
`scripts/investigate_missing_data/c_variant_scanner.py` for the RE that
established the channel_field mapping and timing rules.

| Variant | `unk1` | `unk4` | Total size | Payload           | Timecode source               |
|---------|:------:|:------:|:----------:|-------------------|-------------------------------|
| V1      | 0x00   | 0x06   | 12 + N     | N bytes, CHS-sized | Embedded at offset 7 (4 bytes) |
| V2 long | 0x00   | 0x08   | 16         | 4 bytes (2 × fp16) | Embedded at offset 7 (4 bytes) |
| V3 short| 0x01   | 0x02   | 10         | 2 bytes (1 × fp16) | Synthesized — inherits from preceding V2 on same logical channel plus the channel's sample period |

### `channel_field` (uint16 at offsets [3:5])

**V1** uses `channel_index = channel_field >> 3`, with the low 3 bits always
equal to 0x4.

**V2 and V3** do not follow the V1 rule (observed `channel_field` values
for V2/V3 exceed the valid CHS index range and the low 3 bits vary). The
mapping is resolved empirically against CHS at post-parse time — see
`spec.xrk_format._resolve_c_variants` and issue #68:

- V2/V3 `channel_field` values in the observed corpus form **pairs**
  `(base, base+4)` where `base & 0xF ∈ {0, 8}`. Each pair represents one
  logical channel (typically a 500Hz shock potentiometer carrying V2
  messages on both `base` and `base+4` plus V3 messages on `base+4`).
- **Orphan** channel_fields appear only on V2 messages with no paired
  partner. Each orphan is one logical channel (typically a 100Hz
  accelerometer).
- Pairs and orphans are assigned to CHS channels (`decoder_type=20`,
  `source_type=1`, `hardware_id ≠ 0`) in sorted order of
  `(hardware_ref, source_channel_id)`, partitioned by sample period:
  `mms ≤ 5` → paired candidates, `5 < mms ≤ 15` → orphan candidates.

### V2 payload layout

Bytes [11:13] and [13:15] each encode an fp16 sample of the same logical
channel. The sample timestamps depend on which side of the pair the
`channel_field` is on:

| `channel_field` side | bytes [11:13] (V2[0]) sample tc | bytes [13:15] (V2[1]) sample tc |
|----------------------|---------------------------------|---------------------------------|
| base (low nibble 0 or 8) | `tc`                        | `tc − 4ms`                      |
| +4 (low nibble 4 or c)   | `tc − 2ms`                  | `tc − 4ms`                      |
| orphan (accelerometers)  | `tc`                        | `tc − 4ms` (provisional)        |

The base/+4 offset difference reflects that the two `channel_field`
values in a pair interleave in the device's sampling schedule: V2(+4)
covers the "earlier half" of a 10ms cycle and V2(base) covers the
"later half". Together with V3 they produce 5 samples at 2ms intervals.

On the very first V2 message on a `channel_field`, bytes [13:15] may
contain a stale pre-roll value not reflected in DLL output — this is a
sub-percent boundary effect, not a decode bug.

### V3 payload layout

Bytes [7:9] encode one fp16 sample of the logical channel carried by the
same `channel_field`. V3 always appears on the `+4` side of a pair and
is absent on orphan channel_fields. Its timecode is synthesized
post-parse by `_resolve_c_variants` as follows (per-pair, in file order):

- If the most recently seen V2 on the pair was on `base`:
  `V3.tc = last_V2(base).tc − mms` (i.e., one sample period before the
  latest V2(base).tc).
- Otherwise (most recent was on `+4`):
  `V3.tc = last_V2(+4).tc + mms`.

This branching rule handles 11ms "long" cycles (occasional cycle-boundary
drift in the logger clock) where a single rule would produce tcs
off-by-one-sample relative to DLL.

### Accuracy vs AIM DLL on the issue68 fixture (LR_Shock_Pot, 213k samples)

| Metric                                      | Value        |
|---------------------------------------------|--------------|
| DLL sample count                            | 213,385      |
| Spec-emitted unique-tc sample count         | 213,406      |
| DLL samples covered by spec at same tc      | 213,385 (100%) |
| Spec samples whose value matches DLL at tc  | 213,355 (99.97%) |
| "Extra" spec samples (no corresponding DLL tc) | 21 (<0.01%) |
| Spec samples with value differing from DLL  | 30 (<0.02%) |

The residual ~51 disagreements are not decode bugs — they are wire-level
samples whose fp16 values are not reflected in DLL output at any nearby
tc. Most are V2[1] values that appear to be ignored or filtered by AIM's
proprietary sample-reconstruction pipeline. The spec decode exposes the
raw wire data faithfully; downstream backends (Cython, Rust) conform
to the spec and therefore exhibit the same ~0.02% DLL offset.

Reaching 100% exact match is not achievable without reverse-engineering
AIM's internal sample filtering, which is not part of the wire format.
For libxrk users, the practical impact is: ≤0.02% of shock-pot samples
may differ from RaceStudio's displayed values by small amounts, and ≤0.01%
of spec-emitted samples may not appear in RaceStudio at all. Both are
below AIM's own sample-rate jitter (occasional 3ms gaps in 2ms streams).
