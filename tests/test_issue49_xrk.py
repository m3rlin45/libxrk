"""Tests for issue #49 XRK file (AIM logger model 768 + Mectronik MKE7 ECU).

This file must ONLY be tested with the Rust backend — the Cython backend
must never open this file (safety constraint from issue investigation).
"""

import os
import unittest
from pathlib import Path

TEST_DATA_DIR = Path(__file__).parent / "test_data"
ISSUE49_XRK_FILE = TEST_DATA_DIR / "issue49" / "badGPSdata.xrk"


def _rust_backend_available():
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestIssue49XRK(unittest.TestCase):
    """Tests for the issue49 badGPSdata.xrk file (Rust backend only)."""

    @classmethod
    def setUpClass(cls):
        # Force Rust backend for this entire test class
        os.environ["LIBXRK_BACKEND"] = "rust"
        # Re-import to pick up Rust backend
        from libxrk._aim_xrk_rs import aim_xrk
        cls._aim_xrk = staticmethod(aim_xrk)
        cls.log = aim_xrk(str(ISSUE49_XRK_FILE))

    def test_file_exists(self):
        """Verify the test data file exists."""
        self.assertTrue(ISSUE49_XRK_FILE.exists())

    def test_metadata_vet_is_string(self):
        """VET should be '...' (string), not an integer."""
        vet = self.log.metadata.get("Vehicle Electronics Type")
        self.assertIsNotNone(vet, "VET metadata missing")
        self.assertIsInstance(vet, str, f"VET should be str, got {type(vet)}")
        self.assertEqual(vet, "...")

    def test_metadata_race_mode(self):
        """Race Mode should be '...' (placeholder string)."""
        racm = self.log.metadata.get("Race Mode")
        self.assertIsNotNone(racm, "Race Mode metadata missing")
        self.assertEqual(racm, "...")

    def test_gps_channels_exist(self):
        """GPS channels should exist with expected sample count."""
        gps_names = ["GPS Speed", "GPS Latitude", "GPS Longitude", "GPS Altitude"]
        for name in gps_names:
            self.assertIn(name, self.log.channels, f"Missing GPS channel: {name}")
            count = len(self.log.channels[name])
            self.assertEqual(count, 5995, f"{name}: expected 5995 samples, got {count}")

    def test_channel_count(self):
        """Should have a reasonable number of channels."""
        self.assertGreater(len(self.log.channels), 10)

    def test_laps_table_exists(self):
        """Laps table should exist and have rows."""
        self.assertIsNotNone(self.log.laps)
        self.assertGreater(self.log.laps.num_rows, 0)

    def test_logger_model_id(self):
        """Logger model ID should be 768 (currently unmapped)."""
        model_id = self.log.metadata.get("Logger Model ID")
        self.assertIsNotNone(model_id)
        self.assertEqual(model_id, 768)


if __name__ == "__main__":
    unittest.main()
