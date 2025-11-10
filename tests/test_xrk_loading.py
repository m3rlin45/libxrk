"""End-to-end tests for libxrk XRK file reading."""

import unittest
from pathlib import Path
from libxrk.data import AIMXRK


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
        for channel_name, channel in log.channels.items():
            self.assertTrue(
                hasattr(channel, "timecodes"),
                f"Channel '{channel_name}' missing timecodes attribute",
            )
            self.assertTrue(
                hasattr(channel, "values"), f"Channel '{channel_name}' missing values attribute"
            )
            self.assertGreater(
                len(channel.timecodes),
                0,
                f"Channel '{channel_name}' has empty timecodes",
            )

    def test_sfj_xrk_timecode_lengths(self):
        """Test and validate the length of timecodes in the SFJ XRK file."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Collect timecode lengths for all channels
        timecode_info = {}
        for channel_name, channel in log.channels.items():
            timecode_length = len(channel.timecodes)
            value_length = len(channel.values)
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
            self.assertGreater(
                timecode_length, 0, f"Channel '{channel_name}' has zero timecodes"
            )

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
            channel = log.channels[gps_channel]
            self.assertGreater(
                len(channel.timecodes),
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

        # Should have laps
        self.assertIsInstance(log.laps, list, "Expected laps to be a list")

        if len(log.laps) > 0:
            # Verify lap structure
            for lap in log.laps:
                self.assertTrue(hasattr(lap, "num"), "Lap missing num attribute")
                self.assertTrue(hasattr(lap, "start_time"), "Lap missing start_time attribute")
                self.assertTrue(hasattr(lap, "end_time"), "Lap missing end_time attribute")
                self.assertGreaterEqual(
                    lap.end_time,
                    lap.start_time,
                    f"Lap {lap.num} end_time ({lap.end_time}) should be >= start_time ({lap.start_time})",
                )

            print(f"\nFound {len(log.laps)} laps")
