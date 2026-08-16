"""Backend tests for GPS timecode reconstruction (issue #84).

The wire-level rule and its reference implementation live in
`spec/xrk_format.py::reconstruct_gps_timecodes` (see
`spec/tests/test_gps_timecodes.py` and `spec/docs/companion.md` section 6).
These tests pin the same behaviour in the Cython backend at the unit level, and
check both backends end-to-end against the `issue84` fixture.

The fixture is a real Solo 2 qualifying session in which the logger re-emits a
block of 41 GPS records. That steps the logger clock backwards by 1600ms without
being a 16-bit rollover — the case the superseded "+65536 on any decrease" rule
got wrong, inflating the GPS timeline and drifting it away from every other
channel.
"""

import struct
from pathlib import Path

import pytest

import libxrk

# The `aim_xrk` function shadows the `aim_xrk` submodule on the package, so
# importing the submodule replaces the function. Put it back.
_entry = libxrk.aim_xrk
from libxrk.aim_xrk import _decode_gps  # type: ignore[attr-defined]  # noqa: E402

libxrk.aim_xrk = _entry

ISSUE84_XRZ = (
    Path(__file__).parent
    / "test_data"
    / "issue84"
    / "CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz"
)

WRAP = 65536


def _gps_record(timecode: int) -> bytes:
    """A minimal well-formed 56-byte NAV-SOL record with a 3D fix."""
    r = bytearray(56)
    r[0:4] = struct.pack("<i", timecode)
    r[14] = 3  # gpsFix = 3D
    r[16:20] = struct.pack("<i", 637_813_700)  # ecefX ~6378137m in cm
    r[28:32] = struct.pack("<I", 100)  # pAcc 1m
    r[44:48] = struct.pack("<I", 50)  # sAcc 0.5 m/s
    r[48:50] = struct.pack("<H", 150)  # pDOP 1.50
    r[51] = 12  # numSV
    return bytes(r)


def _decoded_timecodes(timecodes) -> list:
    """Run the Cython GPS decoder over synthetic records, return its timecodes."""
    blob = b"".join(_gps_record(t) for t in timecodes)
    channels = _decode_gps(blob, 0)
    return list(channels[0].timecodes)


class TestCythonTimecodeReconstruction:
    """Only a backwards step close to 65536 is a 16-bit rollover."""

    def test_clean_stream_unchanged(self):
        tcs = [1000, 1040, 1080, 1120]
        assert _decoded_timecodes(tcs) == tcs

    def test_clean_stream_with_large_forward_gap_untouched(self):
        # A legitimate forward gap beyond the 32768ms half-range must survive.
        tcs = [1000, 1040, 200_000, 200_040]
        assert _decoded_timecodes(tcs) == tcs

    def test_true_rollover_advances_one_band(self):
        assert _decoded_timecodes([65440, 65480, 65520, 24, 64]) == [
            65440,
            65480,
            65520,
            65560,
            65600,
        ]

    def test_replayed_block_is_not_a_rollover(self):
        # The issue84 fault in miniature.
        tcs = [1000, 1040, 1080, 1000, 1040, 1080, 1120]
        assert _decoded_timecodes(tcs) == tcs

    def test_seam_jitter_is_not_a_rollover(self):
        tcs = [100, 200, 160, 240, 280]
        assert _decoded_timecodes(tcs) == tcs

    def test_straggler_after_rollover_resolves_pre_wrap(self):
        # A record from just before a rollover, arriving just after it, must
        # land at its true pre-wrap time rather than a whole band later.
        assert _decoded_timecodes([65500, 65530, 20, 65510, 50]) == [
            65500,
            65530,
            65556,
            65510,
            65586,
        ]

    def test_zero_dropout_record_absorbed(self):
        # A single all-zero record must not shift everything after it.
        out = _decoded_timecodes([66063, 66103, 66143, 0, 66183, 66223])
        assert out[4] == 66183
        assert out[5] == 66223

    def test_upper_bits_garbage_reconstructs_from_low_16(self):
        truth = [500 + i * 40 for i in range(8)]
        corrupt = [(t & 0xFFFF) + (WRAP * 7 if i % 3 == 2 else 0) for i, t in enumerate(truth)]
        assert _decoded_timecodes(corrupt) == truth

    def test_low_16_bits_always_preserved(self):
        # Reconstruction never alters a real sample time, only its 65536 band.
        corrupt = [500, 540, 400, 65_000, 20, 60]
        out = _decoded_timecodes(corrupt)
        assert [t & 0xFFFF for t in out] == [t & 0xFFFF for t in corrupt]


@pytest.fixture(scope="module")
def cython_log():
    from libxrk import aim_xrk

    return aim_xrk(str(ISSUE84_XRZ))


@pytest.fixture(scope="module")
def rust_log():
    try:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
    except ImportError:
        pytest.skip("Rust backend not available")
    return rust_aim_xrk(str(ISSUE84_XRZ))


def _spans(log):
    """(GPS timecode span, widest non-GPS channel span) in ms."""
    gps = log.channels["GPS Speed"].column("timecodes").to_pylist()
    others = [
        log.channels[c].column("timecodes").to_pylist()
        for c in log.channels
        if not c.startswith("GPS")
    ]
    others = [t for t in others if len(t) > 1]
    return gps[-1] - gps[0], max(t[-1] - t[0] for t in others)


class TestIssue84Fixture:
    """End-to-end: the GPS timeline must not drift away from the rest."""

    def test_cython_gps_in_sync_with_other_channels(self, cython_log):
        gps_span, ref_span = _spans(cython_log)
        assert (
            abs(gps_span - ref_span) <= 100
        ), f"GPS span {gps_span}ms drifted from non-GPS span {ref_span}ms"

    def test_rust_gps_in_sync_with_other_channels(self, rust_log):
        gps_span, ref_span = _spans(rust_log)
        assert abs(gps_span - ref_span) <= 100

    def test_backends_agree_on_gps_timecodes(self, cython_log, rust_log):
        a = cython_log.channels["GPS Speed"].column("timecodes").to_pylist()
        b = rust_log.channels["GPS Speed"].column("timecodes").to_pylist()
        assert a == b

    def test_replayed_block_keeps_its_true_times(self, cython_log):
        """The 41 replayed records must land back on the times they duplicate.

        Under the old rule they were pushed a 65536ms band forward, which the
        downstream 65533ms-gap correction then squashed into ~1.6s of fabricated
        timeline plus 41 phantom samples.
        """
        t = cython_log.channels["GPS Speed"].column("timecodes").to_pylist()
        seams = [i for i in range(len(t) - 1) if t[i + 1] < t[i]]
        assert len(seams) == 1
        i = seams[0]
        assert t[i - 40 : i + 1] == t[i + 1 : i + 42]
        assert len(t) - len(set(t)) == 41

    def test_merged_table_is_monotonic_and_deduplicated(self, cython_log):
        """The duplicate timecodes are absorbed by the merge path."""
        table = cython_log.get_channels_as_table()
        merged = table.column("timecodes").to_pylist()
        assert merged == sorted(merged)
        assert len(merged) == len(set(merged))
