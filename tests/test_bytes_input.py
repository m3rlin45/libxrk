"""Tests for aim_xrk bytes and file-like object input support."""

import io
import unittest
from pathlib import Path
from typing import ClassVar

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


if __name__ == "__main__":
    unittest.main()
