"""Unit strings match what AIM's own tools report.

Ground truth is ``tests/reference_dll`` (``get_channel_units`` over every
channel of every corpus file, matched by name against the CHS unit byte). This
pins the one entry that was measurably wrong, code 11 ("number"), on both
backends, and checks the backends agree with each other on every unit.

For channels the DLL does not expose (the logger's internal distance
channels), ground truth is RaceStudio3's own CSV export, which prints a unit
row under the channel names.
"""

from pathlib import Path

import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk._aim_xrk_cy import aim_xrk as decode_cython

TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK = TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
EK9_XRZ = TEST_DATA_DIR / "ek9/CMD_EK9_Motegi_Generic testing_a_0351.xrz"
AIM_OFFICIAL_XRK = TEST_DATA_DIR / "aim_official/test.xrk"

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
    """Unit code 8 is metres.

    The EK9 fixture's ``Distance Lap`` and ``Distance Lap Int`` carry code 8,
    as does ``Mileage`` on the KK-F loggers, and RaceStudio3 exports Mileage
    with the unit "m" (20/20 corpus CSV exports). The values agree: over a
    session at Motegi (4.8 km) the channel reaches thousands, i.e. metres,
    not millimetres or kilometres.
    """
    log = decode(str(EK9_XRZ))
    for name in ("Distance Lap", "Distance Lap Int"):
        assert _units(log, name) == "m"
    dist = log.channels["Distance Lap"].column("Distance Lap").to_pylist()
    assert 1_000 < max(dist) < 20_000


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unit_code_28_is_percent(decode):
    """Unit code 28 is "%".

    The official AIM sample's Luminosity carries code 28 and RaceStudio3
    displays it as "%"; the sample's Backlight and WiFi channels share the
    code. Neither the DLL nor a CSV export exposes these channels.
    """
    log = decode(str(AIM_OFFICIAL_XRK))
    for name in ("Luminosity", "Backlight", "WiFi"):
        assert _units(log, name) == "%"
