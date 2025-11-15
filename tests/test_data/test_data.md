# Test Data for libXRK

This contains 2 AIM run sessions, from 2 different cars. Both runs were done at Fuji Speedway.

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
| ADC Voffset | 1696 | 0.198 | 0.198 | V | 1 | True |
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

**File-Level Metadata:** 18 entries

| Key | Value |
|-----|-------|
| Driver | CMD |
| Log Date | 11/04/2025 |
| Log Time | 15:50:07 |
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
Contains data from a run by an intermediate driver in a Toyota 86. It has data from:

* GPS
* Accelerometer
* Gyro
* RPM
* ECT
* Oil Temp
* ...and much more