"""End-to-end tests for libxrk XRK file reading."""

import unittest
from pathlib import Path
from libxrk import AIMXRK
import pyarrow as pa


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK_FILE = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


class TestXRKFileLoading(unittest.TestCase):
    """Tests for loading and parsing XRK files."""

    def test_sfj_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(SFJ_XRK_FILE.exists(), f"Test file not found: {SFJ_XRK_FILE}")

    def test_load_sfj_xrk_file(self):
        """Test loading the SFJ XRK file."""
        # Load the file
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Verify basic structure
        self.assertIsNotNone(log, "AIMXRK returned None")
        self.assertIsNotNone(log.channels, "LogFile.channels is None")
        self.assertIsNotNone(log.laps, "LogFile.laps is None")
        self.assertIsNotNone(log.metadata, "LogFile.metadata is None")

    def test_sfj_xrk_has_channels(self):
        """Test that the SFJ XRK file contains channels."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have channels
        self.assertGreater(len(log.channels), 0, "Expected channels in the XRK file")

        # Verify channels have data
        for channel_name, channel_table in log.channels.items():
            # Each channel should be a PyArrow table
            self.assertIsInstance(
                channel_table, pa.Table, f"Channel '{channel_name}' is not a PyArrow Table"
            )

            # Check that table has the expected columns
            self.assertIn(
                "timecodes",
                channel_table.column_names,
                f"Channel '{channel_name}' missing 'timecodes' column",
            )
            self.assertIn(
                "values",
                channel_table.column_names,
                f"Channel '{channel_name}' missing 'values' column",
            )

            self.assertGreater(
                len(channel_table),
                0,
                f"Channel '{channel_name}' has empty data",
            )

    def test_sfj_xrk_timecode_lengths(self):
        """Test and validate the length of timecodes in the SFJ XRK file."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Collect timecode lengths for all channels
        timecode_info = {}
        for channel_name, channel_table in log.channels.items():
            timecode_length = len(channel_table.column("timecodes"))
            value_length = len(channel_table.column("values"))
            timecode_info[channel_name] = {
                "timecodes": timecode_length,
                "values": value_length,
            }

            # Verify timecodes and values have matching lengths
            self.assertEqual(
                timecode_length,
                value_length,
                f"Channel '{channel_name}' has mismatched lengths: "
                f"timecodes={timecode_length}, values={value_length}",
            )

            # Verify non-zero length
            self.assertGreater(timecode_length, 0, f"Channel '{channel_name}' has zero timecodes")

        # Print summary for debugging
        print("\nChannel timecode lengths:")
        for name, info in sorted(timecode_info.items()):
            print(f"  {name}: {info['timecodes']} samples")

        # Verify we have a reasonable number of channels
        self.assertGreaterEqual(
            len(timecode_info),
            5,
            f"Expected at least 5 channels, got {len(timecode_info)}",
        )

    def test_sfj_xrk_has_gps_channels(self):
        """Test that the SFJ XRK file contains GPS channels."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Check for GPS channels
        expected_gps_channels = ["GPS Speed", "GPS Latitude", "GPS Longitude", "GPS Altitude"]

        for gps_channel in expected_gps_channels:
            self.assertIn(
                gps_channel,
                log.channels,
                f"Expected GPS channel '{gps_channel}' not found in channels",
            )

            # Verify GPS channels have data
            channel_table = log.channels[gps_channel]
            self.assertGreater(
                len(channel_table),
                0,
                f"GPS channel '{gps_channel}' has no data",
            )

    def test_sfj_xrk_metadata(self):
        """Test that the SFJ XRK file contains metadata."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have metadata
        self.assertIsInstance(log.metadata, dict, "Expected metadata to be a dict")
        self.assertGreater(len(log.metadata), 0, "Expected metadata in the XRK file")

        # Print metadata for debugging
        print("\nMetadata:")
        for key, value in sorted(log.metadata.items()):
            print(f"  {key}: {value}")

    def test_sfj_xrk_laps(self):
        """Test that the SFJ XRK file contains lap data."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have laps as a PyArrow table
        self.assertIsInstance(log.laps, pa.Table, "Expected laps to be a PyArrow Table")

        # Check that table has the expected columns
        self.assertIn("num", log.laps.column_names, "Laps table missing 'num' column")
        self.assertIn("start_time", log.laps.column_names, "Laps table missing 'start_time' column")
        self.assertIn("end_time", log.laps.column_names, "Laps table missing 'end_time' column")

        # Should have exactly 13 laps
        self.assertEqual(len(log.laps), 13, f"Expected 13 laps, got {len(log.laps)}")

        if len(log.laps) > 0:
            # Verify lap data
            num_col = log.laps.column("num").to_pylist()
            start_time_col = log.laps.column("start_time").to_pylist()
            end_time_col = log.laps.column("end_time").to_pylist()

            for i in range(len(log.laps)):
                lap_num = num_col[i]
                start_time = start_time_col[i]
                end_time = end_time_col[i]

                self.assertGreaterEqual(
                    end_time,
                    start_time,
                    f"Lap {lap_num} end_time ({end_time}) should be >= start_time ({start_time})",
                )

            print(f"\nFound {len(log.laps)} laps")
