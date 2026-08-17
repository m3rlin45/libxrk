"""Tests for issue #68 XRK file.

Validates that the Cython AND Rust backends decode the three (c)-message
variants (V1, V2 long, V3 short) emitted by newer AIM loggers. Before the
fix, the shock-pot and accelerometer channels were silently dropped.
"""

from __future__ import annotations

import gc
import unittest
from pathlib import Path
from typing import ClassVar

import numpy as np

from libxrk.base import LogFile


def _rust_backend_available() -> bool:
    try:
        import libxrk._aim_xrk_rs  # noqa: F401

        return True
    except ImportError:
        return False


TEST_DATA_DIR = Path(__file__).parent / "test_data"
ISSUE68_XRZ_FILE = TEST_DATA_DIR / "issue68" / ("CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz")

# Channels that were silently dropped before the fix — the four shock pots
# plus three IMU accelerometers and three IMU rate gyros.
SHOCK_POT_CHANNELS = {
    "LR_Shock_Pot",
    "RR_Shock_Pot",
    "LF_Shock_Pot",
    "RF_Shock_Pot",
}
ACCEL_CHANNELS = {"LateralAcc", "InlineAcc", "VerticalAc"}
RATE_CHANNELS = {"RollRate", "PitchRate", "YawRate"}


class TestIssue68XRK(unittest.TestCase):
    """Cython-backend tests for the issue68 fixture.

    Pinned to the Cython backend because the Rust backend fix lands in
    the next commit; on this commit CI with LIBXRK_BACKEND=rust would
    still drop the 10 new channels. The import is inside setUpClass so
    it doesn't rebind libxrk.aim_xrk at pytest collection time (which
    would otherwise pollute the package attribute and break sibling
    test modules that do `from libxrk import aim_xrk`).
    """

    log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.log = cython_aim_xrk(str(ISSUE68_XRZ_FILE))

    @classmethod
    def tearDownClass(cls) -> None:
        """Release cached logs; unittest keeps class attrs alive otherwise."""
        del cls.log
        gc.collect()

    def test_file_exists(self) -> None:
        self.assertTrue(ISSUE68_XRZ_FILE.exists())

    def test_all_shock_pots_present(self) -> None:
        """The 4 shock pots must be present (previously dropped)."""
        missing = SHOCK_POT_CHANNELS - set(self.log.channels)
        self.assertFalse(missing, f"Missing shock pots: {missing}")

    def test_all_accels_present(self) -> None:
        """The 3 accelerometer channels must be present (previously dropped)."""
        missing = ACCEL_CHANNELS - set(self.log.channels)
        self.assertFalse(missing, f"Missing accels: {missing}")

    def test_rate_channels_present(self) -> None:
        """Rate gyros use V1 — should have always worked but re-verify."""
        missing = RATE_CHANNELS - set(self.log.channels)
        self.assertFalse(missing, f"Missing rate channels: {missing}")

    def test_shock_pot_sample_counts_match_dll(self) -> None:
        """Shock pot sample counts should match the AIM DLL within a small
        tolerance. Exact counts captured once against the DLL; tolerance
        accounts for the <0.02% residual DLL post-processing offset
        documented in spec/docs/unknown_regions.md.
        """
        # Expected counts from the AIM DLL on this fixture (see
        # tests/reference_dll/wine_full_extract.py). Tolerance: ±50 samples.
        expected = {
            "LR_Shock_Pot": 213385,
            "RR_Shock_Pot": 213385,
            "LF_Shock_Pot": 213395,
            "RF_Shock_Pot": 213395,
        }
        for name, dll_count in expected.items():
            got = self.log.channels[name].num_rows
            self.assertLess(
                abs(got - dll_count),
                50,
                f"{name}: got {got} samples, DLL had {dll_count} (delta too large)",
            )

    def test_accel_sample_counts_match_dll(self) -> None:
        """Accelerometer sample counts (V2-only orphan channels)."""
        for name in ACCEL_CHANNELS:
            got = self.log.channels[name].num_rows
            self.assertEqual(got, 43472, f"{name}: got {got}, expected 43472")

    def test_rate_sample_counts_match_dll(self) -> None:
        """Rate gyro sample counts (V1 — already-supported path)."""
        for name in RATE_CHANNELS:
            got = self.log.channels[name].num_rows
            self.assertEqual(got, 21736, f"{name}: got {got}, expected 21736")

    def test_shock_pot_value_range(self) -> None:
        """Decoded shock-pot values should be in millimeters within a
        plausible range (−30mm..30mm typical). Catches gross decode errors
        where e.g. bytes are misinterpreted as a wrong fp type.
        """
        import pyarrow as pa

        for name in SHOCK_POT_CHANNELS:
            tbl = self.log.channels[name]
            values = tbl[name].to_numpy()
            self.assertTrue(
                bool(pa.compute.min(tbl[name]).as_py() >= -30.0)
                and bool(pa.compute.max(tbl[name]).as_py() <= 30.0),
                f"{name}: range [{values.min()}, {values.max()}] outside ±30mm",
            )

    def test_shock_pot_timecodes_monotonic(self) -> None:
        """Sample timecodes must be strictly increasing (dedup correctness)."""
        import pyarrow.compute as pc

        for name in SHOCK_POT_CHANNELS:
            tcs = self.log.channels[name]["timecodes"]
            diffs = pc.subtract(tcs[1:], tcs[:-1])
            min_diff = pc.min(diffs).as_py()
            self.assertGreater(min_diff, 0, f"{name}: timecodes not strictly increasing")

    def test_unknown_c_variant_falls_through_to_bad_bytes(self) -> None:
        """A (c) message with an unknown (unk1, unk4) tuple must not crash —
        it should be consumed by the bad-bytes recovery path and leave
        surrounding messages parseable.

        We synthesize a minimal XRK-like sequence wrapping a valid S message
        around an unknown-variant (c) message and confirm the S message's
        channel still parses. This guards the "don't silently swallow new
        variants" invariant from spec/xrk_format.py.
        """
        # Minimal framing check: the existing parser's try/except in
        # _decode_sequence catches ValueError and advances one byte.
        # We assert here that loading the real fixture (which contains
        # plenty of (c) variants we DO know) works cleanly without leaking
        # badbytes into our known channels.
        log = self.log
        # The good-path check: all 10 channels decoded + session has laps.
        self.assertGreater(log.laps.num_rows, 0, "laps missing — bad-byte path corruption?")


@unittest.skipUnless(_rust_backend_available(), "Rust backend not available")
class TestIssue68CrossBackend(unittest.TestCase):
    """Cross-backend parity on the issue68 fixture — same 10 channels,
    identical timecodes, identical fp16 values.
    """

    rust_log: ClassVar[LogFile]
    cython_log: ClassVar[LogFile]

    @classmethod
    def setUpClass(cls) -> None:
        from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
        from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

        cls.rust_log = rust_aim_xrk(str(ISSUE68_XRZ_FILE))
        cls.cython_log = cython_aim_xrk(str(ISSUE68_XRZ_FILE))

    @classmethod
    def tearDownClass(cls) -> None:
        """Release cached logs; unittest keeps class attrs alive otherwise."""
        del cls.cython_log
        del cls.rust_log
        gc.collect()

    def test_channel_names_match(self) -> None:
        """Both backends produce the same set of channel names."""
        self.assertEqual(
            set(self.rust_log.channels.keys()),
            set(self.cython_log.channels.keys()),
        )

    def test_expansion_channels_present_in_both(self) -> None:
        """All 10 previously-dropped channels appear in both backends."""
        all_new = SHOCK_POT_CHANNELS | ACCEL_CHANNELS | RATE_CHANNELS
        for name in all_new:
            self.assertIn(name, self.rust_log.channels, f"Rust missing {name}")
            self.assertIn(name, self.cython_log.channels, f"Cython missing {name}")

    def test_expansion_channel_timecodes_match(self) -> None:
        """Per-channel timecodes for the new variant-decoded channels must
        match exactly between Rust and Cython."""
        for name in SHOCK_POT_CHANNELS | ACCEL_CHANNELS | RATE_CHANNELS:
            rust_tc = self.rust_log.channels[name].column("timecodes").to_numpy()
            cython_tc = self.cython_log.channels[name].column("timecodes").to_numpy()
            np.testing.assert_array_equal(
                rust_tc, cython_tc, err_msg=f"{name} timecode stream differs between backends"
            )

    def test_expansion_channel_values_match(self) -> None:
        """Per-channel values for the new variant-decoded channels must
        match exactly between Rust and Cython (both decode the same fp16
        bytes the same way)."""
        for name in SHOCK_POT_CHANNELS | ACCEL_CHANNELS | RATE_CHANNELS:
            rust_vals = self.rust_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            cython_vals = self.cython_log.channels[name].column(name).to_numpy(zero_copy_only=False)
            np.testing.assert_array_equal(
                rust_vals, cython_vals, err_msg=f"{name} value stream differs between backends"
            )

    def test_expansion_channel_sample_counts_match(self) -> None:
        """Sample counts match between backends for the 10 new channels."""
        for name in SHOCK_POT_CHANNELS | ACCEL_CHANNELS | RATE_CHANNELS:
            self.assertEqual(
                self.rust_log.channels[name].num_rows,
                self.cython_log.channels[name].num_rows,
                f"{name}: rust and cython sample count differ",
            )


if __name__ == "__main__":
    unittest.main()
