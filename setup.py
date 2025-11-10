"""Setup script for compiling Cython extensions."""

from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

extensions = [
    Extension(
        "libxrk.data.aim_xrk",
        sources=["src/libxrk/data/aim_xrk.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
        extra_compile_args=["-std=c++11"],
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
