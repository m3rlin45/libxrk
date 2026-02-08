# XRK Missing Data Investigation Report

**Date:** 2026-02-05
**Investigator:** Claude Code

## Executive Summary

This investigation identified several categories of data in XRK files that libxrk is NOT currently extracting:

1. **Dropped Channels (13 channels)** - Channels with unknown decoder types
2. **GPS Derived Channels (DLL-only)** - GPS_InlineAcc, GPS_LateralAcc, GPS_Yaw_Rate
3. **Unprocessed Message Types** - CDE, ENF, CAL, ODO fuel data, and others
4. **Raw GPS Data Fields** - Satellite count, accuracy metrics, raw ECEF velocities

---

## Phase 1: DLL API Gap Analysis

### Functions Wrapped vs Missing

| Category | Wrapped | Missing | Priority |
|----------|---------|---------|----------|
| Metadata | 5 | 2 | LOW |
| File Management | 2 | 2 | LOW |
| Laps | 2 | 0 | ✓ |
| Standard Channels | 5 | 2 | LOW |
| **GPS Derived Channels** | **0** | **5** | **HIGH** |
| **GPS Raw Channels** | **0** | **5** | **MEDIUM** |

### GPS Derived Channels (HIGH PRIORITY)

The DLL exposes computed GPS channels that libxrk does NOT:

```
get_GPS_channels_count(idx)
get_GPS_channel_name(idx, ch)
get_GPS_channel_units(idx, ch)
get_GPS_channel_samples_count(idx, ch)
get_GPS_channel_samples(idx, ch, times, values, count)
```

Expected channels:
- **GPS_InlineAcc** - Longitudinal acceleration computed from GPS speed
- **GPS_LateralAcc** - Lateral acceleration from GPS heading change
- **GPS_Yaw_Rate** - Rotation rate from GPS heading

**Action Required:** DLL wrapper expanded with these functions. Need to run comparison to determine exact channels exposed.

### GPS Raw Channels (MEDIUM PRIORITY)

May expose additional raw GPS sensor data:

```
get_GPS_raw_channels_count(idx)
get_GPS_raw_channel_name(idx, ch)
...
```

---

## Phase 2: Dropped Channels Analysis

### Decoder Types Found

From analysis of `tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk`:

| Decoder | Status | Channels |
|---------|--------|----------|
| 3 | KNOWN | Master Clk |
| 6 | KNOWN | 72 channels (Brake, Clutch, RPM, etc.) |
| 8 | **UNKNOWN** | GPS (2x) |
| 12 | KNOWN | Predictive Time |
| 15 | KNOWN | Oil_Temp, Water_Temp, TIRE_ALM, Gear |
| 20 | KNOWN | External Voltage, Shock Pots, etc. (16) |
| 24 | KNOWN | Best Run Diff, etc. (4) |
| 26 | **UNKNOWN** | Total Odometer |
| 27 | **UNKNOWN** | Reset Odometer 1-4 |
| 31 | **UNKNOWN** | Lap Time |
| 32 | **UNKNOWN** | Roll Time |
| 33 | **UNKNOWN** | Best Time |
| 37 | **UNKNOWN** | GPS_Hours |
| 38 | **UNKNOWN** | GPS_Date |
| 39 | **UNKNOWN** | GPS_Time |

### Channels Being Dropped

Consistent across all test files:

| Channel | Decoder | Notes |
|---------|---------|-------|
| iGPS / GPS | 8 | Internal GPS reference channel |
| Lap Time | 22 or 31 | Time value (decoder varies by file version) |
| Roll Time | 32 | Session rolling time |
| Best Time | 33 | Best lap time |
| Total Odometer | 26 | Running distance |
| Reset Odometer 1-4 | 27 | Resettable counters (4 channels) |
| GPS_Hours | 37 | GPS timestamp hours |
| GPS_Date | 38 | GPS date |
| GPS_Time | 39 | GPS time of day |

### Dropped Channels by File

| File | Total Channels | Dropped | Dropped % |
|------|----------------|---------|-----------|
| 86/...2248.xrk | 111 | 13 | 12% |
| SFJ/...0033.xrk | 39 | 12 | 31% |
| SFJ/...0101.xrk | 40 | 12 | 30% |
| SFJ/...0090.xrk | 40 | 12 | 30% |
| aim_official/test.xrk | 128 | 7 | 5% |

**Note:** All dropped channels typically have `size=0`, indicating they are either:
- Virtual/computed channels
- Stored in a different format
- Header-only metadata without sample data

---

## Phase 3: Message Types Analysis

### All Message Types Found

From scanning test files:

| Token | Count | Size | Current Handling |
|-------|-------|------|------------------|
| GPS | 54031 | 56 | ✓ Fully decoded to GPS Speed/Lat/Long/Alt |
| CHS | 111 | 112 | ✓ Channel definitions |
| CDE | 111 | 6 | ⚠️ Converted to hex string, not interpreted |
| GRP | 25 | 6-38 | ✓ Group definitions |
| LAP | 16 | 20 | ✓ Lap timing |
| CAL | 10 | 144 | ❌ Not processed |
| ODO | 6 | 384 | ⚠️ Partial - Fuel entries skipped |
| CNF | 2 | 158-18122 | ✓ Container for CHS/GRP |
| ENF | 2 | 199-203 | ⚠️ Recursively decoded but content discarded |
| SRC | 1 | 128 | ⚠️ Only extracts idn, rest ignored |
| GPSR | 1 | 36 | ❌ Not processed |
| HWNF | 1 | 33 | ✓ Parsed as string |
| iSLV | 2 | 64 | ❌ Not processed |
| RACM | 1 | 1 | ❌ Not processed |
| VET | 1 | 1 | ❌ Not processed |

### ENF Message Contents

ENF (Expansion Node?) messages contain nested messages:
- `DBUN` - Device bundle name
- `DBUT` - Device bundle type
- `DVER` - Device version
- `MANL` - Manufacturer (long name)
- `MODL` - Model (long name)
- `MANI` - Manufacturer (internal)
- `MODI` - Model (internal)

These are currently parsed but discarded.

### CAL Messages (144 bytes each)

Found 10 CAL messages per file. Likely calibration data for sensors. Not currently parsed.

---

## Phase 4: GPS Data Analysis

### Currently Extracted GPS Data

From GPS messages (56 bytes each):
- Timecode (offset 0-3)
- ECEF X/Y/Z position (cm) - converted to lat/long/alt
- ECEF dX/dY/dZ velocity (cm/s) - converted to speed

### GPS Data We're Ignoring

From the 56-byte GPS message structure:
- `itow_ms` (offset 4-7) - GPS time of week in milliseconds
- `weekN` (offset 12-13) - GPS week number
- `posacc_cm` (offset 28-31) - Position accuracy estimate
- `velacc_cms` (offset 44-47) - Velocity accuracy estimate
- `nsat` (offset 51) - Number of satellites

### GPS Derived Channels (DLL computes)

The DLL derives additional channels from GPS:
- **GPS_InlineAcc** - Differentiate GPS speed for longitudinal acceleration
- **GPS_LateralAcc** - Derived from speed and heading change
- **GPS_Yaw_Rate** - Heading rate of change

These could be computed from our existing GPS data.

---

## Phase 5: ODO/Fuel Data Gap

Current handling (aim_xrk.pyx:481-490):
```python
elif tok == _tokdec('ODO'):
    # not sure how to map fuel.
    # Fuel Used channel claims 8.56l used (2046.0-2037.4)
    # Fuel Used odo says 70689.
    data = {_nullterm_string(data[i:i+16]):
            {'time': ..., 'dist': ...}
            for i in range(0, len(data), 64)
            # not sure how to parse fuel, doesn't match any expected units
            if not _nullterm_string(data[i:i+16]).startswith('Fuel')}
```

Fuel entries are explicitly skipped because the mapping is unknown.

---

## Recommendations

### High Priority

1. **Add GPS Derived Channels**
   - Compute `GPS_InlineAcc` by differentiating GPS Speed
   - Compute `GPS_LateralAcc` from speed and heading
   - Compute `GPS_Yaw_Rate` from heading change
   - These are valuable for analysis and match DLL output

2. **Implement Missing Decoders**
   - Decoder 8 (iGPS) - Internal GPS reference
   - Decoder 22/31 (Lap Time) - Time format decoding (varies by file version)
   - Decoder 26 (Total Odometer) - Distance counter
   - Decoder 27 (Reset Odometer) - Resettable distance counters
   - Decoder 32 (Roll Time) - Time format decoding
   - Decoder 33 (Best Time) - Time format decoding
   - Decoder 37-39 (GPS Time/Date) - GPS timestamp fields
   - Note: Most have `size=0` - may be special metadata channels without sample data

### Medium Priority

3. **Expose GPS Accuracy Metrics**
   - Add `GPS_Satellites` channel from `nsat` field
   - Add `GPS_Position_Accuracy` from `posacc_cm`
   - Add `GPS_Velocity_Accuracy` from `velacc_cms`

4. **Expose ENF Device Info**
   - Add expansion device metadata to `log.metadata`

### Low Priority

5. **Investigate Fuel Mapping**
   - Correlate Fuel Used channel values with ODO fuel entries
   - Determine conversion formula

---

## Phase 6: Unknown Message Types Investigation

**Date:** 2026-02-08

Detailed analysis of the 5 unknown message types: CAL, GPSR, iSLV, RACM, VET.

### CAL — Calibration Data (144 bytes)

**Purpose:** Per-channel sensor calibration/conversion parameters. Links to specific channels via channel index.

**Count:** 8–10 per file (only for channels with analog sensor inputs or special decoders). Not present in `aim_official/test.xrk`.

**Structure:**

| Offset | Type | Description | Notes |
|--------|------|-------------|-------|
| 0–3 | 4 bytes | Hardware address / sensor ID | Varies per sensor; bytes[0:2] seem hardware-specific |
| 4–7 | uint32 | **Channel index** | Matches CHS channel index exactly |
| 8–11 | uint32 | Always 1 | Version or entry count |
| 12–15 | uint32 | Always 0 | Reserved |
| 16–19 | uint32 | Always 0 | Reserved |
| 20–23 | uint32 | Calibration point count? | Usually 20; sometimes 1 for special channels |
| 24–27 | float32 | **Zero offset / bias** | Varies per sensor installation (-68 to +1669) |
| 28–31 | float32 | Scale factor | 1.0 for standard analog channels |
| 32–35 | float32 | Range parameter | 1.0 for standard; -180 for GPS-type |
| 36–39 | float32 | Range parameter | 0.0 for standard; 180 for GPS-type |
| 40–43 | float32 | Midpoint / coefficient | 0.5 for standard analog |
| 44–47 | float32 | Additional parameter | Usually 0.0 |
| 48–51 | float32 | Max range / ADC max | 5000 for standard analog |
| 52–55 | float32 | Additional range | 0.0 for standard |
| 56–59 | float32 | Mid range / ADC mid | 2500 for standard analog |
| 60–63 | float32 | Additional parameter | Usually 0.0 |
| 64–139 | zeros | Reserved | Room for extended calibration curves |
| 140–143 | 4 bytes | Possibly timestamp or CRC | Non-zero, varies per message |

**Observations:**
- Standard analog channels (decoder=20): `offset=bias, scale=1.0, mid=0.5, max=5000, mid=2500`
- Lap Time (decoder=31): Very different parameters (1669, -3315, -180..180 range) — likely lap timing conversion
- Best Run Diff (decoder=24): Also different parameters (80, 50, 3427, 303)
- Odometer channels (decoder=26/27): Standard analog pattern despite being virtual
- The float32 at offset 24 is the **zero offset bias** — unique per physical sensor

**Channel correlation (86 file):**

| CAL | Channel | Decoder | Offset (f32[24]) |
|-----|---------|---------|------------------|
| 0 | LateralAcc | 20 | -0.083 |
| 1 | VerticalAcc | 20 | 0.012 |
| 2 | RollRate | 20 | -0.014 |
| 3 | PitchRate | 20 | -0.634 |
| 4 | YawRate | 20 | 0.499 |
| 5 | GPS_LateralAcc | 20 | 0.681 |
| 6 | Best Today Diff | 24 | -68.4 |
| 7 | Prev Lap Diff | 24 | -64.3 |
| 8 | Ref Lap Diff | 24 | -63.0 |
| 9 | Roll Time | 32 | -66.0 |

**Priority: LOW** — Calibration is already applied before data is stored in the XRK file. These values are informational only and would be useful for diagnostics/verification but not for data extraction.

---

### GPSR — GPS Receiver Configuration (36 bytes)

**Purpose:** Identifies the GPS receiver type and links to the GPS reference channel.

**Count:** 1 per file. Not present in `aim_official/test.xrk`.

**Structure:**

| Offset | Type | Description | Notes |
|--------|------|-------------|-------|
| 0–3 | uint32 | Session/config ID | Varies per session |
| 4–7 | char[4] | GPS type identifier | `"GPS\0"` (external) or `"iGPS"` (internal) |
| 8–11 | 4 bytes | Config parameters | Non-zero only for external GPS (e.g., 0x0422, "LA") |
| 12–19 | 8 bytes | Receiver config | Non-zero only for internal GPS (consistent across SFJ sessions) |
| 20–21 | uint16 | Reserved | Always 0 |
| 22–23 | uint16 | **GPS channel index** | Points to GPS/iGPS channel in CHS (109 or 37) |
| 24–27 | uint16+uint16 | Device ID (external only) | Matches iSLV idn[2:4] for 86 file |
| 28–31 | uint32 | Logger ID (external only) | Matches main logger idn for 86 file |
| 32–35 | uint32 | Always 410 (0x019A) | Possibly GPS sample interval config |

**Key findings:**
- `"GPS\0"` at offset 4 = external GPS receiver (86 file with expansion devices)
- `"iGPS"` at offset 4 = internal GPS receiver (SFJ files with built-in GPS)
- Offset 22–23 contains the **channel index** for the GPS reference channel
- External GPS receivers have logger ID info; internal ones have receiver config bytes

**Priority: LOW** — Metadata only. Could expose GPS receiver type in `log.metadata` but provides no new data channels.

---

### iSLV — Slave/Expansion Device Configuration (64 bytes)

**Purpose:** Describes CAN expansion devices connected to the logger. Wraps an embedded `idn` message.

**Count:** 0–2 per file. Present in 86 file (2 devices) and Suzuka SFJ (1 device). Not in Fuji SFJ or aim_official files.

**Structure:**

| Offset | Type | Description | Notes |
|--------|------|-------------|-------|
| 0–2 | char[3] | Token: `"idn"` | Same format as SRC embedded idn |
| 3 | uint8 | Version: 1 | |
| 4–5 | uint16 | Payload length: 56 | |
| 6–7 | uint16 | Model ID | Device model (e.g., 1313=MXP, 739=MXm, 639=SmartyCam) |
| 8–9 | uint16 | Additional ID | Hardware revision? |
| 10–11 | uint16 | Reserved | Always 0 |
| 12–15 | uint32 | Device serial number | Unique per device |
| 16–61 | 46 bytes | Firmware version / capabilities | Contains version pairs, mostly zeros |
| 62–63 | padding | Zeros | |

**Observations:**
- Format identical to SRC-embedded idn messages
- Already partially handled by ENF message parsing (which extracts device name/manufacturer)
- iSLV provides the **hardware identification** while ENF provides **software/bundle metadata**
- 86 file has 2 iSLV messages matching the 2 ENF messages (both expansion CAN devices)

**Priority: LOW** — Already have device info from ENF. Could enrich `Expansion Devices` metadata with serial numbers and model IDs from iSLV, but low user value.

---

### RACM — Race Mode (1 or 6 bytes)

**Purpose:** Race/timing mode configuration flag.

**Count:** 1–2 per file. Not present in `aim_official/test.xrk`.

**Structure (varies by version):**

| Version | Size | Content | Files |
|---------|------|---------|-------|
| ver=0 | 1 byte | `0x00` — mode flag (always 0) | All files |
| ver=1 | 6 bytes | `"speed\0"` — null-terminated mode string | SFJ files only |

**Observations:**
- 86 file: single byte (0) — no race mode set
- SFJ files: have both version 0 (byte=0) and version 1 ("speed")
- "speed" likely refers to speed-based lap detection mode (vs beacon-based)
- Other possible values might include "beacon", "gps", etc.

**Priority: LOW** — Simple metadata. Could expose as `log.metadata['Race Mode']` but provides no new data.

---

### VET — Vehicle Electronics Type (1 byte)

**Purpose:** Vehicle electronics/wiring configuration type.

**Count:** 1 per file. Not present in `aim_official/test.xrk`.

**Value:** Always `0x00` across all test files.

**Observations:**
- Likely an enum for different vehicle electronics configurations
- Value 0 probably means "default" or "custom"
- Would need files from different vehicle types to understand the range of values

**Priority: VERY LOW** — Single byte flag, always 0 in test data. No actionable information.

---

### Summary Table

| Message | Decoded? | Contains Data? | Actionable? | Priority |
|---------|----------|----------------|-------------|----------|
| **CAL** | Yes | Calibration coefficients per channel | Diagnostic only — calibration already applied | LOW |
| **GPSR** | Yes | GPS type, channel index, device ID | Metadata enrichment | LOW |
| **iSLV** | Yes | Expansion device idn (model, serial) | Complements ENF data | LOW |
| **RACM** | Yes | Race/timing mode flag | Simple metadata | LOW |
| **VET** | Yes | Vehicle electronics type | Always 0 | VERY LOW |

**Conclusion:** None of these message types contain sample data or new channels. They are all configuration/metadata messages. The most potentially useful for end users would be:
1. CAL — for sensor diagnostics/verification (confirming calibration is correct)
2. GPSR — for distinguishing external vs internal GPS receiver
3. RACM — for knowing what lap detection mode was configured

---

## Files Modified/Created

| File | Change |
|------|--------|
| `tests/reference_dll/aim_dll_wrapper.py` | Expanded with GPS channel functions |
| `scripts/investigate_missing_data/message_scanner.py` | New tool |
| `scripts/investigate_missing_data/decoder_analysis.py` | New tool |
| `scripts/investigate_missing_data/channel_comparison.py` | New tool |
| `scripts/investigate_missing_data/dll_function_enumeration.py` | New tool |
| `scripts/investigate_missing_data/unknown_messages.py` | New tool — CAL/GPSR/iSLV/RACM/VET analysis |

---

## Test Data Summary

| File | Defined Channels | libxrk Extracts | Dropped | Synthesized |
|------|------------------|-----------------|---------|-------------|
| 86/...2248.xrk | 111 | 92 | 13 | 4 GPS |
| SFJ/...0033.xrk | 39 | ~27 | 12 | 4 GPS |
| SFJ/...0101.xrk | 40 | ~28 | 12 | 4 GPS |
| SFJ/...0090.xrk | 40 | ~28 | 12 | 4 GPS |
| aim_official/test.xrk | 128 | ~100+ | 7 | 4 GPS |

libxrk synthesizes 4 GPS channels from raw GPS messages:
- GPS Speed (from ECEF velocity)
- GPS Latitude (converted from ECEF)
- GPS Longitude (converted from ECEF)
- GPS Altitude (converted from ECEF)

And filters out internal channels (Master Clk, StrtRec).

---

## Next Steps

1. **Run DLL comparison** - Use Wine to compare GPS derived channels from DLL
2. **Implement GPS derived channels** - Compute GPS_InlineAcc, GPS_LateralAcc, GPS_Yaw_Rate
3. **Add GPS accuracy channels** - Expose satellite count and accuracy metrics
4. **Document decoder formats** - For channels with `size > 0` that are being dropped
