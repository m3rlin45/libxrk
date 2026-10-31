"""GPS timecode reconstruction on the real fault, against receiver ground truth.

Some AIM firmware corrupts the upper 16 bits of the GPS record timecode, so
reconstruction has to work from the low 16 bits alone. The rule that matters is
*which backwards steps are 16-bit rollovers*: only those close to 65536. The
previous rule treated every backwards step as a rollover and added 65536ms to
every later sample.

The reconstruction is implemented once, in the shared GPS crate
(`libxrk-gps`, exposed as `libxrk._aim_xrk_rs.reconstruct_gps_timecodes`); its
synthetic unit tests live in `tests/test_gps_processing.py`. This file applies
it to the raw timecodes the spec parses out of the `issue84` fixture: a real
Solo 2 qualifying session in which the logger re-emits a block of 41 GPS
records, stepping the logger clock backwards by 1600ms and duplicating 41 iTOW
epochs -- a backwards step with nothing to do with a 16-bit wrap.

iTOW (GPS time of week, written by the receiver) is the ground truth here: the
logger firmware bug cannot touch it. It is used only to *verify* reconstruction,
never to perform it.

See spec/docs/companion.md section 6.
"""

from collections import Counter

import pytest

from libxrk._aim_xrk_rs import reconstruct_gps_timecodes as reconstruct_timecodes

WRAP = 65536


def _raw_timecodes_and_itow(parsed):
    samples = parsed.get_gps_samples()
    return [s.timecode for s in samples], [s.iTOW for s in samples]


class TestIssue84Fixture:
    """The real replayed-block fault, and what each rule makes of it."""

    def test_fixture_exhibits_a_single_backwards_step(self, issue84_parsed):
        tcs, _ = _raw_timecodes_and_itow(issue84_parsed)
        seams = [i for i in range(len(tcs) - 1) if tcs[i + 1] < tcs[i]]
        assert len(seams) == 1, f"expected one seam, got {seams}"
        step = tcs[seams[0] + 1] - tcs[seams[0]]
        # The step is the replayed block's duration, NOT a 16-bit rollover.
        assert step == -1600
        assert abs(step) < WRAP // 2

    def test_fixture_replays_a_block_of_41_records(self, issue84_parsed):
        _, itow = _raw_timecodes_and_itow(issue84_parsed)
        counts = Counter(t for t in itow if t > 0)
        assert sum(1 for c in counts.values() if c > 1) == 41

    def test_reconstruction_matches_receiver_clock(self, issue84_parsed):
        """The span of the rebuilt timeline must match iTOW's own span."""
        tcs, itow = _raw_timecodes_and_itow(issue84_parsed)
        out = reconstruct_timecodes(tcs)
        nonzero = [t for t in itow if t > 0]
        truth_span = max(nonzero) - min(nonzero)
        assert (
            abs((out[-1] - out[0]) - truth_span) <= 50
        ), f"rebuilt span {out[-1] - out[0]}ms disagrees with receiver clock {truth_span}ms"

    def test_old_any_decrease_rule_would_inflate_by_65536ms(self, issue84_parsed):
        """Pin the regression this fixture exists to catch.

        The superseded rule ("+65536 on any decrease") turned this file's single
        replayed block into an extra 65.5 seconds of timeline.
        """
        tcs, itow = _raw_timecodes_and_itow(issue84_parsed)
        base = tcs[0] - (tcs[0] & 0xFFFF)
        masked = [(t & 0xFFFF) + base for t in tcs]
        cum, old = 0, [masked[0]]
        for i in range(1, len(masked)):
            if masked[i] < masked[i - 1]:
                cum += WRAP
            old.append(masked[i] + cum)
        nonzero = [t for t in itow if t > 0]
        truth_span = max(nonzero) - min(nonzero)
        assert (old[-1] - old[0]) - truth_span == pytest.approx(WRAP, abs=50)


class TestIssue84CrossImplementation:
    """Both backends must agree with the receiver clock, each other and the AIM DLL."""

    @staticmethod
    def _gps_timecodes(log):
        return log.channels["GPS Speed"].column("timecodes").to_pylist()

    def test_backend_span_matches_receiver_clock(self, issue84_parsed, issue84_backend):
        _, itow = _raw_timecodes_and_itow(issue84_parsed)
        nonzero = [t for t in itow if t > 0]
        truth_span = max(nonzero) - min(nonzero)
        t = self._gps_timecodes(issue84_backend)
        assert abs((t[-1] - t[0]) - truth_span) <= 100

    def test_backends_agree_on_gps_timecodes(self, issue84_cython, issue84_rust):
        assert self._gps_timecodes(issue84_cython) == self._gps_timecodes(issue84_rust)

    def test_matches_official_aim_dll_exactly(self, issue84_backend, issue84_dll_gps):
        """Ground truth: AIM's own parser, via `tests/reference_dll`.

        The DLL reconstructs this file's timeline the same way — it does NOT
        read the backwards step as a 16-bit rollover, and it does NOT drop the
        replayed block. Its raw GPS channels carry all 6062 records, one
        backwards step, and 41 duplicate timecodes, exactly as libxrk does.

        This is what pins the "don't deduplicate" decision: silently dropping
        the replayed records would make libxrk disagree with the reference.
        """
        raw = {c["name"]: c for c in issue84_dll_gps["gps_raw"]}
        dll_t = [round(t * 1000.0) for t in raw["ECEF position_X"]["times"]]
        lx_t = self._gps_timecodes(issue84_backend)

        assert len(dll_t) == len(lx_t), "sample count differs from the AIM DLL"
        offset = dll_t[0] - lx_t[0]
        assert [t - offset for t in dll_t] == lx_t, "GPS timecodes differ from the AIM DLL"

        # And the specific properties the fix is about.
        assert sum(1 for i in range(len(dll_t) - 1) if dll_t[i + 1] < dll_t[i]) == 1
        assert len(dll_t) - len(set(dll_t)) == 41

    def test_gps_stays_in_sync_with_non_gps_channels(self, issue84_backend):
        """The user-visible symptom: GPS drifting away from every other channel."""
        log = issue84_backend
        gps = self._gps_timecodes(log)
        others = [
            log.channels[c].column("timecodes").to_pylist()
            for c in log.channels
            if not c.startswith("GPS")
        ]
        others = [t for t in others if len(t) > 1]
        assert others, "fixture should have non-GPS channels"
        ref_span = max(t[-1] - t[0] for t in others)
        assert abs((gps[-1] - gps[0]) - ref_span) <= 100
