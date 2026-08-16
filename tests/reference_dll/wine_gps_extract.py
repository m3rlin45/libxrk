"""Extract GPS channel timecodes from the official AIM DLL (runs under Wine).

Used to answer: what does AIM's own parser do with a file whose logger clock
steps backwards (issue #84)? Emits JSON on stdout:

    {"gps": [{"name":..., "units":..., "times":[...], "values":[...]}, ...],
     "gps_raw": [...same shape...]}

Usage (under Wine):
    wine64 python.exe wine_gps_extract.py Z:/path/to/file.xrk [max_samples]
"""

import json
import sys
from ctypes import CDLL, c_char_p, c_double, c_int, POINTER


def main():
    path = sys.argv[1]
    dll = CDLL(str(sys.argv[2]) if len(sys.argv) > 2 else "MatLabXRK-2017-64-ReleaseU.dll")

    dll.open_file.argtypes = [c_char_p]
    dll.open_file.restype = c_int
    for base in ("get_GPS_channels", "get_GPS_raw_channels"):
        getattr(dll, f"{base}_count").argtypes = [c_int]
        getattr(dll, f"{base}_count").restype = c_int
    for base in ("get_GPS_channel", "get_GPS_raw_channel"):
        getattr(dll, f"{base}_name").argtypes = [c_int, c_int]
        getattr(dll, f"{base}_name").restype = c_char_p
        getattr(dll, f"{base}_units").argtypes = [c_int, c_int]
        getattr(dll, f"{base}_units").restype = c_char_p
        getattr(dll, f"{base}_samples_count").argtypes = [c_int, c_int]
        getattr(dll, f"{base}_samples_count").restype = c_int
        getattr(dll, f"{base}_samples").argtypes = [
            c_int,
            c_int,
            POINTER(c_double),
            POINTER(c_double),
            c_int,
        ]
        getattr(dll, f"{base}_samples").restype = c_int

    idx = dll.open_file(path.encode())
    if idx <= 0:
        print(json.dumps({"error": f"open_file returned {idx}"}))
        return

    out = {}
    for key, cnt_fn, base in (
        ("gps", "get_GPS_channels_count", "get_GPS_channel"),
        ("gps_raw", "get_GPS_raw_channels_count", "get_GPS_raw_channel"),
    ):
        chans = []
        n = getattr(dll, cnt_fn)(idx)
        for ch in range(n):
            name = getattr(dll, f"{base}_name")(idx, ch)
            units = getattr(dll, f"{base}_units")(idx, ch)
            cnt = getattr(dll, f"{base}_samples_count")(idx, ch)
            if cnt <= 0:
                chans.append({"name": name.decode(errors="replace"), "count": cnt})
                continue
            times = (c_double * cnt)()
            values = (c_double * cnt)()
            got = getattr(dll, f"{base}_samples")(idx, ch, times, values, cnt)
            chans.append(
                {
                    "name": name.decode(errors="replace") if name else "",
                    "units": units.decode(errors="replace") if units else "",
                    "count": cnt,
                    "returned": got,
                    "times": list(times),
                    "values": list(values),
                }
            )
        out[key] = chans

    print(json.dumps(out))


if __name__ == "__main__":
    main()
