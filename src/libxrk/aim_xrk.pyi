# Copyright 2024, Scott Smith.  MIT License (see LICENSE).
"""Type stubs for aim_xrk Cython extension module."""

import os
from typing import Any, BinaryIO, Callable, Optional, Union

from libxrk.base import LogFile

def aim_xrk(
    fname: Union[str, bytes, bytearray, memoryview, BinaryIO, os.PathLike[str]],
    progress: Optional[Callable[[int, int], None]] = None,
) -> LogFile:
    """
    Read and parse an AIM XRK file.

    Args:
        fname: Path to the XRK file, or bytes/BytesIO containing file data.
               Accepts file paths, bytes, bytearray, memoryview, or file-like objects.
        progress: Optional progress callback function that receives (current, total) positions

    Returns:
        LogFile object containing channels, laps, and metadata
    """
    ...

def aim_track_dbg(
    fname: Union[str, bytes, bytearray, memoryview, BinaryIO, os.PathLike[str]],
) -> dict[str, Any]:
    """
    Read track information from an AIM XRK file for debugging purposes.

    Args:
        fname: Path to the XRK file, or bytes/BytesIO containing file data

    Returns:
        Dictionary of track messages and metadata
    """
    ...
