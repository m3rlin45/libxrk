"""Build script for compiling Cython extensions."""
import os
from pathlib import Path
from setuptools import Extension
from Cython.Build import cythonize
import numpy as np


def build(setup_kwargs):
    """
    This function is mandatory in order to build the extensions.
    """
    extensions = [
        Extension(
            "libxrk.data.aim_xrk",
            sources=["src/libxrk/data/aim_xrk.pyx"],
            include_dirs=[np.get_include()],
            language="c++",
            extra_compile_args=["-std=c++11"],
        )
    ]
    
    setup_kwargs.update({
        "ext_modules": cythonize(
            extensions,
            compiler_directives={
                "language_level": "3",
                "embedsignature": True,
            },
            annotate=False,
        ),
        "zip_safe": False,
    })
