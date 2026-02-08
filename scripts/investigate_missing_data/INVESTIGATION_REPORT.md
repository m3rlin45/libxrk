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

**Purpose:** Per-channel sensor calibration parameters. Each CAL message describes how to convert raw sensor readings to engineering units for one channel.

**Count:** 8–10 per file (only for channels with calibrated analog inputs or special decoders). Not present in `aim_official/test.xrk`.

**Channel matching:** CAL messages are matched to channels by their calibration values, NOT by a channel index field. The values at CAL offsets 24–27 and 28–31 are **duplicated** in the corresponding CHS message at offsets 96–99 and 100–103, providing a reliable cross-reference. The `uint32` at CAL offset 4 is an internal reference number (possibly a physical input or calibration slot number), not a channel index.

**Calibration types:** The `uint32` at offset 20 determines the calibration type:

#### Type 1 — 2-point linear calibration (user-calibrated sensors)

Used for potentiometer-based inputs where the user sets two calibration points in RaceStudio (e.g., steering angle sensor, throttle position sensor).

| Offset | Type | Field | Steering example | Throttle (ACCEL) example |
|--------|------|-------|------------------|--------------------------|
| 0–3 | 4 bytes | Hardware/sensor ID | `84 0d 00 00` | `8c 0e 00 00` |
| 4–7 | uint32 | Internal reference number | 1 | 3 |
| 8–11 | uint32 | Always 1 | 1 | 1 |
| 12–19 | 8 bytes | Reserved (zeros) | 0 | 0 |
| 20–23 | uint32 | **Calibration type = 1** | 1 | 1 |
| 24–27 | float32 | **Raw reading at cal point 1** | 1669.06 | -4.85 |
| 28–31 | float32 | **Raw reading at cal point 2** | -3314.92 | 80.03 |
| 32–35 | float32 | **Output at cal point 1** | -180.0 (deg) | 50.0 (mm) |
| 36–39 | float32 | **Output at cal point 2** | 180.0 (deg) | 0.0 (mm) |
| 40–43 | float32 | Unknown | 0.0 | 25.0 |
| 44–47 | float32 | Unknown | -180.0 | -0.032 |
| 48–51 | float32 | Unknown (ADC-related?) | 2789.0 | 3427.0 |
| 52–55 | float32 | Unknown (ADC-related?) | 2246.0 | 303.0 |
| 56–59 | float32 | Unknown (ADC-related?) | 2246.0 | 303.0 |
| 60–63 | float32 | Unknown (ADC-related?) | 2789.0 | 301.0 |
| 64–139 | zeros | Reserved | | |
| 140–143 | 4 bytes | Unknown (CRC/timestamp?) | Non-zero | Non-zero |

**Confirmed by known calibration settings:**
- SFJ steering: calibrated to **-180° / +180°** with a potentiometer → matches f32[32]=-180, f32[36]=+180
- SFJ throttle (ACCEL): calibrated to **0mm / 50mm** pedal travel → matches f32[32]=50, f32[36]=0 (inverted pot wiring)
- Data can exceed the calibrated range (steering goes to -220.5°/+135°, throttle to 54.5mm) via linear extrapolation

#### Type 20 — IMU bias/offset calibration (factory-calibrated sensors)

Used for internal accelerometers and gyroscopes. The bias value corrects for the sensor's zero-point offset from the factory calibration.

| Offset | Type | Field | Example (InlineAcc) |
|--------|------|-------|---------------------|
| 0–3 | 4 bytes | Hardware/sensor ID | `03 11 00 04` |
| 4–7 | uint32 | Internal reference number | 12 |
| 8–11 | uint32 | Always 1 | 1 |
| 12–19 | 8 bytes | Reserved (zeros) | 0 |
| 20–23 | uint32 | **Calibration type = 20** | 20 |
| 24–27 | float32 | **Zero offset bias** | 0.0486 |
| 28–31 | float32 | Scale factor | 1.0 |
| 32–35 | float32 | Scale factor | 1.0 |
| 36–39 | float32 | (zero) | 0.0 |
| 40–43 | float32 | Midpoint | 0.5 |
| 44–47 | float32 | (zero) | 0.0 |
| 48–51 | float32 | Max ADC range | 5000.0 |
| 52–55 | float32 | (zero) | 0.0 |
| 56–59 | float32 | Mid ADC range | 2500.0 |
| 60–63 | float32 | (zero) | 0.0 |
| 64–139 | zeros | Reserved | |
| 140–143 | 4 bytes | Unknown (CRC/timestamp?) | Non-zero |

The bias value at offset 24 is unique per physical sensor installation (e.g., InlineAcc=0.049, LateralAcc=0.018, YawRate=-1.021).

#### CHS / CAL cross-reference

The calibration values at CAL offsets 24–27 and 28–31 are duplicated in the CHS (channel definition) message at offsets 96–99 and 100–103. This provides two ways to find calibration data:

**CHS extended fields (offsets 72+):**

| CHS Offset | Type | Description |
|------------|------|-------------|
| 72–75 | uint32 | Channel sample size (bytes) |
| 80–83 | uint32 | Calibration point count? (10 for type 1, 6 for IMU, 2 for simple) |
| 88–91 | uint32 | Calibration slot index (sequential, increments by 2) |
| 96–99 | float32 | **= CAL f32[24]** (raw at cal point 1, or IMU bias) |
| 100–103 | float32 | **= CAL f32[28]** (raw at cal point 2, or scale factor) |

Channels without calibration (GPS-derived, timers, etc.) have f32[96]=0.0 and f32[100]=1.0 (identity).

**SFJ channel calibration summary (CHS f32[96:104]):**

| Channel | CHS f32[96] | CHS f32[100] | CAL type | Confirmed |
|---------|-------------|--------------|----------|-----------|
| steering | 1669.06 | -3314.92 | 1 (2-point) | Yes: -180°/+180° pot calibration |
| ACCEL | -4.85 | 80.03 | 1 (2-point) | Yes: 0mm/50mm throttle travel |
| InlineAcc | 0.049 | 1.0 | 20 (IMU) | Bias offset |
| LateralAcc | 0.018 | 1.0 | 20 (IMU) | Bias offset |
| VerticalAcc | -0.003 | 1.0 | 20 (IMU) | Bias offset |
| RollRate | 0.734 | 1.0 | 20 (IMU) | Bias offset |
| PitchRate | -0.657 | 1.0 | 20 (IMU) | Bias offset |
| YawRate | -1.021 | 1.0 | 20 (IMU) | Bias offset |

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

## Phase 7: CHS Unknown Bytes Analysis

**Date:** 2026-02-08
**Method:** Byte-level analysis of all 548 CHS messages across 8 test files (5 XRK + 3 XRZ)

### Complete CHS Layout (112 bytes)

Analysis of 548 CHS messages across all test files reveals the following complete layout. Calibration fields (bytes 96-103) were **confirmed** by cross-referencing with CAL message analysis in Phase 6.

| Offset | Size | Type | Field | Status | Evidence |
|--------|------|------|-------|--------|----------|
| 0-1 | 2 | uint16 LE | `index` | KNOWN | Channel index |
| 2-3 | 2 | | *padding* | **IDENTIFIED** | Always zero |
| 4-5 | 2 | uint16 LE | `hardware_id` | **IDENTIFIED** | Only non-zero for GPS decoder 8 (value 0x0846); likely hardware/CAN bus address |
| 6-7 | 2 | uint16 LE | `source_channel_id` | **IDENTIFIED** | Sequential ID within the source device; GPS-derived channels use 800+ range |
| 8-9 | 2 | uint16 LE | `hardware_ref_1` | **IDENTIFIED** | Only non-zero for GPS decoder 8 (value 0xF084); likely GPS hardware reference |
| 10-11 | 2 | uint16 LE | `hardware_ref_2` | **IDENTIFIED** | Only non-zero for GPS decoder 8 (value 0x0084); pairs with bytes 8-9 |
| 12 | 1 | uint8 | `unit_type` (full byte) | KNOWN+ | Lower 7 bits = unit type for `_unit_map`; **high bit = "signed/calibrated" flag** (set for CAN channels, shock pots, analog inputs, etc.) |
| 13 | 1 | uint8 | `maybe_display_format` | **IDENTIFIED** | Varies per channel; 25 unique values; purpose unclear — possibly decimal places or display widget type |
| 14-15 | 2 | uint16 LE | `maybe_config_flags` | **IDENTIFIED** | Channel configuration; non-zero for channels with special input config (shock pots: 1286/1158/774/646, wheel speed: 519, Reset Odometer: 64/192/320/448); encoding unknown |
| 16 | 1 | uint8 | `source_type` | **IDENTIFIED** | Values: 1=internal simple, 2=RPM/encoder, 4=AIM computed/timer, 5=GPS raw, 9=CAN bus, 10=CAN group, 12=expansion |
| 17-19 | 3 | | *padding* | **IDENTIFIED** | Always zero |
| 20 | 1 | uint8 | `decoder_type` | KNOWN | Decoder function selector |
| 21-23 | 3 | | *padding* | **IDENTIFIED** | Always zero |
| 24-31 | 8 | char[8] | `short_name` | KNOWN | Null-terminated ASCII |
| 32-55 | 24 | char[24] | `long_name` | KNOWN | Null-terminated ASCII |
| 56-63 | 8 | | *padding* | **IDENTIFIED** | Always zero |
| 64-67 | 4 | uint32 LE | `sample_period_us` | KNOWN | Microseconds per sample |
| 68-69 | 2 | uint16 LE | `data_offset` | **IDENTIFIED** | Byte offset into packed channel data; consecutive channels pack at `offset + data_size` |
| 70-71 | 2 | | *padding* | **IDENTIFIED** | Always zero |
| 72 | 1 | uint8 | `data_size` | KNOWN | Bytes per sample |
| 73-75 | 3 | | *padding* | **IDENTIFIED** | Always zero |
| 76-79 | 4 | char[4] | `device_tag` | **IDENTIFIED** | Only two values observed: "@AIM" (0x40,0x41,0x49,0x4D) for internal AIM firmware channels, or all zeros for CAN bus/expansion/sensor channels |
| 80 | 1 | uint8 | `device_node_id` | **IDENTIFIED** | Source device node: 0=CAN bus, 1=AIM timer, 2=AIM sensor, 3=AIM computed, 6=AIM IMU |
| 81 | 1 | uint8 | `maybe_device_flags` | **IDENTIFIED** | Only 3 values seen: 0x00=normal, 0x02=alarm/switch type, 0x10=master clock only |
| 82-83 | 2 | | *padding* | **IDENTIFIED** | Always zero |
| 84 | 1 | uint8 | `maybe_output_type` | **IDENTIFIED** | 4 values seen: 1=simple value, 4=CAN decoded, 6=IMU/filtered, 0xFF=virtual/computed; grouping is clean but semantics are inferred |
| 85-87 | 3 | | *padding* | **IDENTIFIED** | Always zero |
| 88-91 | 4 | uint32 LE | `display_index` | **IDENTIFIED** | Sequential display/calibration slot index (0,2,4,6...); 0xFFFFFFFF for virtual/GPS-derived channels with no data storage |
| 92 | 1 | uint8 | `maybe_output_size` | **IDENTIFIED** | Values: 0, 2, 4, 8; loosely correlates with decoded data width but doesn't always match data_size; 0 for virtual channels |
| 93-95 | 3 | | *padding* | **IDENTIFIED** | Always zero |
| 96-99 | 4 | float32 LE | `cal_value_1` | **CONFIRMED** | Calibration parameter 1. For 2-point linear (CAL type 1): raw ADC reading at cal point 1. For IMU (CAL type 20): zero-offset bias. Duplicated in CAL message offset 24-27. Most channels = 0.0 |
| 100-103 | 4 | float32 LE | `cal_value_2` | **CONFIRMED** | Calibration parameter 2. For 2-point linear: raw ADC reading at cal point 2. For IMU: scale factor (always 1.0). Duplicated in CAL message offset 28-31. Most channels = 1.0 |
| 104-107 | 4 | float32 LE | `display_range_min` | **IDENTIFIED** | Minimum display range: -1e30 (auto-range), 0.0 (zero-based), or specific min value |
| 108-111 | 4 | float32 LE | `display_range_max` | **IDENTIFIED** | Maximum display range: +1e30 (auto-range), +inf, 99999.9, or specific max value |

### Key Findings

#### 1. Calibration Fields (bytes 96-103) — CONFIRMED via CAL cross-reference

The Phase 6 CAL message analysis independently confirmed these fields. CHS bytes 96-99 and 100-103 are **exact duplicates** of CAL message offsets 24-27 and 28-31 respectively. This was verified for all channels that have both CHS and CAL entries:

| Channel | CHS f32[96] | CHS f32[100] | CAL type | Confirmed meaning |
|---------|-------------|--------------|----------|-------------------|
| steering | 1669.06 | -3314.92 | 1 (2-point) | Raw ADC readings at -180° and +180° |
| ACCEL | -4.85 | 80.03 | 1 (2-point) | Raw ADC readings at 50mm and 0mm pedal travel |
| LF_Shock_Pot | -68.45 | 80.03 | 1 (2-point) | Raw ADC readings at two calibration points |
| InlineAcc | 0.049 | 1.0 | 20 (IMU) | Zero-offset bias and scale (factory calibration) |
| LateralAcc | 0.018 | 1.0 | 20 (IMU) | Zero-offset bias and scale |
| YawRate | -1.021 | 1.0 | 20 (IMU) | Zero-offset bias and scale |

Channels without CAL messages have identity values (0.0, 1.0), meaning calibration is handled internally by the decoder.

Note: these are NOT `offset` and `scale` in the `value = raw * scale + offset` sense. For type 1 calibration, they are the two raw ADC readings at the two user-defined calibration points. The actual output values at those points are in the CAL message at offsets 32-35 and 36-39 (not duplicated in CHS).

#### 2. Byte 12 High Bit (Previously Masked Out)

The current parser uses `dcopy[12] & 127` to extract unit_type, discarding the high bit. Analysis shows the high bit is set for ~32% of channels (177/548), specifically:
- All CAN bus channels (decoder 6/15)
- Shock pot channels
- Analog input channels with calibration
- GPS-derived computed channels
- Timer/odometer channels

This bit likely indicates "has extended calibration" or "signed data" — channels where the raw data needs the cal_value fields to produce meaningful values.

#### 3. Data Offset (bytes 68-69)

This uint16 LE field is a **byte offset into packed channel data**. When channels are sorted by this offset, consecutive channels pack tightly:

```
offset=  0, size= 4: Master Clk     (gap=0)
offset=  4, size=20: Lap Time       (gap=4)
offset= 24, size= 4: Predictive Time (gap=20)
offset= 28, size= 4: Best Run Diff  (gap=4)
...
```

Each channel's data starts at `data_offset` and occupies `data_size` bytes. This is used internally by AIM software for direct data addressing.

#### 4. Source Channel ID (bytes 6-7)

This field identifies the channel's source within its hardware device:
- Internal AIM channels: small sequential numbers (0-50)
- CAN bus channels: CAN signal ID / message offset (0-1011)
- GPS-derived channels: 800+ range (800=GPS_LateralAcc, 801=GPS_InlineAcc, etc.)

#### 5. Display Range (bytes 104-111)

Two float32 fields define the display range in AIM RaceStudio software:
- `display_range_min` (bytes 104-107): -1e30 = auto-range, 0.0 = zero-based
- `display_range_max` (bytes 108-111): +1e30 = auto-range, +inf = unbounded, 99999.9 = specific max

#### 6. Device Tag (bytes 76-79)

Only two values were observed across all 548 CHS messages:
- `40 41 49 4d` = "@AIM" (177 messages) — channels originating from the AIM logger's firmware (Master Clk, computed timers, odometers, expansion device alarms)
- `00 00 00 00` = null (371 messages) — CAN bus, sensor, and expansion channels

No other device tag values were seen in any test file.

#### 7. Virtual Channels

GPS-derived channels (GPS_LateralAcc, GPS_InlineAcc, GPS_Yaw_Rate, GPS_Hours, GPS_Date, GPS_Time) have distinctive signatures:
- `output_type` = 0xFF
- `display_index` = 0xFFFFFFFF
- `output_size` = 0

These are computed by AIM firmware from GPS data and don't have their own raw data storage.

#### 8. Always-Zero Regions (Confirmed Padding)

The following byte ranges are confirmed padding (always zero across all 548 messages):
- Bytes 2-3
- Bytes 17-19
- Bytes 21-23
- Bytes 56-63
- Bytes 70-71
- Bytes 73-75
- Bytes 82-83
- Bytes 85-87
- Bytes 93-95

Total: 30 of 112 bytes are padding (27%).

The parser now validates these padding bytes and prints a warning with a link to the issue tracker if any are non-zero, to help identify new fields in future XRK files.

### Cross-File Stability

119 channels appear across multiple files. For the vast majority, **all unknown bytes are identical across files** — confirming these are channel definition properties, not session-specific data. The few that differ:
- `data_offset` (bytes 68-69): Varies because different files have different numbers of channels, so the packing order changes
- `display_index` (bytes 88-91): Sequential index varies with file channel count
- `device_tag` (bytes 76-79): Differs between AIM logger models (present on some, absent on others for the same channel)

### Practical Impact

The identified fields have limited impact on libxrk's core functionality:
- **Calibration values**: Already applied before data is stored in XRK. Informational only — useful for diagnostics/verification (see Phase 6 CAL analysis).
- **CDE offset / display_index**: Internal AIM addressing; not needed for data extraction.
- **Display range**: Only useful for visualization defaults; not needed for data extraction.
- **Source channel ID / device tag**: Useful for debugging channel provenance but not needed for data parsing.

The byte 12 high bit could potentially be exposed as metadata, but since the current masking works correctly for `_unit_map` lookup, changing it is low priority.

---

## Phase 8: CDE Message Analysis

**Date:** 2026-02-08
**Method:** Byte-level analysis of all 358 CDE messages across 5 XRK files, paired with their corresponding CHS messages

### Background

CDE ("Channel Definition Extension"?) messages appear once per channel, paired 1:1 with CHS messages inside CNF containers. Each CDE is exactly 6 bytes. The parser currently converts them to hex strings without interpretation:

```python
elif tok == _tokdec('CDE'):
    data = ['%02x' % x for x in data]
```

### CDE Layout (6 bytes)

| Offset | Size | Type | Field | Status | Evidence |
|--------|------|------|-------|--------|----------|
| 0-1 | 2 | uint16 LE | `channel_index` | **CONFIRMED** | 358/358 pairs match the paired CHS channel index |
| 2-5 | 4 | uint32 LE | `session_uid` | **IDENTIFIED** | Opaque per-session unique identifier (see analysis below) |

### Analysis of Bytes 2-5

**Statistical properties:**
- 357 unique values out of 358 total (one collision: `IntakeAirT` vs `WheelSpdFR` in the 86 file)
- Near-uniform bit distribution across all 32 bits (~42-62% set per bit, centered around 50%)
- Range: 0x01604109 to 0xFEFFB1FA (full 32-bit space)

**What it is NOT:**
- Not CRC32 of CHS data (no matches)
- Not derived from channel name, decoder type, unit type, or any CHS field
- Not stable across sessions: 0/38 common channels matched between two SFJ files from the same logger
- Not unique per file in all cases: one collision exists in the 86 file (110 unique out of 111)

**Conclusion:** Bytes 2-5 are a **per-session random/opaque unique identifier**, likely assigned when AIM RaceStudio generates the logger configuration or when the session starts. Probably used internally by AIM software for channel identity tracking across configuration changes.

### Cross-File Comparison

| File | CDE Count | Unique UIDs | All Unique? |
|------|-----------|-------------|-------------|
| 86_2248.xrk | 111 | 110 | No (1 collision) |
| SFJ_0033.xrk | 39 | 39 | Yes |
| SFJ_0101.xrk | 40 | 40 | Yes |
| SFJ_Suzuka_0090.xrk | 40 | 40 | Yes |
| test.xrk | 128 | 128 | Yes |

Same channel names across different files always have **different** CDE UIDs, confirming these are session-specific, not channel-specific.

### Practical Impact

**None.** The CDE message contains:
1. A channel index (redundant — already in the paired CHS message)
2. An opaque session UID with no data extraction value

No changes to the parser are needed. The current hex-string storage is adequate for debugging purposes.

---

## Next Steps

1. **Run DLL comparison** - Use Wine to compare GPS derived channels from DLL
2. **Implement GPS derived channels** - Compute GPS_InlineAcc, GPS_LateralAcc, GPS_Yaw_Rate
3. **Add GPS accuracy channels** - Expose satellite count and accuracy metrics
4. **Document decoder formats** - For channels with `size > 0` that are being dropped
