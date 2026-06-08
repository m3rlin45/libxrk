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

# Build the standalone browser wasm parser (no Python) -> crates/xrk-wasm/pkg/
wasm-build:
    ./scripts/build-xrk-wasm.sh

# Install Emscripten SDK for Pyodide 0.27.x
emsdk-setup:
    #!/usr/bin/env bash
    EMSDK_VERSION=$(uv run pyodide config get emscripten_version) && \
    mkdir -p build && \
    ([ -d build/emsdk ] || (git clone https://github.com/emscripten-core/emsdk.git build/emsdk && cd build/emsdk && git config core.autocrlf false && git checkout -- .)) && \
    ./build/emsdk/emsdk install $EMSDK_VERSION && \
    ./build/emsdk/emsdk activate $EMSDK_VERSION && \
    WASM_OPT=build/emsdk/upstream/bin/wasm-opt && \
    ([ -f ${WASM_OPT}.real ] || mv $WASM_OPT ${WASM_OPT}.real) && \
    sed 's/\r$//' scripts/wasm-opt-wrapper.sh > $WASM_OPT && chmod +x $WASM_OPT

# Install Pyodide 0.27.x npm package
pyodide-setup:
    npm install pyodide-0.27@npm:pyodide@0.27.3

# Build Pyodide 0.27.x wheel (Cython + Rust for WASM)
pyodide-build: emsdk-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    source $HOME/.cargo/env && \
    export RUSTUP_TOOLCHAIN=nightly && \
    source ./build/emsdk/emsdk_env.sh && \
    export RUSTFLAGS="-C target-feature=-exception-handling -C panic=abort" && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    uv run pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Build and run tests in Pyodide 0.27.x (WebAssembly)
pyodide-test: emsdk-setup pyodide-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    rm -f dist/*pyodide_2024*.whl && \
    source $HOME/.cargo/env && \
    export RUSTUP_TOOLCHAIN=nightly && \
    source ./build/emsdk/emsdk_env.sh && \
    export RUSTFLAGS="-C target-feature=-exception-handling -C panic=abort" && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    uv run pyodide build --exports whole_archive && \
    node scripts/run_pyodide_tests.mjs --dist-dir=./dist --pyodide-version=0.27 && \
    node scripts/run_pyodide_tests_idbfs.mjs --dist-dir=./dist --pyodide-version=0.27; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Install Emscripten SDK for Pyodide 0.29.x (requires Python 3.13 via pyenv)
emsdk-setup-0-29:
    #!/usr/bin/env bash
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$PATH" && \
    pip install pyodide-build==0.29.3 "wheel<0.44" && \
    EMSDK_VERSION=$(pyodide config get emscripten_version) && \
    mkdir -p build/emsdk-0.29 && \
    ([ -d build/emsdk-0.29/.git ] || git clone https://github.com/emscripten-core/emsdk.git build/emsdk-0.29) && \
    cd build/emsdk-0.29 && git config core.autocrlf false && git checkout -- . && \
    ./emsdk install $EMSDK_VERSION && ./emsdk activate $EMSDK_VERSION && \
    WASM_OPT=upstream/bin/wasm-opt && ([ -f ${WASM_OPT}.real ] || mv $WASM_OPT ${WASM_OPT}.real) && \
    sed 's/\r$//' ../../scripts/wasm-opt-wrapper.sh > $WASM_OPT && chmod +x $WASM_OPT

# Install Pyodide 0.29.x npm package
pyodide-setup-0-29:
    npm install pyodide-0.29@npm:pyodide@0.29.3

# Build Pyodide 0.29.x wheel (requires Python 3.13 via pyenv)
pyodide-build-0-29: emsdk-setup-0-29
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-0.29 && \
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten EMSDK_NODE=$EMSDK/node/22.16.0_64bit/bin/node && \
    export RUSTUP_TOOLCHAIN=nightly RUSTFLAGS="-Zemscripten-wasm-eh" && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    pip install pyodide-build==0.29.3 "wheel<0.44" && \
    pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Build and test with Pyodide 0.29.x (requires Python 3.13 via pyenv)
pyodide-test-0-29: emsdk-setup-0-29 pyodide-setup-0-29
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    EMSDK=$PWD/build/emsdk-0.29 && \
    export PATH="$HOME/.pyenv/versions/3.13.12/bin:$HOME/.cargo/bin:$EMSDK/upstream/emscripten:$PATH" && \
    export EMSDK EM_CONFIG=$EMSDK/.emscripten EMSDK_NODE=$EMSDK/node/22.16.0_64bit/bin/node && \
    export RUSTUP_TOOLCHAIN=nightly RUSTFLAGS="-Zemscripten-wasm-eh" && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    rm -f dist/*pyodide_2025*.whl && \
    pip install pyodide-build==0.29.3 "wheel<0.44" && \
    pyodide build --exports whole_archive && \
    node scripts/run_pyodide_tests.mjs --dist-dir=./dist --pyodide-version=0.29 && \
    node scripts/run_pyodide_tests_idbfs.mjs --dist-dir=./dist --pyodide-version=0.29; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; exit $_rc

# Build CPython wheel, sdist, and Pyodide 0.27.x wheel
build-all: emsdk-setup
    #!/usr/bin/env bash
    _bak=/tmp/_libxrk_native_$$ && mkdir -p "$_bak" && \
    rm -rf dist/ && \
    source $HOME/.cargo/env && \
    uv build && \
    find src/libxrk -maxdepth 1 -name '*linux-gnu.so' -exec mv {} "$_bak/" \; && \
    export RUSTUP_TOOLCHAIN=nightly && \
    source ./build/emsdk/emsdk_env.sh && \
    export RUSTFLAGS="-C target-feature=-exception-handling -C panic=abort" && \
    export CARGO_BUILD_TARGET=wasm32-unknown-emscripten && \
    uv run pyodide build --exports whole_archive; \
    _rc=$?; mv "$_bak"/*.so src/libxrk/ 2>/dev/null; rm -rf "$_bak"; \
    [ $_rc -eq 0 ] && ls -la dist/; exit $_rc
