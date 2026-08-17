"""Run unit tests in Pyodide environment."""

import os
import sys
import unittest


# Test classes exercised in Pyodide, in run order. Each is run in its own
# Pyodide instance by run_pyodide_tests.mjs: the wasm32 heap only ever grows,
# and parsing the large fixtures repeatedly fragments it into the 4GB address
# space limit if every class shares one interpreter.
TEST_CLASSES = ("Test86XRK", "TestSFJXRK", "TestChannelMerge")


def run_tests(only: str | None = None) -> int:
    """Run unit tests and return exit code.

    Args:
        only: Run just this test class. None runs all of them (uses more
            memory than Pyodide has for the large fixtures - see TEST_CLASSES).
    """
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

    by_name = {
        "Test86XRK": Test86XRK,
        "TestSFJXRK": TestSFJXRK,
        "TestChannelMerge": TestChannelMerge,
    }
    if only is not None:
        if only not in by_name:
            raise SystemExit(f"unknown test class {only!r}; expected one of {TEST_CLASSES}")
        suite.addTests(loader.loadTestsFromTestCase(by_name[only]))
    else:
        for name in TEST_CLASSES:
            suite.addTests(loader.loadTestsFromTestCase(by_name[name]))

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
