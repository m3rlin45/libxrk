# Test Data for libXRK

Contains AIM run sessions from multiple cars and tracks plus targeted fixtures for specific issues.

## issue68

`CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz` — KK-SII session at Tsukuba, recorded 2026-04-17. Selected because its logger emits the new `(c)` expansion-channel message variants (V2 long, V3 short) that weren't in the older corpus; referenced by tests verifying issue #68 shock-pot and accelerometer parsing.

## SFJ
The SFJ Folder contains data from a run by a beginner in a Super FJ Junior formula car.

### File: CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk

**Laps:** 13 laps
- Lap 0: start=0.000, end=193611.000 (outlap, 193.61s)
- Lap 1: start=193611.000, end=320961.000 (127.35s)
- Lap 2: start=320961.000, end=450166.000 (129.21s)
- Lap 3: start=450166.000, end=569437.000 (119.27s)
- Lap 4: start=569437.000, end=688126.000 (118.69s)
- Lap 5: start=688126.000, end=819303.000 (131.18s)
- Lap 6: start=819303.000, end=947652.000 (128.35s)
- Lap 7: start=947652.000, end=1079430.000 (131.78s)
- Lap 8: start=1079430.000, end=1202583.000 (123.15s)
- Lap 9: start=1202583.000, end=1322384.000 (119.80s)
- Lap 10: start=1322384.000, end=1445260.000 (122.88s)
- Lap 11: start=1445260.000, end=1578528.000 (133.27s)
- Lap 12: start=1578528.000, end=1696958.000 (inlap, 118.43s)

**Channels:** 26 channels

| Channel Name | Rows | First Value | Last Value | Units | Dec Pts | Interpolate |
|--------------|------|-------------|------------|-------|---------|-------------|
| ACCEL | 33930 | 2.318 | 0.636 | mm | 0 | True |
| ADC Voffset | 1696 | 198.0 | 198.0 | mV | 1 | True |
| BRK | 84825 | -0.115 | -0.166 | bar | 2 | True |
| Best Run Diff | 724 | -12290 | 10232 | ms | 0 | False |
| Best Today Diff | 12 | -12290 | -12290 | ms | 0 | False |
| External Voltage | 1696 | 11.264 | 12.936 | V | 1 | True |
| GPS Altitude | 42409 | 644.161 | 622.024 | m | 1 | True |
| GPS Latitude | 42409 | 35.3725 | 35.3677 | deg | 4 | True |
| GPS Longitude | 42409 | 138.9276 | 138.9202 | deg | 4 | True |
| GPS Speed | 42409 | 0.0 | 2.079 | m/s | 1 | True |
| InlineAcc | 84840 | 0.048 | 0.013 | G | 2 | True |
| Lateral Grip | 42409 | 0.0 | -1.120 | | 0 | True |
| LateralAcc | 84840 | 0.002 | 0.075 | G | 2 | True |
| LoggerTemp | 1696 | 15.945 | 15.805 | C | 1 | True |
| Luminosity | 1696 | 0.460 | 1.933 | % | 2 | True |
| PitchRate | 84830 | 1.192 | 0.506 | deg/s | 1 | True |
| Predictive Time | 724 | -12290 | 128922 | ms | 0 | False |
| Prev Lap Diff | 12 | -12290 | -12290 | ms | 0 | False |
| RPM | 33930 | 2434.0 | 0.0 | rpm | 0 | True |
| Ref Lap Diff | 12 | -12290 | -12290 | ms | 0 | False |
| RollRate | 84830 | 0.362 | -1.028 | deg/s | 1 | True |
| StartRec | 810 | 5.96e-08 | 0.0 | | 0 | True |
| VerticalAcc | 84840 | -1.275 | -0.972 | G | 2 | True |
| WT | 33930 | 40.031 | 52.844 | C | 1 | True |
| YawRate | 84830 | 0.075 | -8.664 | deg/s | 1 | True |
| steering | 33930 | -25.531 | 119.0 | deg | 1 | True |

**Sensor Types:**
* GPS (Speed, Latitude, Longitude, Altitude)
* Accelerometer (InlineAcc, LateralAcc, VerticalAcc)
* Gyro (YawRate, PitchRate, RollRate)
* RPM
* Water Temperature (WT)
* Accelerator Pedal Position (ACCEL)
* Brake pressure (BRK)
* Steering Wheel Position (steering)
* Performance metrics (Best Run Diff, Best Today Diff, Predictive Time, etc.)

**File-Level Metadata:** 22 entries

| Key | Value |
|-----|-------|
| Device Name | MXm ID airs |
| Driver | CMD |
| Log Date | 11/04/2025 |
| Log Time | 15:50:07 |
| Logger ID | 6603435 |
| Logger Model | MXm |
| Logger Model ID | 793 |
| Long Comment | (empty) |
| Odo/System Distance (km) | 165.858 |
| Odo/System Time | 1:25:05 |
| Odo/Usr 1 Distance (km) | 165.858 |
| Odo/Usr 1 Time | 1:25:05 |
| Odo/Usr 2 Distance (km) | 165.858 |
| Odo/Usr 2 Time | 1:25:05 |
| Odo/Usr 3 Distance (km) | 165.858 |
| Odo/Usr 3 Time | 1:25:05 |
| Odo/Usr 4 Distance (km) | 165.858 |
| Odo/Usr 4 Time | 1:25:05 |
| Series | Fuji Practice |
| Session | Generic testing |
| Vehicle | SFJ |
| Venue | Fuji GP Sh |


## 86
Contains data from a run by an intermediate driver in a Toyota 86.

### File: CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk

**Laps:** 16 laps
- Lap 0: start=0.000, end=150454.000 (outlap, 150.45s)
- Lap 1: start=150454.000, end=279602.000 (129.15s)
- Lap 2: start=279602.000, end=406240.000 (126.64s)
- Lap 3: start=406240.000, end=532797.000 (126.56s)
- Lap 4: start=532797.000, end=659282.000 (126.49s)
- Lap 5: start=659282.000, end=787773.000 (128.49s)
- Lap 6: start=787773.000, end=913776.000 (126.00s)
- Lap 7: start=913776.000, end=1041397.000 (127.62s)
- Lap 8: start=1041397.000, end=1168322.000 (126.93s)
- Lap 9: start=1168322.000, end=1294676.000 (126.35s)
- Lap 10: start=1294676.000, end=1420573.000 (125.90s)
- Lap 11: start=1420573.000, end=1547567.000 (126.99s)
- Lap 12: start=1547567.000, end=1672955.000 (125.39s)
- Lap 13: start=1672955.000, end=1799131.000 (126.18s)
- Lap 14: start=1799131.000, end=1924187.000 (125.06s)
- Lap 15: start=1924187.000, end=2161607.000 (inlap, 237.42s)

**Channels:** 92 channels

| Channel Name | Rows | First Value | Last Value | Units | Dec Pts | Interpolate |
|--------------|------|-------------|------------|-------|---------|-------------|
| AmbientTemp | 100396 | 6.000 | 6.000 | C | 1 | True |
| Baro | 100395 | 0.940 | 0.950 | bar | 2 | True |
| Best Run Diff | 963 | -12290 | 36591 | ms | 0 | False |
| Best Today Diff | 15 | -12290 | -12290 | ms | 0 | False |
| BrakePress | 36024 | 9.142 | 18.284 | bar | 2 | True |
| BrakeSw | 21617 | 1.000 | 1.000 |  | 0 | True |
| CAT1 | 100396 | 274.200 | 542.600 | C | 1 | True |
| CH | 10807 | 1.000 | 1.000 |  | 0 | True |
| ClutchSw | 54029 | 1.000 | 1.000 |  | 0 | True |
| ECT | 100395 | 89.000 | 91.000 | C | 1 | True |
| External Voltage | 2161 | 14.464 | 14.480 | V | 1 | True |
| FL_Ch1 | 21565 | 45.900 | 46.500 | C | 1 | True |
| FL_Ch2 | 21565 | 46.100 | 51.400 | C | 1 | True |
| FL_Ch3 | 21565 | 45.400 | 50.300 | C | 1 | True |
| FL_Ch4 | 21565 | 45.500 | 48.900 | C | 1 | True |
| FL_Ch5 | 21565 | 46.000 | 54.300 | C | 1 | True |
| FL_Ch6 | 21565 | 45.300 | 50.000 | C | 1 | True |
| FL_Ch7 | 21565 | 44.000 | 54.200 | C | 1 | True |
| FL_Ch8 | 21565 | 51.100 | 42.600 | C | 1 | True |
| FR_Ch1 | 21568 | 57.800 | 39.200 | C | 1 | True |
| FR_Ch2 | 21568 | 54.100 | 55.400 | C | 1 | True |
| FR_Ch3 | 21568 | 54.600 | 51.800 | C | 1 | True |
| FR_Ch4 | 21568 | 55.300 | 51.700 | C | 1 | True |
| FR_Ch5 | 21568 | 57.300 | 49.000 | C | 1 | True |
| FR_Ch6 | 21568 | 55.400 | 49.300 | C | 1 | True |
| FR_Ch7 | 21568 | 57.100 | 51.000 | C | 1 | True |
| FR_Ch8 | 21568 | 55.500 | 48.400 | C | 1 | True |
| GPS Altitude | 54031 | 619.999 | 625.256 | m | 1 | True |
| GPS Latitude | 54031 | 35.374 | 35.370 | deg | 4 | True |
| GPS Longitude | 54031 | 138.930 | 138.925 | deg | 4 | True |
| GPS Speed | 54031 | 0.030 | 0.000 | m/s | 1 | True |
| Gear | 10806 | 0.000 | 0.000 | gear | 0 | False |
| InlineAcc | 108060 | -0.027 | -0.003 | G | 2 | True |
| IntakeAirT | 100396 | 46.000 | 19.000 | C | 1 | True |
| LF_Shock_Pot | 46990 | -0.914 | -0.151 | mm | 0 | True |
| LR_Shock_Pot | 46990 | 1.221 | -2.594 | mm | 0 | True |
| Lambda | 100395 | 0.995 | 0.995 | lambda | 2 | True |
| LateralAcc | 108060 | -0.008 | 0.032 | G | 2 | True |
| LoggerTemp | 2161 | 35.094 | 37.250 | C | 1 | True |
| Luminosity | 2161 | 14.242 | 15.539 | % | 2 | True |
| MAP | 100396 | 0.310 | 0.280 | bar | 2 | True |
| OilTemp | 10806 | 93.000 | 95.000 | C | 1 | True |
| PPS | 54029 | 0.000 | 0.000 | % | 2 | True |
| PitchRate | 108060 | 0.105 | 0.063 | deg/s | 1 | True |
| Predictive Time | 963 | -12290 | 161647 | ms | 0 | False |
| Prev Lap Diff | 15 | -12290 | -12290 | ms | 0 | False |
| RF_Shock_Pot | 46990 | 0.916 | -2.746 | mm | 0 | True |
| RL_Ch1 | 21568 | 41.200 | 48.200 | C | 1 | True |
| RL_Ch2 | 21568 | 39.500 | 47.100 | C | 1 | True |
| RL_Ch3 | 21568 | 38.300 | 45.800 | C | 1 | True |
| RL_Ch4 | 21568 | 38.700 | 47.300 | C | 1 | True |
| RL_Ch5 | 21568 | 38.900 | 48.200 | C | 1 | True |
| RL_Ch6 | 21568 | 38.400 | 45.900 | C | 1 | True |
| RL_Ch7 | 21568 | 38.000 | 44.300 | C | 1 | True |
| RL_Ch8 | 21568 | 33.700 | 38.700 | C | 1 | True |
| RPM | 54029 | 712.000 | 732.000 | rpm | 0 | True |
| RR_Ch1 | 21561 | 32.900 | 37.400 | C | 1 | True |
| RR_Ch2 | 21561 | 37.900 | 45.200 | C | 1 | True |
| RR_Ch3 | 21561 | 38.500 | 46.600 | C | 1 | True |
| RR_Ch4 | 21561 | 39.600 | 50.400 | C | 1 | True |
| RR_Ch5 | 21561 | 37.500 | 48.700 | C | 1 | True |
| RR_Ch6 | 21561 | 38.000 | 49.700 | C | 1 | True |
| RR_Ch7 | 21561 | 38.000 | 49.500 | C | 1 | True |
| RR_Ch8 | 21561 | 39.600 | 49.400 | C | 1 | True |
| RR_Shock_Pot | 46990 | -1.067 | 2.287 | mm | 0 | True |
| Ref Lap Diff | 15 | -12290 | -12290 | ms | 0 | False |
| RollRate | 108060 | -0.136 | -0.059 | deg/s | 1 | True |
| SpeedAverage | 36024 | 0.000 | 0.000 | km/h | 0 | True |
| SteerAngle | 21614 | -3.600 | -31.600 | deg | 1 | True |
| TPMS_ALM_LF | 10807 | 0.000 | 0.000 |  | 0 | True |
| TPMS_ALM_LR | 10807 | 0.000 | 0.000 |  | 0 | True |
| TPMS_ALM_RF | 10807 | 0.000 | 0.000 |  | 0 | True |
| TPMS_ALM_RR | 10807 | 0.000 | 0.000 |  | 0 | True |
| TPMS_Press_LF | 10807 | 1.820 | 1.850 | bar | 2 | True |
| TPMS_Press_LR | 10807 | 1.820 | 1.880 | bar | 2 | True |
| TPMS_Press_RF | 10807 | 1.820 | 1.850 | bar | 2 | True |
| TPMS_Press_RR | 10807 | 1.790 | 1.880 | bar | 2 | True |
| TPMS_Temp_LF | 10807 | 62.000 | 55.000 | C | 1 | True |
| TPMS_Temp_LR | 10807 | 49.000 | 52.000 | C | 1 | True |
| TPMS_Temp_RF | 10807 | 59.000 | 51.000 | C | 1 | True |
| TPMS_Temp_RR | 10807 | 48.000 | 51.000 | C | 1 | True |
| TPMS_Volt_LF | 10807 | 2900.0 | 3000.0 | mV | 1 | True |
| TPMS_Volt_LR | 10807 | 2900.0 | 3000.0 | mV | 1 | True |
| TPMS_Volt_RF | 10807 | 2900.0 | 3000.0 | mV | 1 | True |
| TPMS_Volt_RR | 10807 | 2900.0 | 3000.0 | mV | 1 | True |
| TPS | 100396 | 16.450 | 15.980 | % | 2 | True |
| VerticalAcc | 108060 | -1.000 | -1.001 | G | 2 | True |
| WheelSpdFL | 36024 | 0.000 | 0.000 | km/h | 0 | True |
| WheelSpdFR | 36024 | 0.000 | 0.000 | km/h | 0 | True |
| WheelSpdRL | 36024 | 0.000 | 0.000 | km/h | 0 | True |
| WheelSpdRR | 36024 | 0.000 | 0.000 | km/h | 0 | True |
| YawRate | 108060 | 0.012 | 0.034 | deg/s | 1 | True |

**Sensor Types:**
* GPS (Speed, Latitude, Longitude, Altitude)
* Accelerometer (InlineAcc, LateralAcc, VerticalAcc)
* Gyro (YawRate, PitchRate, RollRate)
* Engine: RPM, TPS, PPS, ECT, OilTemp, IntakeAirT, Lambda, CAT1, MAP, Baro
* Brake system: BrakePress, BrakeSw
* Steering: SteerAngle
* Gear: Gear, ClutchSw
* Wheel speeds: WheelSpdFL, WheelSpdFR, WheelSpdRL, WheelSpdRR
* Shock potentiometers: LF_Shock_Pot, RF_Shock_Pot, LR_Shock_Pot, RR_Shock_Pot
* TPMS (Tire Pressure Monitoring): Pressure, Temperature, Voltage, Alarms for all 4 corners
* Tire Surface Temps: FL_Ch1-8, FR_Ch1-8, RL_Ch1-8, RR_Ch1-8 (32 channels total)
* Environment: AmbientTemp, LoggerTemp, Luminosity, External Voltage
* Performance metrics: Best Run Diff, Best Today Diff, Predictive Time, Prev Lap Diff, Ref Lap Diff, SpeedAverage

**File-Level Metadata:** 22 entries

| Key | Value |
|-----|-------|
| Device Name | Inferno 86 v2 |
| Driver | CMD |
| Log Date | 11/01/2025 |
| Log Time | 10:39:06 |
| Logger ID | 6701209 |
| Logger Model | MXP 1.3 |
| Logger Model ID | 649 |
| Long Comment | Front 15, 2/2<br/>Rear 20 3/3<br/>A052 Used |
| Odo/System Distance (km) | 5313.42 |
| Odo/System Time | 79:29:53 |
| Odo/Usr 1 Distance (km) | 5313.42 |
| Odo/Usr 1 Time | 79:29:53 |
| Odo/Usr 2 Distance (km) | 5313.42 |
| Odo/Usr 2 Time | 79:29:53 |
| Odo/Usr 3 Distance (km) | 5313.42 |
| Odo/Usr 3 Time | 79:29:53 |
| Odo/Usr 4 Distance (km) | 5313.42 |
| Odo/Usr 4 Time | 79:29:53 |
| Series | Fuji Practice |
| Session | Generic testing |
| Vehicle | Inferno 86 |
| Venue | Fuji GP Sh |


---

## issue84 — GPS timecode reconstruction

**File:** `issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz` (4.1 MB)

Regression fixture for issue #84: GPS timecodes rebuilt from the low 16 bits.

**Why this file:** the smallest file in a 353-file survey that carries more than
one fault class. The logger **re-emits a block of 41 GPS records** — records
4856-4896 are byte-identical to records 4897-4937. That produces two symptoms at
once:

* the logger clock steps **backwards by 1600 ms** at index 4896, and
* **41 iTOW epochs are duplicated**.

The backwards step is *not* a 16-bit rollover. The superseded rule ("accumulate
+65536 whenever `tc[i+1] < tc[i]`") treated it as one and added 65536 ms to every
later sample; the downstream 65533 ms-gap correction in `gps.py` then partly
compensated, leaving ~1.6 s of fabricated timeline and 41 phantom GPS samples,
with GPS drifting away from every other channel.

`iTOW` (GPS time of week, written by the receiver) is the ground truth used to
verify reconstruction — the logger firmware bug cannot touch it. It is never
used to *perform* the reconstruction.

**Expected after correct reconstruction:**

* GPS span 240.8 s, matching iTOW's span and the non-GPS channels to within 100 ms
* the 41 replayed records land back on the timecodes they duplicate, so the raw
  `GPS Speed` table has one backwards step and 41 duplicate timecodes — these are
  absorbed by `get_channels_as_table()`, whose output is monotonic and unique
* 6062 GPS samples, 6021 distinct timecodes

Tests: `spec/tests/test_gps_timecodes.py`, `tests/test_issue84_gps_timecodes.py`,
and `fix_timecodes` unit tests in `crates/libxrk/src/gps/processing.rs`.
Algorithm: `spec/docs/companion.md` section 6, reference implementation
`spec/xrk_format.py::reconstruct_gps_timecodes`.

**File-Level Metadata:**

| Key | Value |
|-----|-------|
| Log Date | 05/23/2026 |
| Log Time | 13:19:33 |
| Logger ID | 8401203 |
| Logger Model ID | 519 (Solo 2, not in the model table) |
| Venue | Tsukuba_Car |
| GPS Receiver | GPS |
| Channels | 87 |
| Laps | 3 |
