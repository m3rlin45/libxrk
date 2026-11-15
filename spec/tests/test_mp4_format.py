"""Tests for the SmartyCam MP4 telemetry spec (spec/mp4_format.py).

The trimmed fixture (tests/test_data/smartycam/SCHD0487_aimd_trimmed.mp4) holds the
first 210 s of telemetry media time of SCHD0487.MP4: the camera CNF sample, the
syslog burst sample, two LAP messages and the exact aim_meta_data tail. The XRZ next
to it is the logger session recorded at the same time. Whole-file tests run when
LIBXRK_MP4_FIXTURE points at the original 1.9 GB MP4.
"""

import datetime
import struct
from collections import Counter

import pytest

from spec import isobmff
from spec.mp4_format import (
    AIMD_HEADER_SIZE,
    AimMetaDataTail,
    AimdSample,
    AimdSampleHeader,
    TrackSamples,
    gps_fix_utc,
    is_telemetry_track,
    link_report,
    parse_aim_meta_data_xml,
    parse_telemetry,
)
from spec.tests.conftest import SMARTYCAM_MP4


def _read(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class TestContainer:
    def test_top_level_boxes(self, smartycam_mp4):
        types = [b.type for b in smartycam_mp4.top_level_boxes]
        assert types == [b"ftyp", b"free", b"mdat", b"moov"]
        # The boxes are contiguous and the moov ends where the XML tail starts.
        for a, b in zip(smartycam_mp4.top_level_boxes, smartycam_mp4.top_level_boxes[1:]):
            assert a.end == b.offset

    def test_movie_header(self, smartycam_mp4):
        assert smartycam_mp4.movie_timescale == 6000
        # Camera local time (UTC+9); the XML tail's <hour> 02:00:56 is UTC.
        assert smartycam_mp4.creation_time == datetime.datetime(2026, 10, 25, 11, 0, 59)

    def test_telemetry_track_identity(self, smartycam_mp4):
        trk = smartycam_mp4.telemetry_track
        assert trk is not None
        assert isinstance(trk, TrackSamples)
        assert trk.track_id == 3
        # The adapter was filled from the generic container layer.
        assert is_telemetry_track(smartycam_mp4.movie.track_by_id(3))
        assert [is_telemetry_track(t) for t in smartycam_mp4.tracks] == [True]
        assert trk.handler_type == b"meta"
        assert trk.handler_name == "MetaAimHandler"
        assert trk.sample_format == b"aimd"
        # The aimd sample entry is the bare 16-byte SampleEntry: no extra fields.
        assert trk.sample_entry_raw == struct.pack(">I4s6sH", 16, b"aimd", bytes(6), 1)
        assert trk.media_timescale == 1000  # media time is milliseconds

    def test_telemetry_track_edit_list_and_tref(self, smartycam_mp4):
        trk = smartycam_mp4.telemetry_track
        box_track = smartycam_mp4.movie.track_by_id(3)
        # Leading empty edit of 631 movie ticks (105.2 ms) then one full edit.
        assert [(e.segment_duration, e.media_time) for e in box_track.edit_list][0] == (631, -1)
        assert box_track.edit_list[1].media_time == 0
        assert box_track.empty_edit_duration() == 631 == trk.empty_edit_ticks
        assert trk.empty_edit_ms() == pytest.approx(105.1667, abs=1e-3)
        # The track references itself as a 'tmcd' (timecode) track.
        assert trk.track_references == [(b"tmcd", [3])]

    def test_sample_tables(self, smartycam_mp4):
        trk = smartycam_mp4.telemetry_track
        box_track = smartycam_mp4.movie.track_by_id(3)
        assert box_track.stsc == [(1, 3, 1)]  # three samples per chunk
        assert trk.sample_count == box_track.sample_count == len(smartycam_mp4.samples)
        assert trk.sizes == box_track.sample_sizes
        deltas = Counter(d for _, d in box_track.stts)
        assert set(deltas) <= {100, 101, 102, 103}  # ~10 Hz
        # Resolved offsets are increasing and inside mdat.
        mdat = next(b for b in smartycam_mp4.top_level_boxes if b.type == b"mdat")
        offs = trk.offsets
        assert offs == box_track.sample_offsets()
        assert all(a < b for a, b in zip(offs, offs[1:]))
        assert offs[0] >= mdat.payload_offset
        assert offs[-1] + trk.sizes[-1] <= mdat.end
        assert trk.dts == box_track.sample_dts()


# ---------------------------------------------------------------------------
# aimd sample header
# ---------------------------------------------------------------------------


class TestAimdSampleHeader:
    def test_every_sample_round_trips(self, smartycam_mp4):
        for s in smartycam_mp4.samples:
            raw = _read(smartycam_mp4.path, s.offset, s.size)
            parsed = AimdSample.parse(raw)
            assert AimdSample.build(parsed) == raw
            assert len(parsed.payload) == s.payload_length == s.size - AIMD_HEADER_SIZE

    def test_header_invariants(self, smartycam_mp4):
        for s in smartycam_mp4.samples:
            raw = _read(smartycam_mp4.path, s.offset, AIMD_HEADER_SIZE)
            assert struct.unpack(">H", raw[0:2])[0] == (s.size - 2) & 0xFFFF
            assert raw[2:6] == b"\x40\x00\x00\x00"
            assert raw[6:10] == b"amv0"
            assert raw[26:62] == bytes(36)
            assert struct.unpack("<I", raw[62:66])[0] == s.size - AIMD_HEADER_SIZE
            hdr = AimdSampleHeader.parse(raw)
            assert hdr.logger_time_us == s.logger_time_us
            assert hdr.media_time_us == s.media_time_us

    def test_length_field_wraps_at_16_bits(self, smartycam_mp4):
        big = [s for s in smartycam_mp4.samples if s.size > 0xFFFF]
        assert len(big) == 1  # the syslog burst (sample 7, 71 818 bytes)
        raw = _read(smartycam_mp4.path, big[0].offset, 2)
        assert struct.unpack(">H", raw)[0] == (big[0].size - 2) - 0x10000

    def test_header_clocks(self, smartycam_mp4):
        regular = [s for s in smartycam_mp4.samples if s.index > 0]
        # Both header clocks are strictly increasing on regular samples.
        for a, b in zip(regular, regular[1:]):
            assert a.logger_time_us < b.logger_time_us
            assert a.media_time_us < b.media_time_us
        # Header media time tracks the stts DTS within ~11-12 ms.
        for s in regular:
            assert 10.0 <= s.media_time_us / 1000.0 - s.dts <= 13.0
        # Sample 0's media time is the known outlier.
        assert smartycam_mp4.samples[0].media_time_us > 10_000_000
        assert smartycam_mp4.samples[1].media_time_us < 200_000

    def test_logger_clock_matches_payload_timecodes(self, smartycam_mp4):
        # The header logger clock (us) is just after the newest timecode (ms) in the payload.
        for s in smartycam_mp4.samples[1:200]:
            raw = _read(smartycam_mp4.path, s.payload_offset, s.payload_length)
            tcs = [
                struct.unpack("<i", raw[i + 2 : i + 6])[0]
                for i in range(len(raw) - 6)
                if raw[i : i + 2] == b"(S"
            ]
            if not tcs:
                continue
            newest = max(tcs) * 1000
            assert 0 <= s.logger_time_us - newest < 150_000

    def test_sample_zero_is_the_camera_cnf(self, smartycam_mp4):
        s0 = smartycam_mp4.samples[0]
        raw = _read(smartycam_mp4.path, s0.payload_offset, 5)
        assert raw == b"<hCNF"


# ---------------------------------------------------------------------------
# Trailing aim_meta_data pseudo-box
# ---------------------------------------------------------------------------


class TestMetaDataTail:
    def test_tail_round_trip_and_quirk(self, smartycam_mp4):
        moov = smartycam_mp4.top_level_boxes[-1]
        tail = _read(smartycam_mp4.path, moov.end, smartycam_mp4.file_size - moov.end)
        assert tail[:8] == b"4096free"  # ASCII "4096" where a uint32 size belongs
        assert struct.unpack(">I", tail[:4])[0] == 875575606  # what a naive walker reads
        assert len(tail) == 1025  # 8 + 1017 bytes of XML; not padded to 4096
        parsed = AimMetaDataTail.parse(tail)
        assert AimMetaDataTail.build(parsed) == tail
        assert bytes(parsed.xml) == smartycam_mp4.meta_data_raw
        assert smartycam_mp4.meta_data_raw.startswith(b'<?xml version="1.0"?>\n<aim_meta_data')

    def test_xml_fields(self, smartycam_mp4):
        md = smartycam_mp4.meta_data
        assert md["_root"] == {"version": "0.0.0"}
        gi = md["generic_info"]
        assert gi["track_name"] == "Tsukuba_Car"
        assert (int(gi["track_lat"]) / 1e7, int(gi["track_lon"]) / 1e7) == (36.150084, 139.9193874)
        assert gi["date"] == "25/10/2026" and gi["hour"] == "02:00:56"
        assert gi["file_duration"] == "1487"
        assert gi["nlap"] == "20" and gi["best"] == "59218" and gi["nbest"] == "16"
        assert gi["hwserial"] == "4118157" and gi["release"] == "01.17.08"
        hw = md["hw_info"]
        assert hw["machine"] == "sc3gp" and hw["meccaniche"] == "Smarty3_GP"
        assert hw["logger_code"] == "613" and hw["lens"] == ""
        assert md["audio_conf"] == {"mic_int_gain": "5", "mic_ext_gain": "0"}

    def test_xml_parser_rejects_other_roots(self):
        with pytest.raises(ValueError):
            parse_aim_meta_data_xml(b"<other/>")


# ---------------------------------------------------------------------------
# The XRK message stream inside the samples
# ---------------------------------------------------------------------------


class TestTelemetryStream:
    def test_parses_cleanly(self, smartycam_mp4):
        tel = smartycam_mp4.telemetry
        assert tel.leftover_bytes == 0
        assert Counter(m.msg_type for m in tel.data_messages()).keys() == {"S"}

    def test_header_tokens(self, smartycam_mp4):
        tel = smartycam_mp4.telemetry
        tally = Counter(m.token_str() for m in tel.header_messages())
        for tok in ("CNF", "RCR", "VEH", "CMP", "VTY", "NDV", "SRC", "TRK", "TMD", "TMT"):
            assert tally[tok] == 1, tok
        assert tally["LAP"] == 2  # trimmed at 210 s: laps 1 and 2
        assert tally["GPS"] > 5000  # 25 Hz
        assert tally["XLOG"] > 0
        assert set(tally) <= {
            "CNF",
            "RCR",
            "VEH",
            "CMP",
            "VTY",
            "NDV",
            "SRC",
            "TRK",
            "TMD",
            "TMT",
            "LAP",
            "GPS",
            "XLOG",
        }

    def test_camera_cnf_channels(self, smartycam_mp4):
        tel = smartycam_mp4.telemetry
        chs = tel.find_nested_messages("CHS")
        assert len(chs) == 40
        names = tel.channel_display_names()
        assert names[0] == "LoggerTemp"
        assert names[12] == "Master_Clock"
        assert names[35] == "Engine_RPM" and names[36] == "Speed" and names[37] == "Gear"
        assert names[54] == "Heart_Rate" and names[55] == "GPS0"
        # CHS [4:6] carries the camera's logger_code (613) on all but one channel.
        assert Counter(c.payload.hardware_id for c in chs) == {613: 39, 2120: 1}
        # Every CHS/CDE pair lives inside the single CNF container.
        assert len(tel.find_nested_messages("CDE")) == 40

    def test_data_samples_are_float32_on_camera_channels(self, smartycam_mp4):
        tel = smartycam_mp4.telemetry
        used = Counter(m.parsed["channel_index"] for m in tel.data_messages())
        assert set(used) <= set(tel.channels)
        for m in tel.data_messages()[:5000]:
            assert len(m.parsed["data"]) == tel.channel_sizes[m.parsed["channel_index"]]
        assert tel.channel_sizes[36] == 4  # Speed: float32

    def test_identity_messages(self, smartycam_mp4):
        mp4 = smartycam_mp4
        tel = mp4.telemetry
        for tok in ("RCR", "VEH", "CMP", "VTY", "NDV"):
            assert tel.find_nested_messages(tok)[0].payload == ""
        assert tel.find_nested_messages("TMD")[0].payload == "10/25/2026"
        assert tel.find_nested_messages("TMT")[0].payload == "02:00:56"  # UTC
        assert mp4.camera_identity() == {"model_id": 635, "serial": 4118157}
        assert mp4.track_info() == {
            "name": "Tsukuba_Car",
            "sf_lat": 36.150084,
            "sf_long": 139.9193874,
        }

    def test_xlog_is_the_camera_syslog(self, smartycam_mp4):
        xlog = smartycam_mp4.telemetry.xlog_payloads
        assert xlog
        # The boot burst (sample 7) is sliced into 1000-byte chunks; later samples
        # carry whatever syslog lines arrived since the previous sample.
        assert all(0 < len(chunk) <= 1000 for chunk in xlog)
        assert sum(1 for chunk in xlog if len(chunk) == 1000) >= 60
        assert xlog[0].startswith(b"Oct 25 11:00:54 sc3gp syslog-ng")
        assert any(b"aim-main-app: INFO:  LAP: n.2" in chunk for chunk in xlog)

    def test_gps_timebase_is_the_logger_clock(self, smartycam_mp4):
        gps = smartycam_mp4.telemetry.get_gps_samples()
        assert gps[0].week == 2442 and gps[0].iTOW == 7275480 and gps[0].timecode == 14601
        assert gps_fix_utc(gps[0].week, gps[0].iTOW) == datetime.datetime(
            2026, 10, 25, 2, 0, 57, 480000
        )


# ---------------------------------------------------------------------------
# Linking to the XRK/XRZ of the same session
# ---------------------------------------------------------------------------


class TestLinking:
    def test_laps_are_the_loggers(self, smartycam_mp4, smartycam_xrz_parsed):
        mp4_laps = smartycam_mp4.telemetry.get_laps()
        xrk_laps = smartycam_xrz_parsed.get_laps()
        assert len(mp4_laps) == 2
        assert mp4_laps == xrk_laps[:2]
        assert mp4_laps[0] == {"segment": 0, "lap_num": 1, "duration": 46658, "end_time": 49202}

    def test_gps_fixes_are_the_loggers(self, smartycam_mp4, smartycam_xrz_parsed):
        mg = {s.iTOW: s for s in smartycam_mp4.telemetry.get_gps_samples()}
        xg = {s.iTOW: s for s in smartycam_xrz_parsed.get_gps_samples()}
        shared = set(mg) & set(xg)
        assert len(shared) > 5000
        for k in shared:
            a, b = mg[k], xg[k]
            assert a.timecode == b.timecode  # same logger clock
            assert (a.ecefX, a.ecefY, a.ecefZ) == (b.ecefX, b.ecefY, b.ecefZ)
            assert a.week == b.week

    def test_link_report(self, smartycam_mp4, smartycam_xrz_parsed):
        rep = link_report(smartycam_mp4, smartycam_xrz_parsed)
        assert rep["laps_identical"] is False  # trimmed file has 2 of 20 laps
        assert rep["common_laps"] == 2
        assert rep["gps_shared"] > 0 and rep["gps_shared_identical"] == rep["gps_shared"]
        assert rep["track_match"] is True
        assert rep["mp4_date"] == rep["xrk_date"] == "10/25/2026"
        assert rep["mp4_time_utc"] == "02:00:56" and rep["xrk_time_local"] == "10:59:46"
        assert rep["matched"] is True

    def test_link_keys(self, smartycam_mp4):
        keys = smartycam_mp4.link_keys()
        assert keys["laps"][0] == (1, 46658, 49202)
        assert keys["gps_first"] == {"week": 2442, "iTOW": 7275480, "timecode": 14601}
        assert keys["camera"] == {"model_id": 635, "serial": 4118157}
        assert keys["meta"]["hwserial"] == "4118157" and keys["meta"]["nlap"] == "20"


# ---------------------------------------------------------------------------
# Time mapping
# ---------------------------------------------------------------------------


class TestTimeMapping:
    def test_media_zero_is_logger_offset(self, smartycam_mp4):
        assert smartycam_mp4.media_us_to_logger_ms(0) == pytest.approx(14632.835, abs=0.001)

    def test_round_trip(self, smartycam_mp4):
        for lap in smartycam_mp4.telemetry.get_laps():
            end = lap["end_time"]
            media = smartycam_mp4.logger_ms_to_media_us(end)
            assert smartycam_mp4.media_us_to_logger_ms(media) == pytest.approx(end, abs=1e-3)
            movie = smartycam_mp4.logger_ms_to_movie_ms(end)
            assert smartycam_mp4.movie_ms_to_logger_ms(movie) == pytest.approx(end, abs=1e-3)

    def test_movie_time_applies_empty_edit(self, smartycam_mp4):
        media_ms = smartycam_mp4.logger_ms_to_media_us(49202) / 1000.0
        movie_ms = smartycam_mp4.logger_ms_to_movie_ms(49202)
        assert movie_ms - media_ms == pytest.approx(631 * 1000 / 6000, abs=1e-6)
        assert movie_ms == pytest.approx(34674.33, abs=0.05)

    def test_offset_drifts_slowly(self, smartycam_mp4):
        table = smartycam_mp4.clock_offset_table()
        offsets = [o for _, o in table]
        assert offsets[0] == 14632835
        # Positive drift of a few ppm over the trimmed 210 s (~100 ms over 25 min on the full file).
        assert 0 <= offsets[-1] - offsets[0] < 30_000
        assert table == sorted(table)


# ---------------------------------------------------------------------------
# Layering: the AIM layer only needs a TrackSamples, not spec.isobmff
# ---------------------------------------------------------------------------


class TestLayering:
    def test_parse_telemetry_from_hand_built_track_samples(self, smartycam_mp4):
        """Simulate a different MP4 parser: hand the AIM layer a TrackSamples it filled itself."""
        ref = smartycam_mp4.telemetry_track
        handmade = TrackSamples(
            track_id=ref.track_id,
            handler_type=b"meta",
            handler_name="MetaAimHandler",
            sample_format=b"aimd",
            sample_entry_raw=None,
            media_timescale=1000,
            movie_timescale=6000,
            empty_edit_ticks=631,
            offsets=list(ref.offsets),
            sizes=list(ref.sizes),
            dts=list(ref.dts),
        )
        with open(SMARTYCAM_MP4, "rb") as f:
            res = parse_telemetry(
                f,
                handmade,
                meta_tail_offset=smartycam_mp4.movie.trailing_offset,
                file_size=smartycam_mp4.file_size,
            )
        assert res.movie is None  # no container layer involved
        assert len(res.samples) == len(smartycam_mp4.samples)
        assert res.telemetry.get_laps() == smartycam_mp4.telemetry.get_laps()
        assert res.meta_data == smartycam_mp4.meta_data
        assert res.media_us_to_logger_ms(0) == smartycam_mp4.media_us_to_logger_ms(0)
        assert res.logger_ms_to_movie_ms(49202) == smartycam_mp4.logger_ms_to_movie_ms(49202)
        assert res.movie_timescale == 6000

    def test_isobmff_layer_is_aim_agnostic(self, smartycam_mp4):
        movie = isobmff.read_movie(SMARTYCAM_MP4)
        assert [t.track_id for t in movie.tracks] == [3]
        assert movie.trailing_offset == smartycam_mp4.top_level_boxes[-1].end
        assert movie.file_size - movie.trailing_offset == 1025
        # The container layer carries no camera knowledge and does not import the AIM layer.
        src = open(isobmff.__file__).read()
        imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        assert not any("spec." in ln for ln in imports), imports
        assert "aimd" not in src and "MetaAimHandler" not in src and "amv0" not in src


# ---------------------------------------------------------------------------
# Whole-file tests (LIBXRK_MP4_FIXTURE)
# ---------------------------------------------------------------------------


class TestFullFile:
    def test_counts(self, smartycam_full_mp4):
        mp4 = smartycam_full_mp4
        assert len(mp4.samples) == 14850
        assert [t.handler_type for t in mp4.tracks] == [b"vide", b"soun", b"meta"]
        assert mp4.movie_duration == 8932969
        tel = mp4.telemetry
        assert tel.leftover_bytes == 0
        assert len(tel.get_laps()) == 20
        assert len(tel.get_gps_samples()) == 36592
        assert len(tel.xlog_payloads) == 442

    def test_full_link(self, smartycam_full_mp4, smartycam_xrz_parsed):
        rep = link_report(smartycam_full_mp4, smartycam_xrz_parsed)
        assert rep["laps_identical"] is True
        assert rep["gps_shared"] == 36549 == rep["gps_shared_identical"]
        assert rep["matched"] is True

    def test_full_drift(self, smartycam_full_mp4):
        table = smartycam_full_mp4.clock_offset_table()
        assert table[0][1] == 14632835 and table[-1][1] == 14735908
        assert smartycam_full_mp4.media_us_to_logger_ms(table[-1][0]) == pytest.approx(
            1503370.509, abs=0.001
        )
