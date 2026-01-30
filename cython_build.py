"""Build script for compiling Cython extensions."""

import os
import platform
import shutil
from pathlib import Path
from setuptools import Extension
from setuptools.command.build_ext import build_ext as _build_ext
from Cython.Build import cythonize
import numpy as np


class build_ext(_build_ext):
    """Custom build_ext to handle build directory and file placement."""

    def run(self):
        # Set build directory to build/lib instead of in-place
        if not self.inplace:
            # Standard build - put intermediate files in build/
            pass
        super().run()

    def copy_extensions_to_source(self):
        """Copy built extensions to the source tree for development."""
        super().copy_extensions_to_source()

        # Clean up .cpp files from source tree after build
        src_path = Path("src/libxrk")
        for cpp_file in src_path.glob("*.cpp"):
            print(f"Removing intermediate file: {cpp_file}")
            cpp_file.unlink()


def build(setup_kwargs):
    """
    This function is mandatory in order to build the extensions.
    """
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

    ext_modules = cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "embedsignature": True,
        },
        annotate=False,
        build_dir="build/cython",  # Put Cython-generated C++ files in build/
    )

    setup_kwargs.update(
        {
            "ext_modules": ext_modules,
            "cmdclass": {"build_ext": build_ext},
            "zip_safe": False,
        }
    )


if __name__ == "__main__":
    # Build extensions in-place for development
    import sys
    from setuptools import setup
    from typing import Any

    setup_kwargs: dict[str, Any] = {}
    build(setup_kwargs)

    # Configure to build in-place
    sys.argv = ["cython_build.py", "build_ext", "--inplace"]
    setup(**setup_kwargs)
