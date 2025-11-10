# libxrk

A Python library for reading AIM XRK and XRZ files from AIM automotive data loggers.

## Features

- Read AIM XRK files (raw data logs)
- Parse track data and telemetry channels
- GPS coordinate conversion and lap detection
- High-performance Cython implementation

## Installation

### Prerequisites

On Ubuntu/Debian:
```bash
sudo apt install build-essential python3-dev
```

### Install with Poetry

```bash
poetry install
```

The Cython extension will be automatically compiled during installation.

## Usage

```python
from libxrk.data import AIMXRK

# Read an XRK file
log = AIMXRK('path/to/file.xrk', progress=None)

# Access channels
for channel_name, channel in log.channels.items():
    print(f"{channel_name}: {len(channel.timecodes)} samples")

# Access laps
for lap in log.laps:
    print(f"Lap {lap.num}: {lap.start_time} - {lap.end_time}")

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

### Building

```bash
poetry build
```

### Clean Build

```bash
# Clean all build artifacts and rebuild
rm -rf build/ dist/ src/libxrk/data/*.so && poetry install
```

## Testing

The project includes end-to-end tests that validate XRK file loading and parsing.

Test files are located in `tests/test_data/` and include real XRK files for validation.

## Credits

This project incorporates code from [TrackDataAnalysis](https://github.com/racer-coder/TrackDataAnalysis) by Scott Smith, used under the MIT License.

## License

MIT License - See LICENSE file for details.
