# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

"""
libxrk - Library for reading AIM XRK and XRZ motorsports telemetry files.

This library parses binary telemetry data from AIM data loggers and provides
the data as PyArrow tables for efficient analysis.

Quick Start:
    >>> from libxrk import aim_xrk
    >>> log = aim_xrk('path/to/file.xrk')  # or .xrz, bytes, BytesIO
    >>> df = log.get_channels_as_table().to_pandas()

Main Components:
    aim_xrk: Function to load XRK/XRZ files, returns a LogFile object
    LogFile: Dataclass containing channels, laps, and metadata
    GPS_CHANNEL_NAMES: List of standard GPS channel names

Backend Selection:
    Set LIBXRK_BACKEND=rust to use the Rust parser (preview).
    Default is Cython.
"""

import os as _os

_backend = _os.environ.get("LIBXRK_BACKEND", "").lower()

if _backend == "rust":
    # Import the Cython submodule FIRST, then rebind the name. `libxrk.aim_xrk`
    # is both a submodule and an exported function: whichever module imports
    # `libxrk.aim_xrk` first makes the import machinery set the submodule as an
    # attribute of the package, which shadows the function exported here. On the
    # Cython path the two are the same object, so nothing shows; on the Rust path
    # `from libxrk import aim_xrk` then yields a module and calling it raises
    # "TypeError: 'module' object is not callable" — depending on import order,
    # which makes it look like a random failure.
    from . import aim_xrk as _aim_xrk_cython_module  # noqa: F401
    from ._aim_xrk_rs import aim_xrk, aim_track_dbg
else:
    from .aim_xrk import aim_xrk, aim_track_dbg

from .base import ChannelMetadata, LogFile
from .gps import GPS_CHANNEL_NAMES

__all__ = [
    "aim_xrk",
    "aim_track_dbg",
    "ChannelMetadata",
    "LogFile",
    "GPS_CHANNEL_NAMES",
]
