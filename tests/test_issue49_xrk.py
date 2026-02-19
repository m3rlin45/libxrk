"""Tests for issue #49 XRK file (AIM logger model 768 + Mectronik MKE7 ECU).

Tests both Rust and Cython backends, plus cross-backend comparison.
"""

import os
import unittest
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from libxrk.base import LogFile

TEST_DATA_DIR = Path(__file__).parent / "test_data"
ISSUE49_XRK_FILE = TEST_DATA_DIR / "issue49" / "badGPSdata.xrk"


def _rust_backend_available() -> bool:
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestIssue49XRK(unittest.TestCase):
    """Tests for the issue49 badGPSdata.xrk file (Rust backend)."""

    log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        # Force Rust backend for this entire test class
        os.environ["LIBXRK_BACKEND"] = "rust"
        # Import Rust backend directly
        from libxrk._aim_xrk_rs import aim_xrk

        cls.log = aim_xrk(str(ISSUE49_XRK_FILE))

    def test_file_exists(self) -> None:
        """Verify the test data file exists."""
        self.assertTrue(ISSUE49_XRK_FILE.exists())

    def test_metadata_vet_is_string(self) -> None:
        """VET should be '...' (string), not an integer."""
        vet = self.log.metadata.get("Vehicle Electronics Type")
        self.assertIsNotNone(vet, "VET metadata missing")
        self.assertIsInstance(vet, str, f"VET should be str, got {type(vet)}")
        self.assertEqual(vet, "...")

    def test_metadata_race_mode(self) -> None:
        """Race Mode should be '...' (placeholder string)."""
        racm = self.log.metadata.get("Race Mode")
        self.assertIsNotNone(racm, "Race Mode metadata missing")
        self.assertEqual(racm, "...")

    def test_gps_channels_exist(self) -> None:
        """GPS channels should exist with expected sample count."""
        gps_names = ["GPS Speed", "GPS Latitude", "GPS Longitude", "GPS Altitude"]
        for name in gps_names:
            self.assertIn(name, self.log.channels, f"Missing GPS channel: {name}")
            count = len(self.log.channels[name])
            self.assertEqual(count, 5995, f"{name}: expected 5995 samples, got {count}")

    def test_channel_count(self) -> None:
        """Should have a reasonable number of channels."""
        self.assertGreater(len(self.log.channels), 10)

    def test_laps_table_exists(self) -> None:
        """Laps table should exist and have rows."""
        self.assertIsNotNone(self.log.laps)
        self.assertGreater(self.log.laps.num_rows, 0)

    def test_logger_model_id(self) -> None:
        """Logger model ID should be 768 (currently unmapped)."""
        model_id = self.log.metadata.get("Logger Model ID")
        self.assertIsNotNone(model_id)
        self.assertEqual(model_id, 768)


class TestIssue49CythonXRK(unittest.TestCase):
    """Tests for the issue49 badGPSdata.xrk file (Cython backend)."""

    log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk.aim_xrk import aim_xrk

        cls.log = aim_xrk(str(ISSUE49_XRK_FILE))

    def test_metadata_vet_is_string(self) -> None:
        """VET should be '...' (string), not an integer."""
        vet = self.log.metadata.get("Vehicle Electronics Type")
        self.assertIsNotNone(vet, "VET metadata missing")
        self.assertIsInstance(vet, str, f"VET should be str, got {type(vet)}")
        self.assertEqual(vet, "...")

    def test_metadata_race_mode(self) -> None:
        """Race Mode should be '...' (placeholder string)."""
        racm = self.log.metadata.get("Race Mode")
        self.assertIsNotNone(racm, "Race Mode metadata missing")
        self.assertEqual(racm, "...")

    def test_gps_channels_exist(self) -> None:
        """GPS channels should exist with expected sample count."""
        gps_names = ["GPS Speed", "GPS Latitude", "GPS Longitude", "GPS Altitude"]
        for name in gps_names:
            self.assertIn(name, self.log.channels, f"Missing GPS channel: {name}")
            count = len(self.log.channels[name])
            self.assertEqual(count, 5995, f"{name}: expected 5995 samples, got {count}")

    def test_channel_count(self) -> None:
        """Should have a reasonable number of channels."""
        self.assertGreater(len(self.log.channels), 10)

    def test_laps_table_exists(self) -> None:
        """Laps table should exist and have rows."""
        self.assertIsNotNone(self.log.laps)
        self.assertGreater(self.log.laps.num_rows, 0)

    def test_logger_model_id(self) -> None:
        """Logger model ID should be 768 (currently unmapped)."""
        model_id = self.log.metadata.get("Logger Model ID")
        self.assertIsNotNone(model_id)
        self.assertEqual(model_id, 768)


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestIssue49CrossBackend(unittest.TestCase):
    """Cross-backend comparison for the issue49 badGPSdata.xrk file."""

    rust_log: ClassVar[LogFile]
    cython_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.rust_log = rust_aim_xrk(str(ISSUE49_XRK_FILE))
        cls.cython_log = cython_aim_xrk(str(ISSUE49_XRK_FILE))

    def test_metadata_match(self) -> None:
        """Key metadata should match between backends."""
        for key in ("Vehicle Electronics Type", "Race Mode", "Logger Model ID", "Logger ID"):
            rust_val = self.rust_log.metadata.get(key)
            cython_val = self.cython_log.metadata.get(key)
            self.assertEqual(
                rust_val,
                cython_val,
                f"Metadata {key!r}: rust={rust_val!r} != cython={cython_val!r}",
            )

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

    def test_non_gps_channel_values_exact_match(self) -> None:
        """Non-GPS CHS channels should produce identical timecodes and values."""
        gps_prefixes = ("GPS ", "GPS_")
        for name in self.rust_log.channels:
            if any(name.startswith(p) for p in gps_prefixes):
                continue
            rust_tc = self.rust_log.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_log.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(rust_tc, cython_tc, err_msg=f"{name} timecodes")
            rust_vals = self.rust_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = self.cython_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            np.testing.assert_array_equal(rust_vals, cython_vals, err_msg=f"{name} values")

    def test_gps_channel_values_close(self) -> None:
        """GPS channel values should be close between backends (tolerance for float math)."""
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
        self.assertEqual(
            self.rust_log.laps.num_rows,
            self.cython_log.laps.num_rows,
        )


if __name__ == "__main__":
    unittest.main()
