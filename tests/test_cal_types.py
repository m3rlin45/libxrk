"""Calibration records of a type libxrk does not decode.

CAL records feed ``metadata["Calibrations"]`` and nothing else. Types 1
(two-point) and 20 (IMU bias) are the only ones in any file available; a
MyChron 5S file was reported with type 33 (issue #81). No fixture has one, so
these tests patch the SFJ fixture's two type-1 records to 33.

The official AIM DLL returns identical channels, units, samples and laps for
the patched file and the original.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk._aim_xrk_cy import aim_xrk as decode_cython

from . import xrk_patch

SFJ_XRK = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
CAL_TYPE_OFFSET = 20  # uint32 at CAL payload [20:24]

BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])


def _sfj_with_cal_type_33() -> tuple[bytes, int]:
    """SFJ bytes with every type-1 CAL record retyped to 33, and how many there were."""
    original = xrk_patch.load_bytes(SFJ_XRK)
    data = bytearray(original)
    count = 0
    for frame in xrk_patch.frames(original):
        if frame["tok"] != "CAL":
            continue
        if struct.unpack_from("<I", original, frame["pstart"] + CAL_TYPE_OFFSET)[0] == 1:
            xrk_patch.patch(data, frame, CAL_TYPE_OFFSET, struct.pack("<I", 33))
            count += 1
    return bytes(data), count


@BACKENDS
def test_undecoded_cal_type_leaves_every_channel_unchanged(decode):
    """The calibration type never reaches the samples (issue #81)."""
    patched, count = _sfj_with_cal_type_33()
    assert count == 2
    before, after = decode(str(SFJ_XRK)), decode(patched)
    assert list(after.channels) == list(before.channels)
    for name, table in before.channels.items():
        assert after.channels[name].equals(table), name
    assert after.laps.equals(before.laps)


@BACKENDS
def test_undecoded_cal_type_is_reported_as_read(decode):
    """Type 33 keeps its type, raw values and channel; only type 1 has outputs."""
    patched, _ = _sfj_with_cal_type_33()
    before = decode(str(SFJ_XRK)).metadata["Calibrations"]
    after = decode(patched).metadata["Calibrations"]
    assert len(after) == len(before)
    retyped = 0
    for old, new in zip(before, after):
        if old["type"] != 1:
            assert new == old
            continue
        retyped += 1
        assert new == {k: old[k] for k in ("raw_1", "raw_2", "channel")} | {"type": 33}
    assert retyped == 2


@BACKENDS
def test_undecoded_cal_type_warns_once_per_record(decode, capfd):
    """One warning per record. The Rust backend used to print each three times."""
    patched, count = _sfj_with_cal_type_33()
    capfd.readouterr()
    decode(patched)
    out, err = capfd.readouterr()
    lines = [line for line in (out + err).splitlines() if "unexpected CAL type" in line]
    assert len(lines) == count
    assert all(line.startswith("libxrk: unexpected CAL type 33 ") for line in lines)
