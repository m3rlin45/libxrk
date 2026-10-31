"""Regression test for issue #111: repeated parses must not grow the process.

The Cython backend used to leak roughly the decoded size of the file on every
parse. Its per-message sample accumulators were a C array of C++ vectors
captured by a closure, and Cython never destroys a C array of C++ objects held
in a closure scope. That memory comes from C++ ``new``, so tracemalloc and the
Arrow memory pool never see it; RSS is the only reliable signal.
"""

import gc
import os
import sys
from pathlib import Path

import pytest

from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

TEST_FILE = Path(__file__).parent / "test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"

WARMUP_PARSES = 2
MEASURED_PARSES = 10
# The leak was ~8 MB per parse of this 8.2 MB file (~80 MB over the measured
# parses); a fixed build stays within allocator noise of a few MB.
MAX_GROWTH_MB = 30


def _rss_mb() -> float:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="RSS measured via /proc/self/statm"
)
@pytest.mark.parametrize("aim_xrk", [cython_aim_xrk, rust_aim_xrk], ids=["cython", "rust"])
def test_repeated_parse_does_not_leak(aim_xrk):
    for _ in range(WARMUP_PARSES):
        log = aim_xrk(TEST_FILE)
        del log
        gc.collect()
    start = _rss_mb()

    for _ in range(MEASURED_PARSES):
        log = aim_xrk(TEST_FILE)
        del log
        gc.collect()
    growth = _rss_mb() - start

    assert growth < MAX_GROWTH_MB, (
        f"RSS grew {growth:.0f} MB over {MEASURED_PARSES} parses "
        f"(limit {MAX_GROWTH_MB} MB) - decoder buffers are leaking"
    )
