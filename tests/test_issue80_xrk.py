"""A GRP that no G message uses: MyChron 5S recordings (issue #80).

The fixture is a real MyChron 5S session (Logger Model ID 822). Its first CNF
defines channels 0..71 and one GRP, index 1, listing 3945 members: channels
16..3960. No ``(G`` message follows anywhere in the file; every channel's
samples, the listed ones included, are ``(S`` messages.

Two faults met here, one per backend:

* Cython indexed its channel table with each listed member and raised on the
  first one without a CHS (72). That discarded the CNF half registered, and
  the parse ended in a segfault on 0.12.0 and an ``IndexError`` from
  ``process_group`` later (the traceback of issue #83).
* Both backends read a GRP member's samples only from G messages, so the 17
  listed channels that do have samples (RPM, Water Temp, Internal Battery,
  ...) came back empty and were dropped. The Rust backend returned the file
  without them and without a word.

Ground truth is the AIM DLL (``tests/reference_dll``), which exposes three
channels for this file; the numbers below are its output. The spec suite
compares every sample of every channel against ``spec/xrk_format.py``
(``spec/tests/test_spec.py``).
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from libxrk import GPS_CHANNEL_NAMES
from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

from . import xrk_patch

TEST_DATA_DIR = Path(__file__).parent / "test_data"
ISSUE80_XRK = TEST_DATA_DIR / "issue80" / "libxrk_issue80_5S_b.xrk"
ISSUE49_XRK = TEST_DATA_DIR / "issue49" / "badGPSdata.xrk"

BACKENDS = {"cython": cython_aim_xrk, "rust": rust_aim_xrk}

# What the AIM DLL returns for the fixture:
# name -> (units, samples, first value, last value, first time ms, last time ms)
DLL_CHANNELS = {
    "Water Temp": ("C", 6571, 56.875, 58.53125, 40, 328540),
    "Internal Battery": ("V", 328, 3.968, 3.978, 805, 327805),
    "RPM": ("rpm", 6571, 1490, 0, 63, 328563),
}
DLL_LAP_STARTS = [0, 17262, 60385, 102461, 144141, 185752, 227322, 269017, 311577]
DLL_LAST_LAP_END = 328576

# Channels the GRP lists that carry samples, all of them as S messages.
GRP_MEMBERS_WITH_SAMPLES = [
    "Temp TC 1",
    "Temp TC 2",
    "Water Temp",
    "Temp TR 2",
    "Internal Battery",
    "Logger Temperature",
    "Internal Batt",
    "RPM",
    "Jackshaft",
    "WheelSpeed",
    "Luminosity",
    "ZeroX5",
    "ZeroX50",
    "Lateral Grip",
    "Calculated_Gear",
    "Alarm_1_max",
    "Alarm_2_max",
]


@pytest.fixture(scope="module", params=list(BACKENDS))
def log(request):
    return BACKENDS[request.param](str(ISSUE80_XRK))


def test_fixture_has_a_grp_and_no_g_message():
    """The property the fixture was chosen for, read from its bytes."""
    data = ISSUE80_XRK.read_bytes()
    all_frames = list(xrk_patch.frames(data))
    (grp,) = [f for f in all_frames if f["tok"] == "GRP"]
    index, count = struct.unpack_from("<HH", data, grp["pstart"])
    members = struct.unpack_from(f"<{count}H", data, grp["pstart"] + 4)
    assert (index, count) == (1, 3945)
    assert members == tuple(range(16, 3961))
    defined = {
        struct.unpack_from("<H", data, f["pstart"])[0] for f in all_frames if f["tok"] == "CHS"
    }
    assert defined == set(range(72))

    # Everything between two top-level frames is sample data, and all of it
    # is S messages.
    top = [f for f in all_frames if not f["parents"]]
    openers = set()
    for prev, nxt in zip(top, top[1:]):
        gap = data[prev["pend"] + 8 : nxt["start"]]
        if gap:
            openers.add(gap[:2])
    assert openers == {b"(S"}


def test_parses_with_every_sampled_channel(log):
    """Neither a crash nor a silently shorter channel list."""
    chs_channels = [name for name in log.channels if name not in GPS_CHANNEL_NAMES]
    assert len(chs_channels) == 25
    assert len(log.channels) == 25 + len(GPS_CHANNEL_NAMES)
    for name in GRP_MEMBERS_WITH_SAMPLES:
        assert name in log.channels, name
        assert log.channels[name].num_rows > 0, name


@pytest.mark.parametrize("name", DLL_CHANNELS)
def test_channels_match_the_aim_dll(log, name):
    units, rows, first, last, first_ms, last_ms = DLL_CHANNELS[name]
    table = log.channels[name]
    values = table.column(name).to_numpy(zero_copy_only=False)
    timecodes = table.column("timecodes").to_pylist()
    assert table.schema.field(name).metadata[b"units"].decode() == units
    assert table.num_rows == rows
    assert values[0] == pytest.approx(first, abs=1e-6)
    assert values[-1] == pytest.approx(last, abs=1e-6)
    assert (timecodes[0], timecodes[-1]) == (first_ms, last_ms)


def test_laps_match_the_aim_dll(log):
    assert log.laps.column("start_time").to_pylist() == DLL_LAP_STARTS
    assert log.laps.column("end_time").to_pylist() == DLL_LAP_STARTS[1:] + [DLL_LAST_LAP_END]


def test_logger_identity(log):
    """What tells a 5S (822) from a 5S2T (813, per the reporter)."""
    assert log.metadata["Logger Model ID"] == 822


@pytest.mark.parametrize("backend", list(BACKENDS))
def test_grp_member_without_a_chs_costs_nothing(backend):
    """A GRP that G messages do use, with one member no CHS defines.

    The issue49 file's one GRP gets channel 4000 appended. That member adds
    no bytes to a G payload, so the file must parse exactly as it did before:
    same channels, same samples. (Cython raised ``IndexError`` here, issue
    #83.)
    """
    data = ISSUE49_XRK.read_bytes()
    (grp,) = [f for f in xrk_patch.frames(data) if f["tok"] == "GRP"]
    index, count = struct.unpack_from("<HH", data, grp["pstart"])
    payload = (
        struct.pack("<HH", index, count + 1)
        + data[grp["pstart"] + 4 : grp["pend"]]
        + struct.pack("<H", 4000)
    )
    mutant = xrk_patch.replace_payload(data, grp, payload)
    assert len(mutant) == len(data) + 2

    decode = BACKENDS[backend]
    expected, got = decode(data), decode(mutant)
    assert list(got.channels) == list(expected.channels)
    for name, table in expected.channels.items():
        for column in ("timecodes", name):
            np.testing.assert_array_equal(
                got.channels[name].column(column).to_numpy(zero_copy_only=False),
                table.column(column).to_numpy(zero_copy_only=False),
                err_msg=name,
            )
    assert got.laps.equals(expected.laps)
