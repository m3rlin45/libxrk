# libxrk API Reference

## Quick Start

```python
from libxrk import aim_xrk

log = aim_xrk('path/to/file.xrk')  # or .xrz, bytes, BytesIO

# All channels merged into DataFrame
df = log.get_channels_as_table().to_pandas()
```

## aim_xrk Function

```python
aim_xrk(fname, progress=None) -> LogFile
```

**Parameters:**
- `fname`: Path to XRK/XRZ file, or bytes/BytesIO containing file data
- `progress`: Optional callback `(current: int, total: int) -> None` for progress updates

## LogFile Structure

```python
log.channels   # Dict[str, pa.Table] - channel name -> PyArrow table
log.laps       # pa.Table - columns: num, start_time, end_time (ms), lap_type (str)
log.metadata   # Dict[str, Any] - session info
```

## Metadata Fields

Standard metadata fields extracted from XRK files:

| Key | Type | Description |
|-----|------|-------------|
| Driver | str | Driver name |
| Vehicle | str | Vehicle name |
| Venue | str | Track/venue name |
| Log Date | str | Date of log (DD/MM/YYYY) |
| Log Time | str | Time of log (HH:MM:SS) |
| Series | str | Series/competition name |
| Session | str | Session type |
| Long Comment | str | User notes |
| Logger ID | int | Unique logger serial number |
| Logger Model ID | int | Numeric model code |
| Logger Model | str or None | Human-readable model name (e.g., "MXP 1.3", "MXm") |
| Device Name | str | User-configured device name |
| GPS Receiver | str | GPS receiver type: `"GPS"` (external) or `"iGPS"` (internal) |
| Race Mode | str | Lap detection mode: `"speed"` or `"performance"` (absent if not set) |
| Vehicle Electronics Type | str \| int | Vehicle electronics type (string for unconfigured loggers, int otherwise) |
| Odo/* | various | Odometer readings |
| Expansion Devices | list[dict] | CAN expansion devices (Bus Unit, Bus Type, Version, Manufacturer, Model, Logger ID, Model ID) |
| Calibrations | list[dict] | Per-channel calibration data (type, raw_1, raw_2, channel; type 1 adds output_1, output_2) |

## Laps Table

The laps table has columns:
- `num` (int32) — 0-based lap number
- `start_time` (int64) — lap start time in milliseconds
- `end_time` (int64) — lap end time in milliseconds
- `lap_type` (utf8) — lap classification:
  - `"full"` — normal start/finish to start/finish lap
  - `"out"` — first lap, which began when logging started rather than at a S/F
    crossing (`start_time <= 0`)
  - `"in"` — last lap, where GPS puts the car more than 30 m from the start/finish
    line at the lap's end time — i.e. the session ended on the way back to the pits
    rather than at the line. The 30 m threshold is the same one `find_laps()` uses.
    On the GPS-fallback detection path (files with no LAP messages) the last lap is
    always `"in"`, because its end is synthesized at session end rather than at a
    detected crossing.

Out and in laps are not comparable to full laps — exclude them when computing best
lap, lap-time distributions, or lap-over-lap deltas.

## Channel Tables

Each channel table has:
- `timecodes` column (int64, milliseconds)
- `<channel_name>` column (values)

Channels have different sample rates. Use `get_channels_as_table()` to merge.

**Duplicate channel names:** a file's CHS table may define the same long_name
at several channel indices — each is a genuinely distinct channel with its own
`source_channel_id` and data stream. Following the official AIM DLL's
convention, the first occurrence keeps the plain name and the k-th occurrence
is exposed as `"<name> dup <k>"` (e.g. `"RotaryMiddle_led dup 2"`), numbered
by ascending channel index. No duplicate is ever dropped.

## Channel Metadata

Use the `ChannelMetadata` dataclass for typed access to channel metadata:

```python
from libxrk import ChannelMetadata

meta = ChannelMetadata.from_channel_table(log.channels['Engine RPM'])
meta.units        # "rpm"
meta.dec_pts      # 0
meta.interpolate  # True
meta.function     # "Engine RPM"
```

Or extract from a PyArrow field directly:

```python
field = log.channels['Engine RPM'].schema.field('Engine RPM')
meta = ChannelMetadata.from_field(field)
```

To create metadata for new channels:

```python
meta = ChannelMetadata(units="m/s", dec_pts=1, interpolate=True)
field = pa.field("speed", pa.float32(), metadata=meta.to_field_metadata())
```

### ChannelMetadata Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `units` | str | `""` | Unit string (e.g., "rpm", "m/s") |
| `dec_pts` | int | `0` | Display decimal places |
| `interpolate` | bool | `False` | Linear interpolation when resampling |
| `function` | str | `""` | RS3 function classification |
| `source_type` | int | `0` | Source type (1=internal, 5=GPS, 9=CAN) |
| `source_channel_id` | int | `0` | Channel ID within device |
| `device_tag` | str | `""` | Device identifier (e.g., "@AIM") |
| `cal_value_1` | float | `0.0` | Calibration offset |
| `cal_value_2` | float | `1.0` | Calibration scale |
| `display_range_min` | float | `0.0` | Min display range |
| `display_range_max` | float | `0.0` | Max display range |

The `function` field contains the RS3 channel function classification (e.g., "Temperature", "Engine RPM", "Lateral Acceleration"). This is a best-effort lookup derived from `(maybe_display_format, unit_type_byte, config_flags)` in the CHS binary. Empty string for GPS-derived channels and unrecognized combinations.

## LogFile Methods

### Channel Selection
```python
log.select_channels(['GPS Speed', 'Engine RPM'])  # Returns new LogFile
```

### Time Filtering
```python
log.filter_by_time_range(60000, 120000)  # [start, end) in ms
log.filter_by_time_range(60000, 120000, channel_names=['GPS Speed'])
```

### Lap Filtering
```python
log.filter_by_lap(5)  # Filter to lap 5's time range
log.filter_by_lap(5, channel_names=['GPS Speed'])
```

### Resampling
```python
# Resample to a reference channel's timebase
log.resample_to_channel('GPS Speed')

# Resample to custom timebase
target = pa.array(range(0, 100000, 100), type=pa.int64())
log.resample_to_timecodes(target)
```

### Merging Channels
```python
log.get_channels_as_table()  # Returns pa.Table with all channels merged
```

All methods return new `LogFile` instances, enabling chaining:
```python
df = (log
    .filter_by_lap(5)
    .select_channels(['Engine RPM', 'GPS Speed'])
    .get_channels_as_table()
    .to_pandas())
```

## GPS Timing Fix

Some AIM loggers have a firmware bug causing 65533ms timestamp gaps in GPS data (16-bit overflow). This is automatically corrected when loading files - no action needed.

Affected channels: `GPS Speed`, `GPS Latitude`, `GPS Longitude`, `GPS Altitude`

## Common Patterns

```python
# Single channel to pandas
df = log.channels['Engine RPM'].to_pandas()

# All channels merged (handles different sample rates)
df = log.get_channels_as_table().to_pandas()

# Load from bytes/BytesIO
log = aim_xrk(file_bytes)

# Filter and analyze a specific lap
lap5 = log.filter_by_lap(5, ['GPS Speed', 'Engine RPM'])
df = lap5.get_channels_as_table().to_pandas()
```
