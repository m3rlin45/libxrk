"""Tests for official AIM test.xrk file - verifies GPS-based lap detection.

This file tests the fix for GitHub Issue #2: incorrect lap times when XRK files
have no embedded LAP messages and rely on GPS-based lap detection.

The official AIM test.xrk file has 0 LAP messages, so lap detection is done via GPS.
Before the fix, last_time was incorrectly 0, causing the last lap to have end_time=0.
"""

import unittest
from pathlib import Path
from libxrk import aim_xrk


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
AIM_OFFICIAL_XRK_FILE = TEST_DATA_DIR / "aim_official" / "test.xrk"


class TestAIMOfficialXRK(unittest.TestCase):
    """Tests for the official AIM test.xrk file with GPS-based lap detection."""

    def test_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(
            AIM_OFFICIAL_XRK_FILE.exists(), f"Test file not found: {AIM_OFFICIAL_XRK_FILE}"
        )

    def test_load_aim_official_xrk(self):
        """Test loading the official AIM test.xrk file."""
        log = aim_xrk(str(AIM_OFFICIAL_XRK_FILE), progress=None)

        # Verify basic structure
        self.assertIsNotNone(log, "aim_xrk returned None")
        self.assertIsNotNone(log.channels, "LogFile.channels is None")
        self.assertIsNotNone(log.laps, "LogFile.laps is None")
        self.assertIsNotNone(log.metadata, "LogFile.metadata is None")

    def test_lap_times_are_valid(self):
        """Test that lap times are correctly calculated (Issue #2 fix).

        This is the core test for the fix. Before the fix, the last lap had
        end_time=0 because last_time was not set when there are no LAP messages.
        """
        log = aim_xrk(str(AIM_OFFICIAL_XRK_FILE), progress=None)

        # Should have laps detected via GPS
        self.assertGreater(log.laps.num_rows, 0, "Expected at least one lap")

        # Get lap data as Python lists for easier assertions
        lap_nums = log.laps.column("num").to_pylist()
        start_times = log.laps.column("start_time").to_pylist()
        end_times = log.laps.column("end_time").to_pylist()

        # All lap end times should be positive (not 0)
        for i, (lap_num, start_time, end_time) in enumerate(zip(lap_nums, start_times, end_times)):
            self.assertGreater(end_time, 0, f"Lap {lap_num} has invalid end_time=0 (Issue #2 bug)")
            self.assertGreater(
                end_time,
                start_time,
                f"Lap {lap_num} has end_time ({end_time}) <= start_time ({start_time})",
            )

        # The last lap's end_time should be reasonable (> 500000ms based on file content)
        last_end_time = end_times[-1]
        self.assertGreater(
            last_end_time,
            500000,
            f"Last lap end_time ({last_end_time}) is too low - GPS-based detection may have failed",
        )

    def test_lap_durations_are_reasonable(self):
        """Test that all lap durations are reasonable (not negative or zero)."""
        log = aim_xrk(str(AIM_OFFICIAL_XRK_FILE), progress=None)

        lap_nums = log.laps.column("num").to_pylist()
        start_times = log.laps.column("start_time").to_pylist()
        end_times = log.laps.column("end_time").to_pylist()

        for lap_num, start_time, end_time in zip(lap_nums, start_times, end_times):
            duration = end_time - start_time
            # Each lap should have a reasonable duration (at least 10 seconds)
            self.assertGreater(
                duration,
                10000,
                f"Lap {lap_num} has too short duration: {duration}ms",
            )
            # Laps shouldn't be excessively long either (less than 5 minutes for a test track)
            self.assertLess(
                duration,
                300000,
                f"Lap {lap_num} has suspiciously long duration: {duration}ms",
            )


if __name__ == "__main__":
    unittest.main()
