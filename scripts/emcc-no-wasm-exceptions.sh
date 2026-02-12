#!/bin/bash
# Wrapper for emcc that strips -fwasm-exceptions for Pyodide 0.27 compatibility.
#
# Rust nightly passes -fwasm-exceptions to emcc for wasm32-unknown-emscripten,
# but Pyodide 0.27 doesn't support the __cpp_exception WebAssembly tag.
# Stripping the flag makes emcc use JS-based exception handling instead.

if [ -n "$EMSDK" ]; then
    EMCC_PY="$EMSDK/upstream/emscripten/emcc.py"
else
    echo "ERROR: EMSDK environment variable not set" >&2
    exit 1
fi

if [ ! -f "$EMCC_PY" ]; then
    echo "ERROR: emcc.py not found at $EMCC_PY" >&2
    exit 1
fi

args=()
for arg in "$@"; do
    case "$arg" in
        -fwasm-exceptions)
            ;; # strip
        *)
            args+=("$arg")
            ;;
    esac
done

unset _PYTHON_SYSCONFIGDATA_NAME
if [ -z "$PYTHON" ]; then PYTHON=$EMSDK_PYTHON; fi
if [ -z "$PYTHON" ]; then PYTHON=$(command -v python3 2>/dev/null); fi
if [ -z "$PYTHON" ]; then PYTHON=$(command -v python 2>/dev/null); fi
exec "$PYTHON" -E "$EMCC_PY" "${args[@]}"
