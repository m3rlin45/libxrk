"""Tests for official AIM test.xrk file - verifies GPS-based lap detection.

This file tests the fix for GitHub Issue #2: incorrect lap times when XRK files
have no embedded LAP messages and rely on GPS-based lap detection.

The official AIM test.xrk file has 0 LAP messages, so lap detection is done via GPS.
Before the fix, last_time was incorrectly 0, causing the last lap to have end_time=0.
"""

import unittest
from pathlib import Path
from typing import ClassVar

import numpy as np

from libxrk import aim_xrk
from libxrk.base import ChannelMetadata, LogFile


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

        # Should have laps detected via GPS (DLL reports 11 laps)
        self.assertEqual(log.laps.num_rows, 11, "Expected 11 laps (matching DLL)")
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


def _rust_backend_available() -> bool:
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestAIMOfficialCrossBackend(unittest.TestCase):
    """Cross-backend comparison for the official AIM test.xrk file."""

    rust_log: ClassVar[LogFile]
    cython_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.rust_log = rust_aim_xrk(str(AIM_OFFICIAL_XRK_FILE))
        cls.cython_log = cython_aim_xrk(str(AIM_OFFICIAL_XRK_FILE))

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

    def test_non_gps_channel_values_match(self) -> None:
        """Non-GPS CHS channels should produce matching values.

        Note: This file has no LAP messages, so time_offset is computed from
        the first data message timecode. The backends may differ by a small
        constant offset (~18ms), so timecodes are compared with tolerance.
        Values (which are offset-independent) should match exactly.
        """
        gps_prefixes = ("GPS ", "GPS_")
        for name in self.rust_log.channels:
            if any(name.startswith(p) for p in gps_prefixes):
                continue
            rust_tc = self.rust_log.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_log.channels[name].column("timecodes").to_numpy()
            # Timecodes may have a constant offset; check they differ uniformly
            tc_diffs = rust_tc - cython_tc
            np.testing.assert_array_equal(
                tc_diffs,
                tc_diffs[0],
                err_msg=f"{name} timecodes have non-uniform offset",
            )
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

    def test_lap_count_match(self) -> None:
        """Lap count should match between backends."""
        self.assertEqual(self.rust_log.laps.num_rows, self.cython_log.laps.num_rows)

    def test_lap_durations_match(self) -> None:
        """Lap durations should match between backends.

        Note: This file has no LAP messages (GPS-based lap detection), so
        absolute lap times may differ by a constant time_offset between backends.
        Durations are offset-independent and should match exactly.
        """
        rust_starts = np.array(self.rust_log.laps.column("start_time").to_pylist())
        rust_ends = np.array(self.rust_log.laps.column("end_time").to_pylist())
        cython_starts = np.array(self.cython_log.laps.column("start_time").to_pylist())
        cython_ends = np.array(self.cython_log.laps.column("end_time").to_pylist())
        np.testing.assert_array_equal(
            rust_ends - rust_starts,
            cython_ends - cython_starts,
            err_msg="Lap durations differ between backends",
        )

    def test_channel_metadata_match(self) -> None:
        """Channel metadata (units, dec_pts, interpolate) should match between backends."""
        for name in self.rust_log.channels:
            rust_meta = ChannelMetadata.from_channel_table(self.rust_log.channels[name], name)
            cython_meta = ChannelMetadata.from_channel_table(self.cython_log.channels[name], name)
            self.assertEqual(rust_meta.units, cython_meta.units, f"Channel {name!r} units mismatch")
            self.assertEqual(
                rust_meta.dec_pts, cython_meta.dec_pts, f"Channel {name!r} dec_pts mismatch"
            )
            self.assertEqual(
                rust_meta.interpolate,
                cython_meta.interpolate,
                f"Channel {name!r} interpolate mismatch",
            )


if __name__ == "__main__":
    unittest.main()
