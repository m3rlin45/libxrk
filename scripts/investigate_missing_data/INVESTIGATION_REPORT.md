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

5. **Parse CAL Messages**
   - Calibration data (144 bytes per sensor)
   - Would enable channel scaling verification

6. **Investigate Fuel Mapping**
   - Correlate Fuel Used channel values with ODO fuel entries
   - Determine conversion formula

---

## Files Modified/Created

| File | Change |
|------|--------|
| `tests/reference_dll/aim_dll_wrapper.py` | Expanded with GPS channel functions |
| `scripts/investigate_missing_data/message_scanner.py` | New tool |
| `scripts/investigate_missing_data/decoder_analysis.py` | New tool |
| `scripts/investigate_missing_data/channel_comparison.py` | New tool |
| `scripts/investigate_missing_data/dll_function_enumeration.py` | New tool |

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
