"""Parse validation and cross-validation tests for the XRK Construct spec.

Tests in this module verify that:
1. All test files parse without errors or leftover bytes
2. All header message checksums validate correctly
3. Construct-parsed values match the Cython parser for all fields
"""

import functools
import math
import struct
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pytest

from spec.xrk_format import (
    CHSPayload,
    NAMED_VALUE_DTYPES,
    SAMPLE_CODECS,
    TIMECODE_DTYPE,
    _decompress_if_zlib,
    channel_sample_codec,
    channel_stream,
    channel_value_dtype,
    chs_padding_bytes,
    decode_channel_values,
    GMessageCompiled,
    MMessageCompiled,
    ParseResult,
)
from spec.tests.conftest import ALL_XRK_FILES, ISSUE84_XRZ, SFJ_XRK, _BACKENDS
from tests import xrk_patch
from libxrk._aim_xrk_rs import process_gps
from libxrk import GPS_CHANNEL_NAMES


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_gps_pipeline(parsed):
    """Run the shared GPS pipeline over the spec's parse.

    Returns (gps channels {name: pa.Table}, finished lap table as a list of
    dicts). The spec contributes only raw material -- lap_table() from the
    LAP messages and gps_input() (GPS/GNFI records, time offset, TRK
    marker, end of the non-GPS streams) -- exactly as a backend does.
    """
    rows = [(r["num"], r["start_time"], r["end_time"], r["lap_type"]) for r in parsed.lap_table()]
    channels, laps = process_gps(laps=rows, **parsed.gps_input())
    return channels, laps.to_pylist()


def _assert_tables_identical(a, b, label):
    """Same schema (field metadata included) and bit-identical columns (NaN == NaN)."""
    assert a.schema.equals(b.schema, check_metadata=True), f"{label}: schema differs"
    for col in a.column_names:
        x = a.column(col).to_numpy(zero_copy_only=False)
        y = b.column(col).to_numpy(zero_copy_only=False)
        assert np.array_equal(
            x, y, equal_nan=np.issubdtype(x.dtype, np.floating)
        ), f"{label}.{col}: values differ"


def _backend_channel_names(parsed):
    """{channel_index: the name a backend exposes the CHS channel under}.

    channel_display_names() with the GPS channel names reserved when the file
    carries GPS data, which is what both backends do.
    """
    return parsed.channel_display_names(GPS_CHANNEL_NAMES if parsed.gps_payloads else ())


def _differing(a, b):
    """Indices where two arrays of one dtype and length differ (a NaN equals a NaN)."""
    same = a == b
    if a.dtype.kind == "f":
        same |= np.isnan(a) & np.isnan(b)
    return np.flatnonzero(~same)


def _decode_and_fixup(data_bytes, ch):
    """Decode one raw sample with the spec's channel codec (float), or None."""
    codec = channel_sample_codec(ch)
    if codec is None:
        return None
    size = codec.sizeof()
    if len(data_bytes) < size:
        return None
    return float(codec.parse(data_bytes[:size]))


def _unique_seg0_laps(parsed):
    """Filter segment 0 laps and deduplicate by lap_num."""
    laps = parsed.get_laps()
    seg0 = [l for l in laps if l["segment"] == 0]
    seen = set()
    unique = []
    for l in seg0:
        if l["lap_num"] not in seen:
            seen.add(l["lap_num"])
            unique.append(l)
    return unique


# ---------------------------------------------------------------------------
# Parse validation — all test files
# ---------------------------------------------------------------------------


class TestParseAllFiles:
    """Verify that all test files parse successfully."""

    def test_parse_validates(self, any_parsed):
        """Each file should parse completely and contain expected message types."""
        result = any_parsed
        assert isinstance(result, ParseResult)
        assert len(result.messages) > 0
        assert result.leftover_bytes < 20, f"{result.leftover_bytes} leftover bytes"
        assert len(result.header_messages()) > 0
        assert len(result.channels) > 0
        assert len(result.messages_by_token("LAP")) > 0
        assert len(result.gps_payloads) > 0

    def test_cnf_redefinition_last_name_wins(self, aim_official_parsed):
        """A later CNF re-definition wins on names: test.xrk's final CNFs
        rename index 11 'Temperature 1' -> 'Exhaust Temp' and index 12
        'Temperature 2' -> 'Water Temp 2' (null-terminated to 'Water Temp'),
        which is what the official DLL exposes."""
        assert aim_official_parsed.channels[11].name == "Exhaust Temp"
        assert aim_official_parsed.channels[12].name == "Water Temp"

    @pytest.mark.parametrize("filepath", ALL_XRK_FILES, ids=lambda p: p.name)
    def test_xrk_xrz_equivalence(self, filepath):
        """An XRZ is its XRK twin zlib-compressed. Decompressed, it is a
        strict prefix of the XRK: the only difference is a short trailer of
        RaceStudio-appended session metadata strings (RCR, VEH, CMP, VTY,
        NTE) that the XRK gained after the XRZ was exported. So every
        parse-level property proven on the XRK fixture (channel definitions,
        data messages) holds for the XRZ without parsing it a second time."""
        xrz = filepath.with_suffix(".xrz")
        if not xrz.exists():
            pytest.skip(f"No XRZ counterpart for {filepath.name}")
        xrk_bytes = filepath.read_bytes()
        xrz_bytes = _decompress_if_zlib(xrz.read_bytes())
        assert xrk_bytes.startswith(xrz_bytes)
        trailer = xrk_bytes[len(xrz_bytes) :]
        assert len(trailer) < 1024, f"unexpected {len(trailer)}-byte trailer"
        assert trailer == b"" or trailer.startswith(b"<h"), trailer[:16]


# ---------------------------------------------------------------------------
# Cross-validation: CHS channel definitions
# ---------------------------------------------------------------------------


class TestCHSCrossValidation:
    """Compare Construct-parsed CHS fields against Cython parser output."""

    def _get_channel_metadata(self, cython_log, channel_name):
        """Extract metadata dict from Cython-parsed channel."""
        if channel_name not in cython_log.channels:
            return None
        table = cython_log.channels[channel_name]
        field = table.schema.field(channel_name)
        md = field.metadata or {}
        return {
            "units": md.get(b"units", b"").decode(),
            "dec_pts": md.get(b"dec_pts", b"").decode(),
            "interpolate": md.get(b"interpolate", b"").decode(),
            "source_type": md.get(b"source_type", b"").decode(),
            "source_channel_id": md.get(b"source_channel_id", b"").decode(),
            "device_tag": md.get(b"device_tag", b"").decode(),
            "cal_value_1": md.get(b"cal_value_1", b"").decode(),
            "cal_value_2": md.get(b"cal_value_2", b"").decode(),
            "display_range_min": md.get(b"display_range_min", b"").decode(),
            "display_range_max": md.get(b"display_range_max", b"").decode(),
        }

    # GPS-derived channels the backends synthesize (process_gps) rather than
    # take from a CHS definition. Their metadata won't match CHS values.
    _GPS_COMPUTED_CHANNELS = {
        "GPS Speed",
        "GPS Latitude",
        "GPS Longitude",
        "GPS Altitude",
        "GPS_Satellites",
        "GPS_Fix",
        "GPS_pDOP",
        "GPS_Position_Accuracy",
        "GPS_Velocity_Accuracy",
        "GPS_InlineAcc",
        "GPS_LateralAcc",
        "GPS_Yaw_Rate",
    }

    def test_channel_names(self, parsed_and_backend):
        """All CHS long_names should match channels known to Cython parser."""
        parsed, cython_log = parsed_and_backend
        spec_names = {ch.name for ch in parsed.channels.values()}
        for name in cython_log.channels:
            # GPS-derived channels don't have CHS entries
            if name.startswith("GPS ") or name.startswith("GPS_"):
                continue
            assert name in spec_names, f"Cython channel {name!r} not in spec CHS definitions"

    def test_channel_metadata(self, parsed_and_backend):
        """Every CHS metadata field should match the Cython parser."""
        parsed, cython_log = parsed_and_backend
        for idx, ch in parsed.channels.items():
            name = ch.name
            if name in self._GPS_COMPUTED_CHANNELS:
                continue  # GPS-derived; synthesized by process_gps, not from CHS
            md = self._get_channel_metadata(cython_log, name)
            if md is None:
                continue  # Channel not exposed by Cython (e.g., Master Clk)

            # Units (Cython stores empty string for size==1 channels)
            expected_units = md["units"]
            if ch.data_size == 1:
                expected_units = ""
            assert (
                ch.units == expected_units
            ), f"Ch {name!r}: units {ch.units!r} != {expected_units!r}"

            # Decimal points
            assert str(ch.dec_pts) == md["dec_pts"], f"Ch {name!r}: dec_pts mismatch"

            # Interpolate
            assert str(ch.interpolate) == md["interpolate"], f"Ch {name!r}: interpolate mismatch"

            # Source type
            assert str(ch.source_type) == md["source_type"], f"Ch {name!r}: source_type mismatch"

            # Source channel ID
            assert (
                str(ch.source_channel_id) == md["source_channel_id"]
            ), f"Ch {name!r}: source_channel_id mismatch"

            # Device tag
            assert ch.device_tag_str == md["device_tag"], f"Ch {name!r}: device_tag mismatch"

            # Calibration values
            assert str(ch.cal_value_1) == md["cal_value_1"], f"Ch {name!r}: cal_value_1 mismatch"
            assert str(ch.cal_value_2) == md["cal_value_2"], f"Ch {name!r}: cal_value_2 mismatch"

            # Display range
            assert (
                str(ch.display_range_min) == md["display_range_min"]
            ), f"Ch {name!r}: display_range_min mismatch"
            assert (
                str(ch.display_range_max) == md["display_range_max"]
            ), f"Ch {name!r}: display_range_max mismatch"

    def test_chs_padding_is_zero(self, any_parsed):
        """All CHS padding fields should be zero across all test files."""
        result = any_parsed
        for idx, ch in result.channels.items():
            padding = chs_padding_bytes(ch)
            if any(padding):
                name = ch.name
                nonzero = [(i, b) for i, b in enumerate(padding) if b != 0]
                pytest.fail(f"CHS padding non-zero for {name!r}: {nonzero}")

    def test_chs_data_size_consistent(self, any_parsed):
        """CHS data_size should match decoder_type expectations."""
        result = any_parsed
        for idx, ch in result.channels.items():
            dt = ch.decoder_type
            if dt in SAMPLE_CODECS:
                expected_size = SAMPLE_CODECS[dt].sizeof()
                # Some channels have larger data_size than the decoded type
                # (e.g., Lap Time has size=20 but decoder=31 which is 'i'=4)
                # The data_size can be larger when the channel carries extra data
                assert ch.data_size >= expected_size, (
                    f"Ch {ch.name!r}: data_size {ch.data_size} < "
                    f"decoder type {dt} expects {expected_size}"
                )


# ---------------------------------------------------------------------------
# Cross-validation: GRP group definitions
# ---------------------------------------------------------------------------


class TestGRPCrossValidation:
    """Verify group definitions are consistent."""

    def test_86_has_groups(self, file_86_parsed):
        """86 file should have group definitions (CAN bus channels)."""
        assert len(file_86_parsed.groups) > 0

    def test_group_sizes_match_channel_sum(self, file_86_parsed):
        """Group payload size should equal sum of member channel data_sizes."""
        for idx, grp in file_86_parsed.groups.items():
            expected = sum(file_86_parsed.channel_sizes.get(ch, 0) for ch in grp.channel_indices)
            actual = file_86_parsed.group_sizes[idx]
            assert actual == expected, f"Group {idx}: size {actual} != sum {expected}"

    def test_group_channel_indices_valid(self, file_86_parsed):
        """All channel indices in groups should reference valid channels."""
        for idx, grp in file_86_parsed.groups.items():
            for ch_idx in grp.channel_indices:
                assert (
                    ch_idx in file_86_parsed.channels
                ), f"Group {idx}: channel index {ch_idx} not defined"


# ---------------------------------------------------------------------------
# Cross-validation: GPS messages
# ---------------------------------------------------------------------------


class TestGPSCrossValidation:
    """Verify GPS message parsing matches Cython output."""

    def test_gps_channels_from_spec_material_match_backend(self, parsed_and_backend):
        """A parser only collects GPS material (GPS and GNFI records, time
        offset, TRK marker); the GPS channels are made from it by the one
        shared pipeline (libxrk._aim_xrk_rs.process_gps). Running that pipeline over the
        spec's material must give the backend's tables bit for bit, metadata
        included: a difference means the parsers disagree on the material.
        The pipeline itself is tested on its own in tests/test_gps_processing.py."""
        parsed, log = parsed_and_backend
        channels, _ = _run_gps_pipeline(parsed)
        assert set(channels) == set(GPS_CHANNEL_NAMES)
        for name in GPS_CHANNEL_NAMES:
            _assert_tables_identical(channels[name], log.channels[name], name)

    def test_gps_count(self, parsed_and_backend):
        """GPS message count should match Cython channel row count."""
        parsed, cython_log = parsed_and_backend
        spec_count = len(parsed.gps_payloads)
        cython_count = len(cython_log.channels["GPS Speed"])
        assert spec_count == cython_count

    def test_gps_first_sample(self, parsed_only):
        """First GPS sample should have reasonable values."""
        samples = parsed_only.get_gps_samples()
        assert len(samples) > 0
        first = samples[0]
        assert first.gpsFix in (0, 2, 3)
        assert first.numSV >= 0
        assert first.pDOP > 0

    def test_gps_all_fields_present(self, sfj_parsed):
        """Every GPS field should be present in the parsed samples."""
        samples = sfj_parsed.get_gps_samples()
        first = samples[0]
        expected_fields = [
            "timecode",
            "iTOW",
            "fTOW",
            "week",
            "gpsFix",
            "flags",
            "ecefX",
            "ecefY",
            "ecefZ",
            "pAcc",
            "ecefVX",
            "ecefVY",
            "ecefVZ",
            "sAcc",
            "pDOP",
            "reserved1",
            "numSV",
            "reserved2",
        ]
        for field in expected_fields:
            assert hasattr(first, field), f"GPS sample missing field {field!r}"


# ---------------------------------------------------------------------------
# Cross-validation: LAP messages
# ---------------------------------------------------------------------------


class TestLAPCrossValidation:
    """Verify LAP messages match Cython laps table."""

    def test_lap_count(self, parsed_and_backend):
        """Lap count should match Cython laps table."""
        parsed, cython_log = parsed_and_backend
        unique_laps = _unique_seg0_laps(parsed)
        assert len(unique_laps) == len(cython_log.laps)

    def test_aim_official_lap_count(self, aim_official_parsed, aim_official_backend):
        # aim_official carries v2 (32-byte) LAP payloads; both the spec and
        # the backends read the absolute end time at [28:32].
        assert len(_unique_seg0_laps(aim_official_parsed)) == len(aim_official_backend.laps)

    _LAP_TABLE_FIXTURES = [
        "sfj",
        "sfj_0101",
        "sfj_suzuka",
        "file_86",
        "aim_official",
        "issue49",
        "issue68",
        "issue84",
        "issue80",
    ]

    @staticmethod
    def _backend_lap_table(log):
        t = log.laps
        return [
            {"num": n, "start_time": s, "end_time": e, "lap_type": k}
            for n, s, e, k in zip(
                t.column("num").to_pylist(),
                t.column("start_time").to_pylist(),
                t.column("end_time").to_pylist(),
                t.column("lap_type").to_pylist(),
            )
        ]

    @pytest.mark.parametrize("name", _LAP_TABLE_FIXTURES)
    def test_lap_table_matches_backend(self, name, backend, request):
        # `backend` is requested directly so its session params are defined
        # for this test; getfixturevalue alone cannot parametrize.
        """ParseResult.lap_table() (what the LAP messages say, companion.md
        section 7) finished by the shared GPS pipeline (the in-lap
        classification, or GPS lap detection for a file without LAP
        messages) is the lap table both backends expose: numbers,
        session-relative times and out/full/in types. Runs once per backend
        via the session `backend` param."""
        parsed = request.getfixturevalue(f"{name}_parsed")
        log = request.getfixturevalue(f"{name}_backend")
        _, laps = _run_gps_pipeline(parsed)
        assert laps == self._backend_lap_table(log)

    def test_sfj_lap_times(self, sfj_parsed, sfj_backend):
        """SFJ lap end_time and duration should produce matching start/end times."""
        unique = _unique_seg0_laps(sfj_parsed)

        # Compute time_offset from first lap (matches Cython logic)
        first_lap = unique[0]
        time_offset = first_lap["end_time"] - first_lap["duration"]

        cython_laps = sfj_backend.laps
        for i, lap in enumerate(unique):
            start = lap["end_time"] - lap["duration"] - time_offset
            end = lap["end_time"] - time_offset
            cython_start = cython_laps.column("start_time")[i].as_py()
            cython_end = cython_laps.column("end_time")[i].as_py()
            assert abs(start - cython_start) <= 1, f"Lap {i} start: {start} vs {cython_start}"
            assert abs(end - cython_end) <= 1, f"Lap {i} end: {end} vs {cython_end}"


# ---------------------------------------------------------------------------
# Cross-validation: Metadata (string messages, idn, TRK, etc.)
# ---------------------------------------------------------------------------


class TestMetadataCrossValidation:
    """Verify metadata from string and structured messages."""

    def test_sfj_string_metadata(self, sfj_parsed, sfj_backend):
        """String metadata should match Cython output for SFJ."""
        # Map token -> metadata key
        string_checks = {
            "RCR": "Driver",
            "VEH": "Vehicle",
            "TMD": "Log Date",
            "TMT": "Log Time",
            "VTY": "Session",
            "CMP": "Series",
            "NTE": "Long Comment",
            "NDV": "Device Name",
        }
        for tok_str, meta_key in string_checks.items():
            msgs = sfj_parsed.messages_by_token(tok_str)
            if not msgs:
                continue
            spec_val = msgs[-1].payload
            if meta_key in sfj_backend.metadata:
                assert (
                    spec_val == sfj_backend.metadata[meta_key]
                ), f"{meta_key}: {spec_val!r} != {sfj_backend.metadata[meta_key]!r}"

    def test_sfj_logger_identity(self, sfj_parsed, sfj_backend):
        """Logger ID and Model ID should match for SFJ."""
        # SRC message contains embedded idn
        src_msgs = sfj_parsed.messages_by_token("SRC")
        assert len(src_msgs) > 0
        src = src_msgs[-1].payload
        assert src is not None
        assert src["logger_id"] == sfj_backend.metadata["Logger ID"]
        assert src["model_id"] == sfj_backend.metadata["Logger Model ID"]

    def test_86_logger_identity(self, file_86_parsed, file_86_backend):
        """Logger ID and Model ID should match for 86."""
        # 86 file may use idn or SRC
        idn_msgs = file_86_parsed.messages_by_token("idn")
        src_msgs = file_86_parsed.messages_by_token("SRC")
        found = None
        if idn_msgs:
            found = idn_msgs[-1].payload
        elif src_msgs and src_msgs[-1].payload:
            found = src_msgs[-1].payload
        assert found is not None
        assert found["logger_id"] == file_86_backend.metadata["Logger ID"]
        assert found["model_id"] == file_86_backend.metadata["Logger Model ID"]

    def test_sfj_gps_receiver(self, sfj_parsed, sfj_backend):
        """GPS Receiver type should match for SFJ."""
        gpsr_msgs = sfj_parsed.messages_by_token("GPSR")
        assert len(gpsr_msgs) > 0
        gpsr = gpsr_msgs[-1].payload
        assert gpsr["type"] == sfj_backend.metadata["GPS Receiver"]

    def test_86_gps_receiver(self, file_86_parsed, file_86_backend):
        """GPS Receiver type should match for 86."""
        gpsr_msgs = file_86_parsed.messages_by_token("GPSR")
        assert len(gpsr_msgs) > 0
        gpsr = gpsr_msgs[-1].payload
        assert gpsr["type"] == file_86_backend.metadata["GPS Receiver"]

    def test_sfj_race_mode(self, sfj_parsed, sfj_backend):
        """Race Mode should match for SFJ ('speed')."""
        racm_msgs = sfj_parsed.messages_by_token("RACM")
        found_string_mode = None
        for m in racm_msgs:
            if m.payload and m.payload["mode"] == "string":
                found_string_mode = m.payload["value"]
        assert found_string_mode == sfj_backend.metadata.get("Race Mode")

    def test_sfj_vet(self, sfj_parsed, sfj_backend):
        """VET should match for SFJ."""
        vet_msgs = sfj_parsed.messages_by_token("VET")
        assert len(vet_msgs) > 0
        assert vet_msgs[-1].payload.value == sfj_backend.metadata["Vehicle Electronics Type"]

    def test_sfj_trk(self, sfj_parsed, sfj_backend):
        """Track info should match for SFJ."""
        trk_msgs = sfj_parsed.messages_by_token("TRK")
        assert len(trk_msgs) > 0
        trk = trk_msgs[-1].payload
        assert trk["name"] == sfj_backend.metadata["Venue"]

    def test_sfj_odo(self, sfj_parsed, sfj_backend):
        """ODO records should match for SFJ."""
        odo_msgs = sfj_parsed.messages_by_token("ODO")
        assert len(odo_msgs) > 0
        odo = odo_msgs[-1].payload
        # Check System distance
        assert "System" in odo
        expected_dist_km = sfj_backend.metadata["Odo/System Distance (km)"]
        actual_dist_km = odo["System"]["dist"] / 1000
        assert abs(actual_dist_km - expected_dist_km) < 0.01

    def test_86_expansion_devices(self, file_86_parsed, file_86_backend):
        """86 should have expansion devices matching Cython metadata."""
        enf_msgs = file_86_parsed.messages_by_token("ENF")
        assert len(enf_msgs) > 0
        # Expansion devices should exist in Cython metadata
        assert "Expansion Devices" in file_86_backend.metadata
        cython_devices = file_86_backend.metadata["Expansion Devices"]
        assert len(enf_msgs) == len(cython_devices)

    def test_sfj_calibrations(self, sfj_parsed, sfj_backend):
        """CAL messages should match Cython metadata."""
        cal_msgs = sfj_parsed.messages_by_token("CAL")
        cython_cals = sfj_backend.metadata.get("Calibrations", [])
        assert len(cal_msgs) == len(cython_cals)
        for i, msg in enumerate(cal_msgs):
            assert msg.payload["type"] == cython_cals[i]["type"]
            assert abs(msg.payload["raw_1"] - cython_cals[i]["raw_1"]) < 1e-6
            assert abs(msg.payload["raw_2"] - cython_cals[i]["raw_2"]) < 1e-6


# ---------------------------------------------------------------------------
# Cross-validation: CDE messages
# ---------------------------------------------------------------------------


class TestCDECrossValidation:
    """Verify CDE messages are consistent."""

    def test_cde_channel_indices_valid(self, any_parsed):
        """CDE channel_index should reference a valid CHS channel."""
        result = any_parsed
        cde_msgs = result.messages_by_token("CDE")
        for m in cde_msgs:
            if m.payload is not None:
                assert (
                    m.payload.channel_index in result.channels
                ), f"CDE channel_index {m.payload.channel_index} not in channels"

    def test_cde_session_uid_consistent(self, any_parsed):
        """All CDE messages in a file should have the same session_uid."""
        result = any_parsed
        cde_msgs = result.messages_by_token("CDE")
        if not cde_msgs:
            pytest.skip("No CDE messages")
        uids = {m.payload.session_uid for m in cde_msgs if m.payload is not None}
        assert len(uids) == 1, f"Multiple session UIDs: {uids}"


# ---------------------------------------------------------------------------
# Cross-validation: Issue #49 (AIM model 768 + Mectronik ECU)
# ---------------------------------------------------------------------------


class TestIssue49CrossValidation:
    """Cross-validation tests for the issue49 badGPSdata.xrk file."""

    def test_vet_cross_validation(self, issue49_parsed, issue49_backend):
        """VET value should match between spec and Cython parsers."""
        vet_msgs = issue49_parsed.messages_by_token("VET")
        assert len(vet_msgs) > 0
        spec_val = vet_msgs[-1].payload.value
        cython_val = issue49_backend.metadata["Vehicle Electronics Type"]
        assert spec_val == cython_val == "..."

    def test_racm_cross_validation(self, issue49_parsed, issue49_backend):
        """RACM string value should match between spec and Cython parsers."""
        racm_msgs = issue49_parsed.messages_by_token("RACM")
        found_string_mode = None
        for m in racm_msgs:
            if m.payload and m.payload["mode"] == "string":
                found_string_mode = m.payload["value"]
        cython_val = issue49_backend.metadata.get("Race Mode")
        assert found_string_mode == cython_val == "..."

    def test_logger_identity(self, issue49_parsed, issue49_backend):
        """Logger ID and Model ID should match between spec and Cython parsers."""
        src_msgs = issue49_parsed.messages_by_token("SRC")
        idn_msgs = issue49_parsed.messages_by_token("idn")
        found = None
        if src_msgs and src_msgs[-1].payload:
            found = src_msgs[-1].payload
        elif idn_msgs:
            found = idn_msgs[-1].payload
        assert found is not None
        assert found["logger_id"] == issue49_backend.metadata["Logger ID"]
        assert found["model_id"] == issue49_backend.metadata["Logger Model ID"]

    def test_new_string_tokens(self, issue49_parsed):
        """New string tokens (+LM, MAN, MOD) should be present with str payloads."""
        for token in ("+LM", "MAN", "MOD"):
            msgs = issue49_parsed.find_nested_messages(token)
            assert len(msgs) >= 1, f"Expected at least one {token!r} message"
            for m in msgs:
                assert isinstance(
                    m.payload, str
                ), f"{token!r} payload should be str, got {type(m.payload)}"

    def test_chs_byte82_nonzero_on_external_gps(self, issue49_parsed):
        """CHS byte[82] should be non-zero for external GPS channels (hardware_id=2018)."""
        found = False
        for idx, ch in issue49_parsed.channels.items():
            if ch.hardware_id == 2018:
                found = True
                assert any(
                    ch.unknown_82
                ), f"Channel {ch.name!r} (hw_id=2018): expected non-zero unknown_82"
        assert found, "No channels with hardware_id=2018 found"

    def test_chs_byte82_zero_on_other_channels(self, issue49_parsed):
        """CHS byte[82] should be zero for non-GPS channels (hardware_id != 2018)."""
        for idx, ch in issue49_parsed.channels.items():
            if ch.hardware_id != 2018:
                assert not any(ch.unknown_82), (
                    f"Channel {ch.name!r} (hw_id={ch.hardware_id}): "
                    f"expected zero unknown_82, got {ch.unknown_82.hex()}"
                )


# ---------------------------------------------------------------------------
# Cross-validation: Data messages (sample-level)
# ---------------------------------------------------------------------------


class TestDataMessageCrossValidation:
    """Verify data messages decode to the same values as Cython parser.

    For every channel with a known decoder type, decode the first and last
    raw data samples from the spec-parsed data messages and compare against
    the Cython parser's channel arrays.
    """

    def _collect_channel_samples(self, result):
        """{channel_index: {"first": bytes, "last": bytes}} from the spec's sample streams."""
        return {
            idx: {"first": raw[0], "last": raw[-1]}
            for idx, raw in result.channel_samples().items()
            if raw
        }

    def _check_first_last_samples(self, parsed, cython_log, position):
        """Check first or last sample for every channel. Returns (checked, failures)."""
        samples = self._collect_channel_samples(parsed)
        checked = 0
        for idx, ch in parsed.channels.items():
            name = ch.name
            if name not in cython_log.channels:
                continue
            if idx not in samples:
                continue
            if ch.decoder_type not in SAMPLE_CODECS:
                continue

            value = _decode_and_fixup(samples[idx][position], ch)
            if value is None:
                continue

            col = cython_log.channels[name].column(name)
            cython_val = col[0 if position == "first" else -1].as_py()
            assert abs(value - float(cython_val)) < 0.01, (
                f"Ch {name!r} (decoder={ch.decoder_type}): "
                f"{position} sample {value} != cython {cython_val}"
            )
            checked += 1

        assert checked > 0, "No channels were cross-validated"

    def test_all_channels_first_sample(self, parsed_and_backend):
        """First sample for every channel should match Cython output."""
        parsed, cython_log = parsed_and_backend
        self._check_first_last_samples(parsed, cython_log, "first")

    def test_all_channels_last_sample(self, parsed_and_backend):
        """Last sample for every channel should match Cython output."""
        parsed, cython_log = parsed_and_backend
        self._check_first_last_samples(parsed, cython_log, "last")

    def test_decoder_type_coverage(self, parsed_and_backend):
        """Every decoder type with Cython-exposed channels should be validated."""
        parsed, cython_log = parsed_and_backend
        samples = self._collect_channel_samples(parsed)
        # Only count decoder types that have at least one Cython-exposed channel
        decoder_types_exposed = set()
        decoder_types_validated = set()

        for idx, ch in parsed.channels.items():
            if ch.decoder_type not in SAMPLE_CODECS:
                continue
            name = ch.name
            if name not in cython_log.channels:
                continue  # Internal channel not exposed by Cython
            decoder_types_exposed.add(ch.decoder_type)
            if idx in samples:
                value = _decode_and_fixup(samples[idx]["first"], ch)
                if value is not None:
                    decoder_types_validated.add(ch.decoder_type)

        missing = decoder_types_exposed - decoder_types_validated
        assert not missing, f"Decoder types not validated: {missing}"
        assert len(decoder_types_validated) >= 3, "Too few decoder types validated"

    def test_86_g_message_group_unpacking(self, file_86_parsed, file_86_backend):
        """G-message data should unpack to match Cython channel values."""
        # Find first G message for each group
        first_g_by_group = {}
        for m in file_86_parsed.data_messages():
            if m.msg_type != "G":
                continue
            grp_idx = m.parsed["group_index"]
            if grp_idx not in first_g_by_group:
                first_g_by_group[grp_idx] = m

        checked = 0
        for grp_idx, msg in first_g_by_group.items():
            if grp_idx not in file_86_parsed.groups:
                continue
            grp = file_86_parsed.groups[grp_idx]
            data = msg.parsed["data"]

            # Unpack individual channel data from the group payload
            offset = 0
            for ch_idx in grp.channel_indices:
                if ch_idx not in file_86_parsed.channels:
                    continue
                ch = file_86_parsed.channels[ch_idx]
                ch_size = file_86_parsed.channel_sizes[ch_idx]
                sample_bytes = data[offset : offset + ch_size]
                offset += ch_size

                name = ch.name
                if name not in file_86_backend.channels:
                    continue
                if ch.decoder_type not in SAMPLE_CODECS:
                    continue

                value = _decode_and_fixup(sample_bytes, ch)
                if value is None:
                    continue

                cython_first = file_86_backend.channels[name].column(name)[0].as_py()
                assert abs(value - float(cython_first)) < 0.01, (
                    f"Group {grp_idx}, Ch {name!r}: " f"unpacked {value} != cython {cython_first}"
                )
                checked += 1

        assert checked > 0, "No G-message channels were cross-validated"


# ---------------------------------------------------------------------------
# Exhaustive cross-validation: every sample of every channel
# ---------------------------------------------------------------------------


class TestSampleCodecs:
    """The declarative sample codecs are the decoder definition."""

    @pytest.mark.parametrize(
        "decoder_type, raw, expected",
        [
            (20, b"\x00\x7c", float("nan")),  # fp16 +inf, the official sample's External Voltage
            (20, b"\x00\xfc", float("nan")),  # fp16 -inf
            (20, b"\x00\x7e", float("nan")),  # fp16 NaN
            (20, b"\x00\x3c", 1.0),  # fp16 1.0 untouched
            (6, b"\x00\x00\x80\xff", float("nan")),  # f32 -inf, CAN dropout sentinel
            (6, b"\x00\x00\x80\x3f", 1.0),
            (15, b"N\x00", 0),  # gear 'N'
            (15, b"3\x00", 3),
            (15, b"\x07\x00", 7),  # not a gear character: passes through
            (13, b"\x05", 5),
        ],
    )
    def test_codec_values(self, decoder_type, raw, expected):
        value = SAMPLE_CODECS[decoder_type].parse(raw)
        if isinstance(expected, float) and math.isnan(expected):
            assert math.isnan(value)
        else:
            assert value == expected

    def test_m_payload_is_an_array_of_samples(self):
        """An (M burst parses into `count` samples of the channel's width."""
        frame = b"\x28\x4d" + struct.pack("<iHH", 1000, 7, 3) + b"aabbcc" + b"\x29"
        msg = MMessageCompiled.parse(frame, channel_sizes={7: 2}, group_sizes={}, group_members={})
        assert list(msg.samples) == [b"aa", b"bb", b"cc"]
        assert msg.data == b"aabbcc"  # raw bytes kept for rebuilds

    def test_g_payload_is_an_array_of_member_samples(self):
        """A (G payload parses into one sample per GRP member, in GRP order
        and at each member's CHS width."""
        frame = b"\x28\x47" + struct.pack("<iH", 1000, 1) + b"xxYYYY" + b"\x29"
        msg = GMessageCompiled.parse(
            frame, channel_sizes={5: 2, 9: 4}, group_sizes={1: 6}, group_members={1: [5, 9]}
        )
        assert list(msg.members) == [5, 9]
        assert list(msg.samples) == [b"xx", b"YYYY"]
        assert msg.data == b"xxYYYY"

    def test_grp_without_g_messages_leaves_members_in_their_s_streams(self, issue80_parsed):
        """A GRP describes a G payload; it does not say G messages exist.

        The issue80 file (MyChron 5S) declares one GRP of 3945 members, 56 of
        which have a CHS, and contains no G message. The members' samples are
        S messages and are in their streams like any other channel's
        (companion.md section 3, "A GRP no G message uses").
        """
        parsed = issue80_parsed
        assert list(parsed.groups) == [1]
        assert list(parsed.groups[1].channel_indices) == list(range(16, 3961))
        assert sorted(parsed.channels) == list(range(72))
        assert parsed.group_members[1] == list(range(16, 72))
        assert parsed.group_sizes[1] == sum(parsed.channel_sizes[ch] for ch in range(16, 72))
        kinds = {m.msg_type for m in parsed.messages if m.msg_type != "header"}
        assert kinds == {"S"}

        sampled_members = [ch for ch in parsed.group_members[1] if parsed.streams.get(ch)]
        names = [parsed.channels[ch].name for ch in sampled_members]
        assert len(sampled_members) == 17
        assert {"Water Temp", "Internal Battery", "RPM"} <= set(names)

    def test_streams_match_the_backend_row_counts(self, parsed_and_backend):
        """The accumulation pass yields one sample per backend row, channel by channel."""
        parsed, log = parsed_and_backend
        names = parsed.channel_display_names()
        values = parsed.channel_values()
        checked = 0
        for idx, arr in values.items():
            name = names.get(idx)
            if name in log.channels:
                assert len(arr) == log.channels[name].num_rows, name
                checked += 1
        assert checked > 10

    def test_channel_stream_grammar_matches_bulk_decoder(self, sfj_parsed):
        """channel_stream(chs) is the Construct grammar of a sample stream;
        decode_channel_values is its vectorised form. On the first 200 slots
        of every SFJ channel the two agree value for value (NaN-aware)."""
        import numpy as np

        checked = set()
        for idx, stream in sfj_parsed.streams.items():
            chs = sfj_parsed.channels[idx]
            width = sfj_parsed.channel_sizes[idx]
            grammar = channel_stream(chs, width)
            if grammar is None:
                continue
            prefix = bytes(stream[: 200 * width])
            vectorised = decode_channel_values(prefix, chs, width)
            declarative = np.array(grammar.parse(prefix), dtype=np.float64).astype(vectorised.dtype)
            np.testing.assert_array_equal(declarative, vectorised, err_msg=chs.name)
            checked.add(chs.decoder_type)
        assert {6, 20} <= checked, "fixture lost its float channels"

    def test_bulk_decoder_matches_per_sample_codec(self, sfj_parsed):
        """decode_channel_values is the vectorised form of the codec: every
        value equals codec.parse() on the same bytes (NaN-aware), for every
        decoder type the SFJ fixture uses, on its first 500 samples."""
        import numpy as np

        checked = set()
        for idx, raw in sfj_parsed.channel_samples().items():
            ch = sfj_parsed.channels[idx]
            codec = channel_sample_codec(ch)
            if codec is None or not raw:
                continue
            raw = raw[:500]
            bulk = decode_channel_values(b"".join(raw), ch, ch.data_size)
            single = np.array([codec.parse(s[: codec.sizeof()]) for s in raw], dtype=np.float64)
            np.testing.assert_array_equal(bulk, single.astype(bulk.dtype), err_msg=ch.name)
            checked.add(ch.decoder_type)
        assert {6, 20} <= checked, "fixture lost its float channels"


class TestExhaustiveChannelValues:
    """Compare EVERY decoded channel value against Cython output.

    Unlike TestDataMessageCrossValidation which only checks first/last samples,
    this class builds complete channel arrays from ALL data messages and compares
    every single value against the Cython parser's output.
    """

    def _build_channel_arrays(self, result):
        """{channel_index: [raw sample bytes]} per the spec's accumulator rules."""
        return result.channel_samples()

    def _decode_channel_array(self, raw_samples, ch):
        """All samples of a channel through the spec's bulk decoder."""
        return decode_channel_values(b"".join(raw_samples), ch, ch.data_size)

    def _compare_all_values(self, result, cython_log):
        """Compare every sample of every channel. Returns (checked_channels, errors).

        Exact: the spec's values carry the channel's value type
        (channel_value_dtype), so the backend column must have the same type
        and bit-identical values (NaN == NaN), with no cast and no tolerance.
        """
        import numpy as np

        values_by_index = result.channel_values()
        names = _backend_channel_names(result)
        checked = 0
        errors = []

        for idx in result.channels:
            name = names[idx]
            if name not in cython_log.channels:
                continue
            spec_values = values_by_index.get(idx)
            if spec_values is None:
                continue

            cython_col = cython_log.channels[name].column(name)
            cython_values = cython_col.to_numpy(zero_copy_only=False)

            if spec_values.dtype != cython_values.dtype:
                errors.append(
                    f"Ch {name!r}: type mismatch spec={spec_values.dtype} "
                    f"cython={cython_values.dtype}"
                )
                continue
            if len(spec_values) != len(cython_values):
                errors.append(
                    f"Ch {name!r}: length mismatch spec={len(spec_values)} "
                    f"cython={len(cython_values)}"
                )
                continue

            # Both NaN is a match, one NaN is a mismatch, otherwise the
            # values must be equal. Report the first offending sample only.
            bad = _differing(spec_values, cython_values)
            if len(bad):
                i = int(bad[0])
                errors.append(
                    f"Ch {name!r} sample {i}/{len(spec_values)}: "
                    f"spec={spec_values[i]!r} cython={cython_values[i]!r}"
                )

            checked += 1

        return checked, errors

    def test_aim_official_every_value(self, aim_official_parsed, aim_official_backend):
        """Every sample of every AIM official channel must match Cython (0.5M)."""
        checked, errors = self._compare_all_values(aim_official_parsed, aim_official_backend)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)

    def test_sfj_every_value(self, sfj_parsed, sfj_backend):
        """Every sample of every SFJ channel must match Cython (0.8M)."""
        checked, errors = self._compare_all_values(sfj_parsed, sfj_backend)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)

    def test_86_every_value(self, file_86_parsed, file_86_backend):
        """Every sample of every 86 channel must match Cython (7.1M)."""
        checked, errors = self._compare_all_values(file_86_parsed, file_86_backend)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)

    def test_issue80_every_value(self, issue80_parsed, issue80_backend):
        """Every sample of every issue80 channel must match the backend.

        All 25 of them: 17 are members of the file's GRP and arrive as S
        messages, which both backends used to drop (issue #80).
        """
        checked, errors = self._compare_all_values(issue80_parsed, issue80_backend)
        assert checked == 25, f"Only {checked} channels compared"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Value types: the type of every channel's value column
# ---------------------------------------------------------------------------


class TestVoltsRule:
    """The mV -> V division is a property of the unit byte, not of the unit string.

    Only unit code 21 with the calibrated flag is divided by 1000. Code 32 is
    also "V" (the AIM DLL's string for it) and is left as stored: the DLL
    returns the SFJ fixture's External Voltage as 11.264 with its own unit
    byte (149) and as 11264 with 21, 32 or 32 | 0x80.
    """

    @staticmethod
    def _chs(unit_byte):
        data = xrk_patch.load_bytes(SFJ_XRK)
        frame = xrk_patch.chs_frames(data)["External Voltage"]
        payload = bytearray(data[frame["pstart"] : frame["pend"]])
        payload[12] = unit_byte
        return CHSPayload.parse(bytes(payload))

    @pytest.mark.parametrize(
        "unit_byte, units, volts",
        [(21 | 0x80, "V", True), (21, "mV", False), (32, "V", False), (32 | 0x80, "V", False)],
    )
    def test_volts_is_millivolts_with_the_calibrated_flag(self, unit_byte, units, volts):
        chs = self._chs(unit_byte)
        assert (chs.units, chs.volts) == (units, volts)
        raw = np.float16(11264.0).tobytes()
        assert channel_sample_codec(chs).parse(raw) == pytest.approx(11.264 if volts else 11264.0)
        assert channel_value_dtype(chs) == (np.float32 if volts else np.float16)


class TestChannelValueTypes:
    """channel_value_dtype() is the type both backends give a channel.

    The values tests above compare numbers; these compare the Arrow type of
    the column itself, so a backend that changes a channel's type (say int32
    to float64) fails here even though every value still matches.
    """

    _FIXTURES = [
        "sfj",
        "sfj_0101",
        "sfj_suzuka",
        "file_86",
        "aim_official",
        "issue49",
        "issue68",
        "issue84",
        "issue80",
    ]

    @pytest.mark.parametrize(
        "decoder_type, volts, name, expected",
        [
            (0, False, "Master Clk", "int32"),
            (1, False, "StartRec", "uint16"),
            (4, False, "RPM", "int16"),
            (6, False, "Lateral Grip", "float32"),
            (11, False, "ECU_GEAR", "int16"),
            (12, False, "Predictive Time", "int32"),
            (13, False, "Status", "uint8"),
            (15, False, "Gear", "uint16"),
            (20, False, "LoggerTemp", "float16"),  # a float16 sample stays float16
            (31, False, "Lap Time", "int32"),
            (20, True, "External Voltage", "float32"),  # mV -> V: float32 for a float type,
            (6, True, "Battery", "float32"),  # float16 included (mV / 1000 is not a float16)
            (4, True, "Battery", "float64"),  # ... and turns an integer type into float64
            (13, True, "Battery", "float64"),
            (15, False, "Calculated_Gear", "uint32"),  # keyed by name, not by decoder_type
            (6, False, "PreCalcGear", "uint32"),
            (15, True, "Calculated_Gear", "float64"),
            (99, False, "No Such Decoder", None),
        ],
    )
    def test_value_type_rule(self, decoder_type, volts, name, expected):
        """The rule on hand-written channel definitions, one row per case of
        companion.md section 3 ("Value types"). `volts` is CHSPayload.volts:
        millivolts with the calibrated flag, divided by 1000."""
        chs = SimpleNamespace(decoder_type=decoder_type, volts=volts, name=name)
        dtype = channel_value_dtype(chs)
        # `dtype and ...` would not do: a plain numpy dtype is falsy.
        assert (None if dtype is None else dtype.name) == expected

    def test_every_other_decoder_is_int32(self):
        """Over the whole of SAMPLE_CODECS: the seven decoders with a type of
        their own, and int32 for every other one, as derived from the codecs'
        base fields."""
        own = {1: "uint16", 15: "uint16", 4: "int16", 11: "int16", 13: "uint8"}
        own |= {6: "float32", 20: "float16"}
        for decoder_type in SAMPLE_CODECS:
            chs = SimpleNamespace(decoder_type=decoder_type, volts=False, name="x")
            expected = own.get(decoder_type, "int32")
            assert channel_value_dtype(chs).name == expected, decoder_type

    @pytest.mark.parametrize("name", _FIXTURES)
    def test_backend_value_types_match_spec(self, name, backend, request):
        """Every channel of every fixture, on both backends: `timecodes` is
        int64 and the value column has the spec's declared type. A channel
        the spec cannot type is a failure, not a skip; the only channels
        without a CHS are the 14 the shared GPS pipeline synthesizes, whose
        types belong to the libxrk-gps crate."""
        # `backend` is requested directly so its session params are defined
        # for this test; getfixturevalue alone cannot parametrize.
        parsed = request.getfixturevalue(f"{name}_parsed")
        log = request.getfixturevalue(f"{name}_backend")
        index_by_name = {n: idx for idx, n in _backend_channel_names(parsed).items()}
        timecodes = pa.from_numpy_dtype(TIMECODE_DTYPE)
        assert timecodes == pa.int64()

        errors, checked = [], 0
        for ch_name, table in log.channels.items():
            if table.schema.names != ["timecodes", ch_name]:
                errors.append(f"{ch_name}: columns {table.schema.names}")
                continue
            if table.schema.field("timecodes").type != timecodes:
                errors.append(f"{ch_name}: timecodes is {table.schema.field('timecodes').type}")
            if ch_name not in index_by_name:
                if ch_name not in GPS_CHANNEL_NAMES:
                    errors.append(f"{ch_name}: no CHS definition in the spec's parse")
                continue
            declared = channel_value_dtype(parsed.channels[index_by_name[ch_name]])
            actual = table.schema.field(ch_name).type
            if declared is None or actual != pa.from_numpy_dtype(declared):
                errors.append(f"{ch_name}: backend {actual}, spec declares {declared}")
            checked += 1

        assert checked > 10, f"only {checked} channels compared"
        assert not errors, f"{backend} value types differ from the spec:\n" + "\n".join(errors)

    # The fixtures only carry data for decoders 1, 4, 6, 11, 12, 15, 20 and 24,
    # the mV -> V conversion only on decoder 20, and no name-keyed gear channel.
    # The rest of the rule is pinned on a patched fixture: issue84 has 30-odd
    # data-carrying channels with decoder 15 in 8-byte slots, wide enough for
    # every codec, so each case rewrites one of their CHS definitions in memory
    # (decoder_type, unit, long_name). The sample values are meaningless; only
    # the types are compared.
    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _patched_issue84(volts):
        """(xrk bytes, {patched channel name: its CHS payload}) for one unit variant."""
        data = xrk_patch.load_bytes(ISSUE84_XRZ)
        exposed = _BACKENDS["cython"](data).channels
        chs_frames = [f for f in xrk_patch.frames(data) if f["tok"] == "CHS"]
        parsed = [CHSPayload.parse(data[f["pstart"] : f["pend"]]) for f in chs_frames]
        # Patch only channels that carry data, found under their display name
        # (the k-th CHS of a name is "<name> dup <k>", by ascending index).
        seen, targets = {}, []
        for chs, frame in sorted(zip(parsed, chs_frames), key=lambda pair: pair[0].index):
            k = seen[chs.name] = seen.get(chs.name, 0) + 1
            shown = chs.name if k == 1 else f"{chs.name} dup {k}"
            if chs.decoder_type == 15 and chs.data_size == 8 and shown in exposed:
                targets.append(frame)
        # Each decoder on a channel of its own; the gear names keep decoder 15.
        cases = [(f"decoder {d}", d) for d in sorted(SAMPLE_CODECS)]
        cases += [(gear, 15) for gear in sorted(NAMED_VALUE_DTYPES)]
        assert len(targets) >= len(cases), "issue84 lost its wide decoder-15 channels"

        buf = bytearray(data)
        payloads = {}
        for frame, (label, decoder_type) in zip(targets, cases):
            xrk_patch.patch(buf, frame, 20, bytes([decoder_type]))
            xrk_patch.patch(buf, frame, 32, label.encode().ljust(24, b"\0"))
            if volts:
                # unit type 21 (mV) + calibrated flag: "V"
                xrk_patch.patch(buf, frame, 12, bytes([21 | 0x80]))
            payloads[label] = bytes(buf[frame["pstart"] : frame["pend"]])
        return bytes(buf), payloads

    @pytest.mark.parametrize("volts", [False, True], ids=["plain", "volts"])
    def test_every_decoder_and_unit_rule_on_both_backends(self, volts, backend):
        """Every decoder in SAMPLE_CODECS and both name-keyed gear channels,
        with and without the mV -> V conversion: the backend's Arrow type is
        the spec's declared type for the same CHS definition.

        Not covered: a gear-named channel with unit "V" whose decoder_type is
        6 or 20. The backends disagree there (Cython float64, Rust float32);
        the spec's rule gives float64. No fixture has such a channel.
        """
        data, payloads = self._patched_issue84(volts)
        log = _BACKENDS[backend](data)
        errors = []
        for label, payload in payloads.items():
            chs = CHSPayload.parse(payload)
            assert chs.name == label and chs.volts == volts
            declared = channel_value_dtype(chs)
            if label not in log.channels:
                errors.append(f"{label}: not exposed by {backend}")
                continue
            schema = log.channels[label].schema
            if schema.field("timecodes").type != pa.int64():
                errors.append(f"{label}: timecodes is {schema.field('timecodes').type}")
            if schema.field(label).type != pa.from_numpy_dtype(declared):
                errors.append(
                    f"{label}: backend {schema.field(label).type}, spec declares {declared}"
                )
        assert not errors, f"{backend} value types differ from the spec:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Cross-validation: Official AIM DLL
# ---------------------------------------------------------------------------


class TestDLLCrossValidation:
    """Compare spec parser against the official AIM MatLabXRK DLL.

    Uses Wine to call the proprietary DLL and compares channel sample counts
    and values. Tests are skipped if Wine or the DLL are not available.
    """

    def _dll_channels_by_name(self, dll_data):
        """Build {name: channel_dict} from DLL extract data."""
        return {ch["name"]: ch for ch in dll_data["channels"]}

    def test_channel_sample_counts(self, parsed_and_dll):
        """Every channel's sample count should match the DLL."""
        parsed, dll_data = parsed_and_dll
        dll_channels = self._dll_channels_by_name(dll_data)
        arrays = TestExhaustiveChannelValues()._build_channel_arrays(parsed)
        errors = []

        for idx, ch in parsed.channels.items():
            name = ch.name
            if name not in dll_channels:
                continue
            if idx not in arrays:
                continue
            dll_count = dll_channels[name]["sample_count"]
            if dll_count == 0:
                continue
            spec_count = len(arrays[idx])
            if spec_count != dll_count:
                errors.append(f"Ch {name!r}: spec={spec_count} dll={dll_count}")

        assert not errors, "Sample count mismatches vs DLL:\n" + "\n".join(errors)

    # Known DLL vs spec/Cython differences:
    # - StartRec: DLL returns 1.0, Cython/spec returns 2^-24 (decoder type mismatch
    #   for this internal flag channel — not real telemetry data)
    _DLL_SKIP_CHANNELS = {
        "StartRec",
    }

    def _compare_spot_values(self, parsed, dll_data):
        """Compare first/last values for all channels. Returns (checked, errors)."""
        dll_channels = self._dll_channels_by_name(dll_data)
        arrays = TestExhaustiveChannelValues()._build_channel_arrays(parsed)
        exhaustive = TestExhaustiveChannelValues()
        errors = []
        checked = 0

        for idx, ch in parsed.channels.items():
            name = ch.name
            if name in self._DLL_SKIP_CHANNELS:
                continue
            if name not in dll_channels:
                continue
            dll_ch = dll_channels[name]
            if dll_ch["sample_count"] == 0 or "first_samples" not in dll_ch:
                continue
            if idx not in arrays or not arrays[idx]:
                continue
            if ch.decoder_type not in SAMPLE_CODECS:
                continue

            spec_values = exhaustive._decode_channel_array(arrays[idx], ch)
            if spec_values is None:
                continue

            dll_first = dll_ch["first_samples"]["values"][0]
            spec_first = spec_values[0]
            if abs(spec_first - dll_first) >= 0.01:
                errors.append(f"Ch {name!r} first: spec={spec_first} dll={dll_first}")

            dll_last = dll_ch["last_samples"]["values"][-1]
            spec_last = spec_values[-1]
            if abs(spec_last - dll_last) >= 0.01:
                errors.append(f"Ch {name!r} last: spec={spec_last} dll={dll_last}")

            checked += 1

        return checked, errors

    def test_channel_values_spot_check(self, parsed_and_dll):
        """First and last sample values should match DLL."""
        parsed, dll_data = parsed_and_dll
        checked, errors = self._compare_spot_values(parsed, dll_data)
        assert checked > 0, "No channels were spot-checked against DLL"
        assert not errors, "Value mismatches vs DLL:\n" + "\n".join(errors)

    def test_lap_count(self, parsed_and_dll):
        """Lap count should match DLL."""
        parsed, dll_data = parsed_and_dll
        dll_lap_count = len(dll_data["laps"])
        unique = _unique_seg0_laps(parsed)
        assert len(unique) == dll_lap_count, f"Lap count: spec={len(unique)} dll={dll_lap_count}"

    def test_aim_official_v2_lap_times(self, aim_official_dll, aim_official_backend):
        """The v2 LAP interpretation (absolute end time at [28:32]) must
        reproduce the official DLL's lap table exactly, to the millisecond."""
        dll_laps = aim_official_dll["laps"]
        laps = aim_official_backend.laps
        assert len(dll_laps) == laps.num_rows
        assert [lap["start_ms"] for lap in dll_laps] == laps.column("start_time").to_pylist()
        assert [lap["end_ms"] for lap in dll_laps] == laps.column("end_time").to_pylist()

    def test_aim_official_renamed_channels_match_dll(self, aim_official_dll, aim_official_backend):
        """Last-write-wins channel renames must match the DLL: test.xrk's
        final CNFs rename 'Temperature 1'/'Temperature 2' to
        'Exhaust Temp'/'Water Temp', and the DLL exposes the new names
        carrying the full data stream."""
        dll_by_name = {ch["name"]: ch["sample_count"] for ch in aim_official_dll["channels"]}
        for name in ("Exhaust Temp", "Water Temp"):
            assert name in dll_by_name
            assert name in aim_official_backend.channels
            assert len(aim_official_backend.channels[name]) == dll_by_name[name]
        assert "Temperature 1" not in aim_official_backend.channels
        assert "Temperature 2" not in aim_official_backend.channels

    def test_issue80_grp_members_match_dll(self, issue80_dll, issue80_parsed):
        """The DLL exposes three channels of the issue80 file, all members of
        the GRP that no G message uses: the spec's S streams for them have
        the DLL's sample counts and values, and its lap table. (The sample
        times are checked on both backends in tests/test_issue80_xrk.py.)"""
        import numpy as np

        parsed = issue80_parsed
        dll_channels = self._dll_channels_by_name(issue80_dll)
        assert sorted(dll_channels) == ["Internal Battery", "RPM", "Water Temp"]
        index_by_name = {ch.name: idx for idx, ch in parsed.channels.items()}
        values = parsed.channel_values()
        for name, dll in dll_channels.items():
            idx = index_by_name[name]
            assert any(idx in members for members in parsed.group_members.values()), name
            assert len(values[idx]) == dll["sample_count"], name
            for end, pick in (("first_samples", slice(None, 5)), ("last_samples", slice(-5, None))):
                np.testing.assert_allclose(
                    values[idx][pick].astype(np.float64), dll[end]["values"], rtol=1e-6
                )

        _, laps = _run_gps_pipeline(parsed)
        assert [lap["start_time"] for lap in laps] == [l["start_ms"] for l in issue80_dll["laps"]]
        assert [lap["end_time"] for lap in laps] == [l["end_ms"] for l in issue80_dll["laps"]]

    # Unit codes where libxrk deliberately spells the unit differently from
    # the DLL. Code 30 comes back from the DLL as the single byte 0xBB, a
    # symbol-font lambda ("\xbb" in latin-1); libxrk writes "lambda". Code 158
    # (30 | calibrated flag) is reported as "A/F" on the one corpus channel
    # that carries it, which is too little evidence to encode a rewrite.
    _KNOWN_UNIT_DIVERGENCES = {30: ("lambda", "\xbb"), 158: ("lambda", "A/F")}

    def test_channel_units(self, parsed_and_dll):
        """Every unit string the DLL reports should match the spec's UNIT_MAP.

        The DLL has reported units all along (``get_channel_units``), but
        nothing asserted on them until unit code 11 turned out to be "#"
        rather than "" (PR #101).
        """
        parsed, dll_data = parsed_and_dll
        dll_channels = self._dll_channels_by_name(dll_data)
        checked, errors = 0, []
        for ch in parsed.channels.values():
            dll_ch = dll_channels.get(ch.name)
            if dll_ch is None:
                continue
            known = self._KNOWN_UNIT_DIVERGENCES.get(ch.unit_type_byte)
            if known is not None:
                assert (
                    ch.units,
                    dll_ch["units"],
                ) == known, (
                    f"Ch {ch.name!r}: known divergence for unit code {ch.unit_type_byte} changed"
                )
                continue
            checked += 1
            if ch.units != dll_ch["units"]:
                errors.append(
                    f"Ch {ch.name!r} (code {ch.unit_type_byte}): "
                    f"spec={ch.units!r} dll={dll_ch['units']!r}"
                )
        assert not errors, f"{len(errors)} unit mismatches:\n" + "\n".join(errors)
        assert checked > 0

    def test_metadata(self, parsed_and_dll):
        """Metadata should match DLL."""
        parsed, dll_data = parsed_and_dll
        dll_meta = dll_data["metadata"]
        # Vehicle
        veh_msgs = parsed.messages_by_token("VEH")
        if veh_msgs:
            assert veh_msgs[-1].payload == dll_meta["vehicle"]
        # Track
        trk_msgs = parsed.messages_by_token("TRK")
        if trk_msgs and trk_msgs[-1].payload:
            assert trk_msgs[-1].payload["name"] == dll_meta["track"]
        # Racer
        rcr_msgs = parsed.messages_by_token("RCR")
        if rcr_msgs:
            assert rcr_msgs[-1].payload == dll_meta["racer"]


# ---------------------------------------------------------------------------
# Issue #68: (c) expansion message variant parsing
# ---------------------------------------------------------------------------


class TestIssue68CVariants:
    """Spec-level checks for the three (c) variants on the issue68 fixture.

    Confirms:
      1. V1, V2, and V3 all appear in the parsed stream.
      2. expansion_channel_map resolves to the correct CHS channels for the
         4 shock pots and 3 accelerometer channels.
      3. Decoded V2[0] fp16 samples for a shock pot match the AIM DLL's
         ground-truth values exactly (V2[1] is covered in the Cython-backend
         tests where the full sample array is produced).
    """

    SHOCK_NAMES = {"LR_Shock_Pot", "RR_Shock_Pot", "LF_Shock_Pot", "RF_Shock_Pot"}
    ACCEL_NAMES = {"LateralAcc", "InlineAcc", "VerticalAc"}

    def test_all_three_variants_present(self, issue68_parsed):
        """Issue68 fixture must contain V1, V2, and V3 c-messages."""
        variants = set()
        for m in issue68_parsed.data_messages():
            if m.msg_type == "c":
                variants.add(m.parsed.get("variant"))
        assert variants == {"V1", "V2", "V3"}, f"missing variants: {variants}"

    def test_expansion_channel_map_covers_shock_pots(self, issue68_parsed):
        """Every shock-pot channel has at least one channel_field mapped to it."""
        resolved = {
            issue68_parsed.channels[idx].name
            for idx in set(issue68_parsed.expansion_channel_map.values())
        }
        missing = self.SHOCK_NAMES - resolved
        assert not missing, f"expansion_channel_map missing shock pots: {missing}"

    def test_expansion_channel_map_covers_accels(self, issue68_parsed):
        """Every accelerometer channel has exactly one channel_field mapped to it."""
        resolved = {
            issue68_parsed.channels[idx].name
            for idx in set(issue68_parsed.expansion_channel_map.values())
        }
        missing = self.ACCEL_NAMES - resolved
        assert not missing, f"expansion_channel_map missing accels: {missing}"

    def test_shock_pot_pairs_share_channel_index(self, issue68_parsed):
        """For each shock pot, both channel_fields in its pair resolve to the
        same CHS channel_index (confirms the pair→channel assignment)."""
        cmap = issue68_parsed.expansion_channel_map
        # Group ch_fields by resolved channel
        by_channel = {}
        for cf, idx in cmap.items():
            by_channel.setdefault(idx, []).append(cf)
        for idx, cfs in by_channel.items():
            name = issue68_parsed.channels[idx].name
            if name in self.SHOCK_NAMES:
                assert (
                    len(cfs) == 2
                ), f"{name}: expected 2 channel_fields (base + base+4), got {cfs}"
                assert max(cfs) - min(cfs) == 4, f"{name}: pair spacing not 4: {cfs}"
            elif name in self.ACCEL_NAMES:
                assert len(cfs) == 1, f"{name}: accel should have 1 ch_field, got {cfs}"

    def test_v3_timecodes_synthesized(self, issue68_parsed):
        """V3 messages should have synthesized (non-negative) timecodes after
        _resolve_c_variants. A negative tc means inheritance failed."""
        v3_msgs = [
            m
            for m in issue68_parsed.data_messages()
            if m.msg_type == "c" and m.parsed.get("variant") == "V3"
        ]
        assert v3_msgs, "issue68 fixture must contain V3 messages"
        negative_tcs = sum(1 for m in v3_msgs if m.parsed["timecode"] < 0)
        # A handful of V3 messages at the very start may precede the first V2
        # on their channel and can't synthesize a tc. The fraction must stay
        # tiny (hard cap at 1% of all V3 messages).
        assert (
            negative_tcs < len(v3_msgs) / 100
        ), f"Too many V3 messages with unresolved tc: {negative_tcs}/{len(v3_msgs)}"

    def test_lr_shock_pot_samples_match_dll(self, issue68_parsed):
        """Decode every V1/V2/V3 message for LR_Shock_Pot, apply the
        variant-specific tc placement rules from spec/docs/unknown_regions.md,
        and assert at least 99% of produced (tc, value) samples match the
        AIM DLL's full sample array exactly.

        This is the authoritative spec correctness check for issue #68.
        We require ≥99% (not 100%) because the AIM DLL applies a
        proprietary sub-millisecond time adjustment at cycle boundaries
        that we cannot replicate without its internal clock model; in
        practice we hit ≥99.9% on this fixture.
        """
        import json
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        import filelock
        import numpy as np
        import pytest

        from spec.tests.conftest import ISSUE68_XRZ, REFERENCE_DLL_DIR, _dll_available

        # Pull full DLL LR_Shock_Pot samples (only if Wine/DLL available).
        if not _dll_available():
            pytest.skip("AIM DLL or Wine not available")
        wine = "/usr/lib/wine/wine64"
        py = str(REFERENCE_DLL_DIR / ".setup" / "python-embed" / "python.exe")
        wine_all_script = "Z:" + str(REFERENCE_DLL_DIR / "wine_all_samples.py")
        env = {
            "WINELOADER": wine,
            "WINEDEBUG": "-all",
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
        }
        # The DLL writes scratch files next to its input (an .xrz gets a
        # sibling .xrk), so hand it a copy in a temp directory.
        lock = filelock.FileLock(Path(__file__).parent / ".wine_extract.lock")
        with tempfile.TemporaryDirectory(prefix="libxrk-dll-") as tmp:
            scratch = Path(tmp) / ISSUE68_XRZ.name
            shutil.copy2(ISSUE68_XRZ, scratch)
            with lock:
                proc = subprocess.run(
                    [wine, py, wine_all_script, "Z:" + str(scratch), "LR_Shock_Pot"],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env=env,
                )
        dll = json.loads(proc.stdout)
        dll_map = {int(round(t)): v for t, v in zip(dll["t_ms"], dll["v"])}

        lr_idx = next(
            (i for i, ch in issue68_parsed.channels.items() if ch.name == "LR_Shock_Pot"), None
        )
        assert lr_idx is not None
        lr_cfs = sorted(cf for cf, i in issue68_parsed.expansion_channel_map.items() if i == lr_idx)
        base_cf, plus4_cf = lr_cfs

        # Per-variant sample emission per spec/docs/unknown_regions.md.
        samples = {}
        for m in issue68_parsed.data_messages():
            if m.msg_type != "c":
                continue
            p = m.parsed
            cf = p["channel_field"]
            if cf not in lr_cfs:
                continue
            variant = p.get("variant")
            data = p["data"]
            if variant == "V2":
                tc = p["timecode"]
                v0 = float(np.frombuffer(data[0:2], dtype=np.float16)[0])
                v1 = float(np.frombuffer(data[2:4], dtype=np.float16)[0])
                if cf == base_cf:
                    samples[tc] = v0
                    samples[tc - 4] = v1
                else:
                    samples[tc - 2] = v0
                    samples[tc - 4] = v1
            elif variant == "V3":
                tc = p["timecode"]
                if tc < 0:
                    continue
                v = float(np.frombuffer(data[:2], dtype=np.float16)[0])
                samples[tc] = v

        # Derive the session time offset from the first V2(base) message,
        # whose V2[0] corresponds to DLL sample at logger_tc - offset.
        first_v2_base = next(
            m
            for m in issue68_parsed.data_messages()
            if m.msg_type == "c"
            and m.parsed.get("variant") == "V2"
            and m.parsed["channel_field"] == base_cf
        )
        v0 = float(np.frombuffer(first_v2_base.parsed["data"][0:2], dtype=np.float16)[0])
        # Smallest DLL ts whose value matches v0 is the one our offset refers to.
        matching_ts = sorted(ts for ts, v in dll_map.items() if abs(v - v0) < 1e-3)
        assert matching_ts, "first V2(base) value not found in DLL LR samples"
        offset = first_v2_base.parsed["timecode"] - matching_ts[0]

        exact = sum(
            1
            for tc, v in samples.items()
            if (tc - offset) in dll_map and abs(dll_map[tc - offset] - v) < 1e-3
        )
        match_pct = exact / len(samples)
        assert match_pct >= 0.99, (
            f"Spec LR_Shock_Pot sample exact match only {match_pct*100:.2f}% "
            f"({exact}/{len(samples)}), expected ≥99%"
        )
        # Sample count also within 1% of DLL
        count_ratio = abs(len(samples) - len(dll_map)) / len(dll_map)
        assert count_ratio < 0.01, (
            f"Spec sample count {len(samples)} differs from DLL {len(dll_map)} by "
            f"{count_ratio*100:.2f}% (>1% threshold)"
        )


def _decode_c_variant_samples(parsed, ch_idx):
    """Decode V1/V2/V3 (c)-message samples for one channel using the
    canonical rules from spec/docs/unknown_regions.md, with the priority
    resolution Cython and Rust both apply (V2(base)/V1 = 3 > V3 = 2 > V2(+4) = 1).

    Returns {logger_tc: value}. Orphan channels (accels, no pair partner)
    use V2[0]@tc, V2[1]@tc-2. Paired channels use V2(base)[1]@tc-4 and
    V2(+4)[0]@tc-2, V2(+4)[1]@tc-4.
    """
    import numpy as np

    pair_fields = {}
    for cf, idx in parsed.expansion_channel_map.items():
        partner = cf ^ 0x4
        if partner in parsed.expansion_channel_map and parsed.expansion_channel_map[partner] == idx:
            pair_fields[idx] = (min(cf, partner), max(cf, partner))

    pair = pair_fields.get(ch_idx)
    base_cf, plus4_cf = pair if pair else (None, None)

    samples = {}  # tc -> (value, priority)

    def put(tc, val, prio):
        existing = samples.get(tc)
        if existing is None or prio > existing[1]:
            samples[tc] = (val, prio)

    for m in parsed.data_messages():
        if m.msg_type != "c" or m.parsed.get("channel_index") != ch_idx:
            continue
        p = m.parsed
        variant = p.get("variant")
        cf = p["channel_field"]
        data = p["data"]
        if variant == "V1":
            tc = p["timecode"]
            put(tc, float(np.frombuffer(data[:2], dtype=np.float16)[0]), 3)
        elif variant == "V2":
            tc = p["timecode"]
            v0 = float(np.frombuffer(data[0:2], dtype=np.float16)[0])
            v1 = float(np.frombuffer(data[2:4], dtype=np.float16)[0])
            if base_cf is not None:
                if cf == base_cf:
                    put(tc, v0, 3)
                    put(tc - 4, v1, 3)
                else:  # +4
                    put(tc - 2, v0, 1)
                    put(tc - 4, v1, 1)
            else:
                # Orphan accelerometer: V2[0]@tc, V2[1]@tc-2
                put(tc, v0, 3)
                put(tc - 2, v1, 3)
        elif variant == "V3":
            tc = p["timecode"]
            if tc < 0:
                continue
            put(tc, float(np.frombuffer(data[:2], dtype=np.float16)[0]), 2)

    return {tc: v for tc, (v, _) in samples.items()}


class TestIssue68SpecVsBackends:
    """Authoritative spec-vs-backend cross-check on the issue68 fixture.

    The spec is the reference wire-format parser. Cython and Rust backends
    are required to produce output identical to the spec's canonical
    decode of V1/V2/V3 (c)-message samples — byte-for-byte, every sample.
    """

    def _decode_spec_channel(self, parsed, ch_idx, expansion_ch_indices, arrays_cache):
        """Produce the spec's canonical samples for one channel.

        Returns ({tc: value}, 'tc') for expansion (V2/V3) channels whose
        tcs are absolute logger-clock values; or ({index: value}, 'pos')
        for ordinary V1/S/M/G channels where the existing builder emits
        samples in file order without absolute tc info.
        """
        if ch_idx in expansion_ch_indices:
            return _decode_c_variant_samples(parsed, ch_idx), "tc"
        exhaustive = TestExhaustiveChannelValues()
        raw = arrays_cache.get(ch_idx, [])
        if not raw:
            return None, None
        values = exhaustive._decode_channel_array(raw, parsed.channels[ch_idx])
        if values is None:
            return None, None
        return values, "pos"

    def _compare_all_channels(self, parsed, backend_log):
        """Assert every channel present in both spec and backend decodes
        identically — same count and same values. Expansion channels
        also check timecode alignment."""
        expansion_ch_indices = set(parsed.expansion_channel_map.values())
        arrays_cache = TestExhaustiveChannelValues()._build_channel_arrays(parsed)
        # CHS names are not always unique — on issue68, RotaryLeft_led,
        # RotaryMiddle_led and RotaryRight_led each have 3 CHS entries.
        # Both backends expose every entry, disambiguated per the AIM DLL
        # "<name> dup <k>" convention; the spec's channel_display_names()
        # defines the same mapping, so display names identify CHS indices
        # uniquely — duplicate-named channels are compared like any other.
        spec_index_by_display_name = {
            name: idx for idx, name in parsed.channel_display_names().items()
        }

        errors = []
        checked = 0

        for name in backend_log.channels:
            ch_idx = spec_index_by_display_name.get(name)
            if ch_idx is None:
                # GPS-derived channels are synthesized by the backend
                # outside CHS; they have no spec equivalent here.
                continue
            spec, kind = self._decode_spec_channel(
                parsed, ch_idx, expansion_ch_indices, arrays_cache
            )
            if spec is None:
                continue
            backend_v = backend_log.channels[name].column(name).to_numpy()

            if kind == "tc":
                backend_tc = backend_log.channels[name].column("timecodes").to_numpy()
                if len(backend_tc) == 0 or not spec:
                    errors.append(
                        f"{name}: empty one side (spec={len(spec)}, backend={len(backend_tc)})"
                    )
                    continue
                offset = min(spec) - int(backend_tc[0])
                spec_session = {tc - offset: v for tc, v in spec.items()}
                if len(spec_session) != len(backend_v):
                    errors.append(
                        f"{name}: count spec={len(spec_session)} backend={len(backend_v)}"
                    )
                    continue
                backend_map = dict(zip(backend_tc.tolist(), backend_v.tolist()))
                diffs = sum(
                    1
                    for tc, v in spec_session.items()
                    if tc not in backend_map or abs(backend_map[tc] - v) > 1e-9
                )
                if diffs:
                    errors.append(f"{name}: {diffs} expansion samples differ")
            else:
                # Ordinary channel: spec gives values in file order.
                if len(spec) != len(backend_v):
                    errors.append(f"{name}: count spec={len(spec)} backend={len(backend_v)}")
                    continue
                # The spec's values carry the channel's value type: the
                # backend column must have it too, with identical values.
                if spec.dtype != backend_v.dtype:
                    errors.append(f"{name}: type spec={spec.dtype} backend={backend_v.dtype}")
                    continue
                diffs = len(_differing(spec, backend_v))
                if diffs:
                    errors.append(f"{name}: {diffs} values differ")
            checked += 1

        assert checked > 20, f"Only {checked} channels compared — fixture wiring broken?"
        assert (
            not errors
        ), f"Spec ↔ backend mismatches ({checked} channels compared):\n" + "\n".join(errors)

    def test_spec_matches_cython_exactly(self, issue68_parsed, issue68_cython):
        """Every channel the Cython backend exposes on issue68 — expansion
        V2/V3 AND ordinary V1/S/M/G alike — must match the spec's
        canonical decode byte-for-byte.
        """
        self._compare_all_channels(issue68_parsed, issue68_cython)

    def test_spec_matches_rust_exactly(self, issue68_parsed, issue68_rust):
        """Every channel the Rust backend exposes on issue68 must match
        the spec's canonical decode byte-for-byte."""
        self._compare_all_channels(issue68_parsed, issue68_rust)
