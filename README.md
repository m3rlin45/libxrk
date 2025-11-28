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
```

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
