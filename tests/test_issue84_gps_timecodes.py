"""Backend tests for GPS timecode reconstruction (issue #84).

Reconstruction is implemented once, in the shared GPS crate (`libxrk-gps`,
`libxrk._gps_rs.reconstruct_timecodes`); its unit tests are in
`tests/test_gps_processing.py` and the spec-level fixture analysis in
`spec/tests/test_gps_timecodes.py` (see `spec/docs/companion.md` section 6).
These tests check both backends end-to-end against the `issue84` fixture.

The fixture is a real Solo 2 qualifying session in which the logger re-emits a
block of 41 GPS records. That steps the logger clock backwards by 1600ms without
being a 16-bit rollover — the case the superseded "+65536 on any decrease" rule
got wrong, inflating the GPS timeline and drifting it away from every other
channel.
"""

from pathlib import Path

import pytest

from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk


ISSUE84_XRZ = (
    Path(__file__).parent
    / "test_data"
    / "issue84"
    / "CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz"
)


@pytest.fixture(scope="module")
def cython_log():
    return cython_aim_xrk(str(ISSUE84_XRZ))


@pytest.fixture(scope="module")
def rust_log():
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
