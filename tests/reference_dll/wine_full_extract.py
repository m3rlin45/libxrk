#!/usr/bin/env python
"""
Standalone script to extract all data from official AIM DLL as JSON.
Designed to run in Wine Python (no external dependencies).

Usage (from WSL):
    WINEDEBUG=-all wine /tmp/python-embed/python.exe Z:/path/to/wine_full_extract.py Z:/path/to/file.xrk

Output is JSON to stdout containing:
- channels: list of {name, units, sample_count, samples_hash, first_samples, last_samples}
- laps: list of {lap_num, start_ms, duration_ms, end_ms}
- metadata: {vehicle, track, racer, championship, venue_type}
"""

import ctypes
from ctypes import POINTER, byref, c_char_p, c_double, c_int
import hashlib
import json
import os
import struct
import sys


def compute_samples_hash(times, values):
    """Compute hash of sample data for integrity checking."""
    # Pack as binary for consistent hashing
    h = hashlib.sha256()
    for t, v in zip(times, values):
        h.update(struct.pack("<dd", t, v))
    return h.hexdigest()[:16]  # Truncate for readability


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: wine_full_extract.py <xrk_file> [dll_path]"}))
        sys.exit(1)

    xrk_path = sys.argv[1]

    # Find DLL - default to same directory as script
    if len(sys.argv) >= 3:
        dll_path = sys.argv[2]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, "MatLabXRK-2017-64-ReleaseU.dll")

    # Load DLL
    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        print(json.dumps({"error": f"Failed to load DLL: {e}"}))
        sys.exit(1)

    # Setup function signatures
    dll.open_file.argtypes = [c_char_p]
    dll.open_file.restype = c_int

    dll.close_file_i.argtypes = [c_int]
    dll.close_file_i.restype = None

    dll.get_laps_count.argtypes = [c_int]
    dll.get_laps_count.restype = c_int

    dll.get_lap_info.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double)]
    dll.get_lap_info.restype = c_int

    dll.get_channels_count.argtypes = [c_int]
    dll.get_channels_count.restype = c_int

    dll.get_channel_name.argtypes = [c_int, c_int]
    dll.get_channel_name.restype = c_char_p

    dll.get_channel_units.argtypes = [c_int, c_int]
    dll.get_channel_units.restype = c_char_p

    dll.get_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_channel_samples_count.restype = c_int

    dll.get_channel_samples.argtypes = [
        c_int,
        c_int,
        POINTER(c_double),
        POINTER(c_double),
        c_int,
    ]
    dll.get_channel_samples.restype = c_int

    dll.get_vehicle_name.argtypes = [c_int]
    dll.get_vehicle_name.restype = c_char_p

    dll.get_track_name.argtypes = [c_int]
    dll.get_track_name.restype = c_char_p

    dll.get_racer_name.argtypes = [c_int]
    dll.get_racer_name.restype = c_char_p

    dll.get_championship_name.argtypes = [c_int]
    dll.get_championship_name.restype = c_char_p

    dll.get_venue_type_name.argtypes = [c_int]
    dll.get_venue_type_name.restype = c_char_p

    # Open file
    xrk_path_bytes = os.path.abspath(xrk_path).encode("utf-8")
    idx = dll.open_file(xrk_path_bytes)
    if idx < 0:
        print(json.dumps({"error": f"Failed to open file: {xrk_path}"}))
        sys.exit(1)

    result = {
        "file": xrk_path,
        "channels": [],
        "laps": [],
        "metadata": {},
    }

    try:
        # Extract channels
        channel_count = dll.get_channels_count(idx)
        for ch in range(channel_count):
            name_bytes = dll.get_channel_name(idx, ch)
            name = name_bytes.decode("latin-1") if name_bytes else ""

            units_bytes = dll.get_channel_units(idx, ch)
            units = units_bytes.decode("latin-1") if units_bytes else ""

            sample_count = dll.get_channel_samples_count(idx, ch)

            channel_data = {
                "name": name,
                "units": units,
                "sample_count": sample_count,
            }

            # Get samples if available
            if sample_count > 0:
                times = (c_double * sample_count)()
                values = (c_double * sample_count)()
                result_code = dll.get_channel_samples(idx, ch, times, values, sample_count)

                if result_code != 0:
                    times_list = list(times)
                    values_list = list(values)

                    # Compute hash
                    channel_data["samples_hash"] = compute_samples_hash(times_list, values_list)

                    # First/last 5 samples for spot checking
                    n_spot = min(5, sample_count)
                    channel_data["first_samples"] = {
                        "times": times_list[:n_spot],
                        "values": values_list[:n_spot],
                    }
                    channel_data["last_samples"] = {
                        "times": times_list[-n_spot:],
                        "values": values_list[-n_spot:],
                    }
                else:
                    channel_data["error"] = "Failed to get samples"

            result["channels"].append(channel_data)

        # Extract laps
        lap_count = dll.get_laps_count(idx)
        for lap in range(lap_count):
            start = c_double()
            duration = c_double()
            lap_result = dll.get_lap_info(idx, lap, byref(start), byref(duration))

            if lap_result != 0:
                start_ms = int(round(start.value * 1000))
                duration_ms = int(round(duration.value * 1000))
                end_ms = start_ms + duration_ms

                result["laps"].append(
                    {
                        "lap_num": lap,
                        "start_ms": start_ms,
                        "duration_ms": duration_ms,
                        "end_ms": end_ms,
                    }
                )

        # Extract metadata
        vehicle = dll.get_vehicle_name(idx)
        result["metadata"]["vehicle"] = vehicle.decode("latin-1") if vehicle else ""

        track = dll.get_track_name(idx)
        result["metadata"]["track"] = track.decode("latin-1") if track else ""

        racer = dll.get_racer_name(idx)
        result["metadata"]["racer"] = racer.decode("latin-1") if racer else ""

        championship = dll.get_championship_name(idx)
        result["metadata"]["championship"] = championship.decode("latin-1") if championship else ""

        venue_type = dll.get_venue_type_name(idx)
        result["metadata"]["venue_type"] = venue_type.decode("latin-1") if venue_type else ""

    finally:
        dll.close_file_i(idx)

    # Output JSON
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
