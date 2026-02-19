"""Parse validation and cross-validation tests for the XRK Construct spec.

Tests in this module verify that:
1. All test files parse without errors or leftover bytes
2. All header message checksums validate correctly
3. Construct-parsed values match the Cython parser for all fields
"""

import struct

import pytest

from spec.xrk_format import (
    ParseResult,
    chs_padding_bytes,
    parse_xrk_file,
    DECODER_TABLE,
)
from spec.tests.conftest import (
    ALL_FILES,
    ALL_XRK_FILES,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _decode_sample(data_bytes, decoder_type):
    """Decode raw data bytes using the decoder type's struct format."""
    if decoder_type not in DECODER_TABLE:
        return None
    fmt = DECODER_TABLE[decoder_type][0]
    size = struct.calcsize(fmt)
    if len(data_bytes) < size:
        return None
    return struct.unpack_from("<" + fmt, data_bytes, 0)[0]


def _apply_fixup(raw_value, decoder_type):
    """Apply decoder fixup to get the final value."""
    import numpy as np

    if decoder_type == 20:
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


def _decode_and_fixup(data_bytes, ch):
    """Decode raw bytes for a channel and apply all fixups."""
    raw = _decode_sample(data_bytes, ch.decoder_type)
    if raw is None:
        return None
    value = _apply_fixup(raw, ch.decoder_type)
    # Voltage channels are stored as mV, Cython divides by 1000
    if ch.units == "V":
        value = value / 1000.0
    return float(value)


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

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_parse_validates(self, filepath):
        """Each file should parse completely and contain expected message types."""
        result = parse_xrk_file(str(filepath))
        assert isinstance(result, ParseResult)
        assert len(result.messages) > 0
        assert (
            result.leftover_bytes < 20
        ), f"{filepath.name}: {result.leftover_bytes} leftover bytes"
        assert len(result.header_messages()) > 0
        assert len(result.channels) > 0
        assert len(result.messages_by_token("LAP")) > 0
        assert len(result.gps_payloads) > 0

    @pytest.mark.parametrize("filepath", ALL_XRK_FILES, ids=lambda p: p.name)
    def test_xrk_xrz_equivalence(self, filepath):
        """XRK and XRZ variants should produce the same channel definitions."""
        xrz = filepath.with_suffix(".xrz")
        if not xrz.exists():
            pytest.skip(f"No XRZ counterpart for {filepath.name}")
        result_xrk = parse_xrk_file(str(filepath))
        result_xrz = parse_xrk_file(str(xrz))
        assert set(result_xrk.channels.keys()) == set(result_xrz.channels.keys())
        for idx in result_xrk.channels:
            assert result_xrk.channels[idx].name == result_xrz.channels[idx].name


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

    def test_channel_names(self, parsed_and_cython):
        """All CHS long_names should match channels known to Cython parser."""
        parsed, cython_log = parsed_and_cython
        spec_names = {ch.name for ch in parsed.channels.values()}
        for name in cython_log.channels:
            # GPS-derived channels don't have CHS entries
            if name.startswith("GPS ") or name.startswith("GPS_"):
                continue
            assert name in spec_names, f"Cython channel {name!r} not in spec CHS definitions"

    def test_channel_metadata(self, parsed_and_cython):
        """Every CHS metadata field should match the Cython parser."""
        parsed, cython_log = parsed_and_cython
        for idx, ch in parsed.channels.items():
            name = ch.name
            if name in self._GPS_COMPUTED_CHANNELS:
                continue  # GPS-derived; Cython creates these independently
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

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_chs_padding_is_zero(self, filepath):
        """All CHS padding fields should be zero across all test files."""
        result = parse_xrk_file(str(filepath))
        for idx, ch in result.channels.items():
            padding = chs_padding_bytes(ch)
            if any(padding):
                name = ch.name
                nonzero = [(i, b) for i, b in enumerate(padding) if b != 0]
                pytest.fail(f"CHS padding non-zero for {name!r}: {nonzero}")

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_chs_data_size_consistent(self, filepath):
        """CHS data_size should match decoder_type expectations."""
        result = parse_xrk_file(str(filepath))
        for idx, ch in result.channels.items():
            dt = ch.decoder_type
            if dt in DECODER_TABLE:
                fmt = DECODER_TABLE[dt][0]
                expected_size = struct.calcsize(fmt)
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

    def test_gps_count(self, parsed_and_cython):
        """GPS message count should match Cython channel row count."""
        parsed, cython_log = parsed_and_cython
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

    def test_lap_count(self, parsed_and_cython):
        """Lap count should match Cython laps table."""
        parsed, cython_log = parsed_and_cython
        unique_laps = _unique_seg0_laps(parsed)
        assert len(unique_laps) == len(cython_log.laps)

    def test_sfj_lap_times(self, sfj_parsed, sfj_cython):
        """SFJ lap end_time and duration should produce matching start/end times."""
        unique = _unique_seg0_laps(sfj_parsed)

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
        assert vet_msgs[-1].payload.value == sfj_cython.metadata["Vehicle Electronics Type"]

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
        result = parse_xrk_file(str(filepath))
        cde_msgs = result.messages_by_token("CDE")
        for m in cde_msgs:
            if m.payload is not None:
                assert (
                    m.payload.channel_index in result.channels
                ), f"CDE channel_index {m.payload.channel_index} not in channels"

    @pytest.mark.parametrize("filepath", ALL_FILES, ids=lambda p: p.name)
    def test_cde_session_uid_consistent(self, filepath):
        """All CDE messages in a file should have the same session_uid."""
        result = parse_xrk_file(str(filepath))
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

    def test_vet_cross_validation(self, issue49_parsed, issue49_cython):
        """VET value should match between spec and Cython parsers."""
        vet_msgs = issue49_parsed.messages_by_token("VET")
        assert len(vet_msgs) > 0
        spec_val = vet_msgs[-1].payload.value
        cython_val = issue49_cython.metadata["Vehicle Electronics Type"]
        assert spec_val == cython_val == "..."

    def test_racm_cross_validation(self, issue49_parsed, issue49_cython):
        """RACM string value should match between spec and Cython parsers."""
        racm_msgs = issue49_parsed.messages_by_token("RACM")
        found_string_mode = None
        for m in racm_msgs:
            if m.payload and m.payload["mode"] == "string":
                found_string_mode = m.payload["value"]
        cython_val = issue49_cython.metadata.get("Race Mode")
        assert found_string_mode == cython_val == "..."

    def test_logger_identity(self, issue49_parsed, issue49_cython):
        """Logger ID and Model ID should match between spec and Cython parsers."""
        src_msgs = issue49_parsed.messages_by_token("SRC")
        idn_msgs = issue49_parsed.messages_by_token("idn")
        found = None
        if src_msgs and src_msgs[-1].payload:
            found = src_msgs[-1].payload
        elif idn_msgs:
            found = idn_msgs[-1].payload
        assert found is not None
        assert found["logger_id"] == issue49_cython.metadata["Logger ID"]
        assert found["model_id"] == issue49_cython.metadata["Logger Model ID"]

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
        """Collect first and last data sample for each channel from data messages.

        Handles S, M, c, and G message types.
        Returns {channel_index: {"first": bytes, "last": bytes}}.
        """
        first = {}  # channel_index -> first raw data bytes
        last = {}  # channel_index -> last raw data bytes

        for m in result.data_messages():
            if m.msg_type == "S" or m.msg_type == "c":
                idx = m.parsed["channel_index"]
                data = m.parsed["data"]
                if idx not in first:
                    first[idx] = data
                last[idx] = data

            elif m.msg_type == "M":
                idx = m.parsed["channel_index"]
                if idx not in result.channels:
                    continue
                ch_size = result.channel_sizes[idx]
                data = m.parsed["data"]
                count = m.parsed["count"]
                if count > 0 and len(data) >= ch_size:
                    first_sample = data[:ch_size]
                    last_sample = data[(count - 1) * ch_size : count * ch_size]
                    if idx not in first:
                        first[idx] = first_sample
                    last[idx] = last_sample

            elif m.msg_type == "G":
                grp_idx = m.parsed["group_index"]
                if grp_idx not in result.groups:
                    continue
                grp = result.groups[grp_idx]
                data = m.parsed["data"]
                offset = 0
                for ch_idx in grp.channel_indices:
                    if ch_idx not in result.channel_sizes:
                        continue
                    ch_size = result.channel_sizes[ch_idx]
                    sample = data[offset : offset + ch_size]
                    if len(sample) == ch_size:
                        if ch_idx not in first:
                            first[ch_idx] = sample
                        last[ch_idx] = sample
                    offset += ch_size

        return {idx: {"first": first[idx], "last": last.get(idx, first[idx])} for idx in first}

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
            if ch.decoder_type not in DECODER_TABLE:
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

    def test_all_channels_first_sample(self, parsed_and_cython):
        """First sample for every channel should match Cython output."""
        parsed, cython_log = parsed_and_cython
        self._check_first_last_samples(parsed, cython_log, "first")

    def test_all_channels_last_sample(self, parsed_and_cython):
        """Last sample for every channel should match Cython output."""
        parsed, cython_log = parsed_and_cython
        self._check_first_last_samples(parsed, cython_log, "last")

    def test_decoder_type_coverage(self, parsed_and_cython):
        """Every decoder type with Cython-exposed channels should be validated."""
        parsed, cython_log = parsed_and_cython
        samples = self._collect_channel_samples(parsed)
        # Only count decoder types that have at least one Cython-exposed channel
        decoder_types_exposed = set()
        decoder_types_validated = set()

        for idx, ch in parsed.channels.items():
            if ch.decoder_type not in DECODER_TABLE:
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

    def test_86_g_message_group_unpacking(self, file_86_parsed, file_86_cython):
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
                if name not in file_86_cython.channels:
                    continue
                if ch.decoder_type not in DECODER_TABLE:
                    continue

                value = _decode_and_fixup(sample_bytes, ch)
                if value is None:
                    continue

                cython_first = file_86_cython.channels[name].column(name)[0].as_py()
                assert abs(value - float(cython_first)) < 0.01, (
                    f"Group {grp_idx}, Ch {name!r}: " f"unpacked {value} != cython {cython_first}"
                )
                checked += 1

        assert checked > 0, "No G-message channels were cross-validated"


# ---------------------------------------------------------------------------
# Exhaustive cross-validation: every sample of every channel
# ---------------------------------------------------------------------------


class TestExhaustiveChannelValues:
    """Compare EVERY decoded channel value against Cython output.

    Unlike TestDataMessageCrossValidation which only checks first/last samples,
    this class builds complete channel arrays from ALL data messages and compares
    every single value against the Cython parser's output.
    """

    def _build_channel_arrays(self, result):
        """Build {channel_index: [raw_bytes, ...]} from all data messages.

        Matches Cython deduplication: only accepts messages whose timecode is
        strictly greater than the last seen timecode for that accumulator.
        The Cython parser (aim_xrk.pyx:303,320,343) uses separate accumulators
        per message type, but since a channel is delivered by exactly one type
        (G xor S/c xor M), we track last_timecode per (msg_type_bucket, index).
        """
        arrays = {}  # channel_index -> list of raw data bytes
        # Separate last_timecode trackers per accumulator, matching Cython's gc_data[0..3]
        last_tc_g = {}  # group_index -> last timecode (G messages)
        last_tc_s = {}  # channel_index -> last timecode (S messages)
        last_tc_c = {}  # channel_index -> last timecode (c messages)
        last_tc_m = {}  # channel_index -> last timecode (M messages)

        for m in result.data_messages():
            if m.msg_type == "S":
                idx = m.parsed["channel_index"]
                tc = m.parsed["timecode"]
                if tc <= last_tc_s.get(idx, -1):
                    continue
                last_tc_s[idx] = tc
                if idx not in arrays:
                    arrays[idx] = []
                arrays[idx].append(m.parsed["data"])

            elif m.msg_type == "c":
                idx = m.parsed["channel_index"]
                tc = m.parsed["timecode"]
                if tc <= last_tc_c.get(idx, -1):
                    continue
                last_tc_c[idx] = tc
                if idx not in arrays:
                    arrays[idx] = []
                arrays[idx].append(m.parsed["data"])

            elif m.msg_type == "M":
                idx = m.parsed["channel_index"]
                if idx not in result.channels:
                    continue
                tc = m.parsed["timecode"]
                ch_size = result.channel_sizes[idx]
                data = m.parsed["data"]
                count = m.parsed["count"]
                mms = result.channel_mms.get(idx, 0)
                # Partial skip: only skip overlapping front samples (matches Cython)
                m_skip = 0
                if tc <= last_tc_m.get(idx, -1) and mms > 0:
                    m_skip = (last_tc_m[idx] - tc) // mms + 1
                if m_skip >= count:
                    continue
                last_tc_m[idx] = tc + (count - 1) * mms
                if idx not in arrays:
                    arrays[idx] = []
                for i in range(m_skip, count):
                    sample = data[i * ch_size : (i + 1) * ch_size]
                    if len(sample) == ch_size:
                        arrays[idx].append(sample)

            elif m.msg_type == "G":
                grp_idx = m.parsed["group_index"]
                if grp_idx not in result.groups:
                    continue
                tc = m.parsed["timecode"]
                if tc <= last_tc_g.get(grp_idx, -1):
                    continue
                last_tc_g[grp_idx] = tc
                grp = result.groups[grp_idx]
                data = m.parsed["data"]
                offset = 0
                for ch_idx in grp.channel_indices:
                    if ch_idx not in result.channel_sizes:
                        continue
                    ch_size = result.channel_sizes[ch_idx]
                    sample = data[offset : offset + ch_size]
                    if len(sample) == ch_size:
                        if ch_idx not in arrays:
                            arrays[ch_idx] = []
                        arrays[ch_idx].append(sample)
                    offset += ch_size

        return arrays

    def _decode_channel_array(self, raw_samples, ch):
        """Decode a list of raw byte samples into float values."""
        values = []
        is_voltage = ch.units == "V"
        for sample_bytes in raw_samples:
            raw = _decode_sample(sample_bytes, ch.decoder_type)
            if raw is None:
                return None
            value = _apply_fixup(raw, ch.decoder_type)
            if is_voltage:
                value = value / 1000.0
            values.append(float(value))
        return values

    def _compare_all_values(self, result, cython_log):
        """Compare every sample of every channel. Returns (checked_channels, errors)."""
        import math

        arrays = self._build_channel_arrays(result)
        checked = 0
        errors = []

        for idx, ch in result.channels.items():
            name = ch.name
            if name not in cython_log.channels:
                continue
            if idx not in arrays:
                continue
            if ch.decoder_type not in DECODER_TABLE:
                continue

            spec_values = self._decode_channel_array(arrays[idx], ch)
            if spec_values is None:
                continue

            cython_col = cython_log.channels[name].column(name)
            cython_values = cython_col.to_pylist()

            if len(spec_values) != len(cython_values):
                errors.append(
                    f"Ch {name!r}: length mismatch spec={len(spec_values)} "
                    f"cython={len(cython_values)}"
                )
                continue

            for i, (sv, cv) in enumerate(zip(spec_values, cython_values)):
                cv = float(cv)
                # Handle NaN
                if math.isnan(sv) and math.isnan(cv):
                    continue
                if math.isnan(sv) or math.isnan(cv):
                    errors.append(f"Ch {name!r} sample {i}: NaN mismatch " f"spec={sv} cython={cv}")
                    break
                if abs(sv - cv) >= 0.01:
                    errors.append(
                        f"Ch {name!r} sample {i}/{len(spec_values)}: "
                        f"spec={sv} cython={cv} diff={abs(sv - cv)}"
                    )
                    break  # One error per channel is enough for diagnostics

            checked += 1

        return checked, errors

    def test_aim_official_every_value(self, aim_official_parsed, aim_official_cython):
        """Every sample of every AIM official channel must match Cython (3.1M)."""
        checked, errors = self._compare_all_values(aim_official_parsed, aim_official_cython)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)

    def test_sfj_every_value(self, sfj_parsed, sfj_cython):
        """Every sample of every SFJ channel must match Cython (7.9M)."""
        checked, errors = self._compare_all_values(sfj_parsed, sfj_cython)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)

    def test_86_every_value(self, file_86_parsed, file_86_cython):
        """Every sample of every 86 channel must match Cython (40M)."""
        checked, errors = self._compare_all_values(file_86_parsed, file_86_cython)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, f"Value mismatches:\n" + "\n".join(errors)


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
            if ch.decoder_type not in DECODER_TABLE:
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
