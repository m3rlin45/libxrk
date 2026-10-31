"""Unit strings match what the official AIM DLL reports.

Ground truth is ``tests/reference_dll`` (``get_channel_units`` over every
channel of every corpus file, matched by name against the CHS unit byte). This
pins the one entry that was measurably wrong, code 11 ("number"), on both
backends, and checks the backends agree with each other on every unit.
"""

from pathlib import Path

import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk._aim_xrk_cy import aim_xrk as decode_cython

from . import xrk_patch

TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK = TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"

# Channel -> unit the AIM DLL reports for this file. StartRec carries unit
# code 139 (11 | calibrated flag), Lateral Grip carries 11.
EXPECTED_UNITS = {"StartRec": "#", "Lateral Grip": "#"}


def _units(log, name):
    return log.channels[name].schema.field(name).metadata[b"units"].decode()


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unit_code_11_is_hash(decode):
    log = decode(str(SFJ_XRK))
    for name, expected in EXPECTED_UNITS.items():
        assert _units(log, name) == expected, f"{name}: AIM DLL reports {expected!r}"


def test_both_backends_agree_on_units():
    """A unit is a fact about the file, not about the backend."""
    a, b = decode_cython(str(SFJ_XRK)), decode_rust(str(SFJ_XRK))
    assert {n: _units(a, n) for n in a.channels} == {n: _units(b, n) for n in b.channels}


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unit_code_8_is_metres(decode):
    """Unit code 8 is "m": the AIM DLL reports metres for the code-8 Mileage
    channel (an odometer, values in the millions), and Distance Lap / Distance
    Lap Int carry the same code. No shipped fixture uses code 8 (141 of 409
    files in the maintainer's corpus do), so the SFJ fixture's ACCEL channel
    (unit code 12, "mm") has its CHS unit byte patched to 8 in memory.
    """
    data = bytearray(xrk_patch.load_bytes(SFJ_XRK))
    chs = xrk_patch.chs_frames(bytes(data))["ACCEL"]
    assert data[chs["pstart"] + 12] & 0x7F == 12, "fixture changed: ACCEL is no longer mm"
    xrk_patch.patch(data, chs, 12, bytes([8]))
    log = decode(bytes(data))
    assert _units(log, "ACCEL") == "m"
    assert log.channels["ACCEL"].schema.field("ACCEL").metadata[b"dec_pts"] == b"0"
