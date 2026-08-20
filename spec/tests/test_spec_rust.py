"""Spec <-> Rust backend cross-validation.

Mirrors the spec <-> Cython checks in test_spec.py for the Rust backend, so
that both backends are validated directly against the Construct reference
spec (not merely against each other):

  * every decoded sample of every channel (exhaustive, 3 fixtures)
  * lap counts
  * key metadata (logger identity, GPS receiver, venue, VET, race mode,
    odometer, calibrations)

The issue68 fixture's spec-vs-Rust check (expansion V2/V3 channels) already
lives in test_spec.py:TestIssue68SpecVsBackends.
"""

import pytest

from spec.tests.conftest import (
    AIM_OFFICIAL_XRK,
    FILE_86_XRK,
    SFJ_XRK,
)
from spec.tests.test_spec import TestExhaustiveChannelValues, _unique_seg0_laps


def _rust_load(path):
    try:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
    except ImportError:
        pytest.skip("Rust backend not available")
    return rust_aim_xrk(str(path))


@pytest.fixture(scope="session")
def sfj_rust():
    return _rust_load(SFJ_XRK)


@pytest.fixture(scope="session")
def file_86_rust():
    return _rust_load(FILE_86_XRK)


@pytest.fixture(scope="session")
def aim_official_rust():
    return _rust_load(AIM_OFFICIAL_XRK)


class TestExhaustiveChannelValuesVsRust:
    """Every decoded channel value must match the spec's canonical decode."""

    def test_sfj_every_value(self, sfj_parsed, sfj_rust):
        checked, errors = TestExhaustiveChannelValues()._compare_all_values(sfj_parsed, sfj_rust)
        assert checked > 0, "No channels were cross-validated"
        assert not errors, "Value mismatches:\n" + "\n".join(errors)

    def test_86_every_value(self, file_86_parsed, file_86_rust):
        checked, errors = TestExhaustiveChannelValues()._compare_all_values(
            file_86_parsed, file_86_rust
        )
        assert checked > 0, "No channels were cross-validated"
        assert not errors, "Value mismatches:\n" + "\n".join(errors)

    def test_aim_official_every_value(self, aim_official_parsed, aim_official_rust):
        checked, errors = TestExhaustiveChannelValues()._compare_all_values(
            aim_official_parsed, aim_official_rust
        )
        assert checked > 0, "No channels were cross-validated"
        assert not errors, "Value mismatches:\n" + "\n".join(errors)


class TestLAPVsRust:
    """Lap counts must match the spec's segment-0 LAP messages."""

    def test_sfj_lap_count(self, sfj_parsed, sfj_rust):
        assert len(_unique_seg0_laps(sfj_parsed)) == len(sfj_rust.laps)

    def test_86_lap_count(self, file_86_parsed, file_86_rust):
        assert len(_unique_seg0_laps(file_86_parsed)) == len(file_86_rust.laps)

    def test_aim_official_lap_count(self, aim_official_parsed, aim_official_rust):
        # aim_official carries v2 (32-byte) LAP payloads; both the spec and
        # the backends read the absolute end time at [28:32].
        assert len(_unique_seg0_laps(aim_official_parsed)) == len(aim_official_rust.laps)

    def test_aim_official_v2_lap_times_match_dll(self, aim_official_dll, aim_official_rust):
        """The Rust backend's v2 LAP laps must reproduce the official DLL's
        lap table exactly, to the millisecond."""
        dll_laps = aim_official_dll["laps"]
        laps = aim_official_rust.laps
        assert len(dll_laps) == laps.num_rows
        assert [lap["start_ms"] for lap in dll_laps] == laps.column("start_time").to_pylist()
        assert [lap["end_ms"] for lap in dll_laps] == laps.column("end_time").to_pylist()


class TestGPSVsRust:
    """GPS record counts must match the spec's GPS payload count."""

    def test_sfj_gps_count(self, sfj_parsed, sfj_rust):
        assert len(sfj_parsed.gps_payloads) == len(sfj_rust.channels["GPS Speed"])

    def test_86_gps_count(self, file_86_parsed, file_86_rust):
        assert len(file_86_parsed.gps_payloads) == len(file_86_rust.channels["GPS Speed"])


class TestMetadataVsRust:
    """Key metadata must match the spec's message payloads."""

    def test_sfj_string_metadata(self, sfj_parsed, sfj_rust):
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
            if meta_key in sfj_rust.metadata:
                assert msgs[-1].payload == sfj_rust.metadata[meta_key], meta_key

    def test_sfj_logger_identity(self, sfj_parsed, sfj_rust):
        src = sfj_parsed.messages_by_token("SRC")[-1].payload
        assert src["logger_id"] == sfj_rust.metadata["Logger ID"]
        assert src["model_id"] == sfj_rust.metadata["Logger Model ID"]

    def test_86_logger_identity(self, file_86_parsed, file_86_rust):
        idn_msgs = file_86_parsed.messages_by_token("idn")
        src_msgs = file_86_parsed.messages_by_token("SRC")
        found = None
        if idn_msgs:
            found = idn_msgs[-1].payload
        elif src_msgs and src_msgs[-1].payload:
            found = src_msgs[-1].payload
        assert found is not None
        assert found["logger_id"] == file_86_rust.metadata["Logger ID"]
        assert found["model_id"] == file_86_rust.metadata["Logger Model ID"]

    def test_sfj_gps_receiver(self, sfj_parsed, sfj_rust):
        gpsr = sfj_parsed.messages_by_token("GPSR")[-1].payload
        assert gpsr["type"] == sfj_rust.metadata["GPS Receiver"]

    def test_sfj_venue(self, sfj_parsed, sfj_rust):
        trk = sfj_parsed.messages_by_token("TRK")[-1].payload
        assert trk["name"] == sfj_rust.metadata["Venue"]

    def test_sfj_vet(self, sfj_parsed, sfj_rust):
        vet = sfj_parsed.messages_by_token("VET")[-1].payload
        assert vet.value == sfj_rust.metadata["Vehicle Electronics Type"]

    def test_sfj_race_mode(self, sfj_parsed, sfj_rust):
        found_string_mode = None
        for m in sfj_parsed.messages_by_token("RACM"):
            if m.payload and m.payload["mode"] == "string":
                found_string_mode = m.payload["value"]
        assert found_string_mode == sfj_rust.metadata.get("Race Mode")

    def test_sfj_odo(self, sfj_parsed, sfj_rust):
        odo = sfj_parsed.messages_by_token("ODO")[-1].payload
        expected_km = sfj_rust.metadata["Odo/System Distance (km)"]
        assert abs(odo["System"]["dist"] / 1000 - expected_km) < 0.01

    def test_sfj_calibrations(self, sfj_parsed, sfj_rust):
        cal_msgs = sfj_parsed.messages_by_token("CAL")
        rust_cals = sfj_rust.metadata.get("Calibrations", [])
        assert len(cal_msgs) == len(rust_cals)
        for msg, cal in zip(cal_msgs, rust_cals):
            assert msg.payload["type"] == cal["type"]
            assert abs(msg.payload["raw_1"] - cal["raw_1"]) < 1e-6
            assert abs(msg.payload["raw_2"] - cal["raw_2"]) < 1e-6
