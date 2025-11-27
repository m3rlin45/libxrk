"""Test bytes input support for aim_xrk in Pyodide/JupyterLite environments.

This module tests that aim_xrk can load XRK files from bytes, BytesIO, and file paths.
This is critical for JupyterLite where files are often loaded via fetch/pyfetch
and don't have file descriptors that support mmap.

The file bytes are passed to run_bytes_input_tests() by the JavaScript test runner.
"""

import io
import os
import sys
import unittest
from typing import Any

# Module-level storage for file bytes, set by run_bytes_input_tests()
_file_bytes: bytes = b""


class TestBytesInputPyodide(unittest.TestCase):
    """Test aim_xrk with bytes input in Pyodide environment."""

    file_bytes: bytes

    @classmethod
    def setUpClass(cls) -> None:
        """Load file bytes from module-level storage."""
        cls.file_bytes = _file_bytes
        print(f"Received {len(cls.file_bytes)} bytes")

    def test_bytesio_has_no_fileno(self) -> None:
        """BytesIO cannot be used with mmap (no fileno)."""
        bio = io.BytesIO(self.file_bytes)
        with self.assertRaises(io.UnsupportedOperation):
            bio.fileno()

    def test_aim_xrk_with_bytesio(self) -> None:
        """aim_xrk can load from BytesIO."""
        from libxrk import aim_xrk

        bio = io.BytesIO(self.file_bytes)
        log = aim_xrk(bio, progress=None)
        self.assertGreater(len(log.channels), 0)

    def test_aim_xrk_with_bytes(self) -> None:
        """aim_xrk can load from bytes directly."""
        from libxrk import aim_xrk

        log = aim_xrk(self.file_bytes, progress=None)
        self.assertGreater(len(log.channels), 0)

    def test_aim_xrk_with_file_path(self) -> None:
        """aim_xrk can load from file path (original usage)."""
        from libxrk import aim_xrk

        temp_path = "/tmp/test_file.xrk"
        try:
            with open(temp_path, "wb") as f:
                f.write(self.file_bytes)
            log = aim_xrk(temp_path, progress=None)
            self.assertGreater(len(log.channels), 0)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_bytes_and_file_produce_same_channels(self) -> None:
        """Loading from bytes produces same channels as file path."""
        from libxrk import aim_xrk

        # Load from bytes
        log_bytes = aim_xrk(self.file_bytes, progress=None)

        # Load from file
        temp_path = "/tmp/test_file.xrk"
        try:
            with open(temp_path, "wb") as f:
                f.write(self.file_bytes)
            log_file = aim_xrk(temp_path, progress=None)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        # Compare
        self.assertEqual(len(log_bytes.channels), len(log_file.channels))
        self.assertEqual(sorted(log_bytes.channels.keys()), sorted(log_file.channels.keys()))


def run_bytes_input_tests(js_file_bytes: Any) -> int:
    """Run bytes input tests and return exit code.

    Args:
        js_file_bytes: JavaScript Uint8Array containing the XRK file bytes.
    """
    global _file_bytes
    _file_bytes = bytes(js_file_bytes.to_py())

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestBytesInputPyodide)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print(f"\n{'='*60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print(f"{'='*60}")

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    # This script is meant to be run from Pyodide with js_file_bytes passed in
    print("This module should be run via run_bytes_input_tests(js_file_bytes)")
    sys.exit(1)
