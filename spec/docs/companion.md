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

Reference: `aim_xrk.pyx:1139-1160` (`_decompress_if_zlib`)

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

Reference: `aim_xrk.pyx:519-521`, `aim_xrk.pyx:652-658`

## 3. Decoder Dispatch Table

Each CHS channel definition includes a `decoder_type` byte at offset [20].
This determines how to interpret the raw data bytes:

| decoder_type | struct fmt | Size | Interpolate | Notes |
|-------------|-----------|------|-------------|-------|
| 0  | `i` (int32)   | 4 | No  | Master Clock on M4GT4 |
| 1  | `H` (uint16)  | 2 | Yes | **float16 encoded** — decode uint16 as IEEE 754 half-precision |
| 3  | `i` (int32)   | 4 | No  | Master Clock on ScottE46 |
| 4  | `h` (int16)   | 2 | No  | |
| 6  | `f` (float32) | 4 | Yes | Standard float |
| 8  | `i` (int32)   | 4 | No  | iGPS reference |
| 11 | `h` (int16)   | 2 | No  | |
| 12 | `i` (int32)   | 4 | No  | Predictive Time |
| 13 | `B` (uint8)   | 1 | No  | Status field |
| 15 | `H` (uint16)  | 2 | No  | **Gear lookup** — map ASCII 'N'→0, '1'→1, ..., '6'→6 |
| 20 | `H` (uint16)  | 2 | Yes | **float16 encoded** — same as decoder 1 |
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

**Float16 encoding (decoders 1, 20):** The raw uint16 value is reinterpreted as
IEEE 754 half-precision (binary16) and promoted to float32. This is the most
common encoding for analog sensor channels.

**Gear lookup (decoder 15):** The raw uint16 value is an ASCII character code.
Map `'N'`(0x4E)→0, `'1'`(0x31)→1, ..., `'6'`(0x36)→6.

**Special fixups:**
- Channels named `Calculated_Gear` or `PreCalcGear` use decoder `Q` (uint64)
  with bit extraction: `(value >> 16) & 7` (returns 0 if bit 19 is set).
- Channels with units `V` (Volts): divide by 1000 (hardware reports millivolts).

Reference: `aim_xrk.pyx:95-137`

## 4. M-Message Timing

`(M` messages carry burst data: multiple consecutive samples for a single
channel. The timing for each sample within the burst is:

```
sample[i].timecode = base_timecode + i * Mms
```

Where `Mms = CHS.sample_period_us // 1000` (the channel's sample period in ms).

This means M-messages are used for high-frequency channels (e.g., 500 Hz shock
potentiometers with Mms=2). The base timecode is the message timecode.

Reference: `aim_xrk.pyx:314-326`

## 5. GPS Data (u-blox NAV-SOL)

GPS messages contain a 4-byte AIM timecode followed by a 52-byte u-blox NAV-SOL
(Navigation Solution Information) payload. All GPS-derived channels are computed
from these raw ECEF coordinates and velocities.

### ECEF to LLA Conversion

The ECEF (Earth-Centered, Earth-Fixed) coordinates from NAV-SOL are converted
to latitude/longitude/altitude using the **Vermeille 2003** algorithm, which
provides high accuracy without iteration.

Reference: `gps.py:62-123` (`ecef2lla`)

### Computed GPS Channels

From the raw NAV-SOL fields, the following channels are derived:

- **GPS Speed** = `sqrt(ecefVX² + ecefVY² + ecefVZ²) / 100.0` [m/s]
- **GPS Latitude/Longitude/Altitude** = `ecef2lla(ecefX/100, ecefY/100, ecefZ/100)`
- **GPS_Satellites** = `numSV`
- **GPS_Fix** = `gpsFix`
- **GPS_pDOP** = `pDOP / 100.0`
- **GPS_Position_Accuracy** = `pAcc / 100.0` [m]
- **GPS_Velocity_Accuracy** = `sAcc / 100.0` [m/s]

### Derived Acceleration/Yaw Channels

These require velocity transformation to ENU (East-North-Up) coordinates:

- **GPS_InlineAcc** = `d(speed)/dt / 9.81` [g]
- **GPS Heading** = `atan2(V_east, V_north)` [deg]
- **GPS_Yaw_Rate** = `d(heading)/dt` [deg/s] (with ±180° wrap handling)
- **GPS_LateralAcc** = `speed × yaw_rate × π/180 / 9.81` [g]

Reference: `aim_xrk.pyx:884-1010`, `gps.py`

## 6. GPS Timing Bug

Some AIM MXP firmware versions have a bug where GPS timecodes periodically
have their upper 16 bits corrupted, causing 65533ms jumps in the timestamp
stream.

### Detection

When GPS timecodes are not monotonically increasing (`timecodes[i+1] < timecodes[i]`),
the upper 16 bits are assumed to be unreliable.

### Correction (Timecode Reconstruction)

1. Mask to lower 16 bits: `tc = timecodes & 0xFFFF`
2. Add the base offset: `tc += timecodes[0] - (timecodes[0] & 0xFFFF)`
3. Fix wrap-arounds: accumulate +65536 whenever `tc[i+1] < tc[i]`

Reference: `aim_xrk.pyx:920-922`

### GNFI-Based Detection

A more robust approach uses GNFI (Logger Internal Clock) messages as ground
truth. GNFI runs on the logger's internal clock, not GPS, so it's immune to
the GPS timing bug. Large discrepancies between GNFI and GPS timecodes indicate
the bug is present.

Reference: `gps.py:131-214` (`fix_gps_timing_gaps`)

## 7. Lap Detection

### Primary: LAP Messages

When LAP messages exist, they define lap boundaries directly:
- Filter to `segment == 0` (ignore segment markers)
- Deduplicate by `lap_num`
- Compute `start_time = end_time - duration`
- Normalize to 0-based indexing

### Fallback: GPS Cross-Track

When no LAP messages exist, laps are detected from GPS by computing the
cross-track distance to the start/finish line:

1. Get S/F coordinates from TRK message (`sf_lat`, `sf_long`)
2. Convert GPS positions to ECEF
3. Find positions closest to the S/F line (minimum cross-track distance)
4. Use these crossing points as lap boundaries

Reference: `aim_xrk.pyx:1038-1099`, `gps.py:216-290` (`find_laps`)

## 8. Channel Merging

Different channels have different sample rates (1 Hz to 500 Hz). To merge
them into a single table:

1. Compute a merged timebase via full outer join on timecodes
2. For each channel, interpolate (if `interpolate=True`) or forward-fill
   values at the merged timecodes

Reference: `base.py` (`get_channels_as_table`)

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
| 9  | Hz     | 0 |
| 11 | (none) | 0 |
| 12 | mm     | 0 |
| 14 | bar    | 2 |
| 15 | rpm    | 0 |
| 16 | km/h   | 0 |
| 17 | C      | 1 |
| 18 | ms     | 0 |
| 19 | Nm     | 0 |
| 20 | km/h   | 0 |
| 21 | V      | 1 |
| 22 | l      | 1 |
| 24 | l/s    | 0 |
| 26 | time?  | 0 |
| 27 | A      | 0 |
| 30 | lambda | 2 |
| 31 | gear   | 0 |
| 33 | %      | 2 |
| 43 | kg     | 3 |

Reference: `aim_xrk.pyx:146-171`
