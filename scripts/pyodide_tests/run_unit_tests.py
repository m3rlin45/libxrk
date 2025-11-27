"""Run unit tests in Pyodide environment."""

import os
import sys
import unittest


def run_tests() -> int:
    """Run all unit tests and return exit code."""
    # Change to tests directory so relative paths work
    os.chdir("/tests")
    sys.path.insert(0, "/tests")

    # Import the existing test modules (must be inside function for Pyodide)
    # These modules are only available at runtime in the Pyodide filesystem
    from test_86_xrk import Test86XRK  # type: ignore[import-not-found]
    from test_get_channels_as_table import TestChannelMerge  # type: ignore[import-not-found]
    from test_sfj_xrk import TestSFJXRK  # type: ignore[import-not-found]

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(Test86XRK))
    suite.addTests(loader.loadTestsFromTestCase(TestSFJXRK))
    suite.addTests(loader.loadTestsFromTestCase(TestChannelMerge))

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
    sys.exit(run_tests())
