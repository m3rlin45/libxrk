# libxrk Development Guide

## Project Overview
Python library for reading AIM XRK/XRZ motorsports telemetry files. Uses Cython for binary parsing and PyArrow for data representation.

**Important:** Always run commands through `poetry run` to use the project's virtual environment.

## Quick Commands

```bash
poetry install          # Install dependencies
poetry run poe check    # Run all checks (lint, typecheck, test)
poetry run poe format   # Black formatting
poetry run poe typecheck # mypy
poetry run poe test     # pytest
poetry build            # Build wheel
```

## Code Structure

- `src/libxrk/aim_xrk.pyx` - Cython binary parser (core logic)
- `src/libxrk/aim_xrk.pyi` - Type stubs for Cython module
- `src/libxrk/base.py` - LogFile dataclass, channel merging
- `src/libxrk/gps.py` - GPS utilities, lap detection, timing fix
- `tests/` - pytest tests with real XRK data in `tests/test_data/`

## Code Style

- Black formatting (100-char lines)
- Type hints checked with mypy
- Docstrings: Google style

## Testing

```bash
poetry run poe test                              # All tests
poetry run pytest tests/test_sfj_xrk.py -v       # Specific file
poetry run pytest tests/test_sfj_xrk.py -k name  # Specific test
```

Test data: `tests/test_data/` contains real XRK/XRZ files (SFJ and 86 vehicles).

## Pyodide (WebAssembly) Builds

The library supports running in the browser via Pyodide. Requires Python 3.12+.

```bash
poetry run poe emsdk-setup    # Install Emscripten SDK (first time only)
poetry run poe pyodide-setup  # Install Pyodide npm package (first time only)
poetry run poe pyodide-build  # Build Pyodide wheel
poetry run poe pyodide-test   # Build and run tests in Pyodide
poetry run poe build-all      # Build CPython wheel, sdist, and Pyodide wheel
```

Pyodide test scripts are in `scripts/run_pyodide_tests*.mjs`.

## Architecture Notes

- Each channel is a PyArrow table with `timecodes` + value columns
- Different channels have different sample rates
- `get_channels_as_table()` merges via full outer join with interpolation/forward-fill
- Channel metadata stored in PyArrow field.metadata (bytes keys: `b"units"`, `b"dec_pts"`, `b"interpolate"`)
- GPS timing fix auto-corrects 65533ms gaps (AIM firmware bug)
