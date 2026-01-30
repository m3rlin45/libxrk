"""Setup script for compiling Cython extensions."""

import platform
from setuptools import setup, Extension
from Cython.Build import cythonize
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

setup(
    name="libxrk",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "embedsignature": True,
        },
        annotate=False,
    ),
)
