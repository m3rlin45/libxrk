"""Unit strings match what the official AIM DLL reports.

The unit map was ported from a hand-built dict; most of it is right, but
nothing pinned it to AIM's own answer. This file pins the one entry that was
measurably wrong, on both backends.

Ground truth: `tests/reference_dll`, `get_channel_units` over every channel of
every corpus file, matched by channel name against the CHS unit byte. Codes
the DLL never exposes are not asserted here — there is nothing to compare to.
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

# Channel -> unit the AIM DLL reports, on a file that carries it.
# StartRec and Lateral Grip both declare unit code 11.
ATTENDU = {
    "StartRec": "#",
    "Lateral Grip": "#",
}
FICHIER = TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"


@pytest.mark.parametrize("decode", BACKENDS)
def test_unit_code_11_is_hash(decode):
    """Code 11 means "number", and AIM writes that as "#", not "".

    Measured over the corpus: 36 channel observations, every one of them "#".
    """
    log = decode(str(FICHIER))
    for nom, unite in ATTENDU.items():
        table = log.channels[nom]
        vu = table.schema.field(nom).metadata[b"units"].decode()
        assert vu == unite, f"{nom}: got {vu!r}, AIM DLL reports {unite!r}"


@pytest.mark.skipif(not _RUST_AVAILABLE, reason="Rust backend not available")
def test_both_backends_agree_on_units():
    """A unit is a fact about the file, not about the backend."""
    a, b = decode_cython(str(FICHIER)), decode_rust(str(FICHIER))
    for nom in a.channels:
        ua = a.channels[nom].schema.field(nom).metadata[b"units"]
        ub = b.channels[nom].schema.field(nom).metadata[b"units"]
        assert ua == ub, f"{nom}: cython {ua!r} vs rust {ub!r}"
