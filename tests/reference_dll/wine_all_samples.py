#!/usr/bin/env python
"""Dump the full sample array of one channel from the official AIM DLL.

Designed to run in Wine Python (no external dependencies).

Usage:
    WINEDEBUG=-all wine python.exe Z:/.../wine_all_samples.py Z:/path/file.xrk <channel_name> [dll_path]

Output is JSON to stdout: {"t_ms": [...], "v": [...]} with times in
milliseconds (the DLL reports seconds).

NOTE: the DLL writes scratch files next to its input (an .xrz input gets a
sibling .xrk); callers should point it at a copy in a temp directory.
"""

import ctypes
from ctypes import POINTER, c_char_p, c_double, c_int
import json
import os
import sys


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: wine_all_samples.py <xrk_file> <channel> [dll_path]"}))
        sys.exit(1)

    xrk_path = sys.argv[1]
    channel_name = sys.argv[2]

    if len(sys.argv) >= 4:
        dll_path = sys.argv[3]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(script_dir, "MatLabXRK-2017-64-ReleaseU.dll")

    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as e:
        print(json.dumps({"error": "Failed to load DLL: %s" % e}))
        sys.exit(1)

    dll.open_file.argtypes = [c_char_p]
    dll.open_file.restype = c_int
    dll.close_file_i.argtypes = [c_int]
    dll.close_file_i.restype = None
    dll.get_channels_count.argtypes = [c_int]
    dll.get_channels_count.restype = c_int
    dll.get_channel_name.argtypes = [c_int, c_int]
    dll.get_channel_name.restype = c_char_p
    dll.get_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_channel_samples_count.restype = c_int
    dll.get_channel_samples.argtypes = [c_int, c_int, POINTER(c_double), POINTER(c_double), c_int]
    dll.get_channel_samples.restype = c_int

    idx = dll.open_file(os.path.abspath(xrk_path).encode("utf-8"))
    if idx < 0:
        print(json.dumps({"error": "Failed to open file: %s" % xrk_path}))
        sys.exit(1)

    try:
        target = None
        for ch in range(dll.get_channels_count(idx)):
            name_bytes = dll.get_channel_name(idx, ch)
            name = name_bytes.decode("latin-1") if name_bytes else ""
            if name == channel_name:
                target = ch
                break
        if target is None:
            print(json.dumps({"error": "Channel not found: %s" % channel_name}))
            sys.exit(1)

        count = dll.get_channel_samples_count(idx, target)
        times = (c_double * count)()
        values = (c_double * count)()
        if count > 0 and dll.get_channel_samples(idx, target, times, values, count) == 0:
            print(json.dumps({"error": "Failed to get samples"}))
            sys.exit(1)

        print(json.dumps({"t_ms": [t * 1000.0 for t in times], "v": list(values)}))
    finally:
        dll.close_file_i(idx)


if __name__ == "__main__":
    main()
