"""A no-fix GPS record (all-zero position) through the whole load path.

The rule itself -- NaN latitude/longitude/altitude for a record written
before the receiver has a fix -- lives in the shared GPS pipeline and is
tested in ``tests/test_gps_processing.py`` (``TestNoFixRecords``). This file
checks that a file starting with such a record loads on both backends without
a ``RuntimeWarning``, keeps its laps, and still merges into a table.

No shipped fixture starts with such a record (one of 409 corpus files does),
so the SFJ fixture's first GPS frame is patched in memory. Issue #122.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from libxrk import aim_xrk
from libxrk._aim_xrk_cy import aim_xrk as decode_cython
from libxrk._aim_xrk_rs import aim_xrk as decode_rust

from . import xrk_patch

SFJ_XRK = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"

# GPS payload: 4-byte timecode then the 52-byte NAV-SOL record.
# NAV-SOL offsets within the payload: gpsFix [14], ecefX/Y/Z [16:28], numSV [51].
_GPSFIX = 14
_ECEF = slice(16, 28)
_NUMSV = 51


@pytest.fixture(scope="module")
def nofix_bytes() -> bytes:
    data = xrk_patch.load_bytes(SFJ_XRK)
    buf = bytearray(data)
    first = next(f for f in xrk_patch.frames(data) if f["tok"] == "GPS")
    assert first["pend"] - first["pstart"] == 56
    xrk_patch.patch(buf, first, _ECEF.start, bytes(12))
    xrk_patch.patch(buf, first, _GPSFIX, b"\x00")
    xrk_patch.patch(buf, first, _NUMSV, b"\x00")
    return bytes(buf)


def _col(log, name):
    return log.channels[name].column(name).to_numpy(zero_copy_only=False)


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_loads_with_nan_position_and_no_warning(decode, nofix_bytes):
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        log = decode(nofix_bytes)
    original = decode(str(SFJ_XRK))

    for name in ("GPS Latitude", "GPS Longitude", "GPS Altitude"):
        v = _col(log, name)
        assert np.isnan(v[0]), f"{name}[0] should be NaN for a no-fix record, got {v[0]}"
        assert np.all(np.isfinite(v[1:])), f"{name}: only the no-fix record may be NaN"
    assert log.channels["GPS Latitude"].num_rows == original.channels["GPS Latitude"].num_rows
    assert log.laps.to_pylist() == original.laps.to_pylist()
    np.testing.assert_array_equal(_col(log, "GPS Speed"), _col(original, "GPS Speed"))


def test_table_merge_survives_nan_position(nofix_bytes):
    log = aim_xrk(nofix_bytes)
    table = log.select_channels(["GPS Latitude", "GPS Speed", "RPM"]).get_channels_as_table()
    assert table.num_rows > 0
