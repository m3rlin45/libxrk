"""Setup script for compiling Cython and Rust extensions."""

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

# MSVC (Windows) defaults to C++14, GCC/Clang need explicit flag
if platform.system() == "Windows":
    extra_compile_args = []
else:
    extra_compile_args = ["-std=c++11"]

extensions = [
    Extension(
        "libxrk.aim_xrk",
        sources=["src/libxrk/aim_xrk.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
        extra_compile_args=extra_compile_args,
    )
]


class build_ext(_build_ext):
    """Custom build_ext that builds Cython extensions then Rust via cargo."""

    def run(self):
        super().run()
        self._build_rust()

    def _build_rust(self):
        """Build Rust extension via cargo. Skips if Cargo.toml or cargo unavailable."""
        if not Path("Cargo.toml").exists():
            print("WARNING: Cargo.toml not found, skipping Rust extension build")
            return

        if shutil.which("cargo") is None:
            print("WARNING: cargo not found, skipping Rust extension build")
            return

        env = os.environ.copy()
        build_platform = sysconfig.get_platform()
        is_emscripten = "emscripten" in build_platform or "wasm" in build_platform

        if is_emscripten:
            # Cross-compiling for Emscripten/Pyodide.
            env.setdefault("CARGO_BUILD_TARGET", "wasm32-unknown-emscripten")
        else:
            # Native build: tell PyO3 which Python to link against.
            env["PYO3_PYTHON"] = sys.executable

        subprocess.run(["cargo", "build", "--release", "--lib"], check=True, env=env)

        # Find output (respects CARGO_BUILD_TARGET for cross-compilation)
        target = env.get("CARGO_BUILD_TARGET")
        cargo_dir = Path(f"target/{target}/release") if target else Path("target/release")

        # Platform-specific cargo output name
        if is_emscripten:
            cargo_lib = cargo_dir / "lib_aim_xrk_rs.wasm"
            if not cargo_lib.exists():
                cargo_lib = cargo_dir / "_aim_xrk_rs.wasm"
        elif platform.system() == "Windows":
            cargo_lib = cargo_dir / "_aim_xrk_rs.dll"
        elif platform.system() == "Darwin":
            cargo_lib = cargo_dir / "lib_aim_xrk_rs.dylib"
        else:
            cargo_lib = cargo_dir / "lib_aim_xrk_rs.so"

        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"

        # Copy to build output
        dest_dir = Path(self.build_lib) / "libxrk"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cargo_lib, dest_dir / f"_aim_xrk_rs{ext_suffix}")

        # Copy to source tree so setuptools includes it in the wheel
        shutil.copy2(cargo_lib, Path("src/libxrk") / f"_aim_xrk_rs{ext_suffix}")

    def copy_extensions_to_source(self):
        """Copy built extensions to the source tree for development."""
        super().copy_extensions_to_source()

        # Clean up .cpp files from source tree after build
        src_path = Path("src/libxrk")
        for cpp_file in src_path.glob("*.cpp"):
            print(f"Removing intermediate file: {cpp_file}")
            cpp_file.unlink()


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
