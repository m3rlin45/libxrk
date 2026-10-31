# Copyright 2024, Scott Smith.  MIT License (see LICENSE).
"""Type stubs for _aim_xrk_rs Rust extension module."""

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

# --- GPS processing (crate crates/libxrk-gps), shared with the Cython backend ---

GPS_CHANNEL_NAMES: tuple[str, ...]

LapRow = tuple[int, int, int, str]

def process_gps(
    gps_data: bytes,
    gnfi_data: bytes,
    time_offset: int,
    non_gps_max_end_time: Optional[int],
    start_finish: Optional[tuple[float, float]],
    laps: list[LapRow],
    expected_dt_ms: float = 40.0,
) -> tuple[dict[str, Any], Any]:
    """Run the GPS pipeline on a parser's raw material.

    Decodes the 56-byte GPS records into the 14 GPS channels, applies the
    65533 ms timing fix (GNFI reference clock, else a direct 60-70 s gap,
    else GPS outliving the non-GPS channels, whose last timecode is
    ``non_gps_max_end_time``) and finishes the lap table: the last lap is
    typed "in" when the car is more than 30 m from ``start_finish`` at its
    end; with no laps, they are detected from the GPS track.

    Returns:
        ``({name: pyarrow.Table}, laps pyarrow.Table)`` with the laps table
        in the LogFile layout (num, start_time, end_time, lap_type).

    Raises:
        ValueError: ``gps_data`` is not a whole number of 56-byte records.
    """
    ...

def decode_gps(gps_data: bytes, time_offset: int) -> dict[str, Any]:
    """The 14 GPS channels as ``{name: pyarrow.Table}``, without the timing fix."""
    ...

def reconstruct_gps_timecodes(timecodes: list[int]) -> list[int]:
    """Rebuild GPS timecodes whose upper 16 bits are unreliable (companion.md section 6)."""
    ...

def detect_gps_gap_corrections(
    gps_timecodes: list[int],
    gnfi_timecodes: list[int],
    non_gps_max_end_time: Optional[int] = None,
    expected_dt_ms: float = 40.0,
) -> list[tuple[int, int]]:
    """Detect the 65533 ms GPS timing bug; returns (gap_time, correction) pairs."""
    ...
