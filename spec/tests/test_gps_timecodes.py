"""GPS timecode reconstruction: reference implementation + cross-impl agreement.

Some AIM firmware corrupts the upper 16 bits of the GPS record timecode, so
reconstruction has to work from the low 16 bits alone. The rule that matters is
*which backwards steps are 16-bit rollovers*: only those close to 65536. The
previous rule treated every backwards step as a rollover and added 65536ms to
every later sample.

The fixture (`issue84`) is a real Solo 2 qualifying session in which the logger
re-emits a block of 41 GPS records. That steps the logger clock backwards by
1600ms and duplicates 41 iTOW epochs — a backwards step with nothing to do with
a 16-bit wrap.

iTOW (GPS time of week, written by the receiver) is the ground truth here: the
logger firmware bug cannot touch it. It is used only to *verify* reconstruction,
never to perform it.

See spec/docs/companion.md section 6.
"""

from collections import Counter

import pytest

from spec.xrk_format import reconstruct_gps_timecodes
from spec.tests.conftest import ISSUE84_XRZ

WRAP = 65536


# ---------------------------------------------------------------------------
# Reference implementation — the four causes of a backwards step
# ---------------------------------------------------------------------------


class TestReconstructGpsTimecodes:
    """Only a backwards step close to 65536 is a 16-bit rollover."""

    def test_clean_stream_returned_unchanged(self):
        tcs = [1000, 1040, 1080, 1120]
        assert reconstruct_gps_timecodes(tcs) == tcs

    def test_clean_stream_with_large_forward_gap_untouched(self):
        # A legitimate forward gap larger than the 32768ms half-range must
        # survive: monotonic streams are never reconstructed.
        tcs = [1000, 1040, 200_000, 200_040]
        assert reconstruct_gps_timecodes(tcs) == tcs

    def test_true_rollover_advances_one_band(self):
        # Backwards step of ~65536 -> a genuine 16-bit wrap.
        assert reconstruct_gps_timecodes([65440, 65480, 65520, 24, 64]) == [
            65440,
            65480,
            65520,
            65560,
            65600,
        ]

    def test_replayed_block_is_not_a_rollover(self):
        # The issue84 fault in miniature: a re-emitted block of records.
        tcs = [1000, 1040, 1080, 1000, 1040, 1080, 1120]
        assert reconstruct_gps_timecodes(tcs) == tcs

    def test_seam_jitter_is_not_a_rollover(self):
        # Small out-of-order jitter at a buffer-block seam.
        tcs = [100, 200, 160, 240, 280]
        assert reconstruct_gps_timecodes(tcs) == tcs

    def test_straggler_after_rollover_resolves_pre_wrap(self):
        # A record from just before a rollover, arriving just after it, must
        # land at its true pre-wrap time rather than a whole band later.
        assert reconstruct_gps_timecodes([65500, 65530, 20, 65510, 50]) == [
            65500,
            65530,
            65556,
            65510,
            65586,
        ]

    def test_zero_dropout_record_absorbed(self):
        # A single all-zero record must not shift everything after it.
        out = reconstruct_gps_timecodes([66063, 66103, 66143, 0, 66183, 66223])
        assert out[4] == 66183
        assert out[5] == 66223

    def test_upper_bits_garbage_reconstructs_from_low_16(self):
        truth = [500 + i * 40 for i in range(8)]
        corrupt = [(t & 0xFFFF) + (WRAP * 7 if i % 3 == 2 else 0) for i, t in enumerate(truth)]
        assert reconstruct_gps_timecodes(corrupt) == truth

    def test_low_16_bits_always_preserved(self):
        # The invariant that makes reconstruction sound: output is congruent to
        # input mod 65536, so no real sample time is ever altered.
        corrupt = [500, 540, 400, 65_000, 20, 60]
        out = reconstruct_gps_timecodes(corrupt)
        assert [t & 0xFFFF for t in out] == [t & 0xFFFF for t in corrupt]

    @pytest.mark.parametrize("tcs", [[], [42]])
    def test_degenerate_inputs(self, tcs):
        assert reconstruct_gps_timecodes(tcs) == tcs


# ---------------------------------------------------------------------------
# Real fixture — the fault, and reconstruction against receiver ground truth
# ---------------------------------------------------------------------------


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
        out = reconstruct_gps_timecodes(tcs)
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


# ---------------------------------------------------------------------------
# Cross-implementation agreement: spec vs Cython vs Rust
# ---------------------------------------------------------------------------


class TestIssue84CrossImplementation:
    """All three implementations must agree with the reference and each other."""

    @staticmethod
    def _gps_timecodes(log):
        return log.channels["GPS Speed"].column("timecodes").to_pylist()

    def test_cython_span_matches_receiver_clock(self, issue84_parsed, issue84_cython):
        _, itow = _raw_timecodes_and_itow(issue84_parsed)
        nonzero = [t for t in itow if t > 0]
        truth_span = max(nonzero) - min(nonzero)
        t = self._gps_timecodes(issue84_cython)
        assert abs((t[-1] - t[0]) - truth_span) <= 100

    def test_rust_span_matches_receiver_clock(self, issue84_parsed, issue84_rust):
        _, itow = _raw_timecodes_and_itow(issue84_parsed)
        nonzero = [t for t in itow if t > 0]
        truth_span = max(nonzero) - min(nonzero)
        t = self._gps_timecodes(issue84_rust)
        assert abs((t[-1] - t[0]) - truth_span) <= 100

    def test_backends_agree_on_gps_timecodes(self, issue84_cython, issue84_rust):
        assert self._gps_timecodes(issue84_cython) == self._gps_timecodes(issue84_rust)

    def test_matches_official_aim_dll_exactly(self, issue84_cython, issue84_dll_gps):
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
        lx_t = self._gps_timecodes(issue84_cython)

        assert len(dll_t) == len(lx_t), "sample count differs from the AIM DLL"
        offset = dll_t[0] - lx_t[0]
        assert [t - offset for t in dll_t] == lx_t, "GPS timecodes differ from the AIM DLL"

        # And the specific properties the fix is about.
        assert sum(1 for i in range(len(dll_t) - 1) if dll_t[i + 1] < dll_t[i]) == 1
        assert len(dll_t) - len(set(dll_t)) == 41

    def test_gps_stays_in_sync_with_non_gps_channels(self, issue84_cython):
        """The user-visible symptom: GPS drifting away from every other channel."""
        log = issue84_cython
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
