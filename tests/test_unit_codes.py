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

from . import xrk_patch

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


def _sfj_with_unit_byte(channel: str, unit_byte: int) -> bytes:
    """SFJ bytes with one channel's unit byte (CHS [12]) replaced."""
    data = bytearray(xrk_patch.load_bytes(SFJ_XRK))
    xrk_patch.patch(data, xrk_patch.chs_frames(bytes(data))[channel], 12, bytes([unit_byte]))
    return bytes(data)


def _sfj_with_unit_0_channel() -> bytes:
    """SFJ bytes with BRK turned into the unconfigured input of issue #81.

    The unit byte (CHS [12]) becomes 0 and the name field (CHS [32:56])
    becomes ``NONE_0``, a NUL, then what is left of the name it overwrote.
    """
    data = bytearray(xrk_patch.load_bytes(SFJ_XRK))
    frame = xrk_patch.chs_frames(bytes(data))["BRK"]
    xrk_patch.patch(data, frame, 12, b"\x00")
    xrk_patch.patch(data, frame, 32, b"NONE_0\x00nnel_1".ljust(24, b"\x00"))
    return bytes(data)


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unit_code_0_is_no_unit(decode, capfd):
    """Unit code 0 is "no unit", and is not reported as unknown.

    A MyChron 5S writes code 0 on inputs with no sensor configured, named
    ``NONE_0``, ``NONE_3`` (issue #81). The AIM DLL exposes such a channel
    under the name up to the first NUL, with an empty unit string and the
    samples unchanged.
    """
    original = decode(str(SFJ_XRK))
    capfd.readouterr()
    log = decode(_sfj_with_unit_0_channel())
    out, err = capfd.readouterr()
    assert "Unknown units" not in out + err

    assert "BRK" not in log.channels
    assert _units(log, "NONE_0") == ""
    assert log.channels["NONE_0"].schema.field("NONE_0").metadata[b"dec_pts"] == b"0"
    assert log.channels["NONE_0"].column("NONE_0").equals(original.channels["BRK"].column("BRK"))
    assert (
        log.channels["NONE_0"]
        .column("timecodes")
        .equals(original.channels["BRK"].column("timecodes"))
    )


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unknown_unit_code_warns_once_with_the_channel_name(decode, capfd):
    """An unmapped code gives an empty unit and one warning naming the channel.

    Code 39 is in no unit table (the AIM DLL's stops at 38). The Rust backend
    used to print the warning three times, and the Cython backend printed the
    raw 24-byte name field instead of the name.
    """
    capfd.readouterr()
    log = decode(_sfj_with_unit_byte("BRK", 39))
    out, err = capfd.readouterr()
    lines = [line for line in (out + err).splitlines() if "Unknown units" in line]
    assert lines == ["Unknown units[39] for BRK"]
    assert _units(log, "BRK") == ""


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unit_code_6_is_hash(decode):
    """Unit code 6 ("number") is "#", like code 11.

    The only fixture channel that carries it, ``Calculated_Gear`` on the
    official AIM sample, has no samples and is not exposed, so this patches
    BRK. The AIM DLL reports "#" for a channel patched to code 6.
    """
    log = decode(_sfj_with_unit_byte("BRK", 6))
    assert _units(log, "BRK") == "#"


# Codes no available file carries, with the string the AIM DLL reports for a
# channel patched to each (spec/docs/companion.md section 9).
DLL_ONLY_UNITS = {
    2: "%/s",
    7: "Coord",
    10: "kg/s",
    13: "kW",
    23: "l*Lap",
    25: "Date",
    29: "rpm/s",
    32: "V",
    34: "l/hkm",
    35: "km/l",
    36: "ft/s2",
    37: "mG",
    38: "krpm",
}


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_units_named_only_by_the_dll(decode, capfd):
    """Each code has the DLL's string, raises no warning and leaves samples alone."""
    original = decode(str(SFJ_XRK)).channels["BRK"]
    for code, unit in DLL_ONLY_UNITS.items():
        capfd.readouterr()
        log = decode(_sfj_with_unit_byte("BRK", code))
        out, err = capfd.readouterr()
        assert "Unknown units" not in out + err, code
        assert _units(log, "BRK") == unit, code
        assert log.channels["BRK"].column("BRK").equals(original.column("BRK")), code


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
@pytest.mark.parametrize("unit_byte", [32, 32 | 0x80], ids=["plain", "calibrated"])
def test_unit_code_32_is_volts_as_stored(decode, unit_byte):
    """Code 32 is "V" and its samples are not divided by 1000.

    Only millivolts with the calibrated flag (byte 149, the fixture's
    ``External Voltage``) is converted. The AIM DLL returns that channel as
    11.264 V, and as 11264 when its unit byte is patched to 21 (mV), 32 or
    32 with the flag. The backends used to convert any channel whose unit
    string was "V".
    """
    name = "External Voltage"
    stored = decode(_sfj_with_unit_byte(name, 21)).channels[name]  # mV, as stored
    assert _units(decode(_sfj_with_unit_byte(name, 21)), name) == "mV"
    converted = decode(str(SFJ_XRK)).channels[name]
    assert converted.column(name)[0].as_py() == pytest.approx(11.264, abs=1e-3)

    log = decode(_sfj_with_unit_byte(name, unit_byte))
    assert _units(log, name) == "V"
    assert log.channels[name].schema.field(name).type == stored.schema.field(name).type
    assert log.channels[name].column(name).equals(stored.column(name))
    assert log.channels[name].column(name)[0].as_py() == 11264.0
