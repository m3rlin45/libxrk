"""Tests for channel synchronization using correlation analysis.

These tests verify that different sensor channels are temporally synchronized
by computing correlation between channels that should measure related physical
phenomena. If channels are offset by a significant time (e.g., >1 second),
the correlation will be low even though both channels are measuring the same event.

Key insight: Gate on correlation value, not lag. If channels are properly
synchronized and measuring the same physical phenomenon, correlation should be high.
"""

import unittest
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pyarrow as pa

from libxrk import aim_xrk, LogFile


# Test data paths
TEST_DATA_DIR = Path(__file__).parent / "test_data"
SFJ_0033_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
SFJ_0101_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk"
T86_2248_XRK = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
SUZUKA_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk"


def compute_gps_longitudinal_acceleration(log: LogFile) -> Tuple[np.ndarray, np.ndarray]:
    """Compute longitudinal acceleration from GPS speed derivative.

    Args:
        log: LogFile with GPS Speed channel

    Returns:
        Tuple of (timecodes in ms, acceleration in G)
    """
    gps_table = log.channels["GPS Speed"]
    gps_tc = gps_table.column("timecodes").to_numpy()
    gps_speed = gps_table.column("GPS Speed").to_numpy()

    # Compute derivative: dv/dt
    dt_s = np.diff(gps_tc) / 1000.0  # Convert ms to seconds
    dv = np.diff(gps_speed)  # m/s

    # Avoid division by zero
    dt_s = np.where(dt_s == 0, 1e-6, dt_s)

    acceleration_ms2 = dv / dt_s  # m/s^2
    acceleration_g = acceleration_ms2 / 9.81  # Convert to G

    # Use midpoint timecodes for the derivative
    midpoint_tc = (gps_tc[:-1] + gps_tc[1:]) / 2

    return midpoint_tc, acceleration_g


def compute_gps_lateral_acceleration(log: LogFile) -> Tuple[np.ndarray, np.ndarray]:
    """Compute lateral acceleration from GPS heading changes and speed.

    Lateral acceleration = v^2 / r = v * omega
    where omega = d(heading)/dt and v = GPS speed

    This requires GPS latitude and longitude to compute heading.

    Args:
        log: LogFile with GPS Speed, GPS Latitude, GPS Longitude channels

    Returns:
        Tuple of (timecodes in ms, lateral acceleration in G)
    """
    # Get GPS data
    gps_lat = log.channels["GPS Latitude"].column("GPS Latitude").to_numpy()
    gps_lon = log.channels["GPS Longitude"].column("GPS Longitude").to_numpy()
    gps_speed = log.channels["GPS Speed"].column("GPS Speed").to_numpy()
    gps_tc = log.channels["GPS Speed"].column("timecodes").to_numpy()

    # Compute heading from lat/lon changes
    # Using simple approximation: heading = atan2(dlon, dlat)
    dlat = np.diff(gps_lat)
    dlon = np.diff(gps_lon)

    # Account for latitude in longitude conversion
    avg_lat = (gps_lat[:-1] + gps_lat[1:]) / 2
    dlon_corrected = dlon * np.cos(np.radians(avg_lat))

    heading = np.arctan2(dlon_corrected, dlat)

    # Compute heading rate (angular velocity in rad/s)
    dt_s = np.diff(gps_tc[:-1]) / 1000.0  # Time between heading samples
    dt_s = np.where(dt_s == 0, 1e-6, dt_s)
    dheading = np.diff(heading)

    # Handle heading wrap-around
    dheading = np.where(dheading > np.pi, dheading - 2 * np.pi, dheading)
    dheading = np.where(dheading < -np.pi, dheading + 2 * np.pi, dheading)

    heading_rate = dheading / dt_s  # rad/s

    # Lateral acceleration = v * omega
    # Use speed at midpoint
    mid_speed = (gps_speed[1:-1] + gps_speed[2:]) / 2
    lateral_acc_ms2 = mid_speed * heading_rate

    lateral_acc_g = lateral_acc_ms2 / 9.81

    # Timecodes at midpoints
    midpoint_tc = (gps_tc[1:-1] + gps_tc[2:]) / 2

    return midpoint_tc, lateral_acc_g


def resample_to_common_timebase(
    log: LogFile,
    channel_a_name: str,
    channel_b_tc: np.ndarray,
    channel_b_values: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Resample channel A to channel B's timebase for correlation.

    Args:
        log: LogFile containing channel_a_name
        channel_a_name: Name of channel to resample
        channel_b_tc: Timecodes of channel B
        channel_b_values: Values of channel B

    Returns:
        Tuple of (resampled channel A values, channel B values) at common timebase
    """
    # Get channel A data
    channel_a = log.channels[channel_a_name]
    tc_a = channel_a.column("timecodes").to_numpy()
    values_a = channel_a.column(channel_a_name).to_numpy()

    # Find common time range
    start = max(tc_a[0], channel_b_tc[0])
    end = min(tc_a[-1], channel_b_tc[-1])

    # Filter channel B to common range
    mask_b = (channel_b_tc >= start) & (channel_b_tc <= end)
    common_tc = channel_b_tc[mask_b]
    values_b_filtered = channel_b_values[mask_b]

    if len(common_tc) < 10:
        return np.array([]), np.array([])

    # Resample channel A to channel B's timecodes
    values_a_resampled = np.interp(common_tc, tc_a, values_a)

    return values_a_resampled, values_b_filtered


def compute_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Compute Pearson correlation between two arrays.

    Args:
        a: First array
        b: Second array (same length as a)

    Returns:
        Pearson correlation coefficient, or 0.0 if arrays are invalid
    """
    if len(a) < 10 or len(b) < 10:
        return 0.0
    if len(a) != len(b):
        return 0.0

    # Check for constant arrays
    if np.std(a) < 1e-10 or np.std(b) < 1e-10:
        return 0.0

    return float(np.corrcoef(a, b)[0, 1])


def smooth_signal(values: np.ndarray, window: int = 5) -> np.ndarray:
    """Apply moving average smoothing to reduce noise.

    Args:
        values: Input signal
        window: Smoothing window size

    Returns:
        Smoothed signal (same length, edges are handled)
    """
    if len(values) < window:
        return values

    kernel = np.ones(window) / window
    smoothed = np.convolve(values, kernel, mode="same")
    return smoothed


class TestChannelSynchronization(unittest.TestCase):
    """Tests that verify channels are temporally synchronized using correlation."""

    log_0033: Optional[LogFile]
    log_0101: Optional[LogFile]
    log_2248: Optional[LogFile]
    log_suzuka: Optional[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        """Load test files once for all tests."""
        cls.log_0033 = None
        cls.log_0101 = None
        cls.log_2248 = None
        cls.log_suzuka = None

        if SFJ_0033_XRK.exists():
            cls.log_0033 = aim_xrk(str(SFJ_0033_XRK))

        if SFJ_0101_XRK.exists():
            cls.log_0101 = aim_xrk(str(SFJ_0101_XRK))

        if T86_2248_XRK.exists():
            cls.log_2248 = aim_xrk(str(T86_2248_XRK))

        if SUZUKA_XRK.exists():
            cls.log_suzuka = aim_xrk(str(SUZUKA_XRK))

    def _test_inline_acc_gps_correlation(self, log: LogFile, min_correlation: float = 0.5):
        """Test that InlineAcc correlates with GPS-derived longitudinal acceleration.

        Args:
            log: LogFile to test
            min_correlation: Minimum acceptable correlation
        """
        if "InlineAcc" not in log.channels or "GPS Speed" not in log.channels:
            self.skipTest("Required channels not available")

        # Compute GPS-derived acceleration
        gps_tc, gps_acc = compute_gps_longitudinal_acceleration(log)

        # Smooth GPS acceleration (it's noisy due to differentiation)
        gps_acc_smooth = smooth_signal(gps_acc, window=5)

        # Resample InlineAcc to GPS timebase
        inline_acc, gps_acc_resampled = resample_to_common_timebase(
            log, "InlineAcc", gps_tc, gps_acc_smooth
        )

        if len(inline_acc) < 100:
            self.skipTest("Insufficient overlapping data for correlation")

        # Smooth InlineAcc as well
        inline_acc_smooth = smooth_signal(inline_acc, window=5)

        # Compute correlation
        correlation = compute_correlation(inline_acc_smooth, gps_acc_resampled)

        self.assertGreater(
            correlation,
            min_correlation,
            f"InlineAcc vs GPS acceleration correlation too low: {correlation:.3f} < {min_correlation}",
        )

        return correlation

    def _test_lateral_acc_gps_correlation(self, log: LogFile, min_correlation: float = 0.3):
        """Test that LateralAcc correlates with GPS-derived lateral acceleration.

        Args:
            log: LogFile to test
            min_correlation: Minimum acceptable correlation (lower than inline due to GPS noise)
        """
        required = ["LateralAcc", "GPS Speed", "GPS Latitude", "GPS Longitude"]
        for ch in required:
            if ch not in log.channels:
                self.skipTest(f"Required channel {ch} not available")

        # Compute GPS-derived lateral acceleration
        gps_tc, gps_lat_acc = compute_gps_lateral_acceleration(log)

        # Smooth GPS lateral acceleration
        gps_lat_acc_smooth = smooth_signal(gps_lat_acc, window=7)

        # Resample LateralAcc to GPS timebase
        lateral_acc, gps_lat_resampled = resample_to_common_timebase(
            log, "LateralAcc", gps_tc, gps_lat_acc_smooth
        )

        if len(lateral_acc) < 100:
            self.skipTest("Insufficient overlapping data for correlation")

        lateral_acc_smooth = smooth_signal(lateral_acc, window=5)

        correlation = compute_correlation(lateral_acc_smooth, gps_lat_resampled)

        self.assertGreater(
            correlation,
            min_correlation,
            f"LateralAcc vs GPS lateral acceleration correlation too low: {correlation:.3f} < {min_correlation}",
        )

        return correlation

    # =========================================================================
    # Tests for existing files (baseline - should all pass)
    # =========================================================================

    def test_0033_inline_acc_gps_correlation(self):
        """Test InlineAcc correlates with GPS acceleration in 0033.xrk."""
        if self.log_0033 is None:
            self.skipTest("0033.xrk not available")

        correlation = self._test_inline_acc_gps_correlation(self.log_0033, min_correlation=0.80)
        print(f"\n0033.xrk InlineAcc vs GPS correlation: {correlation:.3f}")

    def test_0033_lateral_acc_gps_correlation(self):
        """Test LateralAcc correlates with GPS lateral acceleration in 0033.xrk."""
        if self.log_0033 is None:
            self.skipTest("0033.xrk not available")

        correlation = self._test_lateral_acc_gps_correlation(self.log_0033, min_correlation=0.85)
        print(f"\n0033.xrk LateralAcc vs GPS lateral correlation: {correlation:.3f}")

    def test_0101_inline_acc_gps_correlation(self):
        """Test InlineAcc correlates with GPS acceleration in 0101.xrk.

        This file has the 65533ms firmware bug that should be auto-fixed.
        """
        if self.log_0101 is None:
            self.skipTest("0101.xrk not available")

        correlation = self._test_inline_acc_gps_correlation(self.log_0101, min_correlation=0.90)
        print(f"\n0101.xrk InlineAcc vs GPS correlation: {correlation:.3f}")

    def test_0101_lateral_acc_gps_correlation(self):
        """Test LateralAcc correlates with GPS lateral acceleration in 0101.xrk."""
        if self.log_0101 is None:
            self.skipTest("0101.xrk not available")

        correlation = self._test_lateral_acc_gps_correlation(self.log_0101, min_correlation=0.95)
        print(f"\n0101.xrk LateralAcc vs GPS lateral correlation: {correlation:.3f}")

    def test_2248_inline_acc_gps_correlation(self):
        """Test InlineAcc correlates with GPS acceleration in 2248.xrk."""
        if self.log_2248 is None:
            self.skipTest("2248.xrk not available")

        correlation = self._test_inline_acc_gps_correlation(self.log_2248, min_correlation=0.95)
        print(f"\n2248.xrk InlineAcc vs GPS correlation: {correlation:.3f}")

    def test_2248_lateral_acc_gps_correlation(self):
        """Test LateralAcc correlates with GPS lateral acceleration in 2248.xrk."""
        if self.log_2248 is None:
            self.skipTest("2248.xrk not available")

        correlation = self._test_lateral_acc_gps_correlation(self.log_2248, min_correlation=0.95)
        print(f"\n2248.xrk LateralAcc vs GPS lateral correlation: {correlation:.3f}")

    # =========================================================================
    # Tests for Suzuka file (should fail before fix, pass after)
    # =========================================================================

    def test_suzuka_inline_acc_gps_correlation(self):
        """Test InlineAcc correlates with GPS acceleration in Suzuka file.

        This test verifies the GPS timing issue is fixed. Before the fix,
        correlation should be near 0 due to >1 second offset.
        """
        if self.log_suzuka is None:
            self.skipTest("Suzuka file not available")

        correlation = self._test_inline_acc_gps_correlation(self.log_suzuka, min_correlation=0.85)
        print(f"\nSuzuka InlineAcc vs GPS correlation: {correlation:.3f}")

    def test_suzuka_lateral_acc_gps_correlation(self):
        """Test LateralAcc correlates with GPS lateral acceleration in Suzuka file."""
        if self.log_suzuka is None:
            self.skipTest("Suzuka file not available")

        correlation = self._test_lateral_acc_gps_correlation(self.log_suzuka, min_correlation=0.80)
        print(f"\nSuzuka LateralAcc vs GPS lateral correlation: {correlation:.3f}")


class TestGpsGapHandling(unittest.TestCase):
    """Tests that verify GPS gaps are handled correctly.

    - Firmware bug gaps (~65533ms) should be corrected
    - Legitimate gaps (e.g., GPS signal loss) should be preserved
    """

    def test_0101_firmware_bug_corrected(self):
        """Verify 0101.xrk firmware bug (65533ms gap) is corrected."""
        if not SFJ_0101_XRK.exists():
            self.skipTest("0101.xrk not available")

        log = aim_xrk(str(SFJ_0101_XRK))
        gps_tc = log.channels["GPS Speed"].column("timecodes").to_numpy()
        inline_tc = log.channels["InlineAcc"].column("timecodes").to_numpy()

        dt = np.diff(gps_tc)
        large_gaps = np.where(dt > 400)[0]

        # After fix, no large gaps should remain (firmware bug corrected)
        self.assertEqual(
            len(large_gaps), 0, f"Large gaps still present after fix: {dt[large_gaps]}"
        )

        # GPS and InlineAcc should end within 10 seconds of each other
        time_diff = abs(gps_tc[-1] - inline_tc[-1]) / 1000
        self.assertLess(time_diff, 10, f"GPS ends {time_diff:.1f}s after InlineAcc")

    def test_0033_no_gaps(self):
        """Verify 0033.xrk has no large gaps (baseline file)."""
        if not SFJ_0033_XRK.exists():
            self.skipTest("0033.xrk not available")

        log = aim_xrk(str(SFJ_0033_XRK))
        gps_tc = log.channels["GPS Speed"].column("timecodes").to_numpy()
        inline_tc = log.channels["InlineAcc"].column("timecodes").to_numpy()

        dt = np.diff(gps_tc)
        large_gaps = np.where(dt > 400)[0]

        self.assertEqual(len(large_gaps), 0, "Unexpected gaps in baseline file")

        # GPS and InlineAcc should end within 1 second of each other
        time_diff = abs(gps_tc[-1] - inline_tc[-1]) / 1000
        self.assertLess(time_diff, 1, f"GPS ends {time_diff:.1f}s after InlineAcc")

    def test_suzuka_hidden_firmware_bug_corrected(self):
        """Verify Suzuka's hidden firmware bug is corrected.

        The Suzuka file has a ~52 second apparent GPS gap, but this includes
        a hidden 65533ms firmware bug that occurred during GPS signal loss.
        The actual signal loss was ~13 seconds. The fix should:
        1. Detect the bug via GPS extending ~65533ms beyond other channels
        2. Correct by subtracting 65533ms from GPS after the gap
        3. Result in GPS ending at approximately the same time as other channels
        """
        if not SUZUKA_XRK.exists():
            self.skipTest("Suzuka file not available")

        log = aim_xrk(str(SUZUKA_XRK))
        gps_tc = log.channels["GPS Speed"].column("timecodes").to_numpy()
        inline_tc = log.channels["InlineAcc"].column("timecodes").to_numpy()

        # After fix, GPS should end within 10 seconds of InlineAcc
        time_diff = abs(gps_tc[-1] - inline_tc[-1]) / 1000
        self.assertLess(time_diff, 10, f"GPS ends {time_diff:.1f}s after InlineAcc")


class TestChannelSyncDiagnostics(unittest.TestCase):
    """Diagnostic tests that report correlation values without assertions.

    Use these to investigate sync issues in specific files.
    """

    def test_report_all_correlations(self):
        """Report correlation values for all available test files."""
        files = [
            ("0033.xrk", SFJ_0033_XRK),
            ("0101.xrk", SFJ_0101_XRK),
            ("2248.xrk", T86_2248_XRK),
            ("Suzuka", SUZUKA_XRK),
        ]

        print("\n" + "=" * 60)
        print("Channel Synchronization Correlation Report")
        print("=" * 60)

        for name, path in files:
            if not path.exists():
                print(f"\n{name}: File not found")
                continue

            try:
                log = aim_xrk(str(path))

                # InlineAcc vs GPS
                if "InlineAcc" in log.channels and "GPS Speed" in log.channels:
                    gps_tc, gps_acc = compute_gps_longitudinal_acceleration(log)
                    gps_acc_smooth = smooth_signal(gps_acc, window=5)
                    inline_acc, gps_acc_resampled = resample_to_common_timebase(
                        log, "InlineAcc", gps_tc, gps_acc_smooth
                    )
                    if len(inline_acc) >= 10:
                        inline_acc_smooth = smooth_signal(inline_acc, window=5)
                        corr = compute_correlation(inline_acc_smooth, gps_acc_resampled)
                        status = "OK" if corr > 0.5 else "LOW"
                        print(f"\n{name}:")
                        print(f"  InlineAcc vs GPS acc: {corr:.3f} [{status}]")

                # LateralAcc vs GPS
                required = ["LateralAcc", "GPS Speed", "GPS Latitude", "GPS Longitude"]
                if all(ch in log.channels for ch in required):
                    gps_tc, gps_lat_acc = compute_gps_lateral_acceleration(log)
                    gps_lat_acc_smooth = smooth_signal(gps_lat_acc, window=7)
                    lat_acc, gps_lat_resampled = resample_to_common_timebase(
                        log, "LateralAcc", gps_tc, gps_lat_acc_smooth
                    )
                    if len(lat_acc) >= 10:
                        lat_acc_smooth = smooth_signal(lat_acc, window=5)
                        corr = compute_correlation(lat_acc_smooth, gps_lat_resampled)
                        status = "OK" if corr > 0.3 else "LOW"
                        print(f"  LateralAcc vs GPS lat: {corr:.3f} [{status}]")

            except Exception as e:
                print(f"\n{name}: Error - {e}")

        print("\n" + "=" * 60)


if __name__ == "__main__":
    unittest.main()
