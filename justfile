# libxrk task runner

# Format code (Python + Rust)
format:
    uv run black .
    cargo fmt

# Check formatting (Python + Rust)
lint:
    uv run black --check .
    cargo fmt --check

# Rust clippy lints
clippy:
    cargo clippy --workspace --all-targets -- -W clippy::all

# Type check with mypy
typecheck:
    uv run mypy .

# Run tests
test:
    uv run pytest tests/ -v -n auto

# Run spec tests
spec-test:
    uv run pytest spec/tests/ -v -n auto

# Run all checks (lint, clippy, typecheck, test, spec-test)
check: lint clippy typecheck test spec-test

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

# pyodide-build is a BUILD TOOL; its version is independent of the Pyodide
# RUNTIME version. Pinning it to the runtime version (==0.29.3) broke builds:
# those releases hardcode a cross-build-env metadata URL upstream has removed.
# One current pyodide-build serves every runtime. The runtime still dictates the
# host Python (0.29.x xbuildenv refuses to install under 3.12), hence pyenv 3.13.
# Install Emscripten SDK for Pyodide (requires Python 3.13 via pyenv)
emsdk-setup:
    #!/usr/bin/env bash
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$PATH" && \
    uv pip install --python "$HOME/.pyenv/versions/3.13.12/bin/python" pyodide-build==0.39.0 "wheel<0.44" && \
    EMSDK_VERSION=$(pyodide config get emscripten_version) && \
    mkdir -p build/emsdk-0.29 && \
    ([ -d build/emsdk-0.29/.git ] || git clone https://github.com/emscripten-core/emsdk.git build/emsdk-0.29) && \
    cd build/emsdk-0.29 && git config core.autocrlf false && git checkout -- . && \
    ./emsdk install $EMSDK_VERSION && ./emsdk activate $EMSDK_VERSION && \
    WASM_OPT=upstream/bin/wasm-opt && ([ -f ${WASM_OPT}.real ] || mv $WASM_OPT ${WASM_OPT}.real) && \
    sed 's/\r$//' ../../scripts/wasm-opt-wrapper.sh > $WASM_OPT && chmod +x $WASM_OPT

# Install Pyodide npm package
pyodide-setup:
    npm install pyodide-0.29@npm:pyodide@0.29.3

# Build the Pyodide wheel (requires Python 3.13 via pyenv)
pyodide-build: emsdk-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    rm -rf build/lib.* build/bdist.* build/temp.* && \
    rm -f src/libxrk/*wasm32-emscripten.so && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-0.29 && \
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten EMSDK_NODE=$EMSDK/node/22.16.0_64bit/bin/node && \
    export RUSTUP_TOOLCHAIN=nightly USE_LEGACY_PLATFORM=1 && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    uv pip install --python "$HOME/.pyenv/versions/3.13.12/bin/python" pyodide-build==0.39.0 "wheel<0.44" && \
    pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Build and test with Pyodide (requires Python 3.13 via pyenv)
pyodide-test: emsdk-setup pyodide-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    rm -rf build/lib.* build/bdist.* build/temp.* && \
    rm -f src/libxrk/*wasm32-emscripten.so && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-0.29 && \
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten EMSDK_NODE=$EMSDK/node/22.16.0_64bit/bin/node && \
    export RUSTUP_TOOLCHAIN=nightly USE_LEGACY_PLATFORM=1 && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    rm -f dist/*pyodide_2025*.whl && \
    uv pip install --python "$HOME/.pyenv/versions/3.13.12/bin/python" pyodide-build==0.39.0 "wheel<0.44" && \
    pyodide build --exports whole_archive && \
    node scripts/run_pyodide_tests.mjs --dist-dir=./dist --pyodide-version=0.29 && \
    node scripts/run_pyodide_tests_idbfs.mjs --dist-dir=./dist --pyodide-version=0.29; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# --- Pyodide 314 (Python 3.14, PEP 783 pyemscripten_2026_0 -> PyPI) ---
# Unlike 0.29 this builds on STABLE Rust with the rustflags the xbuildenv
# reports, and uses plain emcc: the emcc/wasm-opt shims exist for Emscripten
# 3.1.58 and would strip flags Emscripten 5.0.3 requires.

# Toolchain for Pyodide 314 (Python 3.14 host, Emscripten 5.0.3)
emsdk-setup-314:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -d build/venv-314 ] || uv venv --seed --python 3.14 build/venv-314
    uv pip install --python build/venv-314/bin/python pyodide-build==0.39.0
    build/venv-314/bin/pyodide xbuildenv install 314.0.4
    # The xbuildenv names an exact toolchain (e.g. 1.93.0). rustup treats that
    # as its own toolchain, so the wasm target must be installed for it by name
    # -- having it on `stable` is not enough even when stable is that version.
    RUST_TOOLCHAIN=$(build/venv-314/bin/pyodide config get rust_toolchain)
    rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal         --target wasm32-unknown-emscripten --component rust-src
    EMSDK_VERSION=$(build/venv-314/bin/pyodide config get emscripten_version)
    mkdir -p build/emsdk-314
    [ -d build/emsdk-314/.git ] || git clone https://github.com/emscripten-core/emsdk.git build/emsdk-314
    cd build/emsdk-314 && git config core.autocrlf false && git checkout -- .
    ./emsdk install "$EMSDK_VERSION" && ./emsdk activate "$EMSDK_VERSION"

# Install the Pyodide 314 npm package
pyodide-setup-314:
    npm install pyodide-314@npm:pyodide@314.0.4

# Build the Pyodide 314 wheel (pyemscripten_2026_0, publishable to PyPI)
pyodide-build-314: emsdk-setup-314
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

# Build and test with Pyodide 314
pyodide-test-314: pyodide-build-314 pyodide-setup-314
    #!/usr/bin/env bash
    node scripts/run_pyodide_tests.mjs --dist-dir=./dist --pyodide-version=314.0.4 && \
    node scripts/run_pyodide_tests_idbfs.mjs --dist-dir=./dist --pyodide-version=314.0.4

# Build CPython wheel, sdist, and the Pyodide wheel
build-all: emsdk-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    rm -rf dist/ && \
    source $HOME/.cargo/env && \
    uv build && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-0.29 && \
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten EMSDK_NODE=$EMSDK/node/22.16.0_64bit/bin/node && \
    export RUSTUP_TOOLCHAIN=nightly USE_LEGACY_PLATFORM=1 && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    uv pip install --python "$HOME/.pyenv/versions/3.13.12/bin/python" pyodide-build==0.39.0 "wheel<0.44" && \
    pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; \
    [ $_rc -eq 0 ] && ls -la dist/; exit $_rc
