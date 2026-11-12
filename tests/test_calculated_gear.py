"""Channels named ``Calculated_Gear`` or ``PreCalcGear`` decode by name.

Their samples are 64-bit words: bits 16-18 are the gear, bit 19 set reads as
gear 0, and every other bit is ignored. The name selects the decoder whatever
the CHS ``decoder_type`` says (``CalculatedGear`` / ``NAMED_SAMPLE_CODECS`` in
``spec/xrk_format.py``). Issue #152.

No shipped file has samples for such a channel: the official AIM sample
defines ``Calculated_Gear`` (decoder_type 15, 8-byte samples) but never
samples it. The tests insert ``(S`` messages for it, and rename it to cover
the second name.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk._aim_xrk_cy import aim_xrk as decode_cython

from . import xrk_patch

AIM_OFFICIAL = Path(__file__).parent / "test_data" / "aim_official" / "test.xrk"

BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])
NAMES = pytest.mark.parametrize("name", ["Calculated_Gear", "PreCalcGear"])

# (raw 64-bit sample, gear)
WORDS = [
    *((gear << 16, gear) for gear in range(8)),  # bits 16-18 are the gear
    (1 << 19, 0),  # bit 19 set: 0
    ((1 << 19) | (5 << 16), 0),  # ... whatever the gear bits say
    (0xFFFF_FFFF_FFFF_FFFF, 0),
    (0xFFFF | (4 << 16), 4),  # low garbage is ignored
    ((1 << 63) | (6 << 16), 6),  # high garbage is ignored
    (0xFFFF_FFFF_FFF3_FFFF, 3),  # every ignored bit set
]
BESIDE = "WAT Alarm_1"  # the inserted samples take this channel's first timecodes


def _patched(name: str) -> bytes:
    data = xrk_patch.load_bytes(AIM_OFFICIAL)
    if name != "Calculated_Gear":
        data = xrk_patch.rename_channel(data, "Calculated_Gear", name)
    samples = [struct.pack("<Q", word) for word, _ in WORDS]
    return xrk_patch.add_s_samples(data, name, samples, beside=BESIDE)


def _col(log, channel, column=None):
    return log.channels[channel].column(column or channel).to_numpy(zero_copy_only=False)


@BACKENDS
def test_official_sample_defines_the_channel_without_samples(decode):
    """Why the input is patched: the shipped file never samples the channel."""
    assert "Calculated_Gear" in xrk_patch.chs_frames(xrk_patch.load_bytes(AIM_OFFICIAL))
    assert "Calculated_Gear" not in decode(str(AIM_OFFICIAL)).channels


@BACKENDS
@NAMES
def test_gear_words_decode_by_name(decode, name):
    log = decode(_patched(name))
    values = _col(log, name)
    assert values.tolist() == [gear for _, gear in WORDS]
    assert values.dtype == np.uint32
    np.testing.assert_array_equal(
        _col(log, name, "timecodes"), _col(log, BESIDE, "timecodes")[: len(WORDS)]
    )


@BACKENDS
def test_nothing_else_changes(decode):
    """The inserted messages add one channel and leave the rest of the file alone."""
    original, log = decode(str(AIM_OFFICIAL)), decode(_patched("Calculated_Gear"))
    assert set(log.channels) == set(original.channels) | {"Calculated_Gear"}
    for name, table in original.channels.items():
        for column in table.column_names:
            a, b = _col(original, name, column), _col(log, name, column)
            assert np.array_equal(a, b, equal_nan=a.dtype.kind == "f"), f"{name}.{column}"
    assert log.laps.equals(original.laps)


@NAMES
def test_backends_agree(name):
    data = _patched(name)
    a, b = decode_cython(data), decode_rust(data)
    assert a.channels[name].schema.field(name).type == b.channels[name].schema.field(name).type
    np.testing.assert_array_equal(_col(a, name), _col(b, name))
    np.testing.assert_array_equal(_col(a, name, "timecodes"), _col(b, name, "timecodes"))
