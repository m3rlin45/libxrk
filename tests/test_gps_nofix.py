"""An all-zero NAV-SOL record (no GPS fix yet) must not become a position.

Loggers write a GPS record before the receiver has a fix: ECEF position all
zero, ``gpsFix = 0``, ``numSV = 0``. Fed through the ECEF-to-LLA conversion
that used to come out as latitude 0, longitude 0 and a ``RuntimeWarning:
divide by zero`` with NaN altitude. Both backends now report the position as
unknown (NaN for latitude, longitude and altitude) and keep everything else.

No shipped fixture starts with such a record (one of 409 corpus files does),
so the SFJ fixture's first GPS frame is patched in memory: position zeroed,
fix type and satellite count set to 0. Issue #122.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pytest

from libxrk import aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk.aim_xrk import aim_xrk as decode_cython

from . import xrk_patch

SFJ_XRK = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"

# GPS header payload: 4-byte timecode then the 52-byte NAV-SOL record.
# NAV-SOL offsets within the payload: gpsFix [14], ecefX/Y/Z [16:28], numSV [51].
_GPSFIX = 14
_ECEF = slice(16, 28)
_NUMSV = 51


def _nofix_first_record(data: bytes) -> bytes:
    buf = bytearray(data)
    first = next(f for f in xrk_patch.frames(data) if f["tok"] == "GPS")
    assert first["pend"] - first["pstart"] == 56
    xrk_patch.patch(buf, first, _ECEF.start, bytes(12))
    xrk_patch.patch(buf, first, _GPSFIX, b"\x00")
    xrk_patch.patch(buf, first, _NUMSV, b"\x00")
    return bytes(buf)


@pytest.fixture(scope="module")
def nofix_bytes() -> bytes:
    return _nofix_first_record(xrk_patch.load_bytes(SFJ_XRK))


def _col(log, name):
    return log.channels[name].column(name).to_numpy(zero_copy_only=False)


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_nofix_record_has_nan_position_and_no_warning(decode, nofix_bytes):
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        log = decode(nofix_bytes)

    for name in ("GPS Latitude", "GPS Longitude", "GPS Altitude"):
        v = _col(log, name)
        assert np.isnan(v[0]), f"{name}[0] should be NaN for a no-fix record, got {v[0]}"
        assert np.all(np.isfinite(v[1:])), f"{name}: only the no-fix record may be NaN"

    assert _col(log, "GPS_Fix")[0] == 0
    assert _col(log, "GPS_Satellites")[0] == 0
    # The record is kept, not dropped: same sample count as the unpatched file.
    assert log.channels["GPS Latitude"].num_rows == 42409


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_nofix_record_leaves_laps_and_speed_alone(decode, nofix_bytes):
    patched = decode(nofix_bytes)
    original = decode(str(SFJ_XRK))
    assert patched.laps.to_pylist() == original.laps.to_pylist()
    np.testing.assert_array_equal(_col(patched, "GPS Speed"), _col(original, "GPS Speed"))
    # Position-dependent derived channels may be NaN at the record and the
    # one after it (they are differences); nowhere else.
    for name in ("GPS_Yaw_Rate", "GPS_LateralAcc"):
        v = _col(patched, name)
        assert np.all(np.isfinite(v[2:])), f"{name}: NaN leaked past the no-fix record"


def test_table_merge_survives_nan_position(nofix_bytes):
    log = aim_xrk(nofix_bytes)
    table = log.select_channels(["GPS Latitude", "GPS Speed", "RPM"]).get_channels_as_table()
    assert table.num_rows > 0
