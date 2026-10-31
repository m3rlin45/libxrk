"""Setup script for compiling the Cython and Rust extensions.

Three compiled modules go into the ``libxrk`` package:

- ``_aim_xrk_cy``: the Cython parser (setuptools/Cython);
- ``_aim_xrk_rs``: the Rust parser (cargo, crate ``libxrk-python``);
- ``_gps_rs``: the GPS pipeline shared by both parsers and the spec
  (cargo, crate ``libxrk-gps-python``).

``build_rust_extensions()`` is also what ``just rust-build`` runs, so there is
one definition of how the cargo outputs are named and where they go.
"""

import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext as _build_ext

import numpy as np

# C++17 for std::optional (MSVC defaults to C++14; GCC/Clang need the flag too).
if platform.system() == "Windows":
    extra_compile_args = ["/std:c++17"]
else:
    extra_compile_args = ["-std=c++17"]

extensions = [
    Extension(
        "libxrk._aim_xrk_cy",
        sources=["src/libxrk/_aim_xrk_cy.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
        extra_compile_args=extra_compile_args,
    )
]

# (cargo package, cdylib name) for each Rust extension module.
RUST_EXTENSIONS = [
    ("libxrk-python", "_aim_xrk_rs"),
    ("libxrk-gps-python", "_gps_rs"),
]


def _cargo_output(cargo_dir: Path, lib_name: str, is_emscripten: bool) -> Path:
    """Path of the cdylib cargo produced for ``lib_name`` (platform-specific name)."""
    if is_emscripten:
        candidate = cargo_dir / f"lib{lib_name}.wasm"
        return candidate if candidate.exists() else cargo_dir / f"{lib_name}.wasm"
    if platform.system() == "Windows":
        return cargo_dir / f"{lib_name}.dll"
    if platform.system() == "Darwin":
        return cargo_dir / f"lib{lib_name}.dylib"
    return cargo_dir / f"lib{lib_name}.so"


def build_rust_extensions(dest_dirs=(Path("src/libxrk"),), profile=None):
    """Build every Rust extension with cargo and copy each into ``dest_dirs``.

    Args:
        dest_dirs: Directories that receive ``<module><EXT_SUFFIX>``; the
            source tree by default (an in-place/editable build).
        profile: Cargo profile, ``release`` unless ``LIBXRK_CARGO_PROFILE``
            says otherwise (``debug`` compiles faster).

    Raises:
        FileNotFoundError: ``Cargo.toml`` or ``cargo`` is missing. Both
            backends are always built; there is no fallback.
    """
    if not Path("Cargo.toml").exists():
        raise FileNotFoundError("Cargo.toml not found: the Rust extensions cannot be built")
    if shutil.which("cargo") is None:
        raise FileNotFoundError("cargo not found: the Rust extensions cannot be built")

    profile = profile or os.environ.get("LIBXRK_CARGO_PROFILE", "release")
    env = os.environ.copy()
    build_platform = sysconfig.get_platform()
    is_emscripten = "emscripten" in build_platform or "wasm" in build_platform

    if is_emscripten:
        # Cross-compiling for Emscripten/Pyodide.
        env.setdefault("CARGO_BUILD_TARGET", "wasm32-unknown-emscripten")
    else:
        # Native build: tell PyO3 which Python to link against.
        env["PYO3_PYTHON"] = sys.executable

    cmd = ["cargo", "build", "--lib"]
    if profile == "release":
        cmd.append("--release")
    elif profile != "debug":
        cmd += ["--profile", profile]
    for package, _ in RUST_EXTENSIONS:
        cmd += ["-p", package]
    subprocess.run(cmd, check=True, env=env)

    # Find output (respects CARGO_BUILD_TARGET for cross-compilation)
    target = env.get("CARGO_BUILD_TARGET")
    cargo_dir = Path("target") / target / profile if target else Path("target") / profile
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"

    for _, lib_name in RUST_EXTENSIONS:
        cargo_lib = _cargo_output(cargo_dir, lib_name, is_emscripten)
        for dest in dest_dirs:
            dest = Path(dest)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cargo_lib, dest / f"{lib_name}{ext_suffix}")


class build_ext(_build_ext):
    """Custom build_ext that builds Cython extensions then Rust via cargo."""

    def run(self):
        super().run()
        # Into the build output, and into the source tree so setuptools
        # includes the modules in the wheel.
        build_rust_extensions([Path(self.build_lib) / "libxrk", Path("src/libxrk")])

    def copy_extensions_to_source(self):
        """Copy built extensions to the source tree for development."""
        super().copy_extensions_to_source()

        # Clean up .cpp files from source tree after build
        src_path = Path("src/libxrk")
        for cpp_file in src_path.glob("*.cpp"):
            print(f"Removing intermediate file: {cpp_file}")
            cpp_file.unlink()


if __name__ == "__main__":
    setup(
        name="libxrk",
        ext_modules=cythonize(
            extensions,
            compiler_directives={
                "language_level": "3",
                "embedsignature": True,
            },
            annotate=False,
            build_dir="build/cython",
        ),
        cmdclass={"build_ext": build_ext},
        zip_safe=False,
    )
