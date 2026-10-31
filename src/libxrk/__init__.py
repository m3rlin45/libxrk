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

    The two parsers are the private modules ``libxrk._aim_xrk_cy`` and
    ``libxrk._aim_xrk_rs``; only the selected one is imported. The public
    name ``libxrk.aim_xrk`` is always the function, never a module: if a
    submodule shared that name, its first import would rebind the package
    attribute to the module object (Python binds a submodule to its parent on
    first load), silently breaking ``from libxrk import aim_xrk`` for every
    later importer in the process.
"""

import os as _os

_backend = _os.environ.get("LIBXRK_BACKEND", "").lower()

if _backend == "rust":
    from ._aim_xrk_rs import aim_xrk, aim_track_dbg
else:
    from ._aim_xrk_cy import aim_xrk, aim_track_dbg

from .base import ChannelMetadata, LogFile
from .gps import GPS_CHANNEL_NAMES

__all__ = [
    "aim_xrk",
    "aim_track_dbg",
    "ChannelMetadata",
    "LogFile",
    "GPS_CHANNEL_NAMES",
]
