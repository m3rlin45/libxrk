# libxrk

A Python library for reading AIM XRK and XRZ files from AIM automotive data loggers.

## Features

- Read AIM XRK files (raw data logs)
- Read AIM XRZ files (zlib-compressed XRK files)
- Parse track data and telemetry channels
- GPS coordinate conversion and lap detection
- High-performance Cython implementation
- Supports Python 3.10 - 3.14

## Installation

### Install from PyPI

```bash
pip install libxrk
```

### Install from Source

#### Prerequisites

On Ubuntu/Debian:
```bash
sudo apt install build-essential python3-dev
```

#### Install with Poetry

```bash
poetry install
```

The Cython extension will be automatically compiled during installation.

## Usage

```python
from libxrk import aim_xrk

# Read an XRK file
log = aim_xrk('path/to/file.xrk')

# Read an XRZ file (automatically decompressed)
log = aim_xrk('path/to/file.xrz')

# Access channels (each channel is a PyArrow table with 'timecodes' and value columns)
for channel_name, channel_table in log.channels.items():
    print(f"{channel_name}: {channel_table.num_rows} samples")

# Get all channels merged into a single PyArrow table
# (handles different sample rates with interpolation/forward-fill)
merged_table = log.get_channels_as_table()
print(merged_table.column_names)

# Convert to pandas DataFrame
df = merged_table.to_pandas()

# Access laps (PyArrow table with 'num', 'start_time', 'end_time' columns)
print(f"Laps: {log.laps.num_rows}")
for i in range(log.laps.num_rows):
    lap_num = log.laps.column("num")[i].as_py()
    start = log.laps.column("start_time")[i].as_py()
    end = log.laps.column("end_time")[i].as_py()
    print(f"Lap {lap_num}: {start} - {end}")

# Access metadata
print(log.metadata)
# Includes: Driver, Vehicle, Venue, Log Date/Time, Logger ID, Logger Model, Device Name, etc.
```

### Filtering and Resampling

```python
from libxrk import aim_xrk

log = aim_xrk('session.xrk')

# Select specific channels
gps_log = log.select_channels(['GPS Latitude', 'GPS Longitude', 'GPS Speed'])

# Filter to a time range (milliseconds, inclusive start, exclusive end)
segment = log.filter_by_time_range(60000, 120000)

# Filter to a specific lap
lap5 = log.filter_by_lap(5)

# Combine filtering and channel selection
lap5_gps = log.filter_by_lap(5, channel_names=['GPS Latitude', 'GPS Longitude'])

# Resample all channels to match a reference channel's timebase
aligned = log.resample_to_channel('GPS Speed')

# Resample to a custom timebase
import pyarrow as pa
target = pa.array(range(0, 100000, 100), type=pa.int64())  # 10 Hz
resampled = log.resample_to_timecodes(target)

# Chain operations for analysis workflows
df = (log
    .filter_by_lap(5)
    .select_channels(['Engine RPM', 'GPS Speed'])
    .resample_to_channel('GPS Speed')
    .get_channels_as_table()
    .to_pandas())
```

All filtering and resampling methods return new `LogFile` instances (immutable pattern), enabling method chaining for complex analysis workflows.

## Development

### Quick Check
```bash
# Run all quality checks (format check, type check, tests)
poetry run poe check
```

### Code Formatting

This project uses [Black](https://black.readthedocs.io/) for code formatting.

```bash
# Format all Python files
poetry run black .
```

### Type Checking

This project uses [mypy](https://mypy.readthedocs.io/) for static type checking.

```bash
# Run type checker on all Python files
poetry run mypy .
```

### Running Tests

This project uses [pytest](https://pytest.org/) for testing.

```bash
# Run all tests
poetry run pytest

# Run tests with verbose output
poetry run pytest -v

# Run specific test file
poetry run pytest tests/test_xrk_loading.py

# Run tests with coverage
poetry run pytest --cov=libxrk
```

### Testing with Pyodide (WebAssembly)

You can test the library in a WebAssembly environment using Pyodide.
This requires Node.js to be installed.

```bash
# Build and run tests in Pyodide (installs Emscripten SDK to build/emsdk if needed)
poetry run poe pyodide-test
```

Note: Pyodide tests run automatically in CI via GitHub Actions.

### Building

```bash
# Build CPython wheel and sdist
poetry build

# Build all wheels (CPython, Pyodide/WebAssembly, and sdist)
poetry run poe build-all
```

### Clean Build

```bash
# Clean all build artifacts and rebuild
rm -rf build/ dist/ src/libxrk/*.so && poetry install
```

## Testing

The project includes end-to-end tests that validate XRK and XRZ file loading and parsing.

Test files are located in `tests/test_data/` and include real XRK and XRZ files for validation.

## Credits

This project incorporates code from [TrackDataAnalysis](https://github.com/racer-coder/TrackDataAnalysis) by Scott Smith, used under the MIT License.

## License

MIT License - See LICENSE file for details.
