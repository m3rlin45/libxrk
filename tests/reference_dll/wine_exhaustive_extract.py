#!/usr/bin/env python
"""
Standalone script to extract ALL data from official AIM DLL as binary.
Designed to run in Wine Python (no external dependencies).

Extracts every sample from:
- Regular channels (get_channels_count / get_channel_samples)
- GPS derived channels (get_GPS_channels_count / get_GPS_channel_samples)
- GPS raw channels (get_GPS_raw_channels_count / get_GPS_raw_channel_samples)
- Laps (get_laps_count / get_lap_info)
- Metadata (vehicle, track, racer, championship, venue_type)

Binary format (all integers are little-endian uint32, floats are little-endian float64):
    Header:
        4 bytes: magic b"XDLL"
        4 bytes: version = 1

    3x Channel sections (regular, GPS derived, GPS raw):
        4 bytes: num_channels
        Per channel:
            4 bytes: name_len
            name_len bytes: name (utf-8)
            4 bytes: units_len
            units_len bytes: units (utf-8)
            4 bytes: sample_count
            sample_count * 8 bytes: times (float64, seconds)
            sample_count * 8 bytes: values (float64)

    Laps section:
        4 bytes: num_laps
        Per lap:
            8 bytes: start_seconds (float64)
            8 bytes: duration_seconds (float64)

    Metadata section:
        4 bytes: num_fields
        Per field:
            4 bytes: key_len
            key_len bytes: key (utf-8)
            4 bytes: val_len
            val_len bytes: value (utf-8)

Usage (from WSL):
    WINEDEBUG=-all /usr/lib/wine/wine64 .setup/python-embed/python.exe \\
        Z:/path/to/wine_exhaustive_extract.py \\
        Z:/path/to/file.xrk \\
        Z:/path/to/output.bin \\
        [Z:/path/to/dll]
"""

import ctypes
from ctypes import POINTER, byref, c_char_p, c_double, c_int
import os
import struct
import sys


MAGIC = b"XDLL"
VERSION = 1


def write_str(f, s):
    """Write a length-prefixed utf-8 string."""
    b = s.encode("utf-8")
    f.write(struct.pack("<I", len(b)))
    f.write(b)


def write_channel_section(f, dll, idx, count_fn, name_fn, units_fn, samples_count_fn, samples_fn):
    """Write a full channel section (regular, GPS derived, or GPS raw)."""
    num_channels = count_fn(idx)
    f.write(struct.pack("<I", num_channels))

    for ch in range(num_channels):
        # Name
        name_bytes = name_fn(idx, ch)
        name = name_bytes.decode("latin-1") if name_bytes else ""

        # Units
        units_bytes = units_fn(idx, ch)
        units = units_bytes.decode("latin-1") if units_bytes else ""

        # Sample count
        sample_count = samples_count_fn(idx, ch)

        write_str(f, name)
        write_str(f, units)
        f.write(struct.pack("<I", sample_count))

        if sample_count > 0:
            times = (c_double * sample_count)()
            values = (c_double * sample_count)()
            result = samples_fn(idx, ch, times, values, sample_count)

            if result != 0:
                # Write times as raw float64 bytes
                f.write(bytes(times))
                # Write values as raw float64 bytes
                f.write(bytes(values))
            else:
                # Failed to get samples - write zeros
                f.write(b"\x00" * (sample_count * 8))
                f.write(b"\x00" * (sample_count * 8))

        sys.stderr.write(".")
        sys.stderr.flush()


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("Usage: wine_exhaustive_extract.py <xrk_file> <output.bin> [dll_path]\n")
        sys.exit(1)

    xrk_path = sys.argv[1]
    output_path = sys.argv[2]

    # Find DLL
    if len(sys.argv) >= 4:
        dll_path = sys.argv[3]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, "MatLabXRK-2017-64-ReleaseU.dll")

    # Load DLL
    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        sys.stderr.write("Failed to load DLL: %s\n" % e)
        sys.exit(1)

    # Setup function signatures
    # Regular channels
    dll.open_file.argtypes = [c_char_p]
    dll.open_file.restype = c_int
    dll.close_file_i.argtypes = [c_int]
    dll.close_file_i.restype = None
    dll.get_channels_count.argtypes = [c_int]
    dll.get_channels_count.restype = c_int
    dll.get_channel_name.argtypes = [c_int, c_int]
    dll.get_channel_name.restype = c_char_p
    dll.get_channel_units.argtypes = [c_int, c_int]
    dll.get_channel_units.restype = c_char_p
    dll.get_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_channel_samples_count.restype = c_int
    dll.get_channel_samples.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double), c_int]
    dll.get_channel_samples.restype = c_int

    # GPS derived channels
    dll.get_GPS_channels_count.argtypes = [c_int]
    dll.get_GPS_channels_count.restype = c_int
    dll.get_GPS_channel_name.argtypes = [c_int, c_int]
    dll.get_GPS_channel_name.restype = c_char_p
    dll.get_GPS_channel_units.argtypes = [c_int, c_int]
    dll.get_GPS_channel_units.restype = c_char_p
    dll.get_GPS_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_GPS_channel_samples_count.restype = c_int
    dll.get_GPS_channel_samples.argtypes = [
        c_int,
        c_int,
        POINTER(c_double),
        POINTER(c_double),
        c_int,
    ]
    dll.get_GPS_channel_samples.restype = c_int

    # GPS raw channels
    dll.get_GPS_raw_channels_count.argtypes = [c_int]
    dll.get_GPS_raw_channels_count.restype = c_int
    dll.get_GPS_raw_channel_name.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_name.restype = c_char_p
    dll.get_GPS_raw_channel_units.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_units.restype = c_char_p
    dll.get_GPS_raw_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_samples_count.restype = c_int
    dll.get_GPS_raw_channel_samples.argtypes = [
        c_int,
        c_int,
        POINTER(c_double),
        POINTER(c_double),
        c_int,
    ]
    dll.get_GPS_raw_channel_samples.restype = c_int

    # Laps
    dll.get_laps_count.argtypes = [c_int]
    dll.get_laps_count.restype = c_int
    dll.get_lap_info.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double)]
    dll.get_lap_info.restype = c_int

    # Metadata
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

    # Open XRK file
    xrk_path_bytes = os.path.abspath(xrk_path).encode("utf-8")
    idx = dll.open_file(xrk_path_bytes)
    if idx < 0:
        sys.stderr.write("Failed to open file: %s\n" % xrk_path)
        sys.exit(1)

    sys.stderr.write("Extracting: %s\n" % xrk_path)

    try:
        with open(output_path, "wb") as f:
            # Header
            f.write(MAGIC)
            f.write(struct.pack("<I", VERSION))

            # Section 1: Regular channels
            sys.stderr.write("Regular channels")
            write_channel_section(
                f,
                dll,
                idx,
                dll.get_channels_count,
                dll.get_channel_name,
                dll.get_channel_units,
                dll.get_channel_samples_count,
                dll.get_channel_samples,
            )
            sys.stderr.write(" done\n")

            # Section 2: GPS derived channels
            sys.stderr.write("GPS derived channels")
            write_channel_section(
                f,
                dll,
                idx,
                dll.get_GPS_channels_count,
                dll.get_GPS_channel_name,
                dll.get_GPS_channel_units,
                dll.get_GPS_channel_samples_count,
                dll.get_GPS_channel_samples,
            )
            sys.stderr.write(" done\n")

            # Section 3: GPS raw channels
            sys.stderr.write("GPS raw channels")
            write_channel_section(
                f,
                dll,
                idx,
                dll.get_GPS_raw_channels_count,
                dll.get_GPS_raw_channel_name,
                dll.get_GPS_raw_channel_units,
                dll.get_GPS_raw_channel_samples_count,
                dll.get_GPS_raw_channel_samples,
            )
            sys.stderr.write(" done\n")

            # Section 4: Laps
            sys.stderr.write("Laps")
            lap_count = dll.get_laps_count(idx)
            f.write(struct.pack("<I", lap_count))
            for lap in range(lap_count):
                start = c_double()
                duration = c_double()
                result = dll.get_lap_info(idx, lap, byref(start), byref(duration))
                if result != 0:
                    f.write(struct.pack("<dd", start.value, duration.value))
                else:
                    f.write(struct.pack("<dd", 0.0, 0.0))
            sys.stderr.write(" done\n")

            # Section 5: Metadata
            sys.stderr.write("Metadata")
            metadata = {}
            for key, getter in [
                ("vehicle", dll.get_vehicle_name),
                ("track", dll.get_track_name),
                ("racer", dll.get_racer_name),
                ("championship", dll.get_championship_name),
                ("venue_type", dll.get_venue_type_name),
            ]:
                val = getter(idx)
                metadata[key] = val.decode("latin-1") if val else ""

            f.write(struct.pack("<I", len(metadata)))
            for key, val in metadata.items():
                write_str(f, key)
                write_str(f, val)
            sys.stderr.write(" done\n")

        sys.stderr.write("Output: %s\n" % output_path)

    finally:
        dll.close_file_i(idx)

    # Print summary to stdout (for caller to see)
    print("OK")


if __name__ == "__main__":
    main()
