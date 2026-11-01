"""GPS material for tests of the shared GPS pipeline (``libxrk._aim_xrk_rs.process_gps``).

Two sources: synthetic 56-byte NAV-SOL records built field by field, and the
raw GPS/GNFI/TRK frames lifted straight out of a shipped fixture with
``xrk_patch.frames`` -- no parser involved, so a test of the pipeline does
not depend on either backend.
"""

from __future__ import annotations

import struct
from pathlib import Path

from . import xrk_patch

GPS_RECORD_SIZE = 56
GNFI_RECORD_SIZE = 32

# ECEF position [cm] of the SFJ fixture's first record: Fuji Speedway,
# 35.3725327 N, 138.9275985 E, 644.161 m.
FUJI_ECEF_CM = (-392_555_554, 342_115_191, 367_201_827)
FUJI_LLA = (35.3725327, 138.9275985, 644.1614)


def gps_record(
    timecode: int,
    *,
    ecef_cm: tuple[int, int, int] = (637_813_700, 0, 0),
    vel_cms: tuple[int, int, int] = (0, 0, 0),
    fix: int = 3,
    nsat: int = 12,
    pacc_cm: int = 100,
    sacc_cms: int = 50,
    pdop: int = 150,
    itow: int = 0,
    week: int = 0,
) -> bytes:
    """One well-formed GPS record (4-byte logger timecode + 52-byte NAV-SOL).

    Defaults: a 3D fix at ECEF (6378137 m, 0, 0) -- the equator at the prime
    meridian -- stationary, 12 satellites, pDOP 1.50, 1 m / 0.5 m/s accuracies.
    """
    r = bytearray(GPS_RECORD_SIZE)
    struct.pack_into("<i", r, 0, timecode)
    struct.pack_into("<I", r, 4, itow)
    struct.pack_into("<H", r, 12, week)
    r[14] = fix
    struct.pack_into("<iii", r, 16, *ecef_cm)
    struct.pack_into("<I", r, 28, pacc_cm)
    struct.pack_into("<iii", r, 32, *vel_cms)
    struct.pack_into("<I", r, 44, sacc_cms)
    struct.pack_into("<H", r, 48, pdop)
    r[51] = nsat
    return bytes(r)


def records(timecodes, **fields) -> bytes:
    """Concatenated records at ``timecodes``, all with the same ``fields``."""
    return b"".join(gps_record(t, **fields) for t in timecodes)


def gnfi_records(timecodes) -> bytes:
    """Concatenated 32-byte GNFI records carrying ``timecodes``."""
    out = bytearray()
    for t in timecodes:
        rec = bytearray(GNFI_RECORD_SIZE)
        struct.pack_into("<i", rec, 0, t)
        out += rec
    return bytes(out)


def fixture_material(path: Path) -> dict:
    """Raw GPS material of a fixture, read from its frames (no parser).

    Returns ``{"gps_data", "gnfi_data", "start_finish"}``: the GPS/GPS1 and
    GNFI payloads concatenated in file order, and the TRK start/finish
    (latitude, longitude) in degrees (int32 1e-7 deg at TRK payload [36:44]).
    """
    data = xrk_patch.load_bytes(path)
    gps = bytearray()
    gnfi = bytearray()
    trk = None
    for f in xrk_patch.frames(data):
        if f["tok"] in ("GPS", "GPS1"):
            gps += data[f["pstart"] : f["pend"]]
        elif f["tok"] == "GNFI":
            gnfi += data[f["pstart"] : f["pend"]]
        elif f["tok"] == "TRK":
            trk = f
    start_finish = None
    if trk is not None:
        lat, lon = struct.unpack_from("<ii", data, trk["pstart"] + 36)
        start_finish = (lat / 1e7, lon / 1e7)
    return {"gps_data": bytes(gps), "gnfi_data": bytes(gnfi), "start_finish": start_finish}
