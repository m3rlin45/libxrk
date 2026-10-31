# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

"""GPS: the hand-over to the shared GPS pipeline, plus coordinate utilities.

The parsers (Cython and Rust) only *collect* GPS material: the raw 56-byte
GPS records, the raw 32-byte GNFI records, the session time offset, the lap
table built from LAP messages and the TRK start/finish marker. Everything
derived from that material -- the 14 GPS channels, the 65533 ms timing fix,
the GPS in-lap classification and GPS lap detection -- is implemented once,
in the Rust crate ``crates/libxrk-gps``, and exposed to Python as the
extension module ``libxrk._gps_rs``. :func:`process` runs it over a parser's
output; the Cython backend and the Construct spec both go through it, and
the Rust backend links the same crate directly.

The numpy helpers below (Web Mercator, WGS-84 conversions, crossing lookup)
are vectorised utilities for callers working with arrays of coordinates;
the parsers do not use them.
"""

from collections import namedtuple
from typing import Optional, Sequence

import numpy as np
import pyarrow as pa

from . import _gps_rs

# The 14 channels gps.process() synthesizes from the GPS records. They share
# one timebase (the GPS fix rate) and are the channels the timing fix moves.
GPS_CHANNEL_NAMES: tuple[str, ...] = tuple(_gps_rs.GPS_CHANNEL_NAMES)

GPS = namedtuple("GPS", ["lat", "long", "alt"])

_LAP_SCHEMA = pa.schema(
    [
        pa.field("num", pa.int32()),
        pa.field("start_time", pa.int64()),
        pa.field("end_time", pa.int64()),
        pa.field("lap_type", pa.utf8()),
    ]
)


def laps_table(
    nums: Sequence[int], starts: Sequence[int], ends: Sequence[int], types: Sequence[str]
) -> pa.Table:
    """Build the laps table both backends expose (num, start_time, end_time, lap_type)."""
    return pa.table(
        {
            "num": pa.array(nums, type=pa.int32()),
            "start_time": pa.array(starts, type=pa.int64()),
            "end_time": pa.array(ends, type=pa.int64()),
            "lap_type": pa.array(types, type=pa.utf8()),
        },
        schema=_LAP_SCHEMA,
    )


def _lap_rows(laps: pa.Table) -> list[tuple[int, int, int, str]]:
    return list(
        zip(
            laps.column("num").to_pylist(),
            laps.column("start_time").to_pylist(),
            laps.column("end_time").to_pylist(),
            laps.column("lap_type").to_pylist(),
        )
    )


def process(
    channels: dict[str, pa.Table],
    laps: pa.Table,
    *,
    gps_data: bytes,
    gnfi_data: bytes,
    time_offset: int,
    start_finish: Optional[tuple[float, float]],
    non_gps_max_end_time: Optional[int] = None,
    expected_dt_ms: float = 40.0,
) -> tuple[dict[str, pa.Table], pa.Table]:
    """Run the shared GPS pipeline over a parser's raw output.

    Decodes the GPS records into the 14 GPS channels (reconstructing
    corrupted timecodes), detects and removes the 65533 ms firmware gap
    (using GNFI as the reference clock when present, the last timecode of
    the non-GPS channels otherwise), and finishes the lap table: the last
    lap becomes ``"in"`` when the car is more than 30 m from start/finish at
    its end, or, when ``laps`` is empty, laps are detected from the GPS
    track. See ``spec/docs/companion.md`` sections 5-7.

    Args:
        channels: The parser's channel tables (CHS channels only, no GPS
            channels yet), each with a ``timecodes`` column.
        laps: The lap table built from LAP messages (see
            :func:`laps_table`); empty when the file has none.
        gps_data: Concatenated 56-byte GPS records in file order.
        gnfi_data: Concatenated 32-byte GNFI records in file order.
        time_offset: Session start in logger time, subtracted from every
            GPS timecode.
        start_finish: ``(latitude, longitude)`` in degrees from the TRK
            message, or None.
        non_gps_max_end_time: Last timecode over the non-GPS channels, for
            the timing fix's indirect method; taken from ``channels`` when
            None.
        expected_dt_ms: GPS sample period assumed by the timing fix.

    Returns:
        ``(channels, laps)``: ``channels`` extended with the GPS channels,
        and the finished lap table.

    Raises:
        ValueError: ``gps_data`` is not a whole number of 56-byte records.
    """
    if non_gps_max_end_time is None:
        ends = [t.column("timecodes")[-1].as_py() for t in channels.values() if t.num_rows > 0]
        non_gps_max_end_time = max(ends) if ends else None
    gps_tables, lap_rows = _gps_rs.process(
        gps_data,
        gnfi_data,
        time_offset,
        non_gps_max_end_time,
        start_finish,
        _lap_rows(laps),
        expected_dt_ms,
    )
    out = dict(channels)
    out.update(gps_tables)
    cols: tuple[list, list, list, list] = ([], [], [], [])
    for row in lap_rows:
        for col, v in zip(cols, row):
            col.append(v)
    return out, laps_table(*cols)


# convert lat/long/zoom to web mercator. lat/long are degrees
# returns x,y as floats - integer component is which tile to download
def llz2web(lat, long, zoom=0):
    # wikipedia web mercator projection
    mult = 0.25 * (2 << zoom)
    return (
        mult * (1 + long / 180),
        mult * (1 - np.log(np.tan(np.pi / 4 + np.pi / 360 * lat)) / np.pi),
    )


# returns lat/long as floats in degrees
def web2ll(x, y, zoom=0):
    mult = 1 / (0.25 * (2 << zoom))
    return (
        np.arctan(np.exp(np.pi - np.multiply(np.pi * mult, y))) * 360 / np.pi - 90,
        np.multiply(180 * mult, x) - 180,
    )


# lat, long = degrees
# x, y, z, alt = meters
def lla2ecef(lat, lon, alt):
    a = 6378137
    e = 8.181919084261345e-2
    e_sq = e * e

    lat = lat * (np.pi / 180)
    lon = lon * (np.pi / 180)

    clat = np.cos(lat)
    slat = np.sin(lat)

    N = a / np.sqrt(1 - e_sq * slat * slat)

    x = (N + alt) * clat * np.cos(lon)
    y = (N + alt) * clat * np.sin(lon)
    z = ((1 - e_sq) * N + alt) * slat

    return x, y, z


# Computing geodetic coordinates from geocentric coordinates
# H. Vermeille, 2003/2004
# http://users.auth.gr/kvek/78_Vermeille.pdf
# Vectorised twin of libxrk-gps's geo::ecef2lla (same operation order).
def ecef2lla_vermeille2003(x, y, z):
    a = 6378137.0
    e = 8.181919084261345e-2

    p = (x * x + y * y) * (1 / (a * a))
    q = ((1 - e * e) / (a * a)) * z * z
    r = (p + q - e**4) * (1 / 6)
    s = (e**4 / 4) * p * q / (r**3)
    p = None
    t = np.cbrt(1 + s + np.sqrt(s * (2 + s)))
    s = None
    u = r * (1 + t + 1 / t)
    r = None
    t = None
    v = np.sqrt(u * u + e**4 * q)
    u += v  # precalc
    w = (e**2 / 2) * (u - q) / v
    q = None
    k = np.sqrt(u + w * w) - w
    D = k * np.sqrt(x * x + y * y) / (k + e**2)
    rtDDzz = np.sqrt(D * D + z * z)
    return GPS(
        (180 / np.pi) * 2 * np.arctan2(z, D + rtDDzz),
        (180 / np.pi) * np.arctan2(y, x),
        (k + e**2 - 1) / k * rtDDzz,
    )


def find_crossing_idx(
    XYZ: np.ndarray, marker: np.ndarray  # coordinates to look up in (X, Y, Z), meters
):  # (lat, long), degrees

    if isinstance(marker, tuple):
        marker = np.array(marker)
    if len(marker.shape) == 1:
        # force it to be a 2d shape to make the rest of the code simpler
        return find_crossing_idx(XYZ, marker.reshape((1, len(marker))))[0]

    # very similar to gps lap insert, but we can assume XYZ is a
    # reference (as opposed to GPS lap insert where we aren't sure if
    # the trajectory of the GPS is correct or not - think pit stops,
    # going off track, etc).  As a result, only one pass is needed.
    # Also we do not need to filter based on minspeed, so no timecodes
    # are used in this function.

    lat = marker[:, 0].reshape((len(marker), 1))
    lon = marker[:, 1].reshape((len(marker), 1))
    SO = np.stack(lla2ecef(lat, lon, 0), axis=2)
    SD = np.stack(lla2ecef(lat, lon, 1000), axis=2) - SO

    O = XYZ[:, :3]
    D = O[1:] - O[:-1]
    O = O[:-1] - SO

    SN = np.sum(SD * SD, axis=2, keepdims=True) * D - np.sum(SD * D, axis=2, keepdims=True) * SD
    t = np.clip(
        -np.sum(SN * O, axis=2, keepdims=True) / np.sum(SN * D, axis=2, keepdims=True),
        0,
        1,
    )

    # XXX This won't work with rally stages (anything not a circuit)
    distsq = np.sum(np.square(O + t * D), axis=2)
    minidx = np.argmin(distsq, axis=1)
    colrange = np.arange(t.shape[0])
    return np.column_stack([minidx + t[colrange, minidx, 0], np.sqrt(distsq[colrange, minidx])])


def find_crossing_dist(
    XYZD: np.ndarray,  # coordinates to look up in (X, Y, Z, Distance), meters
    marker: tuple[float, float],
):  # (lat, long) tuple, degrees
    idx, _ = find_crossing_idx(XYZD, np.array(marker))
    if idx + 1 >= len(XYZD):
        return XYZD[int(idx), 3]
    scale, idx = np.modf(idx)
    return XYZD[int(idx), 3] + scale * (XYZD[int(idx) + 1, 3] - XYZD[int(idx), 3])


ecef2lla = ecef2lla_vermeille2003
