# GPS Timing Gap Bug

## Summary

Some AIM data loggers produce GPS data with spurious timestamp jumps—typically a **65533ms gap** that should be approximately 40ms. This causes GPS data to appear misaligned with other channels (BRK, InlineAcc, steering, etc.).

**libxrk automatically detects and corrects this issue** when loading XRK/XRZ files.

## The Problem

### Symptoms

When loading certain AIM log files, you may observe:

- GPS-derived acceleration does not correlate with the InlineAcc channel
- GPS data appears to extend ~66 seconds longer than other channels
- Plotting GPS Speed vs time shows the data "shifted" relative to other channels

### Root Cause

At a random sample index (e.g., index 87 at ~4 seconds into the session), there is a **65533ms gap** in the GPS timestamps:

```
Before gap: time=3986ms, speed=1.80 m/s
After gap:  time=69519ms, speed=1.96 m/s
Actual gap: 65533ms (should be ~40ms)
```

The gap value (`65533 ≈ 0xFFED ≈ 2^16 - 3`) strongly suggests a **16-bit integer overflow** bug in the AIM logger firmware or GPS module.

### Affected Channels

All GPS channels share the same timecodes and are affected together:

- `GPS Speed`
- `GPS Latitude`
- `GPS Longitude`
- `GPS Altitude`

All other channels (BRK, InlineAcc, ACCEL, steering, Throttle, etc.) have consistent timing with no gaps.

## The Fix

libxrk automatically applies the fix in `aim_xrk()` after loading the file. The fix:

1. **Detects gaps** larger than 400ms (10× the expected 40ms sample interval)
2. **Corrects GPS timecodes** by subtracting the excess time from all samples after the gap
3. **Corrects lap boundaries** since lap start/end times are computed from GPS timestamps

### Validation

After applying the fix:

| Metric | Before Fix | After Fix |
|--------|------------|-----------|
| GPS-derived vs InlineAcc correlation | 0.08 | 0.82 |
| Smoothed correlation | 0.09 | 0.95 |

## Technical Details

### Channel Characteristics

| Channel | Sample Rate | Typical Range |
|---------|-------------|---------------|
| GPS Speed/Lat/Long/Alt | 25 Hz (40ms) | ~500ms to end of session |
| InlineAcc | 50 Hz (20ms) | 0ms to end of session |
| BRK | 50 Hz (20ms) | ~460ms to end of session |
| Throttle | ~10 Hz (98ms) | ~475ms to end of session |
| steering | ~44 Hz (22ms) | ~460ms to end of session |

### Gap Detection Parameters

- **Gap threshold**: 10× expected sample interval (400ms for 25Hz GPS)
- **Expected interval**: 40ms (configurable via `expected_dt_ms` parameter)
- **Correction**: `gap_size - expected_dt_ms` subtracted from all subsequent timestamps

### API

The fix is applied automatically when loading files:

```python
from libxrk import aim_xrk

log = aim_xrk("file.xrk")  # GPS timing fix is applied automatically
```

## Files Known to Exhibit This Bug

- `CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk` - 65533ms gap at ~4 seconds

## Files Without This Bug

- `CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk` - Has a 52-second gap near end of session, but this is legitimate (end of recording/GPS signal loss)
- `CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk` - No timing issues detected
