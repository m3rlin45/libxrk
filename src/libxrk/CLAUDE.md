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
```
