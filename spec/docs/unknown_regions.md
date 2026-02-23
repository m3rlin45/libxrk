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

### Byte [2] `unk1` (uint8)
- **Observed**: Always 0x00
- **Status**: Validated as zero (assertion in parser)

### Byte [5] `unk3` (uint8)
- **Observed**: Always 0x84
- **Hypothesis**: Message subtype or expansion bus ID
- **Status**: Constant; validated as 0x84

### Byte [6] `unk4` (uint8)
- **Observed**: Always 0x06
- **Hypothesis**: Protocol version or message format indicator
- **Status**: Constant; validated as 0x06

### Bits [0:3] of `channel_field` (uint16 at offset [3])
- **Observed**: Always 0x4 (binary 100)
- **Hypothesis**: Message variant flag
- **Status**: Validated; actual channel index is `channel_field >> 3`
