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

### Code Formatting

This project uses [Black](https://black.readthedocs.io/) for code formatting.

```bash
# Format all Python files
poetry run black .

```

### Building

```bash
poetry build
```

### Running Tests

```bash
poetry run python test_install.py
```

### Clean Build

```bash
# Clean all build artifacts and rebuild
rm -rf build/ dist/ src/libxrk/data/*.so && poetry install
```

## Credits

This project incorporates code from [TrackDataAnalysis](https://github.com/racer-coder/TrackDataAnalysis) by Scott Smith, used under the MIT License.

## License

MIT License - See LICENSE file for details.
