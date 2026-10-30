"""Non-finite float samples are reported as NaN, not passed through as inf.

Loggers write IEEE infinities into float channels as "no value" sentinels:
every sample of the official AIM sample's ``External Voltage`` channel is
float16 ``0x7C00`` (+inf; the kart ran on its internal battery), and CAN
channels carry float32 ``0xFF800000`` (-inf) during bus dropouts. The AIM DLL
returns 0.0 for those samples and hides a channel that is non-finite
throughout. libxrk reports NaN so they read as missing. Issue #81.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from libxrk._aim_xrk_rs import aim_xrk as decode_rust
from libxrk.aim_xrk import aim_xrk as decode_cython

from . import xrk_patch

TEST_DATA = Path(__file__).parent / "test_data"
AIM_OFFICIAL = TEST_DATA / "aim_official/test.xrk"
SFJ_XRK = TEST_DATA / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"

BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])


def _col(log, name):
    return log.channels[name].column(name).to_numpy(zero_copy_only=False)


@BACKENDS
def test_official_sample_external_voltage_is_nan_throughout(decode):
    """Real file: every raw sample is fp16 0x7C00 (+inf). Was inf, now NaN."""
    log = decode(str(AIM_OFFICIAL))
    v = _col(log, "External Voltage")
    assert len(v) == 570
    assert np.all(np.isnan(v))
    assert not np.any(np.isinf(v))


def _patched_sfj() -> tuple[bytes, dict[str, list[int]]]:
    """SFJ bytes with sentinel samples written into BRK (fp16) and Lateral Grip (f32)."""
    data = bytearray(xrk_patch.load_bytes(SFJ_XRK))
    chs = xrk_patch.chs_frames(bytes(data))
    patched: dict[str, list[int]] = {}

    brk = chs["BRK"]
    brk_idx = struct.unpack_from("<H", data, brk["pstart"])[0]
    assert data[brk["pstart"] + 20] == 20, "fixture changed: BRK is no longer decoder 20"
    offs = xrk_patch.s_message_offsets(bytes(data), brk_idx, 2)[:3]
    for off, word in zip(offs, (0x7C00, 0xFC00, 0x7E00)):  # +inf, -inf, NaN
        struct.pack_into("<H", data, off + 8, word)
    patched["BRK"] = offs

    grip = chs["Lateral Grip"]
    grip_idx = struct.unpack_from("<H", data, grip["pstart"])[0]
    assert data[grip["pstart"] + 20] == 6, "fixture changed: Lateral Grip is no longer decoder 6"
    offs = xrk_patch.s_message_offsets(bytes(data), grip_idx, 4)[:2]
    for off, word in zip(offs, (0xFF800000, 0x7F800000)):  # -inf, +inf
        struct.pack_into("<I", data, off + 8, word)
    patched["Lateral Grip"] = offs
    return bytes(data), patched


@BACKENDS
def test_sentinel_samples_become_nan_and_nothing_else_changes(decode):
    data, patched = _patched_sfj()
    original = decode(str(SFJ_XRK))
    log = decode(data)
    for name, offs in patched.items():
        v, o = _col(log, name), _col(original, name)
        assert len(v) == len(o)
        nan_at = np.flatnonzero(np.isnan(v))
        assert len(nan_at) == len(offs), f"{name}: expected {len(offs)} NaN samples, got {nan_at}"
        assert not np.any(np.isinf(v)), f"{name}: an infinity leaked through"
        keep = ~np.isnan(v)
        np.testing.assert_array_equal(
            v[keep], o[keep], err_msg=f"{name}: untouched samples changed"
        )


@BACKENDS
def test_integer_channels_are_untouched(decode):
    """The rule applies to float decoders only; a uint16 'StartRec' keeps its values."""
    log = decode(str(SFJ_XRK))
    v = _col(log, "StartRec")
    assert v.dtype.kind in "iu"
