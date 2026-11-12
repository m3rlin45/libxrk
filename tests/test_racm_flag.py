"""An unexpected RACM flag is reported once.

A one-byte RACM payload is a flag that is 0 in every file available; a longer
one is the race mode string. Both backends ask for a report when the flag is
not 0. The Rust backend used to print that three times per record, once for
each pass it makes over the header messages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk._aim_xrk_cy import aim_xrk as decode_cython

from . import xrk_patch

SFJ_XRK = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


def _sfj_with_racm_flag(flag: int) -> bytes:
    """SFJ bytes with its one-byte RACM flag record set to ``flag``."""
    original = xrk_patch.load_bytes(SFJ_XRK)
    data = bytearray(original)
    flags = [
        f for f in xrk_patch.frames(original) if f["tok"] == "RACM" and f["pend"] - f["pstart"] == 1
    ]
    assert len(flags) == 1
    xrk_patch.patch(data, flags[0], 0, bytes([flag]))
    return bytes(data)


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_unexpected_racm_flag_warns_once(decode, capfd):
    capfd.readouterr()
    log = decode(_sfj_with_racm_flag(5))
    out, err = capfd.readouterr()
    lines = [line for line in (out + err).splitlines() if "RACM" in line]
    assert len(lines) == 1
    assert lines[0].startswith("libxrk: unexpected RACM flag 5 (expected 0).")
    # The file's other RACM record still gives the race mode.
    assert log.metadata["Race Mode"] == "speed"


@pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
def test_expected_racm_flag_is_silent(decode, capfd):
    capfd.readouterr()
    decode(str(SFJ_XRK))
    out, err = capfd.readouterr()
    assert "RACM" not in out + err
