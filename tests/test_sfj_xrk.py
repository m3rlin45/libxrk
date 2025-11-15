"""End-to-end tests for libxrk SFJ XRK file reading."""

import unittest
from pathlib import Path
from libxrk import aim_xrk
import pyarrow as pa


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK_FILE = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


class TestSFJXRK(unittest.TestCase):
    """Tests for loading and parsing the SFJ test vector XRK file."""

    def test_sfj_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(SFJ_XRK_FILE.exists(), f"Test file not found: {SFJ_XRK_FILE}")

    def test_load_sfj_xrk_file(self):
        """Test loading the SFJ XRK file."""
        # Load the file
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

        # Verify basic structure
        self.assertIsNotNone(log, "aim_xrk returned None")
        self.assertIsNotNone(log.channels, "LogFile.channels is None")
        self.assertIsNotNone(log.laps, "LogFile.laps is None")
        self.assertIsNotNone(log.metadata, "LogFile.metadata is None")

    def test_sfj_xrk_metadata(self):
        """Test that the SFJ XRK file contains metadata."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

        # Should have metadata
        self.assertIsInstance(log.metadata, dict, "Expected metadata to be a dict")

        # Expected metadata from the file
        expected_metadata = {
            "Driver": "CMD",
            "Log Date": "11/04/2025",
            "Log Time": "15:50:07",
            "Long Comment": "",
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
            "Series": "Fuji Practice",
            "Session": "Generic testing",
            "Vehicle": "SFJ",
            "Venue": "Fuji GP Sh",
        }

        # Assert the entire metadata dict matches
        self.assertEqual(log.metadata, expected_metadata)

    def test_sfj_xrk_laps(self):
        """Test that the SFJ XRK file contains lap data."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

        # Should have laps as a PyArrow table
        self.assertIsInstance(log.laps, pa.Table, "Expected laps to be a PyArrow Table")

        # Check that table has the expected columns
        self.assertIn("num", log.laps.column_names, "Laps table missing 'num' column")
        self.assertIn("start_time", log.laps.column_names, "Laps table missing 'start_time' column")
        self.assertIn("end_time", log.laps.column_names, "Laps table missing 'end_time' column")

        # Should have exactly 13 laps
        self.assertEqual(len(log.laps), 13, f"Expected 13 laps, got {len(log.laps)}")

    def test_sfj_xrk_specific_lap_times(self):
        """Test that specific lap times match expected values."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

        # Expected lap data (lap_num, start_time, end_time)
        expected_laps = [
            (0, 0.0, 193611.0),
            (1, 193611.0, 320961.0),
            (2, 320961.0, 450166.0),
            (3, 450166.0, 569437.0),
            (4, 569437.0, 688126.0),
            (5, 688126.0, 819303.0),
            (6, 819303.0, 947652.0),
            (7, 947652.0, 1079430.0),
            (8, 1079430.0, 1202583.0),
            (9, 1202583.0, 1322384.0),
            (10, 1322384.0, 1445260.0),
            (11, 1445260.0, 1578528.0),
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

    def test_sfj_xrk_channel_count_and_names(self):
        """Test that all expected channels are present with correct names."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

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

    def test_sfj_xrk_channel_row_counts(self):
        """Test that channels have the expected number of rows."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

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

    def test_sfj_xrk_channel_first_last_values(self):
        """Test that channels have expected first and last values."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

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

    def test_sfj_xrk_channel_metadata(self):
        """Test that channels have correct metadata (units, dec_pts, interpolate)."""
        log = aim_xrk(str(SFJ_XRK_FILE), progress=None)

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
            ("InlineAcc", "G", "2", "True"),
            ("LateralAcc", "G", "2", "True"),
            ("VerticalAcc", "G", "2", "True"),
            ("YawRate", "deg/s", "1", "True"),
            ("PitchRate", "deg/s", "1", "True"),
            ("RollRate", "deg/s", "1", "True"),
            ("Best Run Diff", "ms", "0", "False"),
            ("Predictive Time", "ms", "0", "False"),
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


if __name__ == "__main__":
    unittest.main()
