"""The final partial lap matches the official AIM DLL.

When a recording continues past the last beacon crossing, the DLL reports one
more lap from the last LAP message's end to the end of data, i.e. the timecode
of the last data message in file order. Two shipped fixtures exhibit this; the
expected values below are the DLL's own lap tables (``tests/reference_dll``).
Files that end at a crossing, or whose only lap is the out-lap, are unchanged.
Issue #119.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk.aim_xrk import aim_xrk as decode_cython

TEST_DATA = Path(__file__).parent / "test_data"
SFJ_0033 = TEST_DATA / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
SFJ_0101 = TEST_DATA / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
SFJ_SUZUKA = TEST_DATA / "SFJ/CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"
AIM_OFFICIAL = TEST_DATA / "aim_official/test.xrk"
ISSUE49 = TEST_DATA / "issue49/badGPSdata.xrk"

BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])


def _laps(log):
    t = log.laps
    return list(
        zip(
            t.column("num").to_pylist(),
            t.column("start_time").to_pylist(),
            t.column("end_time").to_pylist(),
            t.column("lap_type").to_pylist(),
        )
    )


@BACKENDS
def test_sfj_0101_final_partial_lap_matches_dll(decode):
    laps = _laps(decode(str(SFJ_0101)))
    assert len(laps) == 9  # DLL: 9 laps
    # The last LAP message ends at 992456; data runs to 1133262 (DLL lap 8).
    assert laps[7] == (7, 872023, 992456, "full")
    assert laps[8] == (8, 992456, 1133262, "in")


@BACKENDS
def test_sfj_suzuka_final_partial_lap_ends_at_last_message_not_max_timecode(decode):
    """The steering channel's last sample is at 1823361 but sits earlier in the
    file than the last data message (1823303). The DLL ends the lap at the
    latter; so must we."""
    log = decode(str(SFJ_SUZUKA))
    laps = _laps(log)
    assert len(laps) == 11  # DLL: 11 laps
    assert laps[-1] == (10, 1636781, 1823303, "in")
    assert laps[-2][3] == "full"
    steering_last = log.channels["steering"].column("timecodes")[-1].as_py()
    assert steering_last > laps[-1][2], "fixture changed: no longer exercises the file-order rule"


@BACKENDS
@pytest.mark.parametrize(
    "path, n_laps",
    [(SFJ_0033, 13), (AIM_OFFICIAL, 11), (ISSUE49, 12)],
    ids=["sfj_0033", "aim_official", "issue49"],
)
def test_files_ending_at_a_crossing_are_unchanged(decode, path, n_laps):
    """Data ends a few ms before the last LAP end on these: no lap is added."""
    laps = _laps(decode(str(path)))
    assert len(laps) == n_laps


def test_backends_agree_on_trailing_lap():
    for path in (SFJ_0101, SFJ_SUZUKA):
        assert _laps(decode_cython(str(path))) == _laps(decode_rust(str(path)))
