"""The progress callback fires on ordinary-sized files.

The interval between two callbacks used to be 8 MB, so a file smaller than
that, which is most single-session logs, decoded without a single call. Both
backends now check every 256 KB, at the same positions.
"""

from pathlib import Path

import pytest

TEST_DATA_DIR = Path(__file__).parent / "test_data"
# 3.2 MB: under the old 8 MB threshold, so this file never produced a callback.
SMALL_XRK = TEST_DATA_DIR / "aim_official" / "test.xrk"
MEDIUM_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


def _load(backend: str):
    """Resolve a backend's aim_xrk at test time, not at collection time."""
    if backend == "rust":
        try:
            from libxrk._aim_xrk_rs import aim_xrk
        except ImportError:
            pytest.skip("Rust backend not available")
    else:
        from libxrk.aim_xrk import aim_xrk
    return aim_xrk


def _calls(aim_xrk, path: Path) -> list:
    calls: list = []
    aim_xrk(str(path), progress=lambda current, total: calls.append((current, total)))
    return calls


@pytest.mark.parametrize("backend", ["cython", "rust"])
def test_callback_fires_on_a_small_file(backend: str) -> None:
    calls = _calls(_load(backend), SMALL_XRK)
    size = SMALL_XRK.stat().st_size

    assert calls, f"no progress callback on a {size}-byte file"
    assert all(total == size for _, total in calls)
    assert all(0 < current <= size for current, _ in calls)
    positions = [current for current, _ in calls]
    assert positions == sorted(set(positions)), "positions must strictly increase"


@pytest.mark.parametrize("path", [SMALL_XRK, MEDIUM_XRK], ids=lambda p: p.name)
def test_both_backends_call_back_at_the_same_positions(path: Path) -> None:
    assert _calls(_load("cython"), path) == _calls(_load("rust"), path)
