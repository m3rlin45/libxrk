"""The 65533 ms GPS timing fix on real files, both backends.

The detection and correction themselves live in the shared GPS pipeline
(``libxrk._gps_rs``, crate ``crates/libxrk-gps``) and are unit-tested on
synthetic records in ``tests/test_gps_processing.py``. These tests check
the fix is applied when a file is loaded: the SFJ 0101 fixture (direct
detection), the Suzuka fixture (GNFI-based detection) and backend agreement.
"""

import gc
import unittest
from pathlib import Path
from typing import ClassVar, Optional

import numpy as np

from libxrk import GPS_CHANNEL_NAMES, ChannelMetadata, LogFile, aim_xrk
from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk


class TestGpsTimingGapFixIntegration(unittest.TestCase):
    """Integration tests using real XRK files with the GPS timing bug."""

    TEST_DATA_DIR: "Path"
    SFJ_0101_XRK: "Path"
    SFJ_0101_XRZ: "Path"
    log_xrk: "Any"  # type: ignore[name-defined]
    log_xrz: "Any"  # type: ignore[name-defined]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the test file once for all tests."""
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


class TestSuzukaGnfiIntegration(unittest.TestCase):
    """Integration tests for GNFI-based detection on Suzuka file."""

    TEST_DATA_DIR: "Path"
    SUZUKA_XRK: "Path"
    log: "Any"  # type: ignore[name-defined]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the Suzuka test file."""
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


class TestGpsTimingCrossBackend(unittest.TestCase):
    """Cross-backend comparison for GPS timing fix on real files."""

    rust_0101: ClassVar[LogFile]
    cython_0101: ClassVar[LogFile]
    rust_suzuka: ClassVar[Optional[LogFile]]
    cython_suzuka: ClassVar[Optional[LogFile]]

    @classmethod
    def setUpClass(cls) -> None:
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
