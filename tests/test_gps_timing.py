"""Tests for GPS timing gap detection and correction in libxrk."""

import unittest
from typing import Dict, Optional

import numpy as np
import pyarrow as pa

from libxrk import GPS_CHANNEL_NAMES, LogFile
from libxrk.gps import fix_gps_timing_gaps


def create_mock_log_with_gps_gap(
    gap_index: int = 87,
    gap_size_ms: int = 65533,
    expected_dt_ms: float = 40.0,
    n_samples: int = 200,
) -> LogFile:
    """Create a mock LogFile with a GPS timing gap.

    Parameters
    ----------
    gap_index : int
        Index where the gap should occur
    gap_size_ms : int
        Size of the spurious gap in milliseconds
    expected_dt_ms : float
        Normal expected time delta between samples
    n_samples : int
        Total number of GPS samples

    Returns
    -------
    LogFile
        A log file with GPS channels containing the timing gap
    """
    # Create normal timecodes
    timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

    # Inject a gap at gap_index - add extra time to all subsequent samples
    timecodes[gap_index + 1 :] += gap_size_ms - int(expected_dt_ms)

    # Create GPS channel data (simple linear values for testing)
    gps_speed = np.linspace(0, 50, n_samples)  # 0 to 50 m/s
    gps_lat = np.linspace(35.0, 35.1, n_samples)  # degrees
    gps_lon = np.linspace(138.0, 138.1, n_samples)  # degrees
    gps_alt = np.linspace(500, 550, n_samples)  # meters

    channels = {}

    # Create GPS Speed channel with metadata
    channels["GPS Speed"] = pa.table(
        {"timecodes": pa.array(timecodes, type=pa.int64()), "GPS Speed": pa.array(gps_speed)}
    )
    speed_field = (
        channels["GPS Speed"]
        .schema.field("GPS Speed")
        .with_metadata({b"units": b"m/s", b"dec_pts": b"2", b"interpolate": b"True"})
    )
    channels["GPS Speed"] = channels["GPS Speed"].cast(
        pa.schema([channels["GPS Speed"].schema.field("timecodes"), speed_field])
    )

    # Create other GPS channels
    channels["GPS Latitude"] = pa.table(
        {"timecodes": pa.array(timecodes, type=pa.int64()), "GPS Latitude": pa.array(gps_lat)}
    )
    lat_field = (
        channels["GPS Latitude"]
        .schema.field("GPS Latitude")
        .with_metadata({b"units": b"deg", b"dec_pts": b"6", b"interpolate": b"True"})
    )
    channels["GPS Latitude"] = channels["GPS Latitude"].cast(
        pa.schema([channels["GPS Latitude"].schema.field("timecodes"), lat_field])
    )

    channels["GPS Longitude"] = pa.table(
        {"timecodes": pa.array(timecodes, type=pa.int64()), "GPS Longitude": pa.array(gps_lon)}
    )
    lon_field = (
        channels["GPS Longitude"]
        .schema.field("GPS Longitude")
        .with_metadata({b"units": b"deg", b"dec_pts": b"6", b"interpolate": b"True"})
    )
    channels["GPS Longitude"] = channels["GPS Longitude"].cast(
        pa.schema([channels["GPS Longitude"].schema.field("timecodes"), lon_field])
    )

    channels["GPS Altitude"] = pa.table(
        {"timecodes": pa.array(timecodes, type=pa.int64()), "GPS Altitude": pa.array(gps_alt)}
    )
    alt_field = (
        channels["GPS Altitude"]
        .schema.field("GPS Altitude")
        .with_metadata({b"units": b"m", b"dec_pts": b"1", b"interpolate": b"True"})
    )
    channels["GPS Altitude"] = channels["GPS Altitude"].cast(
        pa.schema([channels["GPS Altitude"].schema.field("timecodes"), alt_field])
    )

    # Add a non-GPS channel with normal timing
    non_gps_timecodes = np.arange(0, n_samples * 20, 20, dtype=np.int64)  # 50Hz
    channels["BRK"] = pa.table(
        {
            "timecodes": pa.array(non_gps_timecodes, type=pa.int64()),
            "BRK": pa.array(np.random.rand(n_samples) * 100),
        }
    )

    # Create laps table with some laps spanning the gap
    laps = pa.table(
        {
            "num": pa.array([1, 2, 3], type=pa.int64()),
            "start_time": pa.array([0, 3000, 70000], type=pa.int64()),  # Lap 3 starts after gap
            "end_time": pa.array([3000, 70000, 140000], type=pa.int64()),
        }
    )

    return LogFile(channels=channels, laps=laps, metadata={"test": "data"}, file_name="test.xrk")


class TestGpsTimingGapFix(unittest.TestCase):
    """Tests for fix_gps_timing_gaps function."""

    def test_gps_channel_names_constant(self):
        """Verify GPS_CHANNEL_NAMES contains expected channel names."""
        self.assertIn("GPS Speed", GPS_CHANNEL_NAMES)
        self.assertIn("GPS Latitude", GPS_CHANNEL_NAMES)
        self.assertIn("GPS Longitude", GPS_CHANNEL_NAMES)
        self.assertIn("GPS Altitude", GPS_CHANNEL_NAMES)

    def test_detects_and_fixes_65533ms_gap(self):
        """Test that the typical 65533ms gap is detected and corrected."""
        log = create_mock_log_with_gps_gap(gap_index=87, gap_size_ms=65533)

        # Verify the gap exists before fix
        gps_time_before = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt_before = np.diff(gps_time_before)
        self.assertTrue(np.any(dt_before > 400), "Test setup: gap should exist before fix")

        # Apply the fix
        fix_gps_timing_gaps(log)

        # Verify the gap is fixed
        gps_time_after = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt_after = np.diff(gps_time_after)

        # All time deltas should now be ~40ms (within tolerance)
        self.assertTrue(np.all(dt_after < 100), f"Gap not fixed: max delta = {np.max(dt_after)}ms")

    def test_fixes_all_gps_channels(self):
        """Test that all GPS channels are corrected together."""
        log = create_mock_log_with_gps_gap()
        fix_gps_timing_gaps(log)

        # All GPS channels should have the same corrected timecodes
        reference_time = log.channels["GPS Speed"].column("timecodes").to_numpy()

        for name in GPS_CHANNEL_NAMES:
            if name in log.channels:
                channel_time = log.channels[name].column("timecodes").to_numpy()
                np.testing.assert_array_equal(
                    channel_time, reference_time, f"Channel {name} timecodes not matched"
                )

    def test_preserves_channel_metadata(self):
        """Test that channel metadata is preserved after fix."""
        log = create_mock_log_with_gps_gap()
        fix_gps_timing_gaps(log)

        # Check metadata is preserved
        speed_field = log.channels["GPS Speed"].schema.field("GPS Speed")
        self.assertIsNotNone(speed_field.metadata)
        self.assertEqual(speed_field.metadata.get(b"units"), b"m/s")
        self.assertEqual(speed_field.metadata.get(b"dec_pts"), b"2")

    def test_preserves_channel_values(self):
        """Test that channel values (not timecodes) are unchanged."""
        log = create_mock_log_with_gps_gap()

        # Store original values
        original_speed = log.channels["GPS Speed"].column("GPS Speed").to_numpy().copy()

        fix_gps_timing_gaps(log)

        # Values should be unchanged
        fixed_speed = log.channels["GPS Speed"].column("GPS Speed").to_numpy()
        np.testing.assert_array_equal(original_speed, fixed_speed)

    def test_does_not_modify_non_gps_channels(self):
        """Test that non-GPS channels are not affected."""
        log = create_mock_log_with_gps_gap()

        # Store original BRK timecodes
        original_brk_time = log.channels["BRK"].column("timecodes").to_numpy().copy()

        fix_gps_timing_gaps(log)

        # BRK timecodes should be unchanged
        fixed_brk_time = log.channels["BRK"].column("timecodes").to_numpy()
        np.testing.assert_array_equal(original_brk_time, fixed_brk_time)

    def test_fixes_lap_boundaries(self):
        """Test that lap boundaries are adjusted for the timing correction."""
        log = create_mock_log_with_gps_gap(gap_index=87, gap_size_ms=65533)

        # Get original lap 3 start (which is after the gap at ~70000ms)
        original_lap3_start = log.laps.column("start_time").to_numpy()[2]

        fix_gps_timing_gaps(log)

        # Lap 3 start should be shifted back by the gap correction
        fixed_lap3_start = log.laps.column("start_time").to_numpy()[2]

        # The correction is ~65493ms (65533 - 40)
        expected_correction = 65533 - 40
        expected_fixed_start = original_lap3_start - expected_correction

        self.assertAlmostEqual(fixed_lap3_start, expected_fixed_start, delta=1)

    def test_no_op_when_no_gaps(self):
        """Test that function is a no-op when there are no timing gaps."""
        # Create log without gaps
        n_samples = 200
        expected_dt_ms = 40.0
        timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

        channels = {
            "GPS Speed": pa.table(
                {
                    "timecodes": pa.array(timecodes, type=pa.int64()),
                    "GPS Speed": pa.array(np.linspace(0, 50, n_samples)),
                }
            )
        }
        laps = pa.table(
            {
                "num": pa.array([1], type=pa.int64()),
                "start_time": pa.array([0], type=pa.int64()),
                "end_time": pa.array([8000], type=pa.int64()),
            }
        )

        log = LogFile(channels=channels, laps=laps, metadata={}, file_name="test.xrk")

        original_time = log.channels["GPS Speed"].column("timecodes").to_numpy().copy()

        fix_gps_timing_gaps(log)

        fixed_time = log.channels["GPS Speed"].column("timecodes").to_numpy()
        np.testing.assert_array_equal(original_time, fixed_time)

    def test_no_op_when_no_gps_channels(self):
        """Test that function is a no-op when there are no GPS channels."""
        channels = {
            "BRK": pa.table(
                {
                    "timecodes": pa.array([0, 20, 40, 60], type=pa.int64()),
                    "BRK": pa.array([0.0, 10.0, 20.0, 30.0]),
                }
            )
        }
        laps = pa.table(
            {
                "num": pa.array([1], type=pa.int64()),
                "start_time": pa.array([0], type=pa.int64()),
                "end_time": pa.array([60], type=pa.int64()),
            }
        )

        log = LogFile(channels=channels, laps=laps, metadata={}, file_name="test.xrk")

        # Should not raise, should return the log unchanged
        result = fix_gps_timing_gaps(log)
        self.assertIs(result, log)

    def test_handles_multiple_gaps(self):
        """Test that multiple timing gaps are all corrected."""
        n_samples = 300
        expected_dt_ms = 40.0
        timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

        # Inject two gaps
        gap1_index = 50
        gap2_index = 150
        gap_size = 65533

        timecodes[gap1_index + 1 :] += gap_size - int(expected_dt_ms)
        timecodes[gap2_index + 1 :] += gap_size - int(expected_dt_ms)

        channels = {
            "GPS Speed": pa.table(
                {
                    "timecodes": pa.array(timecodes, type=pa.int64()),
                    "GPS Speed": pa.array(np.linspace(0, 50, n_samples)),
                }
            )
        }
        laps = None  # Test with no laps

        log = LogFile(channels=channels, laps=laps, metadata={}, file_name="test.xrk")

        fix_gps_timing_gaps(log)

        # Verify both gaps are fixed
        fixed_time = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt = np.diff(fixed_time)
        self.assertTrue(np.all(dt < 100), f"Gaps not fixed: max delta = {np.max(dt)}ms")

    def test_handles_empty_gps_channel(self):
        """Test that function handles GPS channels with <2 samples."""
        channels = {
            "GPS Speed": pa.table(
                {
                    "timecodes": pa.array([0], type=pa.int64()),
                    "GPS Speed": pa.array([5.0]),
                }
            )
        }
        laps = None

        log = LogFile(channels=channels, laps=laps, metadata={}, file_name="test.xrk")

        # Should not raise
        result = fix_gps_timing_gaps(log)
        self.assertIs(result, log)

    def test_returns_same_log_object(self):
        """Test that the function returns the same LogFile object (in-place modification)."""
        log = create_mock_log_with_gps_gap()
        result = fix_gps_timing_gaps(log)
        self.assertIs(result, log)


class TestGpsTimingGapFixIntegration(unittest.TestCase):
    """Integration tests using real XRK files with the GPS timing bug."""

    TEST_DATA_DIR: "Path"  # type: ignore[name-defined]
    SFJ_0101_XRK: "Path"  # type: ignore[name-defined]
    SFJ_0101_XRZ: "Path"  # type: ignore[name-defined]
    log_xrk: "Any"  # type: ignore[name-defined]
    log_xrz: "Any"  # type: ignore[name-defined]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the test file once for all tests."""
        from pathlib import Path
        from libxrk import aim_xrk

        cls.TEST_DATA_DIR = Path(__file__).parent / "test_data"
        cls.SFJ_0101_XRK = (
            cls.TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
        )
        cls.SFJ_0101_XRZ = (
            cls.TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz"
        )

        # Load the XRK file (fix is automatically applied)
        cls.log_xrk = aim_xrk(str(cls.SFJ_0101_XRK))

        # Try to load XRZ file, but it may be corrupted/truncated
        cls.log_xrz = None
        if cls.SFJ_0101_XRZ.exists():
            try:
                cls.log_xrz = aim_xrk(str(cls.SFJ_0101_XRZ))
            except Exception:
                pass  # XRZ file may be corrupted

    def test_0101_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(self.SFJ_0101_XRK.exists(), f"Test file not found: {self.SFJ_0101_XRK}")

    def test_gps_channels_exist(self):
        """Verify GPS channels are present in the loaded file."""
        for name in GPS_CHANNEL_NAMES:
            self.assertIn(name, self.log_xrk.channels, f"Missing channel: {name}")

    def test_no_large_gaps_after_fix(self):
        """Verify no large timing gaps remain in GPS channels after automatic fix."""
        gps_time = self.log_xrk.channels["GPS Speed"].column("timecodes").to_numpy()
        dt = np.diff(gps_time)

        # After fix, no gaps should be larger than 400ms (10x expected 40ms)
        max_gap = np.max(dt)
        self.assertLess(
            max_gap, 400, f"Large gap still present after fix: {max_gap}ms at index {np.argmax(dt)}"
        )

    def test_gps_time_range_reasonable(self):
        """Verify GPS time range is now reasonable (not extending way beyond other channels)."""
        gps_time = self.log_xrk.channels["GPS Speed"].column("timecodes").to_numpy()
        gps_end = gps_time[-1]

        # Check against a non-GPS channel
        if "InlineAcc" in self.log_xrk.channels:
            other_time = self.log_xrk.channels["InlineAcc"].column("timecodes").to_numpy()
        elif "BRK" in self.log_xrk.channels:
            other_time = self.log_xrk.channels["BRK"].column("timecodes").to_numpy()
        else:
            self.skipTest("No reference channel (InlineAcc or BRK) available")

        other_end = other_time[-1]

        # GPS should end within ~10 seconds of other channels (not 66 seconds beyond)
        time_diff = abs(gps_end - other_end)
        self.assertLess(
            time_diff,
            10000,  # 10 seconds
            f"GPS end time ({gps_end}ms) differs from other channel ({other_end}ms) by {time_diff}ms",
        )

    def test_all_gps_channels_same_timecodes(self):
        """Verify all GPS channels have identical timecodes after fix."""
        reference_time = self.log_xrk.channels["GPS Speed"].column("timecodes").to_numpy()

        for name in GPS_CHANNEL_NAMES:
            if name in self.log_xrk.channels and name != "GPS Speed":
                channel_time = self.log_xrk.channels[name].column("timecodes").to_numpy()
                np.testing.assert_array_equal(
                    channel_time, reference_time, f"Channel {name} timecodes don't match GPS Speed"
                )

    def test_xrk_and_xrz_produce_same_gps_timecodes(self):
        """Verify XRK and XRZ files produce the same GPS timecodes after fix."""
        if self.log_xrz is None:
            self.skipTest("XRZ file not available or corrupted")

        xrk_time = self.log_xrk.channels["GPS Speed"].column("timecodes").to_numpy()
        xrz_time = self.log_xrz.channels["GPS Speed"].column("timecodes").to_numpy()

        np.testing.assert_array_equal(
            xrk_time, xrz_time, "XRK and XRZ GPS timecodes differ after fix"
        )

    def test_gps_values_preserved(self):
        """Verify GPS values are not modified by the timing fix."""
        if self.log_xrz is None:
            self.skipTest("XRZ file not available or corrupted")

        # The values should be the same between XRK and XRZ (only timecodes were fixed)
        xrk_speed = self.log_xrk.channels["GPS Speed"].column("GPS Speed").to_numpy()
        xrz_speed = self.log_xrz.channels["GPS Speed"].column("GPS Speed").to_numpy()

        np.testing.assert_array_almost_equal(
            xrk_speed, xrz_speed, err_msg="GPS Speed values differ between XRK and XRZ"
        )

    def test_channel_metadata_preserved(self):
        """Verify channel metadata is preserved after fix."""
        for name in GPS_CHANNEL_NAMES:
            if name in self.log_xrk.channels:
                field = self.log_xrk.channels[name].schema.field(name)
                self.assertIsNotNone(field.metadata, f"Metadata missing for {name}")
                self.assertIn(b"units", field.metadata, f"units missing for {name}")

    def test_laps_present_and_reasonable(self):
        """Verify laps are present and most have reasonable timing."""
        self.assertIsNotNone(self.log_xrk.laps)
        self.assertGreater(len(self.log_xrk.laps), 0, "No laps found")

        start_times = self.log_xrk.laps.column("start_time").to_numpy()
        end_times = self.log_xrk.laps.column("end_time").to_numpy()

        # Count valid laps (positive duration, < 5 minutes)
        valid_laps = 0
        invalid_laps = 0
        for i, (start, end) in enumerate(zip(start_times, end_times)):
            lap_duration = end - start
            if lap_duration > 0 and lap_duration < 300000:  # 5 minutes
                valid_laps += 1
            else:
                invalid_laps += 1

        # Most laps should be valid (allow some data anomalies)
        total_laps = len(start_times)
        self.assertGreater(
            valid_laps, total_laps * 0.8, f"Too many invalid laps: {invalid_laps}/{total_laps}"
        )


if __name__ == "__main__":
    unittest.main()
