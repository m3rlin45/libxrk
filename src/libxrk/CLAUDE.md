# libxrk API Reference

## Quick Start

```python
from libxrk import aim_xrk

log = aim_xrk('path/to/file.xrk')  # or .xrz, bytes, BytesIO

# All channels merged into DataFrame
df = log.get_channels_as_table().to_pandas()
```

## LogFile Structure

```python
log.channels   # Dict[str, pa.Table] - channel name -> PyArrow table
log.laps       # pa.Table - columns: num, start_time, end_time (ms)
log.metadata   # Dict[str, str] - session info
```

## Channel Tables

Each channel table has:
- `timecodes` column (int64, milliseconds)
- `<channel_name>` column (values)

Channels have different sample rates. Use `get_channels_as_table()` to merge.

## Channel Metadata

```python
field = log.channels['Engine RPM'].schema.field('Engine RPM')
units = field.metadata.get(b'units', b'').decode()  # e.g., "rpm"
```

Keys: `b"units"`, `b"dec_pts"`, `b"interpolate"`

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
