"""Exhaustive Cython vs Rust backend equivalence tests.

Every test fixture is loaded with both backends and compared at full
strictness: channel name sets, Arrow types, all 11 field-metadata keys,
bit-exact timecodes and values, the laps table, and the complete metadata
dict.  This is deliberately stronger than the per-file CrossBackend classes
(which check a subset of metadata keys).

This is a test of the two *parsers*. GPS processing is not part of it: the
GPS channels, the timing fix and the GPS steps of the lap table come from
one shared implementation (``libxrk._gps_rs``, tested on its own in
``tests/test_gps_processing.py``) on both backends. The GPS channels are
still compared, bit-exactly like everything else, because they are made
from material the parsers collect -- the raw GPS and GNFI records, the time
offset, the TRK marker -- so a difference there means the parsers disagree
on that material.
"""

from pathlib import Path

import numpy as np
import pytest

from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

TEST_DATA_DIR = Path(__file__).parent / "test_data"


FIXTURES = [
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrz",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk",
    TEST_DATA_DIR / "86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk",
    TEST_DATA_DIR / "86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrz",
    TEST_DATA_DIR / "aim_official/test.xrk",
    TEST_DATA_DIR / "issue49/badGPSdata.xrk",
    TEST_DATA_DIR / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz",
    TEST_DATA_DIR / "issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz",
]


def _load_both(path: Path):
    return cython_aim_xrk(str(path)), rust_aim_xrk(str(path))


def _field_metadata(table, name: str) -> dict:
    md = table.schema.field(name).metadata or {}
    return {k.decode(): v.decode() for k, v in md.items()}


def _compare_channel(name: str, cy_t, rs_t, errors: list) -> None:
    cy_f = cy_t.schema.field(name)
    rs_f = rs_t.schema.field(name)
    if cy_f.type != rs_f.type:
        errors.append(f"{name}: arrow type cython={cy_f.type} rust={rs_f.type}")
        return
    cy_md = _field_metadata(cy_t, name)
    rs_md = _field_metadata(rs_t, name)
    for key in sorted(set(cy_md) | set(rs_md)):
        if cy_md.get(key) != rs_md.get(key):
            errors.append(
                f"{name}: metadata {key} cython={cy_md.get(key)!r} rust={rs_md.get(key)!r}"
            )

    cy_tc = cy_t.column("timecodes").to_numpy()
    rs_tc = rs_t.column("timecodes").to_numpy()
    if len(cy_tc) != len(rs_tc):
        errors.append(f"{name}: sample count cython={len(cy_tc)} rust={len(rs_tc)}")
        return
    if not np.array_equal(cy_tc, rs_tc):
        i = int(np.argmax(cy_tc != rs_tc))
        errors.append(
            f"{name}: timecodes differ (first at {i}: cython={cy_tc[i]} != rust={rs_tc[i]})"
        )

    cy_v = cy_t.column(name).to_numpy(zero_copy_only=False)
    rs_v = rs_t.column(name).to_numpy(zero_copy_only=False)
    cy_f64 = cy_v.astype(np.float64)
    rs_f64 = rs_v.astype(np.float64)
    equal = (cy_v == rs_v) | (np.isnan(cy_f64) & np.isnan(rs_f64))
    if not bool(np.all(equal)):
        i = int(np.argmax(~equal))
        errors.append(
            f"{name}: values differ bit-exactly at {i}: "
            f"cython={cy_v[i]!r} rust={rs_v[i]!r} "
            f"({int(np.sum(~equal))}/{len(cy_v)} samples)"
        )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_backends_equivalent(fixture: Path) -> None:
    """Cython and Rust must agree on everything, bit for bit."""
    cy, rs = _load_both(fixture)
    errors: list = []

    cy_names = set(cy.channels)
    rs_names = set(rs.channels)
    if cy_names != rs_names:
        errors.append(
            f"channel sets differ: only-cython={sorted(cy_names - rs_names)} "
            f"only-rust={sorted(rs_names - cy_names)}"
        )

    for name in sorted(cy_names & rs_names):
        _compare_channel(name, cy.channels[name], rs.channels[name], errors)

    # Laps: exact
    cy_laps = cy.laps.to_pydict()
    rs_laps = rs.laps.to_pydict()
    if cy_laps != rs_laps:
        errors.append(f"laps differ: cython={cy_laps} rust={rs_laps}")

    # Full metadata dict: exact
    if cy.metadata != rs.metadata:
        keys = sorted(set(cy.metadata) | set(rs.metadata))
        for k in keys:
            if cy.metadata.get(k) != rs.metadata.get(k):
                errors.append(
                    f"metadata[{k!r}]: cython={cy.metadata.get(k)!r} "
                    f"rust={rs.metadata.get(k)!r}"
                )

    assert not errors, f"{fixture.name}: backend divergence:\n" + "\n".join(errors)


@pytest.mark.parametrize(
    "path",
    [
        TEST_DATA_DIR / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz",
        TEST_DATA_DIR / "issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz",
    ],
    ids=["issue68", "issue84"],
)
def test_duplicate_name_channels_all_exposed(path: Path) -> None:
    """Duplicated CHS long_names are distinct channels (own source_channel_id
    and data stream); every one must be exposed, with later occurrences
    renamed "<name> dup <k>" per the AIM DLL convention (the DLL exposes
    "Right_Btn_4_led dup 2" on the issue84 fixture), identically in both
    backends."""
    from libxrk.base import ChannelMetadata

    # Both fixtures define these channels 3x/3x/3x/2x with ascending
    # source_channel_ids; index order fixes the dup numbering.
    expected = {
        "RotaryLeft_led": 1003,
        "RotaryLeft_led dup 2": 1004,
        "RotaryLeft_led dup 3": 1005,
        "RotaryMiddle_led": 1006,
        "RotaryMiddle_led dup 2": 1007,
        "RotaryMiddle_led dup 3": 1008,
        "RotaryRight_led": 1009,
        "RotaryRight_led dup 2": 1010,
        "RotaryRight_led dup 3": 1011,
        "Right_Btn_4_led": 1020,
        "Right_Btn_4_led dup 2": 1270,
    }
    cy, rs = _load_both(path)
    for name, src_id in expected.items():
        assert name in cy.channels, f"cython missing {name!r}"
        assert name in rs.channels, f"rust missing {name!r}"
        cy_meta = ChannelMetadata.from_channel_table(cy.channels[name])
        rs_meta = ChannelMetadata.from_channel_table(rs.channels[name])
        assert cy_meta.source_channel_id == src_id, name
        assert rs_meta.source_channel_id == src_id, name
        assert len(cy.channels[name]) == len(rs.channels[name]) > 0, name
