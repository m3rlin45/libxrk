# Copyright 2024, Scott Smith.  MIT License (see LICENSE).
"""Type stubs for aim_xrk Cython extension module."""

from typing import Any, Callable, Optional
from libxrk.data.base import LogFile

def AIMXRK(fname: str, progress: Optional[Callable[[int, int], None]]) -> LogFile:
    """
    Read and parse an AIM XRK file.

    Args:
        fname: Path to the XRK file to read
        progress: Optional progress callback function that receives (current, total) positions

    Returns:
        LogFile object containing channels, laps, and metadata
    """
    ...

def aim_track(fname: str) -> dict[str, Any]:
    """
    Read track information from an AIM XRK file.

    Args:
        fname: Path to the XRK file to read

    Returns:
        Dictionary of track messages and metadata
    """
    ...
