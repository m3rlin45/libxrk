"""Tests for aim_xrk bytes and file-like object input support."""

import io
import unittest
from pathlib import Path
from typing import ClassVar

import numpy as np

from libxrk import aim_xrk
from libxrk.base import LogFile


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "test_data"
XRK_86_FILE = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"


class TestBytesInput(unittest.TestCase):
    """Tests for loading XRK files from bytes and file-like objects."""

    file_bytes: ClassVar[bytes]
    reference_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the test file bytes once for all tests."""
        cls.file_bytes = XRK_86_FILE.read_bytes()
        # Load reference from file path for comparison
        cls.reference_log = aim_xrk(str(XRK_86_FILE), progress=None)

    def test_load_from_bytes(self):
        """Test loading XRK data from bytes."""
        log = aim_xrk(self.file_bytes, progress=None)

        self.assertIsNotNone(log, "aim_xrk returned None for bytes input")
        self.assertEqual(
            len(log.channels),
            len(self.reference_log.channels),
            "Channel count mismatch between bytes and file input",
        )
        self.assertEqual(
            len(log.laps),
            len(self.reference_log.laps),
            "Lap count mismatch between bytes and file input",
        )

    def test_load_from_bytearray(self):
        """Test loading XRK data from bytearray."""
        data = bytearray(self.file_bytes)
        log = aim_xrk(data, progress=None)

        self.assertIsNotNone(log, "aim_xrk returned None for bytearray input")
        self.assertEqual(
            len(log.channels),
            len(self.reference_log.channels),
            "Channel count mismatch between bytearray and file input",
        )

    def test_load_from_memoryview(self):
        """Test loading XRK data from memoryview."""
        data = memoryview(self.file_bytes)
        log = aim_xrk(data, progress=None)

        self.assertIsNotNone(log, "aim_xrk returned None for memoryview input")
        self.assertEqual(
            len(log.channels),
            len(self.reference_log.channels),
            "Channel count mismatch between memoryview and file input",
        )

    def test_load_from_bytesio(self):
        """Test loading XRK data from BytesIO."""
        bio = io.BytesIO(self.file_bytes)
        log = aim_xrk(bio, progress=None)

        self.assertIsNotNone(log, "aim_xrk returned None for BytesIO input")
        self.assertEqual(
            len(log.channels),
            len(self.reference_log.channels),
            "Channel count mismatch between BytesIO and file input",
        )

    def test_load_from_bytesio_not_at_start(self):
        """Test loading XRK data from BytesIO that isn't at position 0."""
        bio = io.BytesIO(self.file_bytes)
        bio.seek(100)  # Move to a non-zero position
        log = aim_xrk(bio, progress=None)

        self.assertIsNotNone(log, "aim_xrk returned None for BytesIO not at start")
        self.assertEqual(
            len(log.channels),
            len(self.reference_log.channels),
            "Channel count mismatch - BytesIO should seek to start",
        )

    def test_bytes_input_channel_names_match(self):
        """Test that channel names from bytes input match file input."""
        log = aim_xrk(self.file_bytes, progress=None)

        self.assertEqual(
            set(log.channels.keys()),
            set(self.reference_log.channels.keys()),
            "Channel names don't match between bytes and file input",
        )

    def test_bytes_input_metadata_matches(self):
        """Test that metadata from bytes input matches file input."""
        log = aim_xrk(self.file_bytes, progress=None)

        # Metadata should match (except possibly file_name)
        self.assertEqual(
            log.metadata,
            self.reference_log.metadata,
            "Metadata doesn't match between bytes and file input",
        )

    def test_bytes_input_laps_match(self):
        """Test that lap data from bytes input matches file input."""
        log = aim_xrk(self.file_bytes, progress=None)

        # Compare lap numbers
        ref_laps = self.reference_log.laps.column("num").to_pylist()
        bytes_laps = log.laps.column("num").to_pylist()
        self.assertEqual(
            bytes_laps, ref_laps, "Lap numbers don't match between bytes and file input"
        )

        # Compare lap times
        ref_start = self.reference_log.laps.column("start_time").to_pylist()
        bytes_start = log.laps.column("start_time").to_pylist()
        self.assertEqual(
            bytes_start, ref_start, "Lap start times don't match between bytes and file input"
        )

    def test_bytes_input_channel_data_matches(self):
        """Test that channel data from bytes input matches file input."""
        log = aim_xrk(self.file_bytes, progress=None)

        # Check a few channels in detail
        for channel_name in ["RPM", "GPS Speed", "LateralAcc"]:
            if channel_name in log.channels and channel_name in self.reference_log.channels:
                ref_table = self.reference_log.channels[channel_name]
                bytes_table = log.channels[channel_name]

                self.assertEqual(
                    len(bytes_table),
                    len(ref_table),
                    f"Row count mismatch for channel {channel_name}",
                )

                # Compare first and last values
                ref_col = ref_table.column(channel_name)
                bytes_col = bytes_table.column(channel_name)

                self.assertEqual(
                    bytes_col[0].as_py(),
                    ref_col[0].as_py(),
                    f"First value mismatch for channel {channel_name}",
                )
                self.assertEqual(
                    bytes_col[-1].as_py(),
                    ref_col[-1].as_py(),
                    f"Last value mismatch for channel {channel_name}",
                )

    def test_bytes_input_file_name_is_bytes_marker(self):
        """Test that file_name is '<bytes>' when loading from bytes."""
        log = aim_xrk(self.file_bytes, progress=None)
        self.assertEqual(
            log.file_name, "<bytes>", "file_name should be '<bytes>' when loading from bytes"
        )

    def test_bytesio_input_file_name_is_bytes_marker(self):
        """Test that file_name is '<bytes>' when loading from BytesIO."""
        bio = io.BytesIO(self.file_bytes)
        log = aim_xrk(bio, progress=None)
        self.assertEqual(
            log.file_name, "<bytes>", "file_name should be '<bytes>' when loading from BytesIO"
        )

    def test_file_path_file_name_is_path(self):
        """Test that file_name is the path when loading from file."""
        log = aim_xrk(str(XRK_86_FILE), progress=None)
        self.assertEqual(
            log.file_name,
            str(XRK_86_FILE),
            "file_name should be the file path when loading from file",
        )


class TestFileInput(unittest.TestCase):
    """Tests for traditional file path input (regression tests)."""

    def test_load_from_string_path(self):
        """Test loading from string file path."""
        log = aim_xrk(str(XRK_86_FILE), progress=None)
        self.assertIsNotNone(log)
        self.assertGreater(len(log.channels), 0)

    def test_load_from_path_object(self):
        """Test loading from pathlib.Path object."""
        # Path objects have __fspath__ so they work with open()
        log = aim_xrk(XRK_86_FILE, progress=None)
        self.assertIsNotNone(log)
        self.assertGreater(len(log.channels), 0)


def _rust_backend_available() -> bool:
    """Check if the Rust backend extension is importable."""
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestBytesInputCrossBackend(unittest.TestCase):
    """Cross-backend comparison for bytes/BytesIO input."""

    file_bytes: ClassVar[bytes]
    rust_log: ClassVar[LogFile]
    cython_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.file_bytes = XRK_86_FILE.read_bytes()
        cls.rust_log = rust_aim_xrk(cls.file_bytes)
        cls.cython_log = cython_aim_xrk(cls.file_bytes)

    def test_bytes_channel_names_match(self) -> None:
        """Channel names from bytes input should match between backends."""
        self.assertEqual(
            set(self.rust_log.channels.keys()),
            set(self.cython_log.channels.keys()),
        )

    def test_bytes_channel_types_match(self) -> None:
        """Arrow types from bytes input should match between backends."""
        for name in self.rust_log.channels:
            rust_type = self.rust_log.channels[name].schema.field(name).type
            cython_type = self.cython_log.channels[name].schema.field(name).type
            self.assertEqual(
                rust_type,
                cython_type,
                f"Channel {name!r}: rust type={rust_type} != cython type={cython_type}",
            )

    def test_bytes_non_gps_values_exact_match(self) -> None:
        """Non-GPS channels from bytes input should match exactly between backends."""
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

    def test_bytes_gps_values_close(self) -> None:
        """GPS channels from bytes input should be close between backends."""
        for name in self.rust_log.channels:
            if not (name.startswith("GPS ") or name.startswith("GPS_")):
                continue
            rust_vals = self.rust_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = self.cython_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            np.testing.assert_allclose(
                rust_vals, cython_vals, rtol=1e-5, atol=1e-10, err_msg=f"{name}"
            )

    def test_bytes_lap_data_match(self) -> None:
        """Lap data from bytes input should match between backends."""
        self.assertEqual(self.rust_log.laps.num_rows, self.cython_log.laps.num_rows)
        rust_starts = self.rust_log.laps.column("start_time").to_pylist()
        cython_starts = self.cython_log.laps.column("start_time").to_pylist()
        self.assertEqual(rust_starts, cython_starts)

    def test_bytes_metadata_match(self) -> None:
        """Key metadata from bytes input should match between backends."""
        for key in ("Logger ID", "Logger Model ID", "Venue", "GPS Receiver"):
            rust_val = self.rust_log.metadata.get(key)
            cython_val = self.cython_log.metadata.get(key)
            self.assertEqual(
                rust_val,
                cython_val,
                f"Metadata {key!r}: rust={rust_val!r} != cython={cython_val!r}",
            )

    def test_bytesio_matches_bytes(self) -> None:
        """BytesIO input should produce same result as raw bytes for Rust backend."""
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

        bio_log = rust_aim_xrk(io.BytesIO(self.file_bytes))
        self.assertEqual(
            set(bio_log.channels.keys()),
            set(self.rust_log.channels.keys()),
        )
        for name in bio_log.channels:
            bio_count = len(bio_log.channels[name])
            bytes_count = len(self.rust_log.channels[name])
            self.assertEqual(bio_count, bytes_count, f"Channel {name!r} row count mismatch")


if __name__ == "__main__":
    unittest.main()
