#!/usr/bin/env bash
#
# Build the standalone libxrk WebAssembly module and write the JS-ready artifacts
# into crates/xrk-wasm/pkg/. This is the in-browser AiM .xrk/.xrz parser: it
# compiles libxrk's pure-Rust core (no Python, no Pyodide) to wasm32 via the thin
# wrapper crate in crates/xrk-wasm/.
#
# The wrapper depends on the in-tree libxrk core by path, so the wasm always
# tracks the parser in this repo — there is nothing to pin or bump.
#
# Requirements:
#   - rustup (stable), with the wasm32-unknown-unknown target
#   - wasm-bindgen 0.2.122 (auto-downloaded prebuilt if missing)
#   - wasm-opt (optional; from binaryen — shrinks the module further)
#
# Output (crates/xrk-wasm/pkg/, git-ignored):
#   xrk_wasm.js, xrk_wasm.d.ts, xrk_wasm_bg.wasm, xrk_wasm_bg.wasm.d.ts,
#   THIRD-PARTY-NOTICES.txt
#
# Usage:
#   just wasm-build        # or: scripts/build-xrk-wasm.sh
set -euo pipefail

WBG_VERSION="0.2.122"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE="$REPO_ROOT/crates/xrk-wasm"
OUT="$CRATE/pkg"

# Pick up a rustup-managed toolchain if cargo isn't already on PATH.
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"

echo "==> Ensure wasm32 target"
rustup target add wasm32-unknown-unknown

echo "==> Ensure wasm-bindgen $WBG_VERSION"
WBG="$(command -v wasm-bindgen || true)"
if [ -z "$WBG" ] || ! wasm-bindgen --version 2>/dev/null | grep -q "$WBG_VERSION"; then
  TMP="$(mktemp -d)"
  TARBALL="wasm-bindgen-${WBG_VERSION}-x86_64-unknown-linux-musl"
  curl -sSL -o "$TMP/wbg.tar.gz" \
    "https://github.com/rustwasm/wasm-bindgen/releases/download/${WBG_VERSION}/${TARBALL}.tar.gz"
  tar xzf "$TMP/wbg.tar.gz" -C "$TMP"
  WBG="$TMP/$TARBALL/wasm-bindgen"
fi
echo "    using $("$WBG" --version)"

echo "==> cargo build (release, wasm32)"
( cd "$CRATE" && cargo build --release --target wasm32-unknown-unknown )

echo "==> wasm-bindgen (--target web)"
mkdir -p "$OUT"
"$WBG" --target web --out-dir "$OUT" \
  "$CRATE/target/wasm32-unknown-unknown/release/xrk_wasm.wasm"

if command -v wasm-opt >/dev/null 2>&1; then
  echo "==> wasm-opt -Oz"
  # Rust emits these wasm features by default; wasm-opt must be told to allow them.
  wasm-opt -Oz \
    --enable-bulk-memory --enable-nontrapping-float-to-int \
    --enable-sign-ext --enable-mutable-globals --enable-reference-types \
    "$OUT/xrk_wasm_bg.wasm" -o "$OUT/xrk_wasm_bg.wasm"
else
  echo "==> wasm-opt not found — skipping (artifact still valid, just larger)"
fi

echo "==> Writing THIRD-PARTY-NOTICES.txt"
cat > "$OUT/THIRD-PARTY-NOTICES.txt" <<'NOTICES'
Third-party notices for the libxrk AiM XRK/XRZ parser (wasm)
============================================================

`xrk_wasm_bg.wasm` + `xrk_wasm.js` in this directory are a WebAssembly build of
libxrk's pure-Rust core, for parsing AiM .xrk/.xrz telemetry files entirely in
the browser. Built from source by scripts/build-xrk-wasm.sh via the wrapper crate
in crates/xrk-wasm/. Redistributed under the MIT License.

libxrk
------
  https://github.com/m3rlin45/libxrk
  Incorporates code from TrackDataAnalysis (https://github.com/racer-coder/TrackDataAnalysis).

MIT License

(For components copied from TrackDataAnalysis)
Copyright (c) 2024 Scott Smith

Copyright (c) 2025 Christopher Dewan


Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The wasm also statically links these MIT/Apache-2.0 Rust crates: binrw, flate2
(miniz_oxide), wasm-bindgen, serde, serde-wasm-bindgen.
NOTICES

echo "==> Done. Artifacts:"
ls -la "$OUT"
echo
echo "    gzipped wasm: $(gzip -9 -c "$OUT/xrk_wasm_bg.wasm" | wc -c | awk '{printf "%.0f KB", $1/1024}')"
