"""End-to-end tests for libxrk XRK file reading."""

import pytest
from pathlib import Path
from libxrk.data import AIMXRK


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_XRK_FILE = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


class TestXRKFileLoading:
    """Tests for loading and parsing XRK files."""

    def test_sfj_file_exists(self):
        """Verify the test data file exists."""
        assert SFJ_XRK_FILE.exists(), f"Test file not found: {SFJ_XRK_FILE}"

    def test_load_sfj_xrk_file(self):
        """Test loading the SFJ XRK file."""
        # Load the file
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Verify basic structure
        assert log is not None
        assert log.channels is not None
        assert log.laps is not None
        assert log.metadata is not None

    def test_sfj_xrk_has_channels(self):
        """Test that the SFJ XRK file contains channels."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have channels
        assert len(log.channels) > 0, "Expected channels in the XRK file"

        # Verify channels have data
        for channel_name, channel in log.channels.items():
            assert hasattr(channel, "timecodes"), f"Channel {channel_name} missing timecodes"
            assert hasattr(channel, "values"), f"Channel {channel_name} missing values"
            assert len(channel.timecodes) > 0, f"Channel {channel_name} has empty timecodes"

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
            assert (
                timecode_length == value_length
            ), f"Channel {channel_name}: timecodes ({timecode_length}) and values ({value_length}) length mismatch"

            # Verify non-zero length
            assert timecode_length > 0, f"Channel {channel_name} has zero timecodes"

        # Print summary for debugging
        print("\nChannel timecode lengths:")
        for name, info in sorted(timecode_info.items()):
            print(f"  {name}: {info['timecodes']} samples")

        # Verify we have a reasonable number of channels
        assert len(timecode_info) >= 5, f"Expected at least 5 channels, got {len(timecode_info)}"

    def test_sfj_xrk_has_gps_channels(self):
        """Test that the SFJ XRK file contains GPS channels."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Check for GPS channels
        expected_gps_channels = ["GPS Speed", "GPS Latitude", "GPS Longitude", "GPS Altitude"]

        for gps_channel in expected_gps_channels:
            assert gps_channel in log.channels, f"Expected GPS channel '{gps_channel}' not found"

            # Verify GPS channels have data
            channel = log.channels[gps_channel]
            assert len(channel.timecodes) > 0, f"GPS channel '{gps_channel}' has no data"

    def test_sfj_xrk_metadata(self):
        """Test that the SFJ XRK file contains metadata."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have metadata
        assert isinstance(log.metadata, dict)
        assert len(log.metadata) > 0, "Expected metadata in the XRK file"

        # Print metadata for debugging
        print("\nMetadata:")
        for key, value in sorted(log.metadata.items()):
            print(f"  {key}: {value}")

    def test_sfj_xrk_laps(self):
        """Test that the SFJ XRK file contains lap data."""
        log = AIMXRK(str(SFJ_XRK_FILE), progress=None)

        # Should have laps
        assert isinstance(log.laps, list)

        if len(log.laps) > 0:
            # Verify lap structure
            for lap in log.laps:
                assert hasattr(lap, "num"), "Lap missing num attribute"
                assert hasattr(lap, "start_time"), "Lap missing start_time attribute"
                assert hasattr(lap, "end_time"), "Lap missing end_time attribute"
                assert lap.end_time >= lap.start_time, "Lap end_time should be >= start_time"

            print(f"\nFound {len(log.laps)} laps")
