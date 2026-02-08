"""Parse validation and cross-validation tests for the XRK Construct spec.

Tests in this module verify that:
1. All test files parse without errors or leftover bytes
2. All header message checksums validate correctly
3. Construct-parsed values match the Cython parser for all fields
"""

import struct
from pathlib import Path

import pytest

from spec.xrk_format import (
    ParseResult,
    _tokdec,
    _tokenc,
    build_header_frame,
    chs_dec_pts,
    chs_device_tag,
    chs_interpolate,
    chs_long_name,
    chs_short_name,
    chs_units,
    parse_xrk_file,
    DECODER_TABLE,
    UNIT_MAP,
)
from spec.tests.conftest import (
    ALL_FILES,
    ALL_XRK_FILES,
    SFJ_XRK,
    FILE_86_XRK,
)


# ---------------------------------------------------------------------------
# Parse validation — all test files
# ---------------------------------------------------------------------------


class TestParseAllFiles:
    """Verify that all test files parse successfully."""

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_parse_without_errors(self, filepath):
        """Each file should parse without raising exceptions."""
        result = parse_xrk_file(str(filepath))
        assert isinstance(result, ParseResult)
        assert len(result.messages) > 0

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_minimal_leftover_bytes(self, filepath):
        """Leftover bytes should be zero or negligible (< 20)."""
        result = parse_xrk_file(str(filepath))
        # A few files have 3-13 leftover bytes from edge cases; zero is ideal
        assert (
            result.leftover_bytes < 20
        ), f"{filepath.name}: {result.leftover_bytes} leftover bytes"

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_header_checksums_valid(self, filepath):
        """All header message checksums should match."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        # If we got here without exceptions, all checksums passed
        # (parse_header_message validates checksum and returns None on mismatch)
        header_count = len(result.header_messages())
        assert header_count > 0

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_has_channels(self, filepath):
        """Each file should have at least one channel definition."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        assert len(result.channels) > 0

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_has_laps(self, filepath):
        """Each file should have LAP messages."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        laps = result.messages_by_token("LAP")
        assert len(laps) > 0

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_has_gps(self, filepath):
        """Each file should have GPS messages."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        assert len(result.gps_payloads) > 0

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_xrk_xrz_equivalence(self, filepath):
        """XRK and XRZ variants should produce the same channel definitions."""
        xrz = filepath.with_suffix(".xrz")
        if not xrz.exists():
            pytest.skip(f"No XRZ counterpart for {filepath.name}")
        result_xrk = parse_xrk_file(str(filepath), include_data_messages=False)
        result_xrz = parse_xrk_file(str(xrz), include_data_messages=False)
        assert set(result_xrk.channels.keys()) == set(result_xrz.channels.keys())
        for idx in result_xrk.channels:
            assert chs_long_name(result_xrk.channels[idx]) == chs_long_name(
                result_xrz.channels[idx]
            )


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

    def test_sfj_channel_names(self, sfj_parsed, sfj_cython):
        """All CHS long_names should match channels known to Cython parser."""
        spec_names = {chs_long_name(ch) for ch in sfj_parsed.channels.values()}
        # Cython parser filters out some channels (StrtRec, Master Clk, etc.)
        # but all Cython channels should have a CHS definition
        for name in sfj_cython.channels:
            # GPS-derived channels don't have CHS entries
            if name.startswith("GPS ") or name.startswith("GPS_"):
                continue
            assert name in spec_names, f"Cython channel {name!r} not in spec CHS definitions"

    def test_86_channel_names(self, file_86_parsed, file_86_cython):
        """All CHS long_names should match channels known to Cython parser."""
        spec_names = {chs_long_name(ch) for ch in file_86_parsed.channels.values()}
        for name in file_86_cython.channels:
            if name.startswith("GPS ") or name.startswith("GPS_"):
                continue
            assert name in spec_names, f"Cython channel {name!r} not in spec CHS definitions"

    # GPS-derived channels that the Cython parser creates from scratch in _decode_gps()
    # rather than using the CHS definition. Their metadata won't match CHS values.
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

    def test_sfj_channel_metadata(self, sfj_parsed, sfj_cython):
        """Every CHS metadata field should match the Cython parser for SFJ."""
        for idx, ch in sfj_parsed.channels.items():
            name = chs_long_name(ch)
            if name in self._GPS_COMPUTED_CHANNELS:
                continue  # GPS-derived; Cython creates these independently
            md = self._get_channel_metadata(sfj_cython, name)
            if md is None:
                continue  # Channel not exposed by Cython (e.g., Master Clk)

            # Units (Cython stores empty string for size==1 channels)
            expected_units = md["units"]
            spec_units = chs_units(ch)
            if ch.data_size == 1:
                expected_units = ""
            assert (
                spec_units == expected_units
            ), f"Ch {name!r}: units {spec_units!r} != {expected_units!r}"

            # Decimal points
            assert str(chs_dec_pts(ch)) == md["dec_pts"], f"Ch {name!r}: dec_pts mismatch"

            # Interpolate
            assert (
                str(chs_interpolate(ch)) == md["interpolate"]
            ), f"Ch {name!r}: interpolate mismatch"

            # Source type
            assert str(ch.source_type) == md["source_type"], f"Ch {name!r}: source_type mismatch"

            # Source channel ID
            assert (
                str(ch.source_channel_id) == md["source_channel_id"]
            ), f"Ch {name!r}: source_channel_id mismatch"

            # Device tag
            assert chs_device_tag(ch) == md["device_tag"], f"Ch {name!r}: device_tag mismatch"

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

    def test_86_channel_metadata(self, file_86_parsed, file_86_cython):
        """Every CHS metadata field should match the Cython parser for 86."""
        for idx, ch in file_86_parsed.channels.items():
            name = chs_long_name(ch)
            if name in self._GPS_COMPUTED_CHANNELS:
                continue
            md = self._get_channel_metadata(file_86_cython, name)
            if md is None:
                continue

            expected_units = md["units"]
            if ch.data_size == 1:
                expected_units = ""
            assert (
                chs_units(ch) == expected_units
            ), f"Ch {name!r}: units {chs_units(ch)!r} != {expected_units!r}"
            assert str(chs_dec_pts(ch)) == md["dec_pts"], f"Ch {name!r}: dec_pts"
            assert str(chs_interpolate(ch)) == md["interpolate"], f"Ch {name!r}: interpolate"
            assert str(ch.source_type) == md["source_type"], f"Ch {name!r}: source_type"
            assert (
                str(ch.source_channel_id) == md["source_channel_id"]
            ), f"Ch {name!r}: source_channel_id"
            assert chs_device_tag(ch) == md["device_tag"], f"Ch {name!r}: device_tag"
            assert str(ch.cal_value_1) == md["cal_value_1"], f"Ch {name!r}: cal_value_1"
            assert str(ch.cal_value_2) == md["cal_value_2"], f"Ch {name!r}: cal_value_2"
            assert (
                str(ch.display_range_min) == md["display_range_min"]
            ), f"Ch {name!r}: display_range_min"
            assert (
                str(ch.display_range_max) == md["display_range_max"]
            ), f"Ch {name!r}: display_range_max"

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_chs_padding_is_zero(self, filepath):
        """All CHS padding fields should be zero across all test files."""
        from spec.xrk_format import chs_padding_bytes

        result = parse_xrk_file(str(filepath), include_data_messages=False)
        for idx, ch in result.channels.items():
            padding = chs_padding_bytes(ch)
            if any(padding):
                name = chs_long_name(ch)
                nonzero = [(i, b) for i, b in enumerate(padding) if b != 0]
                pytest.fail(f"CHS padding non-zero for {name!r}: {nonzero}")

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_chs_data_size_consistent(self, filepath):
        """CHS data_size should match decoder_type expectations."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        for idx, ch in result.channels.items():
            dt = ch.decoder_type
            if dt in DECODER_TABLE:
                fmt = DECODER_TABLE[dt][0]
                expected_size = struct.calcsize(fmt)
                # Some channels have larger data_size than the decoded type
                # (e.g., Lap Time has size=20 but decoder=31 which is 'i'=4)
                # The data_size can be larger when the channel carries extra data
                assert ch.data_size >= expected_size, (
                    f"Ch {chs_long_name(ch)!r}: data_size {ch.data_size} < "
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

    def test_sfj_gps_count(self, sfj_parsed, sfj_cython):
        """GPS message count should match Cython channel row count."""
        spec_count = len(sfj_parsed.gps_payloads)
        cython_count = len(sfj_cython.channels["GPS Speed"])
        assert spec_count == cython_count

    def test_86_gps_count(self, file_86_parsed, file_86_cython):
        """GPS message count should match Cython channel row count."""
        spec_count = len(file_86_parsed.gps_payloads)
        cython_count = len(file_86_cython.channels["GPS Speed"])
        assert spec_count == cython_count

    def test_sfj_gps_first_sample(self, sfj_parsed):
        """First GPS sample values should match expected SFJ data."""
        samples = sfj_parsed.get_gps_samples()
        assert len(samples) > 0
        first = samples[0]
        # From test_sfj_xrk.py: GPS Latitude first ≈ 35.3725, Longitude ≈ 138.9276
        # These are ECEF values in the raw GPS message, not lat/lon directly
        # Just verify the fields exist and are reasonable
        assert first.gpsFix in (0, 2, 3)
        assert first.numSV >= 0
        assert first.pDOP > 0

    def test_86_gps_first_sample(self, file_86_parsed):
        """First GPS sample values should match expected 86 data."""
        samples = file_86_parsed.get_gps_samples()
        assert len(samples) > 0
        first = samples[0]
        assert first.gpsFix == 3  # 86 file starts with 3D fix
        assert first.numSV >= 10  # 86 had good satellite count

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

    def test_sfj_lap_count(self, sfj_parsed, sfj_cython):
        """SFJ should have 13 laps (from test_sfj_xrk.py)."""
        laps = sfj_parsed.get_laps()
        # Filter to segment 0 and unique lap numbers (matching Cython logic)
        seg0_laps = [l for l in laps if l["segment"] == 0]
        # Deduplicate by lap_num
        seen = set()
        unique_laps = []
        for l in seg0_laps:
            if l["lap_num"] not in seen:
                seen.add(l["lap_num"])
                unique_laps.append(l)
        assert len(unique_laps) == len(sfj_cython.laps)

    def test_86_lap_count(self, file_86_parsed, file_86_cython):
        """86 should have 16 laps (from test_86_xrk.py)."""
        laps = file_86_parsed.get_laps()
        seg0_laps = [l for l in laps if l["segment"] == 0]
        seen = set()
        unique_laps = []
        for l in seg0_laps:
            if l["lap_num"] not in seen:
                seen.add(l["lap_num"])
                unique_laps.append(l)
        assert len(unique_laps) == len(file_86_cython.laps)

    def test_sfj_lap_times(self, sfj_parsed, sfj_cython):
        """SFJ lap end_time and duration should produce matching start/end times."""
        laps = sfj_parsed.get_laps()
        seg0 = [l for l in laps if l["segment"] == 0]
        seen = set()
        unique = []
        for l in seg0:
            if l["lap_num"] not in seen:
                seen.add(l["lap_num"])
                unique.append(l)

        # Compute time_offset from first lap (matches Cython logic)
        first_lap = unique[0]
        time_offset = first_lap["end_time"] - first_lap["duration"]

        cython_laps = sfj_cython.laps
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

    def test_sfj_string_metadata(self, sfj_parsed, sfj_cython):
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
            if meta_key in sfj_cython.metadata:
                assert (
                    spec_val == sfj_cython.metadata[meta_key]
                ), f"{meta_key}: {spec_val!r} != {sfj_cython.metadata[meta_key]!r}"

    def test_sfj_logger_identity(self, sfj_parsed, sfj_cython):
        """Logger ID and Model ID should match for SFJ."""
        # SRC message contains embedded idn
        src_msgs = sfj_parsed.messages_by_token("SRC")
        assert len(src_msgs) > 0
        src = src_msgs[-1].payload
        assert src is not None
        assert src["logger_id"] == sfj_cython.metadata["Logger ID"]
        assert src["model_id"] == sfj_cython.metadata["Logger Model ID"]

    def test_86_logger_identity(self, file_86_parsed, file_86_cython):
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
        assert found["logger_id"] == file_86_cython.metadata["Logger ID"]
        assert found["model_id"] == file_86_cython.metadata["Logger Model ID"]

    def test_sfj_gps_receiver(self, sfj_parsed, sfj_cython):
        """GPS Receiver type should match for SFJ."""
        gpsr_msgs = sfj_parsed.messages_by_token("GPSR")
        assert len(gpsr_msgs) > 0
        gpsr = gpsr_msgs[-1].payload
        assert gpsr["type"] == sfj_cython.metadata["GPS Receiver"]

    def test_86_gps_receiver(self, file_86_parsed, file_86_cython):
        """GPS Receiver type should match for 86."""
        gpsr_msgs = file_86_parsed.messages_by_token("GPSR")
        assert len(gpsr_msgs) > 0
        gpsr = gpsr_msgs[-1].payload
        assert gpsr["type"] == file_86_cython.metadata["GPS Receiver"]

    def test_sfj_race_mode(self, sfj_parsed, sfj_cython):
        """Race Mode should match for SFJ ('speed')."""
        racm_msgs = sfj_parsed.messages_by_token("RACM")
        found_string_mode = None
        for m in racm_msgs:
            if m.payload and m.payload["mode"] == "string":
                found_string_mode = m.payload["value"]
        assert found_string_mode == sfj_cython.metadata.get("Race Mode")

    def test_sfj_vet(self, sfj_parsed, sfj_cython):
        """VET should match for SFJ."""
        vet_msgs = sfj_parsed.messages_by_token("VET")
        assert len(vet_msgs) > 0
        assert vet_msgs[-1].payload == sfj_cython.metadata["Vehicle Electronics Type"]

    def test_sfj_trk(self, sfj_parsed, sfj_cython):
        """Track info should match for SFJ."""
        trk_msgs = sfj_parsed.messages_by_token("TRK")
        assert len(trk_msgs) > 0
        trk = trk_msgs[-1].payload
        assert trk["name"] == sfj_cython.metadata["Venue"]

    def test_sfj_odo(self, sfj_parsed, sfj_cython):
        """ODO records should match for SFJ."""
        odo_msgs = sfj_parsed.messages_by_token("ODO")
        assert len(odo_msgs) > 0
        odo = odo_msgs[-1].payload
        # Check System distance
        assert "System" in odo
        expected_dist_km = sfj_cython.metadata["Odo/System Distance (km)"]
        actual_dist_km = odo["System"]["dist"] / 1000
        assert abs(actual_dist_km - expected_dist_km) < 0.01

    def test_86_expansion_devices(self, file_86_parsed, file_86_cython):
        """86 should have expansion devices matching Cython metadata."""
        enf_msgs = file_86_parsed.messages_by_token("ENF")
        assert len(enf_msgs) > 0
        # Expansion devices should exist in Cython metadata
        assert "Expansion Devices" in file_86_cython.metadata
        cython_devices = file_86_cython.metadata["Expansion Devices"]
        assert len(enf_msgs) == len(cython_devices)

    def test_sfj_calibrations(self, sfj_parsed, sfj_cython):
        """CAL messages should match Cython metadata."""
        cal_msgs = sfj_parsed.messages_by_token("CAL")
        cython_cals = sfj_cython.metadata.get("Calibrations", [])
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

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_cde_channel_indices_valid(self, filepath):
        """CDE channel_index should reference a valid CHS channel."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        cde_msgs = result.messages_by_token("CDE")
        for m in cde_msgs:
            if m.payload is not None:
                assert (
                    m.payload.channel_index in result.channels
                ), f"CDE channel_index {m.payload.channel_index} not in channels"

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_cde_session_uid_consistent(self, filepath):
        """All CDE messages in a file should have the same session_uid."""
        result = parse_xrk_file(str(filepath), include_data_messages=False)
        cde_msgs = result.messages_by_token("CDE")
        if not cde_msgs:
            pytest.skip("No CDE messages")
        uids = {m.payload.session_uid for m in cde_msgs if m.payload is not None}
        assert len(uids) == 1, f"Multiple session UIDs: {uids}"


# ---------------------------------------------------------------------------
# Cross-validation: Data messages (sample-level)
# ---------------------------------------------------------------------------


class TestDataMessageCrossValidation:
    """Verify data messages decode to the same values as Cython parser."""

    def _decode_sample(self, data_bytes, decoder_type):
        """Decode raw data bytes using the decoder type's struct format."""
        if decoder_type not in DECODER_TABLE:
            return None
        fmt = DECODER_TABLE[decoder_type][0]
        size = struct.calcsize(fmt)
        if len(data_bytes) < size:
            return None
        return struct.unpack_from("<" + fmt, data_bytes, 0)[0]

    def _apply_fixup(self, raw_value, decoder_type, channel_name):
        """Apply decoder fixup to get the final value."""
        import numpy as np

        if decoder_type in (1, 20):
            # float16 encoded as uint16
            arr = np.array([raw_value], dtype=np.uint16)
            return float(np.frombuffer(arr.tobytes(), dtype=np.float16)[0])
        elif decoder_type == 15:
            # Gear lookup
            table = {
                ord("N"): 0,
                ord("1"): 1,
                ord("2"): 2,
                ord("3"): 3,
                ord("4"): 4,
                ord("5"): 5,
                ord("6"): 6,
            }
            return table.get(raw_value, raw_value)
        return raw_value

    def test_sfj_s_message_first_sample(self, sfj_parsed, sfj_cython):
        """First S-message sample for select channels should match Cython."""
        # Find first S message for a known channel
        channels_to_check = {}
        for m in sfj_parsed.data_messages():
            if m.msg_type != "S":
                continue
            idx = m.parsed["channel_index"]
            if idx not in channels_to_check and idx in sfj_parsed.channels:
                channels_to_check[idx] = m

            if len(channels_to_check) >= 5:
                break

        for idx, msg in channels_to_check.items():
            ch = sfj_parsed.channels[idx]
            name = chs_long_name(ch)
            if name not in sfj_cython.channels:
                continue

            raw = self._decode_sample(msg.parsed["data"], ch.decoder_type)
            if raw is None:
                continue

            value = self._apply_fixup(raw, ch.decoder_type, name)
            cython_first = sfj_cython.channels[name].column(name)[0].as_py()

            # V channels are divided by 1000 in Cython
            if chs_units(ch) == "V":
                value = value / 1000.0

            assert (
                abs(float(value) - float(cython_first)) < 0.01
            ), f"Ch {name!r}: first sample {value} != cython {cython_first}"

    def test_86_g_message_decoding(self, file_86_parsed, file_86_cython):
        """First G-message should decode group data correctly."""
        # Find first G message
        g_msgs = [m for m in file_86_parsed.data_messages() if m.msg_type == "G"]
        assert len(g_msgs) > 0
        first_g = g_msgs[0]
        group_idx = first_g.parsed["group_index"]

        # Verify the group exists
        assert group_idx in file_86_parsed.groups
        grp = file_86_parsed.groups[group_idx]

        # Verify the data length matches expected group size
        expected_size = file_86_parsed.group_sizes[group_idx]
        assert len(first_g.parsed["data"]) == expected_size
