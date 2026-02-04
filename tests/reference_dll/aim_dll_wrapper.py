"""
Python wrapper for the official AIM MatLabXRK DLL.

This module provides a Python interface to the official AIM DLL for reading
XRK/XRZ telemetry files. Used as a reference implementation to verify
libxrk produces identical results.

The DLL is Windows-only. On Linux, use Wine with Windows Python.

Usage:
    from aim_dll_wrapper import AimDll

    with AimDll() as dll:
        idx = dll.open_file("path/to/file.xrk")
        lap_count = dll.get_laps_count(idx)
        for lap in range(lap_count):
            start, duration = dll.get_lap_info(idx, lap)
            print(f"Lap {lap}: {start:.3f}s - {duration:.3f}s")
        dll.close_file(idx)
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_wchar_p, create_string_buffer
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Default DLL filename
DEFAULT_DLL_NAME = "MatLabXRK-2017-64-ReleaseU.dll"


class AimDllError(Exception):
    """Error from AIM DLL operations."""

    pass


class AimDll:
    """
    Wrapper for the official AIM MatLabXRK DLL.

    This class provides a Pythonic interface to the C DLL functions.
    Use as a context manager for automatic cleanup.

    Args:
        dll_path: Path to the DLL file. If not specified, looks for the default
            DLL name in the same directory as this module.

    Example:
        >>> with AimDll() as dll:
        ...     idx = dll.open_file("test.xrk")
        ...     print(dll.get_laps_count(idx))
        ...     dll.close_file(idx)
    """

    def __init__(self, dll_path: str | Path | None = None):
        if dll_path is None:
            # Look in same directory as this module
            dll_path = Path(__file__).parent / DEFAULT_DLL_NAME

        dll_path = Path(dll_path)
        if not dll_path.exists():
            raise FileNotFoundError(
                f"DLL not found: {dll_path}\n"
                f"Download from: https://www.aim-sportline.com/aim-software-betas/DLL/"
            )

        self._dll = ctypes.CDLL(str(dll_path))
        self._setup_functions()
        self._open_files: list[int] = []

    def _setup_functions(self) -> None:
        """Configure ctypes function signatures."""
        # open_file_w(const wchar_t* path) -> int
        # Returns file index, or -1 on error
        self._dll.open_file_w.argtypes = [c_wchar_p]
        self._dll.open_file_w.restype = c_int

        # close_file_i(int idx)
        self._dll.close_file_i.argtypes = [c_int]
        self._dll.close_file_i.restype = None

        # get_laps_count(int idx) -> int
        self._dll.get_laps_count.argtypes = [c_int]
        self._dll.get_laps_count.restype = c_int

        # get_lap_info(int idx, int lap, double* start, double* duration) -> int
        # Returns 1 on success, 0 on failure
        self._dll.get_lap_info.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double)]
        self._dll.get_lap_info.restype = c_int

        # get_channels_count(int idx) -> int
        self._dll.get_channels_count.argtypes = [c_int]
        self._dll.get_channels_count.restype = c_int

        # get_channel_name(int idx, int ch) -> const char*
        self._dll.get_channel_name.argtypes = [c_int, c_int]
        self._dll.get_channel_name.restype = c_char_p

        # get_channel_units(int idx, int ch) -> const char*
        self._dll.get_channel_units.argtypes = [c_int, c_int]
        self._dll.get_channel_units.restype = c_char_p

        # get_channel_samples_count(int idx, int ch) -> int
        self._dll.get_channel_samples_count.argtypes = [c_int, c_int]
        self._dll.get_channel_samples_count.restype = c_int

        # get_channel_samples(int idx, int ch, double* times, double* values, int count) -> int
        self._dll.get_channel_samples.argtypes = [
            c_int,
            c_int,
            POINTER(c_double),
            POINTER(c_double),
            c_int,
        ]
        self._dll.get_channel_samples.restype = c_int

        # get_vehicle_name(int idx) -> const char*
        self._dll.get_vehicle_name.argtypes = [c_int]
        self._dll.get_vehicle_name.restype = c_char_p

        # get_track_name(int idx) -> const char*
        self._dll.get_track_name.argtypes = [c_int]
        self._dll.get_track_name.restype = c_char_p

        # get_racer_name(int idx) -> const char*
        self._dll.get_racer_name.argtypes = [c_int]
        self._dll.get_racer_name.restype = c_char_p

        # get_championship_name(int idx) -> const char*
        self._dll.get_championship_name.argtypes = [c_int]
        self._dll.get_championship_name.restype = c_char_p

        # get_venue_type_name(int idx) -> const char*
        self._dll.get_venue_type_name.argtypes = [c_int]
        self._dll.get_venue_type_name.restype = c_char_p

    def __enter__(self) -> "AimDll":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close_all()

    def close_all(self) -> None:
        """Close all open files."""
        for idx in self._open_files:
            try:
                self._dll.close_file_i(idx)
            except Exception:
                pass
        self._open_files.clear()

    def open_file(self, path: str | Path) -> int:
        """
        Open an XRK/XRZ file.

        Args:
            path: Path to the XRK/XRZ file.

        Returns:
            File index for use with other methods.

        Raises:
            AimDllError: If the file cannot be opened.
        """
        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        idx = self._dll.open_file_w(str(path))
        if idx < 0:
            raise AimDllError(f"Failed to open file: {path}")

        self._open_files.append(idx)
        return int(idx)

    def close_file(self, idx: int) -> None:
        """
        Close an open file.

        Args:
            idx: File index from open_file().
        """
        self._dll.close_file_i(idx)
        if idx in self._open_files:
            self._open_files.remove(idx)

    def get_laps_count(self, idx: int) -> int:
        """
        Get the number of laps in the file.

        Args:
            idx: File index from open_file().

        Returns:
            Number of laps (0 if no lap data).
        """
        return int(self._dll.get_laps_count(idx))

    def get_lap_info(self, idx: int, lap: int) -> tuple[float, float]:
        """
        Get lap timing information.

        Args:
            idx: File index from open_file().
            lap: Lap index (0-based).

        Returns:
            Tuple of (start_time_seconds, duration_seconds).

        Raises:
            AimDllError: If lap info cannot be retrieved.
        """
        start = c_double()
        duration = c_double()
        result = self._dll.get_lap_info(idx, lap, byref(start), byref(duration))
        if result == 0:
            raise AimDllError(f"Failed to get lap info for lap {lap}")
        return (start.value, duration.value)

    def get_all_laps(self, idx: int) -> list[tuple[float, float]]:
        """
        Get all lap timing information.

        Args:
            idx: File index from open_file().

        Returns:
            List of (start_time_seconds, duration_seconds) tuples.
        """
        lap_count = self.get_laps_count(idx)
        return [self.get_lap_info(idx, lap) for lap in range(lap_count)]

    def get_channels_count(self, idx: int) -> int:
        """
        Get the number of channels in the file.

        Args:
            idx: File index from open_file().

        Returns:
            Number of channels.
        """
        return int(self._dll.get_channels_count(idx))

    def get_channel_name(self, idx: int, channel: int) -> str:
        """
        Get the name of a channel.

        Args:
            idx: File index from open_file().
            channel: Channel index (0-based).

        Returns:
            Channel name.
        """
        name = self._dll.get_channel_name(idx, channel)
        return name.decode("utf-8") if name else ""

    def get_channel_units(self, idx: int, channel: int) -> str:
        """
        Get the units of a channel.

        Args:
            idx: File index from open_file().
            channel: Channel index (0-based).

        Returns:
            Channel units.
        """
        units = self._dll.get_channel_units(idx, channel)
        return units.decode("utf-8") if units else ""

    def get_channel_samples_count(self, idx: int, channel: int) -> int:
        """
        Get the number of samples in a channel.

        Args:
            idx: File index from open_file().
            channel: Channel index (0-based).

        Returns:
            Number of samples.
        """
        return int(self._dll.get_channel_samples_count(idx, channel))

    def get_channel_samples(self, idx: int, channel: int) -> tuple[list[float], list[float]]:
        """
        Get all samples from a channel.

        Args:
            idx: File index from open_file().
            channel: Channel index (0-based).

        Returns:
            Tuple of (times_list, values_list).

        Raises:
            AimDllError: If samples cannot be retrieved.
        """
        count = self.get_channel_samples_count(idx, channel)
        if count <= 0:
            return ([], [])

        times = (c_double * count)()
        values = (c_double * count)()
        result = self._dll.get_channel_samples(idx, channel, times, values, count)
        if result == 0:
            raise AimDllError(f"Failed to get samples for channel {channel}")

        return (list(times), list(values))

    def get_vehicle_name(self, idx: int) -> str:
        """Get vehicle name."""
        name = self._dll.get_vehicle_name(idx)
        return name.decode("utf-8") if name else ""

    def get_track_name(self, idx: int) -> str:
        """Get track name."""
        name = self._dll.get_track_name(idx)
        return name.decode("utf-8") if name else ""

    def get_racer_name(self, idx: int) -> str:
        """Get racer name."""
        name = self._dll.get_racer_name(idx)
        return name.decode("utf-8") if name else ""

    def get_championship_name(self, idx: int) -> str:
        """Get championship name."""
        name = self._dll.get_championship_name(idx)
        return name.decode("utf-8") if name else ""

    def get_venue_type_name(self, idx: int) -> str:
        """Get venue type name."""
        name = self._dll.get_venue_type_name(idx)
        return name.decode("utf-8") if name else ""
