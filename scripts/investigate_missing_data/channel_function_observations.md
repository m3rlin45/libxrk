# Channel Function Observations from AIM RaceStudio3

Collected by examining channel properties in RS3 for the 86 test file
(`tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk`).

## Key Evidence

- A vehicle config bug causes all 4 WheelSpd channels to show "Rear Wheel Speed" —
  if the function were derived from name/units, this wouldn't happen.
- Channels with identical units have different functions (e.g., ECT="Temperature",
  CAT1="Exhaust Temperature", OilTemp="Oil Temperature" — all °C).
- Some channels (like "Predictive Time") don't show a function in RS3 at all.

These observations prove the function is stored per-channel in the binary, not derived.

## Observations (35 channels)

| Channel | Function | Unit |
|---------|----------|------|
| ECT | Temperature | C |
| CAT1 | Exhaust Temperature | C |
| AmbientTemp | Temperature | C |
| OilTemp | Oil Temperature | C |
| IntakeAirT | Intake Air Temperature | C |
| LoggerTemp | Device Temperature | C |
| TPMS_Temp_LF | Temperature | C |
| FL_Ch1 | Temperature | C |
| LateralAcc | Lateral Acceleration | g |
| InlineAcc | Inline Acceleration | g |
| VerticalAcc | Vertical Acceleration | g |
| YawRate | Yaw Rate | deg/s |
| PitchRate | Pitch Rate | deg/s |
| RollRate | Roll Rate | deg/s |
| RPM | Engine RPM | rpm |
| Gear | Gear | gear |
| BrakePress | Brake Circuit Pressure | bar |
| WheelSpdFL | Rear Wheel Speed | km/h |
| SteerAngle | Steering Angle | deg |
| Lambda | Lambda | lambda |
| TPS | Percent | % |
| External Voltage | Battery Voltage | mV |
| MAP | Pressure | bar |
| LF_Shock_Pot | LF Shock Position | mm |
| Baro | Pressure | bar |
| Luminosity | Device Brightness | (none) |
| SpeedAverage | Vehicle Speed | km/h |
| TPMS_Press_LF | Pressure | bar |
| PPS | Percentage Throttle Load | % |
| ClutchSw | Number | (none) |
| BrakeSw | Number | (none) |
| CH | Number | Hz |
| TPMS_Volt_LF | Voltage | mV |
| TPMS_ALM_LF | Number | (none) |
| Predictive Time | (not shown in RS3) | ms |

Note: WheelSpdFL shows "Rear Wheel Speed" due to a vehicle config bug — all 4
WheelSpd channels share the same function value despite FL/FR/RL/RR names.

~26 distinct function values observed.
