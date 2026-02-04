#!/usr/bin/env python
"""
Standalone script to extract lap times from official AIM DLL.
Designed to run in Wine Python (no external dependencies).

Usage (from WSL):
    WINEDEBUG=-all wine /tmp/python-embed/python.exe Z:/path/to/wine_compare.py Z:/path/to/file.xrk
"""

import ctypes
from ctypes import POINTER, byref, c_char_p, c_double, c_int
import sys
import os


def main():
    if len(sys.argv) < 2:
        print("Usage: wine_compare.py <xrk_file> [dll_path]")
        sys.exit(1)

    xrk_path = sys.argv[1]

    # Find DLL - default to same directory as script
    if len(sys.argv) >= 3:
        dll_path = sys.argv[2]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, "MatLabXRK-2017-64-ReleaseU.dll")

    print(f"XRK file: {xrk_path}")
    print(f"DLL path: {dll_path}")

    # Load DLL
    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        print(f"Failed to load DLL: {e}")
        sys.exit(1)

    # Setup function signatures (uses char* not wchar*)
    # Based on reference: https://github.com/laz-/xrk/blob/master/xrk.py
    dll.open_file.argtypes = [c_char_p]
    dll.open_file.restype = c_int

    dll.close_file_i.argtypes = [c_int]
    dll.close_file_i.restype = None

    dll.get_laps_count.argtypes = [c_int]
    dll.get_laps_count.restype = c_int

    dll.get_lap_info.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double)]
    dll.get_lap_info.restype = c_int

    # Open file (needs bytes path)
    xrk_path_bytes = os.path.abspath(xrk_path).encode("utf-8")
    idx = dll.open_file(xrk_path_bytes)
    if idx < 0:
        print(f"Failed to open file: {xrk_path}")
        sys.exit(1)

    print(f"File opened, index: {idx}")

    # Get lap count
    lap_count = dll.get_laps_count(idx)
    print(f"Lap count: {lap_count}")

    # Get lap info
    print("\n--- DLL Lap Times ---")
    print(f"{'Lap':>4}  {'Start (ms)':>12}  {'Duration (ms)':>14}  {'End (ms)':>12}")
    print("-" * 50)

    for lap in range(lap_count):
        start = c_double()
        duration = c_double()
        result = dll.get_lap_info(idx, lap, byref(start), byref(duration))
        if result == 0:
            print(f"Failed to get info for lap {lap}")
            continue

        start_ms = int(round(start.value * 1000))
        duration_ms = int(round(duration.value * 1000))
        end_ms = start_ms + duration_ms

        print(f"{lap:>4}  {start_ms:>12}  {duration_ms:>14}  {end_ms:>12}")

    # Close file
    dll.close_file_i(idx)
    print("\nFile closed.")


if __name__ == "__main__":
    main()
