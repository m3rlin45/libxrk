"""Tests for GPS timing gap detection and correction in libxrk."""

import gc
import unittest
from typing import ClassVar, Dict, Optional

import numpy as np
import pyarrow as pa

from libxrk import GPS_CHANNEL_NAMES, ChannelMetadata, LogFile, aim_xrk
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

    # GPS channel metadata definitions
    gps_meta = {
        "GPS Speed": ChannelMetadata(units="m/s", dec_pts=2, interpolate=True),
        "GPS Latitude": ChannelMetadata(units="deg", dec_pts=6, interpolate=True),
        "GPS Longitude": ChannelMetadata(units="deg", dec_pts=6, interpolate=True),
        "GPS Altitude": ChannelMetadata(units="m", dec_pts=1, interpolate=True),
    }
    gps_values = {
        "GPS Speed": gps_speed,
        "GPS Latitude": gps_lat,
        "GPS Longitude": gps_lon,
        "GPS Altitude": gps_alt,
    }

    # Create GPS channels with metadata
    for name, values in gps_values.items():
        table = pa.table(
            {"timecodes": pa.array(timecodes, type=pa.int64()), name: pa.array(values)}
        )
        field = table.schema.field(name).with_metadata(gps_meta[name].to_field_metadata())
        channels[name] = table.cast(pa.schema([table.schema.field("timecodes"), field]))

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
            "lap_type": pa.array(["full", "full", "full"], type=pa.utf8()),
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
        log = fix_gps_timing_gaps(log)

        # Verify the gap is fixed
        gps_time_after = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt_after = np.diff(gps_time_after)

        # All time deltas should now be ~40ms (within tolerance)
        self.assertTrue(np.all(dt_after < 100), f"Gap not fixed: max delta = {np.max(dt_after)}ms")

    def test_fixes_all_gps_channels(self):
        """Test that all GPS channels are corrected together."""
        log = create_mock_log_with_gps_gap()
        log = fix_gps_timing_gaps(log)

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
        log = fix_gps_timing_gaps(log)

        # Check metadata is preserved
        meta = ChannelMetadata.from_channel_table(log.channels["GPS Speed"])
        self.assertEqual(meta.units, "m/s")
        self.assertEqual(meta.dec_pts, 2)

    def test_preserves_channel_values(self):
        """Test that channel values (not timecodes) are unchanged."""
        log = create_mock_log_with_gps_gap()

        # Store original values
        original_speed = log.channels["GPS Speed"].column("GPS Speed").to_numpy().copy()

        log = fix_gps_timing_gaps(log)

        # Values should be unchanged
        fixed_speed = log.channels["GPS Speed"].column("GPS Speed").to_numpy()
        np.testing.assert_array_equal(original_speed, fixed_speed)

    def test_does_not_modify_non_gps_channels(self):
        """Test that non-GPS channels are not affected."""
        log = create_mock_log_with_gps_gap()

        # Store original BRK timecodes
        original_brk_time = log.channels["BRK"].column("timecodes").to_numpy().copy()

        log = fix_gps_timing_gaps(log)

        # BRK timecodes should be unchanged
        fixed_brk_time = log.channels["BRK"].column("timecodes").to_numpy()
        np.testing.assert_array_equal(original_brk_time, fixed_brk_time)

    def test_fixes_lap_boundaries(self):
        """Test that lap boundaries are adjusted for the timing correction."""
        log = create_mock_log_with_gps_gap(gap_index=87, gap_size_ms=65533)

        # Get original lap 3 start (which is after the gap at ~70000ms)
        original_lap3_start = log.laps.column("start_time").to_numpy()[2]

        log = fix_gps_timing_gaps(log)

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
                "lap_type": pa.array(["full"], type=pa.utf8()),
            }
        )

        log = LogFile(channels=channels, laps=laps, metadata={}, file_name="test.xrk")

        original_time = log.channels["GPS Speed"].column("timecodes").to_numpy().copy()

        log = fix_gps_timing_gaps(log)

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
                "lap_type": pa.array(["full"], type=pa.utf8()),
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

        log = fix_gps_timing_gaps(log)

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

    def test_returns_new_log_object(self):
        """Test that the function returns a new LogFile (immutable pattern)."""
        log = create_mock_log_with_gps_gap()
        result = fix_gps_timing_gaps(log)
        self.assertIsNot(result, log)


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

        cls.TEST_DATA_DIR = Path(__file__).parent / "test_data"
        cls.SFJ_0101_XRK = (
            cls.TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
        )
        cls.SFJ_0101_XRZ = (
            cls.TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz"
        )

        # Load the XRK file (fix is automatically applied)
        cls.log_xrk = aim_xrk(str(cls.SFJ_0101_XRK))

        # Load XRZ file (truncated files are now handled gracefully)
        cls.log_xrz = aim_xrk(str(cls.SFJ_0101_XRZ))

    @classmethod
    def tearDownClass(cls) -> None:
        """Release cached logs; unittest keeps class attrs alive otherwise."""
        del cls.log_xrk
        del cls.log_xrz
        gc.collect()

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
        xrk_time = self.log_xrk.channels["GPS Speed"].column("timecodes").to_numpy()
        xrz_time = self.log_xrz.channels["GPS Speed"].column("timecodes").to_numpy()

        np.testing.assert_array_equal(
            xrk_time, xrz_time, "XRK and XRZ GPS timecodes differ after fix"
        )

    def test_gps_values_preserved(self):
        """Verify GPS values are not modified by the timing fix."""
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
                meta = ChannelMetadata.from_channel_table(self.log_xrk.channels[name])
                # GPS channels should have units set (except satellite/fix channels)
                field = self.log_xrk.channels[name].schema.field(name)
                self.assertIsNotNone(field.metadata, f"Metadata missing for {name}")

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


class TestGnfiBasedDetection(unittest.TestCase):
    """Tests for GNFI-based GPS timing offset detection."""

    def test_gnfi_detects_offset(self) -> None:
        """Test that GNFI detection correctly identifies the 65533ms offset."""
        from libxrk.gps import detect_gps_timing_offset_from_gnfi

        # Simulate GPS timecodes with hidden bug: GPS ends ~65533ms after GNFI
        # GNFI ends at 100000ms (100 seconds), GPS ends at 165533ms
        n_samples = 2500
        expected_dt_ms = 40.0

        gnfi_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

        # GPS has the same number of samples but includes a bug at sample 1000
        # The bug adds 65533ms to all subsequent samples
        gps_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)
        gap_index = 1000
        gps_timecodes[gap_index + 1 :] += 65533 - int(expected_dt_ms)

        result = detect_gps_timing_offset_from_gnfi(gps_timecodes, gnfi_timecodes, expected_dt_ms)

        self.assertEqual(len(result), 1, "Should detect exactly one offset")
        gap_time, correction = result[0]
        # For direct gaps where gap_size is ~65533ms, correction is gap_size - expected_dt
        self.assertEqual(
            correction,
            65533 - int(expected_dt_ms),
            "Correction should be excess time for direct gap",
        )
        self.assertEqual(gap_time, gps_timecodes[gap_index], "Gap time should match")

    def test_gnfi_no_offset_detected_when_clean(self) -> None:
        """Test that GNFI detection returns empty list when no offset exists."""
        from libxrk.gps import detect_gps_timing_offset_from_gnfi

        n_samples = 2500
        expected_dt_ms = 40.0

        # Both GNFI and GPS are clean (no bug)
        gnfi_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)
        gps_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

        result = detect_gps_timing_offset_from_gnfi(gps_timecodes, gnfi_timecodes, expected_dt_ms)

        self.assertEqual(len(result), 0, "Should not detect any offset when data is clean")

    def test_gnfi_missing_returns_empty(self) -> None:
        """Test that GNFI detection returns empty when GNFI timecodes are None."""
        from libxrk.gps import detect_gps_timing_offset_from_gnfi

        gps_timecodes = np.arange(0, 10000, 40, dtype=np.int64)

        # Pass empty array instead of None to satisfy type checker
        result = detect_gps_timing_offset_from_gnfi(
            gps_timecodes, np.array([], dtype=np.int64), 40.0
        )
        self.assertEqual(len(result), 0)

    def test_gnfi_too_short_returns_empty(self) -> None:
        """Test that GNFI detection returns empty when GNFI has too few samples."""
        from libxrk.gps import detect_gps_timing_offset_from_gnfi

        gps_timecodes = np.arange(0, 10000, 40, dtype=np.int64)
        gnfi_timecodes = np.array([0], dtype=np.int64)  # Only 1 sample

        result = detect_gps_timing_offset_from_gnfi(gps_timecodes, gnfi_timecodes, 40.0)
        self.assertEqual(len(result), 0)

    def test_gnfi_fallback_to_heuristics_when_gnfi_missing(self) -> None:
        """Test that fix_gps_timing_gaps falls back to heuristics when GNFI is None."""
        log = create_mock_log_with_gps_gap(gap_index=87, gap_size_ms=65533)

        # Verify the gap exists before fix
        gps_time_before = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt_before = np.diff(gps_time_before)
        self.assertTrue(np.any(dt_before > 400), "Test setup: gap should exist before fix")

        # Apply the fix WITHOUT GNFI (should fall back to heuristics)
        log = fix_gps_timing_gaps(log, gnfi_timecodes=None)

        # Verify the gap is fixed
        gps_time_after = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt_after = np.diff(gps_time_after)

        self.assertTrue(np.all(dt_after < 100), f"Gap not fixed: max delta = {np.max(dt_after)}ms")

    def test_gnfi_used_when_provided(self) -> None:
        """Test that GNFI-based detection is used when GNFI timecodes are provided."""
        n_samples = 200
        expected_dt_ms = 40.0
        gap_index = 87
        gap_size_ms = 65533

        # Create GPS timecodes with the bug
        gps_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)
        gps_timecodes[gap_index + 1 :] += gap_size_ms - int(expected_dt_ms)

        # Create GNFI timecodes (clean, no bug)
        gnfi_timecodes = np.arange(0, n_samples * expected_dt_ms, expected_dt_ms, dtype=np.int64)

        channels = {
            "GPS Speed": pa.table(
                {
                    "timecodes": pa.array(gps_timecodes, type=pa.int64()),
                    "GPS Speed": pa.array(np.linspace(0, 50, n_samples)),
                }
            )
        }

        log = LogFile(channels=channels, laps=None, metadata={}, file_name="test.xrk")

        # Apply fix with GNFI
        log = fix_gps_timing_gaps(log, gnfi_timecodes=gnfi_timecodes)

        # Verify the gap is fixed
        gps_time_fixed = log.channels["GPS Speed"].column("timecodes").to_numpy()
        dt = np.diff(gps_time_fixed)

        self.assertTrue(np.all(dt < 100), f"Gap not fixed: max delta = {np.max(dt)}ms")


class TestSuzukaGnfiIntegration(unittest.TestCase):
    """Integration tests for GNFI-based detection on Suzuka file."""

    TEST_DATA_DIR: "Path"  # type: ignore[name-defined]
    SUZUKA_XRK: "Path"  # type: ignore[name-defined]
    log: "Any"  # type: ignore[name-defined]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the Suzuka test file."""
        from pathlib import Path

        cls.TEST_DATA_DIR = Path(__file__).parent / "test_data"
        cls.SUZUKA_XRK = cls.TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"

        cls.log = None
        if cls.SUZUKA_XRK.exists():
            cls.log = aim_xrk(str(cls.SUZUKA_XRK))

    @classmethod
    def tearDownClass(cls) -> None:
        """Release cached logs; unittest keeps class attrs alive otherwise."""
        del cls.log
        gc.collect()

    def test_suzuka_file_exists(self) -> None:
        """Verify the Suzuka test file exists."""
        self.assertTrue(self.SUZUKA_XRK.exists(), f"Test file not found: {self.SUZUKA_XRK}")

    def test_suzuka_gps_timing_corrected(self) -> None:
        """Verify GPS timing is corrected in Suzuka file (uses GNFI-based detection)."""
        if self.log is None:
            self.skipTest("Suzuka file not available")

        gps_time = self.log.channels["GPS Speed"].column("timecodes").to_numpy()

        # Check against InlineAcc
        if "InlineAcc" not in self.log.channels:
            self.skipTest("InlineAcc channel not available")

        inline_time = self.log.channels["InlineAcc"].column("timecodes").to_numpy()

        # After fix, GPS should end within 10 seconds of InlineAcc
        # (before fix it would be ~65 seconds beyond)
        time_diff_s = abs(gps_time[-1] - inline_time[-1]) / 1000
        self.assertLess(
            time_diff_s,
            10,
            f"GPS end time differs from InlineAcc by {time_diff_s:.1f}s (should be <10s after fix)",
        )


def _rust_backend_available() -> bool:
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestGpsTimingCrossBackend(unittest.TestCase):
    """Cross-backend comparison for GPS timing fix on real files."""

    rust_0101: ClassVar[LogFile]
    cython_0101: ClassVar[LogFile]
    rust_suzuka: ClassVar[Optional[LogFile]]
    cython_suzuka: ClassVar[Optional[LogFile]]

    @classmethod
    def setUpClass(cls) -> None:
        from pathlib import Path

        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        test_data_dir = Path(__file__).parent / "test_data"
        sfj_0101 = test_data_dir / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
        suzuka = test_data_dir / "SFJ" / "CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"

        cls.rust_0101 = rust_aim_xrk(str(sfj_0101))
        cls.cython_0101 = cython_aim_xrk(str(sfj_0101))

        cls.rust_suzuka = None
        cls.cython_suzuka = None
        if suzuka.exists():
            cls.rust_suzuka = rust_aim_xrk(str(suzuka))
            cls.cython_suzuka = cython_aim_xrk(str(suzuka))

    @classmethod
    def tearDownClass(cls) -> None:
        """Release cached logs; unittest keeps class attrs alive otherwise."""
        del cls.cython_0101
        del cls.cython_suzuka
        del cls.rust_0101
        del cls.rust_suzuka
        gc.collect()

    def test_0101_channel_names_match(self) -> None:
        """Channel names should match for 0101 file."""
        self.assertEqual(
            set(self.rust_0101.channels.keys()),
            set(self.cython_0101.channels.keys()),
        )

    def test_0101_channel_types_match(self) -> None:
        """Arrow types should match for all 0101 channels."""
        for name in self.rust_0101.channels:
            rust_type = self.rust_0101.channels[name].schema.field(name).type
            cython_type = self.cython_0101.channels[name].schema.field(name).type
            self.assertEqual(
                rust_type,
                cython_type,
                f"Channel {name!r}: rust type={rust_type} != cython type={cython_type}",
            )

    def test_0101_non_gps_values_exact_match(self) -> None:
        """Non-GPS channels should match exactly for 0101 file."""
        gps_prefixes = ("GPS ", "GPS_")
        for name in self.rust_0101.channels:
            if any(name.startswith(p) for p in gps_prefixes):
                continue
            rust_tc = self.rust_0101.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_0101.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(rust_tc, cython_tc, err_msg=f"{name} timecodes")
            rust_vals = self.rust_0101.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = (
                self.cython_0101.channels[name].column(name).to_numpy(zero_copy_only=False)
            )
            np.testing.assert_array_equal(rust_vals, cython_vals, err_msg=f"{name} values")

    def test_0101_gps_timecodes_match(self) -> None:
        """GPS timecodes should match after timing fix for 0101 file."""
        for name in GPS_CHANNEL_NAMES:
            if name not in self.rust_0101.channels:
                continue
            rust_tc = self.rust_0101.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_0101.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(rust_tc, cython_tc, err_msg=f"{name} timecodes")

    def test_0101_gps_values_close(self) -> None:
        """GPS values should be close between backends for 0101 file."""
        for name in self.rust_0101.channels:
            if not (name.startswith("GPS ") or name.startswith("GPS_")):
                continue
            rust_vals = self.rust_0101.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = (
                self.cython_0101.channels[name].column(name).to_numpy(zero_copy_only=False)
            )
            np.testing.assert_allclose(
                rust_vals, cython_vals, rtol=1e-5, atol=1e-10, err_msg=f"{name}"
            )

    def test_0101_lap_data_match(self) -> None:
        """Lap times should match exactly between backends for 0101 file.

        The 0101 file has the 65533ms GPS timing bug and LAP messages. Since
        LAP messages use the internal clock (not the GPS clock), the GPS fix
        should NOT modify lap boundaries. Both backends should produce the
        raw LAP message timestamps.
        """
        self.assertEqual(self.rust_0101.laps.num_rows, self.cython_0101.laps.num_rows)
        rust_starts = self.rust_0101.laps.column("start_time").to_pylist()
        cython_starts = self.cython_0101.laps.column("start_time").to_pylist()
        self.assertEqual(rust_starts, cython_starts)
        rust_ends = self.rust_0101.laps.column("end_time").to_pylist()
        cython_ends = self.cython_0101.laps.column("end_time").to_pylist()
        self.assertEqual(rust_ends, cython_ends)

    def test_suzuka_channel_types_match(self) -> None:
        """Arrow types should match for all Suzuka channels."""
        if self.rust_suzuka is None:
            self.skipTest("Suzuka file not available")
        assert self.rust_suzuka is not None and self.cython_suzuka is not None
        for name in self.rust_suzuka.channels:
            rust_type = self.rust_suzuka.channels[name].schema.field(name).type
            cython_type = self.cython_suzuka.channels[name].schema.field(name).type
            self.assertEqual(
                rust_type,
                cython_type,
                f"Channel {name!r}: rust type={rust_type} != cython type={cython_type}",
            )

    def test_suzuka_non_gps_values_exact_match(self) -> None:
        """Non-GPS channels should match exactly for Suzuka file."""
        if self.rust_suzuka is None:
            self.skipTest("Suzuka file not available")
        assert self.rust_suzuka is not None and self.cython_suzuka is not None
        gps_prefixes = ("GPS ", "GPS_")
        for name in self.rust_suzuka.channels:
            if any(name.startswith(p) for p in gps_prefixes):
                continue
            rust_tc = self.rust_suzuka.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_suzuka.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(rust_tc, cython_tc, err_msg=f"{name} timecodes")
            rust_vals = self.rust_suzuka.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = (
                self.cython_suzuka.channels[name].column(name).to_numpy(zero_copy_only=False)
            )
            np.testing.assert_array_equal(rust_vals, cython_vals, err_msg=f"{name} values")

    def test_suzuka_gps_values_close(self) -> None:
        """GPS values should be close between backends for Suzuka file."""
        if self.rust_suzuka is None:
            self.skipTest("Suzuka file not available")
        assert self.rust_suzuka is not None and self.cython_suzuka is not None
        for name in self.rust_suzuka.channels:
            if not (name.startswith("GPS ") or name.startswith("GPS_")):
                continue
            rust_vals = self.rust_suzuka.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = (
                self.cython_suzuka.channels[name].column(name).to_numpy(zero_copy_only=False)
            )
            np.testing.assert_allclose(
                rust_vals, cython_vals, rtol=1e-5, atol=1e-10, err_msg=f"{name}"
            )


if __name__ == "__main__":
    unittest.main()
