"""End-to-end tests for libxrk SFJ XRK/XRZ file reading."""

import unittest
from pathlib import Path
from typing import ClassVar

import numpy as np
import pyarrow as pa
from parameterized import parameterized

from libxrk import aim_xrk
from libxrk.base import LogFile


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK_FILE = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
SFJ_XRZ_FILE = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrz"

# Test file variants for parameterized tests
FILE_VARIANTS = [
    ("xrk", SFJ_XRK_FILE),
    ("xrz", SFJ_XRZ_FILE),
]


class TestSFJXRK(unittest.TestCase):
    """Tests for loading and parsing the SFJ test vector XRK/XRZ files."""

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_file_exists(self, name, file_path):
        """Verify the test data file exists."""
        self.assertTrue(file_path.exists(), f"Test file not found: {file_path}")

    @parameterized.expand(FILE_VARIANTS)
    def test_load_sfj_xrk_file(self, name, file_path):
        """Test loading the SFJ XRK/XRZ file."""
        # Load the file
        log = aim_xrk(str(file_path), progress=None)

        # Verify basic structure
        self.assertIsNotNone(log, "aim_xrk returned None")
        self.assertIsNotNone(log.channels, "LogFile.channels is None")
        self.assertIsNotNone(log.laps, "LogFile.laps is None")
        self.assertIsNotNone(log.metadata, "LogFile.metadata is None")

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_metadata(self, name, file_path):
        """Test that the SFJ XRK/XRZ file contains metadata."""
        log = aim_xrk(str(file_path), progress=None)

        # Should have metadata
        self.assertIsInstance(log.metadata, dict, "Expected metadata to be a dict")

        # Expected metadata - core fields that should be present in both XRK and XRZ
        core_metadata = {
            "Log Date": "11/04/2025",
            "Log Time": "15:50:07",
            "Venue": "Fuji GP Sh",
            "Odo/System Distance (km)": 165.858,
            "Odo/System Time": "1:25:05",
            "Odo/Usr 1 Distance (km)": 165.858,
            "Odo/Usr 1 Time": "1:25:05",
            "Odo/Usr 2 Distance (km)": 165.858,
            "Odo/Usr 2 Time": "1:25:05",
            "Odo/Usr 3 Distance (km)": 165.858,
            "Odo/Usr 3 Time": "1:25:05",
            "Odo/Usr 4 Distance (km)": 165.858,
            "Odo/Usr 4 Time": "1:25:05",
            "Logger ID": 6603435,
            "Logger Model ID": 793,
            "Logger Model": "MXm",
            "Device Name": "MXm ID airs",
            "GPS Receiver": "iGPS",
        }

        # Check core fields present in both formats
        for key, expected_value in core_metadata.items():
            self.assertIn(key, log.metadata, f"Missing metadata key: {key}")
            self.assertEqual(log.metadata[key], expected_value, f"Metadata mismatch for {key}")

        # XRK-specific fields (may be missing or empty in XRZ)
        if name == "xrk":
            self.assertEqual(log.metadata.get("Driver"), "CMD")
            self.assertEqual(log.metadata.get("Vehicle"), "SFJ")
            self.assertEqual(log.metadata.get("Long Comment"), "")
            self.assertEqual(log.metadata.get("Series"), "Fuji Practice")
            self.assertEqual(log.metadata.get("Session"), "Generic testing")

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_race_mode(self, name, file_path):
        """Test that SFJ has Race Mode 'speed' metadata."""
        log = aim_xrk(str(file_path), progress=None)
        self.assertEqual(log.metadata["Race Mode"], "speed")

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_vehicle_electronics_type(self, name, file_path):
        """Test that VET metadata is exposed."""
        log = aim_xrk(str(file_path), progress=None)
        self.assertEqual(log.metadata["Vehicle Electronics Type"], 0)

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_calibrations(self, name, file_path):
        """Test that calibration metadata is exposed with channel cross-references."""
        log = aim_xrk(str(file_path), progress=None)
        cals = log.metadata["Calibrations"]
        self.assertEqual(len(cals), 8)

        # Check type 1 (2-point linear) calibrations
        type1_cals = [c for c in cals if c["type"] == 1]
        self.assertEqual(len(type1_cals), 2)

        # Steering calibration
        steering_cal = next(c for c in type1_cals if c.get("channel") == "steering")
        self.assertAlmostEqual(steering_cal["output_1"], -180.0)
        self.assertAlmostEqual(steering_cal["output_2"], 180.0)

        # Throttle (ACCEL) calibration
        accel_cal = next(c for c in type1_cals if c.get("channel") == "ACCEL")
        self.assertAlmostEqual(accel_cal["output_1"], 50.0)
        self.assertAlmostEqual(accel_cal["output_2"], 0.0)

        # Check type 20 (IMU bias) calibrations
        type20_cals = [c for c in cals if c["type"] == 20]
        self.assertEqual(len(type20_cals), 6)
        for cal in type20_cals:
            self.assertIn("channel", cal)

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_laps(self, name, file_path):
        """Test that the SFJ XRK/XRZ file contains lap data."""
        log = aim_xrk(str(file_path), progress=None)

        # Should have laps as a PyArrow table
        self.assertIsInstance(log.laps, pa.Table, "Expected laps to be a PyArrow Table")

        # Check that table has the expected columns
        self.assertIn("num", log.laps.column_names, "Laps table missing 'num' column")
        self.assertIn("start_time", log.laps.column_names, "Laps table missing 'start_time' column")
        self.assertIn("end_time", log.laps.column_names, "Laps table missing 'end_time' column")

        # Should have exactly 13 laps
        self.assertEqual(len(log.laps), 13, f"Expected 13 laps, got {len(log.laps)}")

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_specific_lap_times(self, name, file_path):
        """Test that specific lap times match expected values."""
        log = aim_xrk(str(file_path), progress=None)

        # Expected lap data (lap_num, start_time, end_time)
        # Values now match official AIM DLL (uses LAP messages, not GPS detection)
        expected_laps = [
            (0, 0.0, 193611.0),
            (1, 193611.0, 320961.0),
            (2, 320961.0, 450167.0),
            (3, 450167.0, 569437.0),
            (4, 569437.0, 688127.0),
            (5, 688127.0, 819303.0),
            (6, 819303.0, 947652.0),
            (7, 947652.0, 1079430.0),
            (8, 1079430.0, 1202583.0),
            (9, 1202583.0, 1322384.0),
            (10, 1322384.0, 1445261.0),
            (11, 1445261.0, 1578528.0),
            (12, 1578528.0, 1696958.0),
        ]

        self.assertEqual(len(log.laps), len(expected_laps), "Lap count mismatch")

        for expected_num, expected_start, expected_end in expected_laps:
            lap_num = log.laps.column("num")[expected_num].as_py()
            start_time = log.laps.column("start_time")[expected_num].as_py()
            end_time = log.laps.column("end_time")[expected_num].as_py()

            self.assertEqual(lap_num, expected_num, f"Lap number mismatch at index {expected_num}")
            self.assertAlmostEqual(
                start_time,
                expected_start,
                delta=1.0,
                msg=f"Lap {expected_num} start time mismatch",
            )
            self.assertAlmostEqual(
                end_time, expected_end, delta=1.0, msg=f"Lap {expected_num} end time mismatch"
            )

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_channel_count_and_names(self, name, file_path):
        """Test that all expected channels are present with correct names."""
        log = aim_xrk(str(file_path), progress=None)

        expected_channels = {
            "ACCEL",
            "ADC Voffset",
            "BRK",
            "Best Run Diff",
            "Best Today Diff",
            "External Voltage",
            "GPS Altitude",
            "GPS Latitude",
            "GPS Longitude",
            "GPS Speed",
            "GPS_Fix",
            "GPS_InlineAcc",
            "GPS_LateralAcc",
            "GPS_Position_Accuracy",
            "GPS_Satellites",
            "GPS_Velocity_Accuracy",
            "GPS_Yaw_Rate",
            "GPS_pDOP",
            "InlineAcc",
            "Lateral Grip",
            "LateralAcc",
            "LoggerTemp",
            "Luminosity",
            "PitchRate",
            "Predictive Time",
            "Prev Lap Diff",
            "RPM",
            "Ref Lap Diff",
            "RollRate",
            "StartRec",
            "VerticalAcc",
            "WT",
            "YawRate",
            "steering",
        }

        actual_channels = set(log.channels.keys())
        self.assertEqual(
            actual_channels,
            expected_channels,
            f"Channel names mismatch.\nMissing: {expected_channels - actual_channels}\nExtra: {actual_channels - expected_channels}",
        )

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_channel_row_counts(self, name, file_path):
        """Test that channels have the expected number of rows."""
        log = aim_xrk(str(file_path), progress=None)

        # Expected row counts for each channel
        expected_row_counts = {
            "ACCEL": 33930,
            "ADC Voffset": 1696,
            "BRK": 84825,
            "Best Run Diff": 724,
            "Best Today Diff": 12,
            "External Voltage": 1696,
            "GPS Altitude": 42409,
            "GPS Latitude": 42409,
            "GPS Longitude": 42409,
            "GPS Speed": 42409,
            "GPS_Fix": 42409,
            "GPS_InlineAcc": 42409,
            "GPS_LateralAcc": 42409,
            "GPS_Position_Accuracy": 42409,
            "GPS_Satellites": 42409,
            "GPS_Velocity_Accuracy": 42409,
            "GPS_Yaw_Rate": 42409,
            "GPS_pDOP": 42409,
            "InlineAcc": 84840,
            "Lateral Grip": 42409,
            "LateralAcc": 84840,
            "LoggerTemp": 1696,
            "Luminosity": 1696,
            "PitchRate": 84830,
            "Predictive Time": 724,
            "Prev Lap Diff": 12,
            "RPM": 33930,
            "Ref Lap Diff": 12,
            "RollRate": 84830,
            "StartRec": 810,
            "VerticalAcc": 84840,
            "WT": 33930,
            "YawRate": 84830,
            "steering": 33930,
        }

        for channel_name, expected_count in expected_row_counts.items():
            self.assertIn(channel_name, log.channels, f"Channel '{channel_name}' not found")
            actual_count = len(log.channels[channel_name])
            self.assertEqual(
                actual_count,
                expected_count,
                f"Channel '{channel_name}' row count mismatch: expected {expected_count}, got {actual_count}",
            )

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_channel_first_last_values(self, name, file_path):
        """Test that channels have expected first and last values."""
        log = aim_xrk(str(file_path), progress=None)

        # Expected first and last values for select channels (name, first, last, tolerance)
        test_cases = [
            ("RPM", 2434.0, 0.0, 1.0),
            ("GPS Speed", 0.0, 2.079, 0.01),
            ("GPS Latitude", 35.3725, 35.3677, 0.0001),
            ("GPS Longitude", 138.9276, 138.9202, 0.0001),
            ("GPS Altitude", 644.161, 622.024, 0.1),
            ("WT", 40.031, 52.844, 0.1),
            ("steering", -25.531, 119.0, 0.1),
            ("BRK", -0.115, -0.166, 0.01),
            ("InlineAcc", 0.048, 0.013, 0.001),
            ("LateralAcc", 0.002, 0.075, 0.001),
            ("VerticalAcc", -1.275, -0.972, 0.001),
        ]

        for channel_name, expected_first, expected_last, tolerance in test_cases:
            self.assertIn(channel_name, log.channels, f"Channel '{channel_name}' not found")
            channel_table = log.channels[channel_name]
            data_column = channel_table.column(channel_name)

            first_value = data_column[0].as_py()
            last_value = data_column[-1].as_py()

            self.assertAlmostEqual(
                first_value,
                expected_first,
                delta=tolerance,
                msg=f"Channel '{channel_name}' first value mismatch",
            )
            self.assertAlmostEqual(
                last_value,
                expected_last,
                delta=tolerance,
                msg=f"Channel '{channel_name}' last value mismatch",
            )

    @parameterized.expand(FILE_VARIANTS)
    def test_sfj_xrk_channel_metadata(self, name, file_path):
        """Test that channels have correct metadata (units, dec_pts, interpolate)."""
        log = aim_xrk(str(file_path), progress=None)

        # Expected metadata for select channels (name, units, dec_pts, interpolate)
        test_cases = [
            ("RPM", "rpm", "0", "True"),
            ("GPS Speed", "m/s", "1", "True"),
            ("GPS Latitude", "deg", "4", "True"),
            ("GPS Longitude", "deg", "4", "True"),
            ("GPS Altitude", "m", "1", "True"),
            ("WT", "C", "1", "True"),
            ("steering", "deg", "1", "True"),
            ("BRK", "bar", "2", "True"),
            ("InlineAcc", "g", "2", "True"),
            ("LateralAcc", "g", "2", "True"),
            ("VerticalAcc", "g", "2", "True"),
            ("YawRate", "deg/s", "1", "True"),
            ("PitchRate", "deg/s", "1", "True"),
            ("RollRate", "deg/s", "1", "True"),
            ("Best Run Diff", "ms", "0", "False"),
            ("Predictive Time", "ms", "0", "False"),
            ("GPS_Satellites", "", "0", "False"),
            ("GPS_Fix", "", "0", "False"),
            ("GPS_pDOP", "", "2", "False"),
        ]

        for channel_name, expected_units, expected_dec_pts, expected_interpolate in test_cases:
            self.assertIn(channel_name, log.channels, f"Channel '{channel_name}' not found")
            channel_table = log.channels[channel_name]
            schema = channel_table.schema
            data_field = schema.field(channel_name)
            metadata = data_field.metadata or {}

            units = metadata.get(b"units", b"").decode("utf-8")
            dec_pts = metadata.get(b"dec_pts", b"").decode("utf-8")
            interpolate = metadata.get(b"interpolate", b"").decode("utf-8")

            self.assertEqual(units, expected_units, f"Channel '{channel_name}' units mismatch")
            self.assertEqual(
                dec_pts, expected_dec_pts, f"Channel '{channel_name}' dec_pts mismatch"
            )
            self.assertEqual(
                interpolate,
                expected_interpolate,
                f"Channel '{channel_name}' interpolate mismatch",
            )

    @parameterized.expand(FILE_VARIANTS)
    def test_filter_by_lap_with_real_data(self, name, file_path):
        """Test filter_by_lap with known lap boundaries from SFJ test data."""
        log = aim_xrk(str(file_path), progress=None)

        # Filter to lap 1 (known boundaries: 193611.0 to 320961.0)
        lap1 = log.filter_by_lap(1)

        # Verify the filtered laps table only contains lap 1
        self.assertEqual(lap1.laps.num_rows, 1)
        self.assertEqual(lap1.laps.column("num")[0].as_py(), 1)

        # Verify channel data is within lap 1 time range
        rpm_channel = lap1.channels["RPM"]
        timecodes = rpm_channel.column("timecodes").to_pylist()

        # All timecodes should be within lap 1 boundaries [193611, 320961)
        for tc in timecodes:
            self.assertGreaterEqual(tc, 193611, "Timecode before lap start")
            self.assertLess(tc, 320961, "Timecode at or after lap end")

        # Verify we got some data (lap 1 should have RPM samples)
        self.assertGreater(len(timecodes), 0, "No RPM data in lap 1")

    @parameterized.expand(FILE_VARIANTS)
    def test_filter_by_lap_with_channel_selection(self, name, file_path):
        """Test filter_by_lap with specific channel selection."""
        log = aim_xrk(str(file_path), progress=None)

        # Filter to lap 5 with only GPS channels
        lap5 = log.filter_by_lap(5, channel_names=["GPS Speed", "GPS Latitude", "GPS Longitude"])

        # Verify only specified channels are present
        self.assertEqual(
            set(lap5.channels.keys()),
            {"GPS Speed", "GPS Latitude", "GPS Longitude"},
        )

        # Verify timecodes are within lap 5 boundaries [688126, 819303)
        gps_timecodes = lap5.channels["GPS Speed"].column("timecodes").to_pylist()
        for tc in gps_timecodes:
            self.assertGreaterEqual(tc, 688126)
            self.assertLess(tc, 819303)

    @parameterized.expand(FILE_VARIANTS)
    def test_resample_to_channel_with_real_data(self, name, file_path):
        """Test resample_to_channel with real data."""
        log = aim_xrk(str(file_path), progress=None)

        # Filter to a short segment to keep test fast
        segment = log.filter_by_time_range(200000, 250000, channel_names=["RPM", "GPS Speed"])

        # Resample RPM to GPS Speed's timebase
        resampled = segment.resample_to_channel("GPS Speed")

        # Both channels should now have the same timecodes
        rpm_timecodes = resampled.channels["RPM"].column("timecodes").to_pylist()
        gps_timecodes = resampled.channels["GPS Speed"].column("timecodes").to_pylist()
        self.assertEqual(rpm_timecodes, gps_timecodes)

        # RPM values should still be reasonable (not corrupted by resampling)
        rpm_values = resampled.channels["RPM"].column("RPM").to_pylist()
        for val in rpm_values:
            self.assertGreaterEqual(val, 0, "RPM should not be negative")
            self.assertLess(val, 20000, "RPM seems unreasonably high")


def _rust_backend_available() -> bool:
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestSFJCrossBackend(unittest.TestCase):
    """Cross-backend comparison for the SFJ XRK file."""

    rust_log: ClassVar[LogFile]
    cython_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.rust_log = rust_aim_xrk(str(SFJ_XRK_FILE))
        cls.cython_log = cython_aim_xrk(str(SFJ_XRK_FILE))

    def test_channel_names_match(self) -> None:
        """Both backends should produce the same set of channel names."""
        self.assertEqual(
            set(self.rust_log.channels.keys()),
            set(self.cython_log.channels.keys()),
        )

    def test_channel_sample_counts_match(self) -> None:
        """Per-channel sample counts should match between backends."""
        for name in self.rust_log.channels:
            rust_count = len(self.rust_log.channels[name])
            cython_count = len(self.cython_log.channels[name])
            self.assertEqual(
                rust_count,
                cython_count,
                f"Channel {name!r}: rust={rust_count} != cython={cython_count}",
            )

    def test_channel_types_match(self) -> None:
        """Arrow types should match between Rust and Cython for all channels."""
        for name in self.rust_log.channels:
            rust_type = self.rust_log.channels[name].schema.field(name).type
            cython_type = self.cython_log.channels[name].schema.field(name).type
            self.assertEqual(
                rust_type,
                cython_type,
                f"Channel {name!r}: rust type={rust_type} != cython type={cython_type}",
            )

    def test_non_gps_channel_values_exact_match(self) -> None:
        """Non-GPS CHS channels should produce identical timecodes and values."""
        gps_prefixes = ("GPS ", "GPS_")
        for name in self.rust_log.channels:
            if any(name.startswith(p) for p in gps_prefixes):
                continue
            rust_tc = self.rust_log.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_log.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(rust_tc, cython_tc, err_msg=f"{name} timecodes")
            rust_vals = self.rust_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = self.cython_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            np.testing.assert_array_equal(rust_vals, cython_vals, err_msg=f"{name} values")

    def test_gps_channel_values_close(self) -> None:
        """GPS channel values should be close between backends."""
        for name in self.rust_log.channels:
            if not (name.startswith("GPS ") or name.startswith("GPS_")):
                continue
            rust_vals = self.rust_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = self.cython_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            np.testing.assert_allclose(
                rust_vals, cython_vals, rtol=1e-5, atol=1e-10, err_msg=f"{name}"
            )

    def test_metadata_match(self) -> None:
        """Key metadata should match between backends."""
        for key in ("Logger ID", "Logger Model ID", "Venue", "GPS Receiver", "Race Mode"):
            rust_val = self.rust_log.metadata.get(key)
            cython_val = self.cython_log.metadata.get(key)
            self.assertEqual(
                rust_val,
                cython_val,
                f"Metadata {key!r}: rust={rust_val!r} != cython={cython_val!r}",
            )

    def test_lap_data_match(self) -> None:
        """Lap counts and timing should match between backends."""
        self.assertEqual(self.rust_log.laps.num_rows, self.cython_log.laps.num_rows)
        rust_starts = self.rust_log.laps.column("start_time").to_pylist()
        cython_starts = self.cython_log.laps.column("start_time").to_pylist()
        self.assertEqual(rust_starts, cython_starts)
        rust_ends = self.rust_log.laps.column("end_time").to_pylist()
        cython_ends = self.cython_log.laps.column("end_time").to_pylist()
        self.assertEqual(rust_ends, cython_ends)

    def test_channel_metadata_match(self) -> None:
        """Channel metadata (units, dec_pts, interpolate) should match between backends."""
        for name in self.rust_log.channels:
            rust_meta = self.rust_log.channels[name].schema.field(name).metadata or {}
            cython_meta = self.cython_log.channels[name].schema.field(name).metadata or {}
            for key in (b"units", b"dec_pts", b"interpolate"):
                self.assertEqual(
                    rust_meta.get(key),
                    cython_meta.get(key),
                    f"Channel {name!r} metadata {key!r}: "
                    f"rust={rust_meta.get(key)!r} != cython={cython_meta.get(key)!r}",
                )


if __name__ == "__main__":
    unittest.main()
