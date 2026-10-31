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
