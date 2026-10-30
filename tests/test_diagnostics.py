"""Parser diagnostics are returned, never printed.

The parser is fault-tolerant: it skips what it cannot read and carries on.
It used to report that on stderr, which is unusable to a caller — a library
embedded in an app, a notebook, or a WebAssembly module has nowhere to put
those lines, and cannot act on text it never sees.

These tests pin both halves of the contract: nothing reaches stdout or stderr,
and everything the parser noticed comes back on the LogFile.
"""

from pathlib import Path

import pytest

from libxrk.aim_xrk import aim_xrk as decode_cython

TEST_DATA_DIR = Path(__file__).parent / "test_data"

try:
    from libxrk._aim_xrk_rs import aim_xrk as decode_rust

    _RUST_AVAILABLE = True
except ImportError:
    decode_rust = None
    _RUST_AVAILABLE = False

BACKENDS = [pytest.param(decode_cython, id="cython")] + (
    [pytest.param(decode_rust, id="rust")] if _RUST_AVAILABLE else []
)

# Files chosen for what they exercise: one the parser reads without skipping a
# single byte, and several where it has to resynchronise.
CLEAN = TEST_DATA_DIR / "issue49/badGPSdata.xrk"
NOISY = [
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk",
    TEST_DATA_DIR / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz",
    TEST_DATA_DIR / "issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz",
]


@pytest.mark.parametrize("decode", BACKENDS)
@pytest.mark.parametrize("path", NOISY, ids=lambda p: p.name)
def test_nothing_is_printed(decode, path, capfd):
    """Not one byte on stdout or stderr, on files that do have something to say."""
    log = decode(str(path))
    captured = capfd.readouterr()

    assert log.diagnostics, f"{path.name} should produce diagnostics to begin with"
    assert captured.out == "", f"wrote {len(captured.out)} bytes to stdout"
    assert captured.err == "", f"wrote {len(captured.err)} bytes to stderr"


@pytest.mark.parametrize("decode", BACKENDS)
@pytest.mark.parametrize("path", NOISY, ids=lambda p: p.name)
def test_what_was_skipped_comes_back(decode, path):
    log = decode(str(path))

    assert log.bad_bytes > 0
    assert len(log.diagnostics) >= 1
    assert any("unrecognised byte" in d for d in log.diagnostics)


@pytest.mark.parametrize("decode", BACKENDS)
def test_a_clean_file_says_nothing(decode):
    log = decode(str(CLEAN))

    assert log.diagnostics == []
    assert log.bad_bytes == 0


@pytest.mark.skipif(not _RUST_AVAILABLE, reason="Rust backend not available")
@pytest.mark.parametrize("path", NOISY + [CLEAN], ids=lambda p: p.name)
def test_both_backends_skip_the_same_bytes(path):
    """The byte total is a fact about the file, not about the backend."""
    assert decode_cython(str(path)).bad_bytes == decode_rust(str(path)).bad_bytes


@pytest.mark.parametrize("decode", BACKENDS)
def test_the_collection_is_capped(decode):
    """A file made of noise must not make the parser allocate without bound.

    Byte totals stay exact past the cap; only the individual entries stop
    being kept, and the count of what was dropped is reported.
    """
    log = decode(str(NOISY[0]))
    # 256 entries kept, plus at most one "… and N more" line.
    assert len(log.diagnostics) <= 257
    if len(log.diagnostics) == 257:
        assert log.diagnostics[-1].startswith("… and ")
