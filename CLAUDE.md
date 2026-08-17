# libxrk Development Guide

## Project Overview
Python library for reading AIM XRK/XRZ motorsports telemetry files. Uses Cython for binary parsing (default) with a Rust+PyO3 parser (~2x faster). Both backends are included in all published wheels. Data is represented as PyArrow tables.

**Important:** Always use `just` recipes when one exists for the task. Only fall back to `uv run` for one-off commands that have no `just` recipe (e.g., running a specific script).

## Quick Commands

```bash
uv sync              # Install dependencies
just check           # Run all checks (lint, typecheck, test)
just format          # Black formatting
just typecheck       # mypy
just test            # pytest
uv build             # Build wheel
```

## Reference Implementation / Wire Format Spec

`spec/xrk_format.py` is the **single source of truth for the XRK wire format**, built with the Construct library. It parses real XRK/XRZ files and round-trips bytes byte-identically (every message type has a `TestXxxRoundTrip` in `spec/tests/test_round_trip.py` asserting `build(parse(raw)) == raw`).

**Any change to how the wire format is interpreted must land in `spec/xrk_format.py` first.** The Cython and Rust backends then conform to the spec — not the other way around. Cross-implementation tests in `spec/tests/test_spec.py` compare the spec's output against the Cython parser and, when available, the official AIM DLL (via Wine, in `tests/reference_dll/`) as ground truth.

When you find data parsing differs from AIM's RaceStudio output, start by reading `spec/xrk_format.py` and `spec/docs/`. Do not patch the Cython or Rust parsers without first updating the spec.

## Code Structure

- `spec/xrk_format.py` - Construct-based executable spec (**reference implementation** — the wire format lives here)
- `spec/tests/` - spec tests: round-trip + cross-impl vs Cython and the AIM DLL
- `spec/docs/companion.md` - application-level algorithms (GPS timing, lap detection, decoder dispatch)
- `spec/docs/unknown_regions.md` - catalog of under-reverse-engineered byte ranges per message type
- `src/libxrk/aim_xrk.pyx` - Cython binary parser (default backend, conforms to spec)
- `src/libxrk/aim_xrk.pyi` - Type stubs for Cython module
- `crates/` - Rust+PyO3 parser (~2x faster, conforms to spec)
- `src/libxrk/_aim_xrk_rs.pyi` - Type stubs for Rust module
- `src/libxrk/base.py` - LogFile dataclass, channel merging
- `src/libxrk/gps.py` - GPS utilities, lap detection, timing fix
- `tests/` - pytest tests with real XRK data in `tests/test_data/`
- `tests/reference_dll/` - official AIM parser (via Wine) used as ground truth for spec cross-checks

## Parser Backends

The library has two parser backends with identical APIs. Both are included in all published wheels. **Both backends implement the wire format defined by `spec/xrk_format.py` — the Construct spec is the reference. If parsing changes, update the spec first.**

- **Cython** (default): `src/libxrk/aim_xrk.pyx` — mature, well-tested
- **Rust**: `crates/` — ~2x faster

Both backends are always included in all build types (CPython wheels, Pyodide/WASM wheels, sdist).
A failure to build either backend fails the whole build. There is no automatic fallback.

Set `LIBXRK_BACKEND=rust` to use the Rust parser. Default is Cython on all platforms, including Pyodide.

```bash
just rust-build                        # Build Rust extension
LIBXRK_BACKEND=rust just test          # Test with Rust backend
uv run python scripts/benchmark.py     # Compare performance
```

## Code Style

- Black formatting (100-char lines)
- Type hints checked with mypy
- Docstrings: Google style

## Testing

```bash
just test                                        # All tests
uv run pytest tests/test_sfj_xrk.py -v           # Specific file
uv run pytest tests/test_sfj_xrk.py -k name      # Specific test
```

Test data: `tests/test_data/` contains real XRK/XRZ files (SFJ and 86 vehicles).

## Pyodide (WebAssembly) Builds

The library supports running in the browser via Pyodide. Two runtimes:

| Version | Python | Emscripten | Rust | ABI Tag | Distribution |
|---------|--------|------------|------|---------|--------------|
| Pyodide 0.29.x | 3.13 | 4.0.9 | nightly | `pyodide_2025_0` | GitHub Release |
| Pyodide 314.x | 3.14 | 5.0.3 | 1.93.0 (stable) | `pyemscripten_2026_0` | **PyPI** |

[PEP 783](https://peps.python.org/pep-0783/) defines the `pyemscripten_*`
platform tag, which PyPI accepts. Only runtimes that set
`PYEMSCRIPTEN_PLATFORM_VERSION` can consume such a wheel:

* **314** reports `2026_0` and installs the PyPI wheel.
* **0.29** reports nothing — it predates the PEP and *rejects* a
  `pyemscripten_*` wheel with "Wheel was built with Emscripten
  vpyemscripten.2025.0 but Pyodide was built with Emscripten v4.0.9". Since
  `pyodide-build` >= 0.35 emits the new tag by default, the 0.29 build sets
  `USE_LEGACY_PLATFORM=1` to keep `pyodide_2025_0`. Do not remove that.

Pyodide 0.27.x (Python 3.12, `pyodide_2024_0`) was dropped: no known consumer,
and PEP 783 starts at the 2025 ABI.

The 314 build differs from 0.29 in ways the xbuildenv dictates — query them with
`pyodide config get rust_toolchain` / `rustflags`:

* **stable** Rust, not nightly, and no `build-std`.
* Plain `emcc`, **not** `scripts/emcc-no-wasm-exceptions.sh`: that shim strips
  `-fwasm-exceptions`, which Emscripten 5.0.3 requires.
* No `wasm-opt` wrapper — that exists for Emscripten 3.1.58's binaryen.

Note `rustup` treats a pinned version like `1.93.0` as its own toolchain: the
wasm target must be added *for that toolchain by name*, even if `stable` is
currently the same version.

**`pyodide-build` version is independent of the Pyodide runtime version.** It is
a build tool: one current version (pinned to `0.32.0` in `uv.lock`, the justfile
and `PYODIDE_BUILD_VERSION` in `.github/workflows/pyodide.yml`) builds for every
runtime, and the runtime is chosen by the argument to `pyodide xbuildenv install`.

Do not pin `pyodide-build` to the runtime version. Doing so previously broke all
Pyodide builds: `pyodide-build` 0.27.3/0.29.3 hardcode a cross-build-environment
metadata URL that upstream has since removed, so `xbuildenv install` fails with a
404. The *host Python* still has to match the runtime (the 0.29.x xbuildenv
refuses to install under Python 3.12), which is why the 0.29 recipes use pyenv.

### Building (requires Python 3.13 via pyenv)

```bash
just emsdk-setup      # Install Emscripten SDK 4.0.9 (first time only)
just pyodide-setup    # Install Pyodide npm package (first time only)
just pyodide-build    # Build the Pyodide wheel
just pyodide-test     # Build and run tests in Pyodide
just build-all        # Build CPython wheel, sdist, and the Pyodide wheel
```

```bash
just pyodide-build-314   # Build the PyPI-publishable wheel
just pyodide-test-314    # Build and test it
```

Pyodide test scripts are in `scripts/run_pyodide_tests*.mjs`. They take
`--pyodide-version=` (default `0.29`); `ABI_TAG` in each script maps a runtime
version to the wheel ABI tag to look for.

**Each test class runs in a fresh Pyodide instance.** The wasm32 heap only ever
grows and the address space is capped at 4GB. One `aim_xrk()` on the 86 fixture
(41.8MB, 100 channels, 7.7M rows) peaks around 190MB, and `Test86XRK` parses it
~30 times; sharing one interpreter across classes ratchets the heap to 3.8GB on
314 (which needs ~30% more than 0.29) and dies with `MemoryError`.

`scripts/emcc-no-wasm-exceptions.sh`, `scripts/wasm-opt-wrapper.sh` and the
`build-std` setting in `.cargo/config.toml` were all added for Emscripten 3.1.58
(Pyodide 0.27) but are still applied to the 0.29 build. Whether 0.29 still needs
them is untested — check before removing.

## Cython Rebuild

After modifying `src/libxrk/aim_xrk.pyx`, you **must** run `uv sync --reinstall-package libxrk` to recompile the Cython extension before running tests. A plain `uv sync` may skip the rebuild if it thinks nothing changed. Stale `.so` files will cause incorrect test results without any obvious error.

## Rust Rebuild

After modifying Rust source in `crates/`, run `just rust-build` (release) or `just rust-build-debug` (faster compile). Both backends are always installed together — a failure to build either fails the whole build.

## Architecture Notes

- Each channel is a PyArrow table with `timecodes` + value columns
- Different channels have different sample rates
- `get_channels_as_table()` merges via full outer join with interpolation/forward-fill
- Channel metadata stored in PyArrow field.metadata; use `ChannelMetadata.from_field()` for typed access
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

## Source Control

This repo uses Sapling (`sl`). **Never use `GIT_DIR=.sl/store/git` or any `git` commands.** Use `sl` commands for all source control operations and `gh --repo m3rlin45/libxrk` for GitHub CLI commands.

## Documentation Requirements

When changing the public API:
1. Update docstrings with Google-style format (Args, Returns, Raises, Example)
2. Update `README.md` usage examples
3. Update `src/libxrk/CLAUDE.md` API reference
4. Update type stubs if needed (`.pyi` files)
