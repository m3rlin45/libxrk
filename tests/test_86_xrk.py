"""End-to-end tests for libxrk 86 XRK file reading."""

import unittest
from pathlib import Path
from libxrk import AIMXRK
import pyarrow as pa


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
XRK_86_FILE = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"


class Test86XRK(unittest.TestCase):
    """Tests for loading and parsing the 86 test vector XRK file."""

    def test_86_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(XRK_86_FILE.exists(), f"Test file not found: {XRK_86_FILE}")

    def test_load_86_xrk_file(self):
        """Test loading the 86 XRK file."""
        # Load the file
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Verify basic structure
        self.assertIsNotNone(log, "AIMXRK returned None")
        self.assertIsNotNone(log.channels, "LogFile.channels is None")
        self.assertIsNotNone(log.laps, "LogFile.laps is None")
        self.assertIsNotNone(log.metadata, "LogFile.metadata is None")

    def test_86_xrk_metadata(self):
        """Test that the 86 XRK file contains metadata."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Should have metadata
        self.assertIsInstance(log.metadata, dict, "Expected metadata to be a dict")

        # Expected metadata from the file
        expected_metadata = {
            "Driver": "CMD",
            "Log Date": "11/01/2025",
            "Log Time": "10:39:06",
            "Long Comment": "Front 15, 2/2\r\nRear 20 3/3\r\nA052 Used",
            "Odo/System Distance (km)": 5313.42,
            "Odo/System Time": "79:29:53",
            "Odo/Usr 1 Distance (km)": 5313.42,
            "Odo/Usr 1 Time": "79:29:53",
            "Odo/Usr 2 Distance (km)": 5313.42,
            "Odo/Usr 2 Time": "79:29:53",
            "Odo/Usr 3 Distance (km)": 5313.42,
            "Odo/Usr 3 Time": "79:29:53",
            "Odo/Usr 4 Distance (km)": 5313.42,
            "Odo/Usr 4 Time": "79:29:53",
            "Series": "Fuji Practice",
            "Session": "Generic testing",
            "Vehicle": "Inferno 86",
            "Venue": "Fuji GP Sh",
        }

        # Assert the entire metadata dict matches
        self.assertEqual(log.metadata, expected_metadata)

    def test_86_xrk_laps(self):
        """Test that the 86 XRK file contains lap data."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Should have laps as a PyArrow table
        self.assertIsInstance(log.laps, pa.Table, "Expected laps to be a PyArrow Table")

        # Check that table has the expected columns
        self.assertIn("num", log.laps.column_names, "Laps table missing 'num' column")
        self.assertIn("start_time", log.laps.column_names, "Laps table missing 'start_time' column")
        self.assertIn("end_time", log.laps.column_names, "Laps table missing 'end_time' column")

        # Should have exactly 16 laps
        self.assertEqual(len(log.laps), 16, f"Expected 16 laps, got {len(log.laps)}")

    def test_86_xrk_specific_lap_times(self):
        """Test that specific lap times match expected values."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Expected lap data (lap_num, start_time, end_time)
        expected_laps = [
            (0, 0, 150454),
            (1, 150454, 279602),
            (2, 279602, 406240),
            (3, 406240, 532797),
            (4, 532797, 659282),
            (5, 659282, 787773),
            (6, 787773, 913776),
            (7, 913776, 1041397),
            (8, 1041397, 1168322),
            (9, 1168322, 1294676),
            (10, 1294676, 1420573),
            (11, 1420573, 1547567),
            (12, 1547567, 1672955),
            (13, 1672955, 1799131),
            (14, 1799131, 1924187),
            (15, 1924187, 2161607),
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

    def test_86_xrk_channel_count_and_names(self):
        """Test that all expected channels are present with correct names."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        expected_channels = {
            "AmbientTemp",
            "Baro",
            "Best Run Diff",
            "Best Today Diff",
            "BrakePress",
            "BrakeSw",
            "CAT1",
            "CH",
            "ClutchSw",
            "ECT",
            "External Voltage",
            "FL_Ch1",
            "FL_Ch2",
            "FL_Ch3",
            "FL_Ch4",
            "FL_Ch5",
            "FL_Ch6",
            "FL_Ch7",
            "FL_Ch8",
            "FR_Ch1",
            "FR_Ch2",
            "FR_Ch3",
            "FR_Ch4",
            "FR_Ch5",
            "FR_Ch6",
            "FR_Ch7",
            "FR_Ch8",
            "GPS Altitude",
            "GPS Latitude",
            "GPS Longitude",
            "GPS Speed",
            "Gear",
            "InlineAcc",
            "IntakeAirT",
            "LF_Shock_Pot",
            "LR_Shock_Pot",
            "Lambda",
            "LateralAcc",
            "LoggerTemp",
            "Luminosity",
            "MAP",
            "OilTemp",
            "PPS",
            "PitchRate",
            "Predictive Time",
            "Prev Lap Diff",
            "RF_Shock_Pot",
            "RL_Ch1",
            "RL_Ch2",
            "RL_Ch3",
            "RL_Ch4",
            "RL_Ch5",
            "RL_Ch6",
            "RL_Ch7",
            "RL_Ch8",
            "RPM",
            "RR_Ch1",
            "RR_Ch2",
            "RR_Ch3",
            "RR_Ch4",
            "RR_Ch5",
            "RR_Ch6",
            "RR_Ch7",
            "RR_Ch8",
            "RR_Shock_Pot",
            "Ref Lap Diff",
            "RollRate",
            "SpeedAverage",
            "SteerAngle",
            "TPMS_ALM_LF",
            "TPMS_ALM_LR",
            "TPMS_ALM_RF",
            "TPMS_ALM_RR",
            "TPMS_Press_LF",
            "TPMS_Press_LR",
            "TPMS_Press_RF",
            "TPMS_Press_RR",
            "TPMS_Temp_LF",
            "TPMS_Temp_LR",
            "TPMS_Temp_RF",
            "TPMS_Temp_RR",
            "TPMS_Volt_LF",
            "TPMS_Volt_LR",
            "TPMS_Volt_RF",
            "TPMS_Volt_RR",
            "TPS",
            "VerticalAcc",
            "WheelSpdFL",
            "WheelSpdFR",
            "WheelSpdRL",
            "WheelSpdRR",
            "YawRate",
        }

        actual_channels = set(log.channels.keys())
        self.assertEqual(
            actual_channels,
            expected_channels,
            f"Channel names mismatch.\nMissing: {expected_channels - actual_channels}\nExtra: {actual_channels - expected_channels}",
        )

    def test_86_xrk_channel_row_counts(self):
        """Test that channels have the expected number of rows."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Expected row counts for each channel
        expected_row_counts = {
            "AmbientTemp": 100396,
            "Baro": 100395,
            "Best Run Diff": 963,
            "Best Today Diff": 15,
            "BrakePress": 36024,
            "BrakeSw": 21617,
            "CAT1": 100396,
            "CH": 10807,
            "ClutchSw": 54029,
            "ECT": 100395,
            "External Voltage": 2161,
            "FL_Ch1": 21565,
            "FL_Ch2": 21565,
            "FL_Ch3": 21565,
            "FL_Ch4": 21565,
            "FL_Ch5": 21565,
            "FL_Ch6": 21565,
            "FL_Ch7": 21565,
            "FL_Ch8": 21565,
            "FR_Ch1": 21568,
            "FR_Ch2": 21568,
            "FR_Ch3": 21568,
            "FR_Ch4": 21568,
            "FR_Ch5": 21568,
            "FR_Ch6": 21568,
            "FR_Ch7": 21568,
            "FR_Ch8": 21568,
            "GPS Altitude": 54031,
            "GPS Latitude": 54031,
            "GPS Longitude": 54031,
            "GPS Speed": 54031,
            "Gear": 10806,
            "InlineAcc": 108060,
            "IntakeAirT": 100396,
            "LF_Shock_Pot": 46990,
            "LR_Shock_Pot": 46990,
            "Lambda": 100395,
            "LateralAcc": 108060,
            "LoggerTemp": 2161,
            "Luminosity": 2161,
            "MAP": 100396,
            "OilTemp": 10806,
            "PPS": 54029,
            "PitchRate": 108060,
            "Predictive Time": 963,
            "Prev Lap Diff": 15,
            "RF_Shock_Pot": 46990,
            "RL_Ch1": 21568,
            "RL_Ch2": 21568,
            "RL_Ch3": 21568,
            "RL_Ch4": 21568,
            "RL_Ch5": 21568,
            "RL_Ch6": 21568,
            "RL_Ch7": 21568,
            "RL_Ch8": 21568,
            "RPM": 54029,
            "RR_Ch1": 21561,
            "RR_Ch2": 21561,
            "RR_Ch3": 21561,
            "RR_Ch4": 21561,
            "RR_Ch5": 21561,
            "RR_Ch6": 21561,
            "RR_Ch7": 21561,
            "RR_Ch8": 21561,
            "RR_Shock_Pot": 46990,
            "Ref Lap Diff": 15,
            "RollRate": 108060,
            "SpeedAverage": 36024,
            "SteerAngle": 21614,
            "TPMS_ALM_LF": 10807,
            "TPMS_ALM_LR": 10807,
            "TPMS_ALM_RF": 10807,
            "TPMS_ALM_RR": 10807,
            "TPMS_Press_LF": 10807,
            "TPMS_Press_LR": 10807,
            "TPMS_Press_RF": 10807,
            "TPMS_Press_RR": 10807,
            "TPMS_Temp_LF": 10807,
            "TPMS_Temp_LR": 10807,
            "TPMS_Temp_RF": 10807,
            "TPMS_Temp_RR": 10807,
            "TPMS_Volt_LF": 10807,
            "TPMS_Volt_LR": 10807,
            "TPMS_Volt_RF": 10807,
            "TPMS_Volt_RR": 10807,
            "TPS": 100396,
            "VerticalAcc": 108060,
            "WheelSpdFL": 36024,
            "WheelSpdFR": 36024,
            "WheelSpdRL": 36024,
            "WheelSpdRR": 36024,
            "YawRate": 108060,
        }

        for channel_name, expected_count in expected_row_counts.items():
            self.assertIn(channel_name, log.channels, f"Channel '{channel_name}' not found")
            actual_count = len(log.channels[channel_name])
            self.assertEqual(
                actual_count,
                expected_count,
                f"Channel '{channel_name}' row count mismatch: expected {expected_count}, got {actual_count}",
            )

    def test_86_xrk_channel_first_last_values(self):
        """Test that channels have expected first and last values (all channels)."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Test all channels with first and last values from test_data.md
        test_cases = [
            ("AmbientTemp", 6.000, 6.000, 0.1),
            ("Baro", 0.940, 0.950, 0.001),
            ("Best Run Diff", -12290.0, 36591.0, 1.0),
            ("Best Today Diff", -12290.0, -12290.0, 1.0),
            ("BrakePress", 9.142, 18.284, 0.1),
            ("BrakeSw", 1.000, 1.000, 0.001),
            ("CAT1", 274.200, 542.600, 1.0),
            ("CH", 1.000, 1.000, 0.001),
            ("ClutchSw", 1.000, 1.000, 0.001),
            ("ECT", 89.000, 91.000, 0.1),
            ("External Voltage", 14.464, 14.480, 0.1),
            ("FL_Ch1", 45.900, 46.500, 0.1),
            ("FL_Ch2", 46.100, 51.400, 0.1),
            ("FL_Ch3", 45.400, 50.300, 0.1),
            ("FL_Ch4", 45.500, 48.900, 0.1),
            ("FL_Ch5", 46.000, 54.300, 0.1),
            ("FL_Ch6", 45.300, 50.000, 0.1),
            ("FL_Ch7", 44.000, 54.200, 0.1),
            ("FL_Ch8", 51.100, 42.600, 0.1),
            ("FR_Ch1", 57.800, 39.200, 0.1),
            ("FR_Ch2", 54.100, 55.400, 0.1),
            ("FR_Ch3", 54.600, 51.800, 0.1),
            ("FR_Ch4", 55.300, 51.700, 0.1),
            ("FR_Ch5", 57.300, 49.000, 0.1),
            ("FR_Ch6", 55.400, 49.300, 0.1),
            ("FR_Ch7", 57.100, 51.000, 0.1),
            ("FR_Ch8", 55.500, 48.400, 0.1),
            ("GPS Altitude", 619.999, 625.256, 1.0),
            ("GPS Latitude", 35.374, 35.370, 0.001),
            ("GPS Longitude", 138.930, 138.925, 0.001),
            ("GPS Speed", 0.030, 0.000, 0.001),
            ("Gear", 0.000, 0.000, 0.001),
            ("InlineAcc", -0.027, -0.003, 0.001),
            ("IntakeAirT", 46.000, 19.000, 0.1),
            ("LF_Shock_Pot", -0.914, -0.151, 0.001),
            ("LR_Shock_Pot", 1.221, -2.594, 0.001),
            ("Lambda", 0.995, 0.995, 0.001),
            ("LateralAcc", -0.008, 0.032, 0.001),
            ("LoggerTemp", 35.094, 37.250, 0.1),
            ("Luminosity", 14.242, 15.539, 0.01),
            ("MAP", 0.310, 0.280, 0.001),
            ("OilTemp", 93.000, 95.000, 0.1),
            ("PPS", 0.000, 0.000, 0.001),
            ("PitchRate", 0.105, 0.063, 0.001),
            ("Predictive Time", -12290.0, 161647.0, 1.0),
            ("Prev Lap Diff", -12290.0, -12290.0, 1.0),
            ("RF_Shock_Pot", 0.916, -2.746, 0.001),
            ("RL_Ch1", 41.200, 48.200, 0.1),
            ("RL_Ch2", 39.500, 47.100, 0.1),
            ("RL_Ch3", 38.300, 45.800, 0.1),
            ("RL_Ch4", 38.700, 47.300, 0.1),
            ("RL_Ch5", 38.900, 48.200, 0.1),
            ("RL_Ch6", 38.400, 45.900, 0.1),
            ("RL_Ch7", 38.000, 44.300, 0.1),
            ("RL_Ch8", 33.700, 38.700, 0.1),
            ("RPM", 712.000, 732.000, 1.0),
            ("RR_Ch1", 32.900, 37.400, 0.1),
            ("RR_Ch2", 37.900, 45.200, 0.1),
            ("RR_Ch3", 38.500, 46.600, 0.1),
            ("RR_Ch4", 39.600, 50.400, 0.1),
            ("RR_Ch5", 37.500, 48.700, 0.1),
            ("RR_Ch6", 38.000, 49.700, 0.1),
            ("RR_Ch7", 38.000, 49.500, 0.1),
            ("RR_Ch8", 39.600, 49.400, 0.1),
            ("RR_Shock_Pot", -1.067, 2.287, 0.001),
            ("Ref Lap Diff", -12290.0, -12290.0, 1.0),
            ("RollRate", -0.136, -0.059, 0.001),
            ("SpeedAverage", 0.000, 0.000, 0.001),
            ("SteerAngle", -3.600, -31.600, 0.1),
            ("TPMS_ALM_LF", 0.000, 0.000, 0.001),
            ("TPMS_ALM_LR", 0.000, 0.000, 0.001),
            ("TPMS_ALM_RF", 0.000, 0.000, 0.001),
            ("TPMS_ALM_RR", 0.000, 0.000, 0.001),
            ("TPMS_Press_LF", 1.820, 1.850, 0.01),
            ("TPMS_Press_LR", 1.820, 1.880, 0.01),
            ("TPMS_Press_RF", 1.820, 1.850, 0.01),
            ("TPMS_Press_RR", 1.790, 1.880, 0.01),
            ("TPMS_Temp_LF", 62.000, 55.000, 0.1),
            ("TPMS_Temp_LR", 49.000, 52.000, 0.1),
            ("TPMS_Temp_RF", 59.000, 51.000, 0.1),
            ("TPMS_Temp_RR", 48.000, 51.000, 0.1),
            ("TPMS_Volt_LF", 2.900, 3.000, 0.1),
            ("TPMS_Volt_LR", 2.900, 3.000, 0.1),
            ("TPMS_Volt_RF", 2.900, 3.000, 0.1),
            ("TPMS_Volt_RR", 2.900, 3.000, 0.1),
            ("TPS", 16.450, 15.980, 0.1),
            ("VerticalAcc", -1.000, -1.001, 0.001),
            ("WheelSpdFL", 0.000, 0.000, 0.001),
            ("WheelSpdFR", 0.000, 0.000, 0.001),
            ("WheelSpdRL", 0.000, 0.000, 0.001),
            ("WheelSpdRR", 0.000, 0.000, 0.001),
            ("YawRate", 0.012, 0.034, 0.001),
        ]

        # Ensure we're testing all channels
        test_channel_names = {channel_name for channel_name, _, _, _ in test_cases}
        actual_channel_names = set(log.channels.keys())
        self.assertEqual(
            test_channel_names,
            actual_channel_names,
            f"Test cases don't cover all channels.\nMissing: {actual_channel_names - test_channel_names}\nExtra: {test_channel_names - actual_channel_names}",
        )

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

    def test_86_xrk_channel_metadata(self):
        """Test that channels have correct metadata (all channels)."""
        log = AIMXRK(str(XRK_86_FILE), progress=None)

        # Test all channels with metadata from test_data.md
        # Format: (channel_name, units, dec_pts, interpolate)
        test_cases = [
            ("AmbientTemp", "C", "1", "True"),
            ("Baro", "bar", "2", "True"),
            ("Best Run Diff", "ms", "0", "False"),
            ("Best Today Diff", "ms", "0", "False"),
            ("BrakePress", "bar", "2", "True"),
            ("BrakeSw", "", "0", "True"),
            ("CAT1", "C", "1", "True"),
            ("CH", "", "0", "True"),
            ("ClutchSw", "", "0", "True"),
            ("ECT", "C", "1", "True"),
            ("External Voltage", "V", "1", "True"),
            ("FL_Ch1", "C", "1", "True"),
            ("FL_Ch2", "C", "1", "True"),
            ("FL_Ch3", "C", "1", "True"),
            ("FL_Ch4", "C", "1", "True"),
            ("FL_Ch5", "C", "1", "True"),
            ("FL_Ch6", "C", "1", "True"),
            ("FL_Ch7", "C", "1", "True"),
            ("FL_Ch8", "C", "1", "True"),
            ("FR_Ch1", "C", "1", "True"),
            ("FR_Ch2", "C", "1", "True"),
            ("FR_Ch3", "C", "1", "True"),
            ("FR_Ch4", "C", "1", "True"),
            ("FR_Ch5", "C", "1", "True"),
            ("FR_Ch6", "C", "1", "True"),
            ("FR_Ch7", "C", "1", "True"),
            ("FR_Ch8", "C", "1", "True"),
            ("GPS Altitude", "m", "1", "True"),
            ("GPS Latitude", "deg", "4", "True"),
            ("GPS Longitude", "deg", "4", "True"),
            ("GPS Speed", "m/s", "1", "True"),
            ("Gear", "gear", "0", "False"),
            ("InlineAcc", "G", "2", "True"),
            ("IntakeAirT", "C", "1", "True"),
            ("LF_Shock_Pot", "mm", "0", "True"),
            ("LR_Shock_Pot", "mm", "0", "True"),
            ("Lambda", "lambda", "2", "True"),
            ("LateralAcc", "G", "2", "True"),
            ("LoggerTemp", "C", "1", "True"),
            ("Luminosity", "%", "2", "True"),
            ("MAP", "bar", "2", "True"),
            ("OilTemp", "C", "1", "True"),
            ("PPS", "%", "2", "True"),
            ("PitchRate", "deg/s", "1", "True"),
            ("Predictive Time", "ms", "0", "False"),
            ("Prev Lap Diff", "ms", "0", "False"),
            ("RF_Shock_Pot", "mm", "0", "True"),
            ("RL_Ch1", "C", "1", "True"),
            ("RL_Ch2", "C", "1", "True"),
            ("RL_Ch3", "C", "1", "True"),
            ("RL_Ch4", "C", "1", "True"),
            ("RL_Ch5", "C", "1", "True"),
            ("RL_Ch6", "C", "1", "True"),
            ("RL_Ch7", "C", "1", "True"),
            ("RL_Ch8", "C", "1", "True"),
            ("RPM", "rpm", "0", "True"),
            ("RR_Ch1", "C", "1", "True"),
            ("RR_Ch2", "C", "1", "True"),
            ("RR_Ch3", "C", "1", "True"),
            ("RR_Ch4", "C", "1", "True"),
            ("RR_Ch5", "C", "1", "True"),
            ("RR_Ch6", "C", "1", "True"),
            ("RR_Ch7", "C", "1", "True"),
            ("RR_Ch8", "C", "1", "True"),
            ("RR_Shock_Pot", "mm", "0", "True"),
            ("Ref Lap Diff", "ms", "0", "False"),
            ("RollRate", "deg/s", "1", "True"),
            ("SpeedAverage", "km/h", "0", "True"),
            ("SteerAngle", "deg", "1", "True"),
            ("TPMS_ALM_LF", "", "0", "True"),
            ("TPMS_ALM_LR", "", "0", "True"),
            ("TPMS_ALM_RF", "", "0", "True"),
            ("TPMS_ALM_RR", "", "0", "True"),
            ("TPMS_Press_LF", "bar", "2", "True"),
            ("TPMS_Press_LR", "bar", "2", "True"),
            ("TPMS_Press_RF", "bar", "2", "True"),
            ("TPMS_Press_RR", "bar", "2", "True"),
            ("TPMS_Temp_LF", "C", "1", "True"),
            ("TPMS_Temp_LR", "C", "1", "True"),
            ("TPMS_Temp_RF", "C", "1", "True"),
            ("TPMS_Temp_RR", "C", "1", "True"),
            ("TPMS_Volt_LF", "V", "1", "True"),
            ("TPMS_Volt_LR", "V", "1", "True"),
            ("TPMS_Volt_RF", "V", "1", "True"),
            ("TPMS_Volt_RR", "V", "1", "True"),
            ("TPS", "%", "2", "True"),
            ("VerticalAcc", "G", "2", "True"),
            ("WheelSpdFL", "km/h", "0", "True"),
            ("WheelSpdFR", "km/h", "0", "True"),
            ("WheelSpdRL", "km/h", "0", "True"),
            ("WheelSpdRR", "km/h", "0", "True"),
            ("YawRate", "deg/s", "1", "True"),
        ]

        # Ensure we're testing all channels
        test_channel_names = {channel_name for channel_name, _, _, _ in test_cases}
        actual_channel_names = set(log.channels.keys())
        self.assertEqual(
            test_channel_names,
            actual_channel_names,
            f"Test cases don't cover all channels.\nMissing: {actual_channel_names - test_channel_names}\nExtra: {test_channel_names - actual_channel_names}",
        )

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
