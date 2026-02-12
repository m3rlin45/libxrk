#!/usr/bin/env python3
"""Repackage a maturin-built emscripten wheel with the pyodide platform tag.

Usage: python scripts/repackage_pyodide_wheel.py <emscripten_tag> <pyodide_tag>

Example:
    python scripts/repackage_pyodide_wheel.py emscripten_3_1_58_wasm32 pyodide_2024_0_wasm32
    python scripts/repackage_pyodide_wheel.py emscripten_4_0_9_wasm32 pyodide_2025_0_wasm32
"""

import glob
import os
import sys
import zipfile


def repackage(src_tag: str, dst_tag: str) -> str:
    pattern = f"target/wheels/*-{src_tag}.whl"
    wheels = glob.glob(pattern)
    if not wheels:
        print(f"ERROR: No wheel matching {pattern}", file=sys.stderr)
        sys.exit(1)
    src = wheels[0]
    dst_name = os.path.basename(src).replace(src_tag, dst_tag)
    os.makedirs("dist", exist_ok=True)
    dst = os.path.join("dist", dst_name)

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("WHEEL") or item.filename.endswith("RECORD"):
                data = data.replace(src_tag.encode(), dst_tag.encode())
            zout.writestr(item, data)

    print(dst)
    return dst


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    repackage(sys.argv[1], sys.argv[2])
