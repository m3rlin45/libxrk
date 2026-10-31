"""The shared GPS pipeline: ``libxrk._gps_rs`` (crate ``crates/libxrk-gps``).

Every GPS-derived result libxrk exposes -- the 14 GPS channels, the
reconstruction of corrupted timecodes, the 65533 ms timing fix, the in-lap
classification and GPS lap detection -- comes from this one module, on both
backends and in the spec. So it is tested here on its own, from raw material:
synthetic NAV-SOL records (``gps_synth``) and the GPS/GNFI/TRK frames lifted
out of the SFJ fixture without a parser. The backends' only GPS job is to hand
over that material; ``spec/tests/test_spec.py`` checks they do.

Algorithms: ``spec/docs/companion.md`` sections 5 (channels), 6 (timing) and
7 (laps).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from libxrk import ChannelMetadata, GPS_CHANNEL_NAMES, _gps_rs, gps
from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk

from . import gps_synth
from .gps_synth import FUJI_ECEF_CM, FUJI_LLA, gnfi_records, gps_record, records

SFJ_XRK = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
WRAP = 65536


def _col(tables, name):
    return tables[name].column(name).to_numpy(zero_copy_only=False)


def _tc(tables, name="GPS Speed"):
    return tables[name].column("timecodes").to_numpy()


def _process(
    gps_data,
    laps=(),
    *,
    gnfi_data=b"",
    time_offset=0,
    non_gps_max_end_time=None,
    start_finish=None,
):
    return _gps_rs.process(
        gps_data, gnfi_data, time_offset, non_gps_max_end_time, start_finish, list(laps)
    )


# ---------------------------------------------------------------------------
# Channels (section 5)
# ---------------------------------------------------------------------------


class TestChannels:
    def test_channel_names_are_the_public_constant(self):
        assert gps.GPS_CHANNEL_NAMES == GPS_CHANNEL_NAMES == tuple(_gps_rs.GPS_CHANNEL_NAMES)
        assert len(GPS_CHANNEL_NAMES) == 14
        tables = _gps_rs.decode(gps_record(0), 0)
        assert tuple(tables) == GPS_CHANNEL_NAMES

    def test_empty_input_gives_no_channels(self):
        assert _gps_rs.decode(b"", 0) == {}
        channels, laps = _process(b"", [(0, 0, 1000, "full")])
        assert channels == {} and laps == [(0, 0, 1000, "full")]

    @pytest.mark.parametrize("length", [55, 57, 111])
    def test_partial_record_is_rejected(self, length):
        with pytest.raises(ValueError, match="not a multiple of 56"):
            _gps_rs.decode(bytes(length), 0)
        with pytest.raises(ValueError, match="not a multiple of 56"):
            _process(bytes(length))

    def test_table_layout_and_metadata(self):
        tables = _gps_rs.decode(records([0, 40]), 0)
        expected = {
            "GPS Speed": ("m/s", 1, True, pa.float64()),
            "GPS Latitude": ("deg", 4, True, pa.float64()),
            "GPS Longitude": ("deg", 4, True, pa.float64()),
            "GPS Altitude": ("m", 1, True, pa.float64()),
            "GPS_Satellites": ("", 0, False, pa.float32()),
            "GPS_Fix": ("", 0, False, pa.float32()),
            "GPS_pDOP": ("", 2, False, pa.float32()),
            "GPS_Position_Accuracy": ("m", 2, True, pa.float32()),
            "GPS_Velocity_Accuracy": ("m/s", 2, True, pa.float32()),
            "GPS_InlineAcc": ("g", 2, True, pa.float32()),
            "GPS_LateralAcc": ("g", 2, True, pa.float32()),
            "GPS_Yaw_Rate": ("deg/s", 1, True, pa.float32()),
            "GPS_ITOW": ("ms", 0, False, pa.float64()),
            "GPS_Week": ("", 0, False, pa.float32()),
        }
        for name, (units, dec_pts, interpolate, dtype) in expected.items():
            t = tables[name]
            assert t.column_names == ["timecodes", name]
            assert t.schema.field("timecodes").type == pa.int64()
            assert t.schema.field(name).type == dtype, name
            meta = ChannelMetadata.from_channel_table(t)
            assert (meta.units, meta.dec_pts, meta.interpolate) == (units, dec_pts, interpolate)
            assert meta.function == "" and meta.cal_value_2 == 1.0
            assert t.num_rows == 2

    def test_time_offset_is_subtracted(self):
        assert list(_tc(_gps_rs.decode(records([1000, 2000]), 0))) == [1000, 2000]
        assert list(_tc(_gps_rs.decode(records([5000]), 3000))) == [2000]

    def test_raw_fields_scale_as_documented(self):
        rec = gps_record(
            0, fix=2, nsat=7, pacc_cm=250, sacc_cms=35, pdop=187, itow=123456, week=2345
        )
        t = _gps_rs.decode(rec, 0)
        assert _col(t, "GPS_Fix")[0] == 2
        assert _col(t, "GPS_Satellites")[0] == 7
        assert _col(t, "GPS_Position_Accuracy")[0] == pytest.approx(2.5)
        assert _col(t, "GPS_Velocity_Accuracy")[0] == pytest.approx(0.35)
        assert _col(t, "GPS_pDOP")[0] == pytest.approx(1.87)
        assert _col(t, "GPS_ITOW")[0] == 123456
        assert _col(t, "GPS_Week")[0] == 2345

    def test_fuji_speedway_position(self):
        t = _gps_rs.decode(gps_record(0, ecef_cm=FUJI_ECEF_CM), 0)
        assert _col(t, "GPS Latitude")[0] == pytest.approx(FUJI_LLA[0], abs=1e-6)
        assert _col(t, "GPS Longitude")[0] == pytest.approx(FUJI_LLA[1], abs=1e-6)
        assert _col(t, "GPS Altitude")[0] == pytest.approx(FUJI_LLA[2], abs=1e-3)

    def test_speed_inline_acc_and_yaw_from_velocity(self):
        # At (0 N, 0 E), ECEF +Z is due north and +Y due east. Two samples 1 s
        # apart, 10 then 20 m/s north: speed, a 10 m/s^2 inline acceleration,
        # no heading change. Then a sample heading east: 90 deg/s of yaw.
        data = (
            gps_record(0, vel_cms=(0, 0, 1000))
            + gps_record(1000, vel_cms=(0, 0, 2000))
            + gps_record(2000, vel_cms=(0, 2000, 0))
        )
        t = _gps_rs.decode(data, 0)
        np.testing.assert_allclose(_col(t, "GPS Speed"), [10.0, 20.0, 20.0], atol=1e-12)
        inline = _col(t, "GPS_InlineAcc")
        assert inline[0] == 0 and inline[1] == pytest.approx(10 / 9.81, abs=1e-6)
        yaw = _col(t, "GPS_Yaw_Rate")
        assert yaw[0] == 0 and yaw[1] == 0 and yaw[2] == pytest.approx(90.0, abs=1e-4)
        lateral = _col(t, "GPS_LateralAcc")
        assert lateral[2] == pytest.approx(20.0 * math.radians(90.0) / 9.81, abs=1e-5)

    def test_zero_interval_gives_zero_not_inf(self):
        data = gps_record(1000, vel_cms=(0, 0, 1000)) + gps_record(1000)
        t = _gps_rs.decode(data, 0)
        assert _col(t, "GPS_InlineAcc")[1] == 0 and _col(t, "GPS_Yaw_Rate")[1] == 0

    def test_geo_conversions_round_trip(self):
        for lla in [FUJI_LLA, (-33.9, 151.2, 10.0), (0.0, 0.0, 0.0)]:
            xyz = _gps_rs.lla2ecef(*lla)
            back = _gps_rs.ecef2lla(*xyz)
            assert back[0] == pytest.approx(lla[0], abs=1e-9)
            assert back[1] == pytest.approx(lla[1], abs=1e-9)
            assert back[2] == pytest.approx(lla[2], abs=1e-6)


# ---------------------------------------------------------------------------
# Timecode reconstruction (section 6): only a backwards step close to 65536
# is a 16-bit rollover.
# ---------------------------------------------------------------------------


class TestReconstructTimecodes:
    def test_clean_stream_returned_unchanged(self):
        tcs = [1000, 1040, 1080, 1120]
        assert _gps_rs.reconstruct_timecodes(tcs) == tcs

    def test_clean_stream_with_large_forward_gap_untouched(self):
        # A legitimate forward gap beyond the 32768ms half-range must survive:
        # monotonic streams are never reconstructed.
        tcs = [1000, 1040, 200_000, 200_040]
        assert _gps_rs.reconstruct_timecodes(tcs) == tcs

    def test_true_rollover_advances_one_band(self):
        assert _gps_rs.reconstruct_timecodes([65440, 65480, 65520, 24, 64]) == [
            65440,
            65480,
            65520,
            65560,
            65600,
        ]

    def test_replayed_block_is_not_a_rollover(self):
        # The issue84 fault in miniature: a re-emitted block of records.
        tcs = [1000, 1040, 1080, 1000, 1040, 1080, 1120]
        assert _gps_rs.reconstruct_timecodes(tcs) == tcs

    def test_seam_jitter_is_not_a_rollover(self):
        tcs = [100, 200, 160, 240, 280]
        assert _gps_rs.reconstruct_timecodes(tcs) == tcs

    def test_straggler_after_rollover_resolves_pre_wrap(self):
        # A record from just before a rollover, arriving just after it, must
        # land at its true pre-wrap time rather than a whole band later.
        assert _gps_rs.reconstruct_timecodes([65500, 65530, 20, 65510, 50]) == [
            65500,
            65530,
            65556,
            65510,
            65586,
        ]

    def test_zero_dropout_record_absorbed(self):
        out = _gps_rs.reconstruct_timecodes([66063, 66103, 66143, 0, 66183, 66223])
        assert out[4] == 66183 and out[5] == 66223

    def test_upper_bits_garbage_reconstructs_from_low_16(self):
        truth = [500 + i * 40 for i in range(8)]
        corrupt = [(t & 0xFFFF) + (WRAP * 7 if i % 3 == 2 else 0) for i, t in enumerate(truth)]
        assert _gps_rs.reconstruct_timecodes(corrupt) == truth

    def test_low_16_bits_always_preserved(self):
        # The invariant that makes reconstruction sound: output is congruent to
        # input mod 65536, so no real sample time is ever altered.
        corrupt = [500, 540, 400, 65_000, 20, 60]
        out = _gps_rs.reconstruct_timecodes(corrupt)
        assert [t & 0xFFFF for t in out] == [t & 0xFFFF for t in corrupt]

    @pytest.mark.parametrize("tcs", [[], [42]])
    def test_degenerate_inputs(self, tcs):
        assert _gps_rs.reconstruct_timecodes(tcs) == tcs

    def test_decode_applies_the_reconstruction(self):
        t = _gps_rs.decode(records([65440, 65480, 65520, 24, 64]), 0)
        assert list(_tc(t)) == [65440, 65480, 65520, 65560, 65600]


# ---------------------------------------------------------------------------
# The 65533 ms timing bug (section 6)
# ---------------------------------------------------------------------------


def _clean(n, dt=40):
    return [i * dt for i in range(n)]


def _with_jump(n, at, jump, dt=40):
    tcs = _clean(n, dt)
    return tcs[:at] + [t + jump for t in tcs[at:]]


class TestGapDetection:
    def test_no_corrections_without_gaps(self):
        assert _gps_rs.detect_gap_corrections(_clean(100), _clean(100)) == []
        assert _gps_rs.detect_gap_corrections(_clean(100), [], 99 * 40) == []

    def test_direct_detection_of_the_jump(self):
        tcs = _with_jump(50, 25, 65533)
        assert _gps_rs.detect_gap_corrections(tcs, []) == [(tcs[24], 65533)]

    def test_legitimate_long_gap_is_not_the_bug(self):
        # A 30 s stop in the pits is left alone.
        assert _gps_rs.detect_gap_corrections(_with_jump(50, 25, 30_000), []) == []

    def test_gnfi_detects_the_jump(self):
        tcs = _with_jump(2500, 1001, 65533 - 40)
        assert _gps_rs.detect_gap_corrections(tcs, _clean(2500)) == [(tcs[1000], 65533 - 40)]

    def test_gnfi_detects_a_jump_hidden_in_signal_loss(self):
        # 10 s of samples lost around the jump: the gap is not the bug's own
        # size, but GNFI still ends 65533 ms before GPS, so the full overflow
        # is removed at the largest gap.
        tcs = _clean(2500)
        del tcs[1000:1250]
        tcs = tcs[:1000] + [t + 65533 for t in tcs[1000:]]
        assert _gps_rs.detect_gap_corrections(tcs, _clean(2500)) == [(tcs[999], 65533)]

    def test_gnfi_silent_when_clean_or_too_short(self):
        assert _gps_rs.detect_gap_corrections(_clean(2500), _clean(2500)) == []
        tcs = _with_jump(250, 100, 65533)
        # Fewer than two GNFI samples: no reference; the direct method still sees it.
        assert _gps_rs.detect_gap_corrections(tcs, [0]) == [(tcs[99], 65533)]

    def test_indirect_detection_from_non_gps_end(self):
        # A 5 s gap hides the jump; GPS ends 65533 ms after the other channels.
        tcs = _with_jump(200, 100, 5000 + 65533)
        non_gps_end = 199 * 40 + 5000
        assert _gps_rs.detect_gap_corrections(tcs, [], non_gps_end) == [(tcs[99], 65533)]
        assert _gps_rs.detect_gap_corrections(tcs, [], None) == []


class TestTimingFixInPipeline:
    def test_gap_is_repaired_on_every_channel(self):
        channels, _ = _process(records(_with_jump(200, 88, 65533 - 40)))
        reference = _tc(channels)
        assert np.all(np.diff(reference) == 40)
        for name in GPS_CHANNEL_NAMES:
            np.testing.assert_array_equal(_tc(channels, name), reference, name)

    def test_values_are_untouched_by_the_fix(self):
        data = records(_with_jump(200, 88, 65533 - 40), vel_cms=(300, 400, 0))
        fixed, _ = _process(data)
        plain = _gps_rs.decode(data, 0)
        for name in GPS_CHANNEL_NAMES:
            np.testing.assert_array_equal(_col(fixed, name), _col(plain, name), name)

    def test_gnfi_reference_is_used(self):
        tcs = _with_jump(200, 88, 65533 - 40)
        channels, _ = _process(records(tcs), gnfi_data=gnfi_records(_clean(200)))
        assert np.all(np.diff(_tc(channels)) == 40)

    def test_non_gps_end_reference_is_used(self):
        tcs = _with_jump(200, 100, 5000 + 65533)
        channels, _ = _process(records(tcs), non_gps_max_end_time=199 * 40 + 5000)
        assert _tc(channels)[-1] == 199 * 40 + 5000

    def test_two_jumps_are_both_repaired(self):
        tcs = _clean(300)
        tcs = tcs[:51] + [t + 65533 - 40 for t in tcs[51:]]
        tcs = tcs[:151] + [t + 65533 - 40 for t in tcs[151:]]
        channels, _ = _process(records(tcs))
        assert list(_tc(channels)) == _clean(300)

    def test_lap_message_laps_are_not_shifted(self):
        # LAP messages run on the logger clock, which the bug does not touch.
        laps = [(0, 0, 3000, "out"), (1, 3000, 70_000, "full"), (2, 70_000, 140_000, "full")]
        _, out = _process(records(_with_jump(200, 88, 65533 - 40)), laps)
        assert out == laps

    def test_single_sample_stream(self):
        channels, _ = _process(gps_record(0))
        assert list(_tc(channels)) == [0]


# ---------------------------------------------------------------------------
# Laps (section 7) and the whole pipeline on real material
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sfj():
    """Raw SFJ material (from the file's frames) and the Cython backend's log."""
    material = gps_synth.fixture_material(SFJ_XRK)
    log = cython_aim_xrk(str(SFJ_XRK))
    # Session start in logger time: the parsers take the minimum over the
    # LAP, data-stream and GPS first timecodes; recover it from the first
    # GPS record (the SFJ stream is clean) against the backend's timebase.
    raw_first = int.from_bytes(material["gps_data"][:4], "little", signed=True)
    material["time_offset"] = raw_first - log.channels["GPS Speed"].column("timecodes")[0].as_py()
    non_gps = [
        t.column("timecodes")[-1].as_py()
        for n, t in log.channels.items()
        if n not in GPS_CHANNEL_NAMES
    ]
    material["non_gps_max_end_time"] = max(non_gps)
    return material, log


def _lap_rows(table):
    return list(
        zip(
            table.column("num").to_pylist(),
            table.column("start_time").to_pylist(),
            table.column("end_time").to_pylist(),
            table.column("lap_type").to_pylist(),
        )
    )


class TestLapsOnSfj:
    def test_pipeline_over_raw_frames_matches_the_backend(self, sfj):
        material, log = sfj
        channels, laps = _process(**material, laps=_lap_rows(log.laps))
        assert tuple(channels) == GPS_CHANNEL_NAMES
        for name in GPS_CHANNEL_NAMES:
            assert channels[name].schema.equals(log.channels[name].schema, check_metadata=True)
            np.testing.assert_array_equal(_tc(channels, name), _tc(log.channels, name), name)
            np.testing.assert_array_equal(_col(channels, name), _col(log.channels, name), name)
        assert laps == _lap_rows(log.laps)

    def test_last_lap_far_from_start_finish_becomes_in(self, sfj):
        material, log = sfj
        rows = _lap_rows(log.laps)
        assert rows[-1][3] == "in", "fixture premise: SFJ ends 799 m from the line"
        unfinished = [(n, s, e, "full" if t == "in" else t) for n, s, e, t in rows]
        _, laps = _process(**material, laps=unfinished)
        assert laps == rows

    def test_an_out_lap_alone_is_never_reclassified(self, sfj):
        material, log = sfj
        n, s, e, _ = _lap_rows(log.laps)[-1]
        _, laps = _process(**material, laps=[(0, 0, e, "out")])
        assert laps == [(0, 0, e, "out")]

    def test_no_start_finish_marker_leaves_laps_alone(self, sfj):
        material, log = sfj
        rows = _lap_rows(log.laps)
        unfinished = [(n, s, e, "full" if t == "in" else t) for n, s, e, t in rows]
        _, laps = _process(**{**material, "start_finish": None}, laps=unfinished)
        assert laps == unfinished

    def test_gps_lap_detection_reproduces_the_beacon_laps(self, sfj):
        """With no LAP messages, laps come from the GPS track crossing the
        start/finish line. On SFJ every crossing lands within 1 ms of the
        beacon time the LAP messages recorded; the out-lap has no crossing
        at its start so detection begins one lap later, and the last lap
        ends at the final GPS sample, typed "in"."""
        material, log = sfj
        beacon = _lap_rows(log.laps)
        _, detected = _process(**material, laps=[])
        assert len(detected) == len(beacon) - 1
        for d, b in zip(detected, beacon[1:]):
            assert abs(d[1] - b[1]) <= 1, (d, b)
        assert [d[3] for d in detected] == ["full"] * (len(detected) - 1) + ["in"]
        assert [d[0] for d in detected] == list(range(len(detected)))
        assert detected[-1][2] == int(_tc(log.channels)[-1])

    def test_find_laps_crossing_times(self, sfj):
        material, log = sfj
        lat = _col(log.channels, "GPS Latitude").tolist()
        lon = _col(log.channels, "GPS Longitude").tolist()
        tcs = _tc(log.channels).tolist()
        crossings = _gps_rs.find_laps(lat, lon, tcs, material["start_finish"])
        beacon = [s for _, s, _, _ in _lap_rows(log.laps)[1:]]
        assert len(crossings) == len(beacon)
        assert all(abs(c - b) <= 1 for c, b in zip(crossings, beacon))
        with pytest.raises(ValueError):
            _gps_rs.find_laps(lat[:-1], lon, tcs, material["start_finish"])


class TestPythonWrapper:
    """libxrk.gps.process: the hand-over the Cython backend and the spec use."""

    def test_adds_gps_channels_and_builds_the_lap_table(self, sfj):
        material, log = sfj
        rpm = {"RPM": log.channels["RPM"]}
        laps_in = gps.laps_table([0], [0], [1000], ["out"])
        channels, laps = gps.process(
            rpm,
            laps_in,
            gps_data=material["gps_data"],
            gnfi_data=material["gnfi_data"],
            time_offset=material["time_offset"],
            start_finish=material["start_finish"],
        )
        assert set(channels) == {"RPM", *GPS_CHANNEL_NAMES}
        assert channels["RPM"] is rpm["RPM"]
        assert rpm == {"RPM": log.channels["RPM"]}, "the caller's dict is not modified"
        assert laps.schema.equals(log.laps.schema)
        assert laps.to_pylist() == [
            {"num": 0, "start_time": 0, "end_time": 1000, "lap_type": "out"}
        ]
        np.testing.assert_array_equal(
            _col(channels, "GPS Latitude"), _col(log.channels, "GPS Latitude")
        )

    def test_without_gps_material(self):
        laps_in = gps.laps_table([0, 1], [0, 500], [500, 900], ["out", "full"])
        channels, laps = gps.process(
            {}, laps_in, gps_data=b"", gnfi_data=b"", time_offset=0, start_finish=None
        )
        assert channels == {} and laps.equals(laps_in)
