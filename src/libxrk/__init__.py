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
"""

from .aim_xrk import aim_xrk, aim_track_dbg
from .base import LogFile
from .gps import GPS_CHANNEL_NAMES

__all__ = [
    "aim_xrk",
    "aim_track_dbg",
    "LogFile",
    "GPS_CHANNEL_NAMES",
]
