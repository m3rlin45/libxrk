# libxrk Development Guide

## Project Overview
Python library for reading AIM XRK/XRZ motorsports telemetry files. Uses Cython for binary parsing (default) with a Rust+PyO3 parser (~2x faster). Both backends are included in all published wheels. Data is represented as PyArrow tables.

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

- `src/libxrk/aim_xrk.pyx` - Cython binary parser (default backend)
- `src/libxrk/aim_xrk.pyi` - Type stubs for Cython module
- `rust/` - Rust+PyO3 parser (~2x faster)
- `src/libxrk/_aim_xrk_rs.pyi` - Type stubs for Rust module
- `src/libxrk/base.py` - LogFile dataclass, channel merging
- `src/libxrk/gps.py` - GPS utilities, lap detection, timing fix
- `tests/` - pytest tests with real XRK data in `tests/test_data/`

## Parser Backends

The library has two parser backends with identical APIs. Both are included in all published wheels.

- **Cython** (default): `src/libxrk/aim_xrk.pyx` — mature, well-tested
- **Rust**: `rust/` — ~2x faster, used automatically in Pyodide/WASM

Set `LIBXRK_BACKEND=rust` to use the Rust parser. Falls back to Rust if Cython is unavailable.

```bash
poetry run poe rust-build       # Build Rust extension
LIBXRK_BACKEND=rust poetry run poe test  # Test with Rust backend
poetry run python scripts/benchmark.py   # Compare performance
```

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

The library supports running in the browser via Pyodide. Two versions are supported:

| Version | Python | Emscripten | ABI Tag |
|---------|--------|------------|---------|
| Pyodide 0.27.x | 3.12 | 3.1.58 | `pyodide_2024_0` |
| Pyodide 0.29.x | 3.13 | 4.0.9 | `pyodide_2025_0` |

### Pyodide 0.27.x (default)

```bash
poetry run poe emsdk-setup    # Install Emscripten SDK (first time only)
poetry run poe pyodide-setup  # Install Pyodide npm package (first time only)
poetry run poe pyodide-build  # Build Pyodide wheel
poetry run poe pyodide-test   # Build and run tests in Pyodide
poetry run poe build-all      # Build CPython wheel, sdist, and Pyodide wheel
```

### Pyodide 0.29.x (requires Python 3.13 via pyenv)

```bash
poetry run poe emsdk-setup-0-29    # Install Emscripten SDK 4.0.9
poetry run poe pyodide-setup-0-29  # Install Pyodide 0.29.x npm package
poetry run poe pyodide-build-0-29  # Build Pyodide 0.29.x wheel
poetry run poe pyodide-test-0-29   # Build and run tests in Pyodide 0.29.x
```

Pyodide test scripts are in `scripts/run_pyodide_tests*.mjs`. They accept `--pyodide-version=0.27` or `--pyodide-version=0.29` to select the version.

## Cython Rebuild

After modifying `src/libxrk/aim_xrk.pyx`, you **must** run `poetry install` to recompile the Cython extension before running tests. Stale `.so` files will cause incorrect test results without any obvious error.

## Rust Rebuild

After modifying Rust source in `rust/`, run `poetry run poe rust-build` (release) or `poetry run poe rust-build-debug` (faster compile). The Rust extension coexists with Cython — both can be installed simultaneously.

## Architecture Notes

- Each channel is a PyArrow table with `timecodes` + value columns
- Different channels have different sample rates
- `get_channels_as_table()` merges via full outer join with interpolation/forward-fill
- Channel metadata stored in PyArrow field.metadata (bytes keys: `b"units"`, `b"dec_pts"`, `b"interpolate"`)
- GPS timing fix auto-corrects 65533ms gaps (AIM firmware bug)
- All filtering/resampling methods return new `LogFile` instances (immutable pattern)
- `resample_to_timecodes()` is the core resampling logic, other methods delegate to it

## LogFile Methods

- `select_channels(names)` - Return LogFile with only specified channels
- `filter_by_time_range(start, end, channels?)` - Filter to time range [start, end)
- `filter_by_lap(lap_num, channels?)` - Filter to specific lap's time range
- `resample_to_timecodes(timecodes, channels?)` - Resample all channels to target timebase
- `resample_to_channel(ref_channel, channels?)` - Resample to reference channel's timebase
- `get_channels_as_table()` - Merge all channels into single table

## Documentation Requirements

When changing the public API:
1. Update docstrings with Google-style format (Args, Returns, Raises, Example)
2. Update `README.md` usage examples
3. Update `src/libxrk/CLAUDE.md` API reference
4. Update type stubs if needed (`.pyi` files)
