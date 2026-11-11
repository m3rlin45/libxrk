# libxrk task runner

# Format code (Python + Rust)
format:
    uv run black .
    cargo fmt

# Check formatting (Python + Rust)
lint:
    uv run black --check .
    cargo fmt --check

# Rust clippy lints. Under `uv run` so PyO3's build script finds the project
# venv's Python: a bare cargo takes the first python3 on PATH, which may be
# newer than PyO3 supports.
clippy:
    uv run cargo clippy --workspace --all-targets -- -W clippy::all

# Type check with mypy
typecheck:
    uv run mypy .

# Run tests
test:
    uv run pytest tests/ -v -n auto

# Run spec tests. Single-process on purpose: the suite holds every parsed
# fixture in session fixtures (~3 GB) and each xdist worker would hold its own copy.
spec-test:
    uv run pytest spec/tests/ -v

# Rust unit and integration tests (core crate and the C ABI crate;
# libxrk-python is an extension module and only links inside Python)
rust-test:
    cargo test -p libxrk -p libxrk-ffi

# Run all checks (lint, clippy, typecheck, test, spec-test, rust-test)
check: lint clippy typecheck test spec-test rust-test

# Generate spec test vectors
spec-vectors:
    uv run python spec/test_vectors/generate_vectors.py

# Run benchmark
benchmark: rust-build
    uv run python scripts/benchmark.py

# Interactive REPL with a loaded XRK file
repl:
    uv run python -i -c "from libxrk import aim_xrk; log = aim_xrk('tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk'); print('XRK file loaded as: log'); print(f'Channels: {len(log.channels)}'); print(f'Laps: {len(log.laps)}'); print(f'Metadata keys: {list(log.metadata.keys())}')"

# Build Rust extension module (release)
rust-build:
    #!/usr/bin/env bash
    source $HOME/.cargo/env && uv run maturin develop --release

# Build Rust extension module (debug, faster compile)
rust-build-debug:
    #!/usr/bin/env bash
    source $HOME/.cargo/env && uv run maturin develop

# Build the C ABI library for non-Python consumers
# (target/release/libxrk_ffi.so, libxrk_ffi.dylib or xrk_ffi.dll)
ffi-build:
    cargo build --release -p libxrk-ffi

# Check the C ABI against the Python LogFile on every fixture
ffi-test: ffi-build
    #!/usr/bin/env bash
    set -euo pipefail
    case "$(uname -s)" in
        Darwin) lib=libxrk_ffi.dylib ;;
        MINGW*|MSYS*|CYGWIN*) lib=xrk_ffi.dll ;;
        *) lib=libxrk_ffi.so ;;
    esac
    LIBXRK_FFI_LIB="target/release/$lib" uv run pytest tests/test_ffi.py -v

# --- Pyodide (runtime 314 = Python 3.14, PEP 783 pyemscripten_2026_0 -> PyPI) ---
# Builds on STABLE Rust with the rustflags the xbuildenv reports, using plain
# emcc. Query toolchain details with `pyodide config get rust_toolchain` /
# `rustflags` / `emscripten_version`.
#
# pyodide-build is a BUILD TOOL; its version is independent of the Pyodide
# RUNTIME version. Pinning it to the runtime version (==0.29.3) broke builds:
# those releases hardcode a cross-build-env metadata URL upstream has removed.
# The runtime is selected by the argument to `pyodide xbuildenv install`.

# Toolchain for Pyodide (Python 3.14 host, Emscripten 5.0.3)
emsdk-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -d build/venv-314 ] || uv venv --seed --python 3.14 build/venv-314
    uv pip install --python build/venv-314/bin/python pyodide-build==0.39.0
    build/venv-314/bin/pyodide xbuildenv install 314.0.4
    # The xbuildenv names an exact toolchain (e.g. 1.93.0). rustup treats that
    # as its own toolchain, so the wasm target must be installed for it by name
    # -- having it on `stable` is not enough even when stable is that version.
    RUST_TOOLCHAIN=$(build/venv-314/bin/pyodide config get rust_toolchain)
    rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal \
        --target wasm32-unknown-emscripten
    EMSDK_VERSION=$(build/venv-314/bin/pyodide config get emscripten_version)
    mkdir -p build/emsdk-314
    [ -d build/emsdk-314/.git ] || git clone https://github.com/emscripten-core/emsdk.git build/emsdk-314
    cd build/emsdk-314 && git config core.autocrlf false && git checkout -- .
    ./emsdk install "$EMSDK_VERSION" && ./emsdk activate "$EMSDK_VERSION"

# Install the Pyodide npm package (for running tests under Node)
pyodide-setup:
    npm install

# Build the Pyodide wheel (pyemscripten_2026_0, publishable to PyPI)
pyodide-build: emsdk-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    rm -rf build/lib.* build/bdist.* build/temp.* && \
    rm -f src/libxrk/*wasm32-emscripten.so dist/*pyemscripten_2026*.whl && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-314 && \
    export PATH="$PWD/build/venv-314/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    export RUSTUP_TOOLCHAIN=$(build/venv-314/bin/pyodide config get rust_toolchain) && \
    export RUSTFLAGS=$(build/venv-314/bin/pyodide config get rustflags) && \
    pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Build and test with Pyodide
pyodide-test: pyodide-build pyodide-setup
    #!/usr/bin/env bash
    node scripts/run_pyodide_tests.mjs --dist-dir=./dist && \
    node scripts/run_pyodide_tests_idbfs.mjs --dist-dir=./dist

# Build CPython wheel, sdist, and the Pyodide wheel
build-all:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf dist/
    source $HOME/.cargo/env
    uv build
    just pyodide-build
    ls -la dist/
