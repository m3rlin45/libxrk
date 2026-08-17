"""Exhaustive Cython vs Rust backend equivalence tests.

Every test fixture is loaded with both backends and compared at full
strictness: channel name sets, Arrow types, all 11 field-metadata keys,
bit-exact timecodes and values, the laps table, and the complete metadata
dict.  This is deliberately stronger than the per-file CrossBackend classes
(which check a subset of metadata keys and use loose GPS tolerances).

Documented residual discrepancy (see the xfail test at the bottom, which
flips visibly when the underlying divergence is fixed):

1. GPS float paths.  Both backends compute GPS-derived channels in float64,
   but with slightly different operation orders:
     - GPS Latitude / GPS Altitude: the Rust Vermeille-2003 ECEF->LLA uses
       ``/ (a*a)`` and ``r*r*r`` where numpy uses ``* (1/(a*a))`` and
       ``r**3`` (and ``np.cbrt`` vs ``f64::cbrt``); observed diffs are
       <= 1.5e-14 deg / <= 2.9e-9 m.
     - GPS_LateralAcc: Rust multiplies the float32-rounded yaw rate where
       Cython uses the intermediate float64 value; observed <= 5e-7 g.
     - GPS_Yaw_Rate: rare 1-ulp float32 rounding differences (<= 1e-12).
     - GPS Longitude: pure atan2; bit-exact on some hosts, but numpy's
       atan2 kernel varies with glibc version and CPU SIMD dispatch while
       Rust links a vendored libm, so it can differ by 1 ulp elsewhere.
   All other GPS channels (Speed, InlineAcc, Satellites, Fix, pDOP,
   Position/Velocity Accuracy) are pure arithmetic + sqrt and are
   required to be bit-exact on every platform.
"""

from pathlib import Path

import numpy as np
import pytest

TEST_DATA_DIR = Path(__file__).parent / "test_data"

try:
    import libxrk._aim_xrk_rs  # noqa: F401

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _RUST_AVAILABLE, reason="Rust backend not available")


# Per-channel float tolerances for known GPS float-path differences
# (rtol, atol); channels not listed must match bit-exactly.
_GPS_FLOAT_TOLERANCES = {
    "GPS Latitude": (0.0, 1e-11),
    "GPS Longitude": (0.0, 1e-11),
    "GPS Altitude": (0.0, 1e-6),
    "GPS_LateralAcc": (1e-4, 1e-7),
    "GPS_Yaw_Rate": (1e-4, 1e-9),
}


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
    from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk
    from libxrk.aim_xrk import aim_xrk as cython_aim_xrk

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
    tol = _GPS_FLOAT_TOLERANCES.get(name)
    if tol is not None:
        rtol, atol = tol
        if not np.allclose(cy_v, rs_v, rtol=rtol, atol=atol, equal_nan=True):
            maxdiff = float(np.nanmax(np.abs(cy_v - rs_v)))
            errors.append(
                f"{name}: values exceed documented float tolerance "
                f"(rtol={rtol}, atol={atol}), max abs diff {maxdiff:.3e}"
            )
    else:
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
    """Cython and Rust must agree on everything except documented gaps."""
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


# ---------------------------------------------------------------------------
# Probes for documented residual discrepancies.  The strict xfail below
# turns into an XPASS failure when the underlying divergence is fixed,
# prompting removal of the marker (and of the corresponding carve-out
# above).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, dup_channels",
    [
        (
            TEST_DATA_DIR / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz",
            ("RotaryLeft_led", "RotaryMiddle_led", "RotaryRight_led", "Right_Btn_4_led"),
        ),
        (
            TEST_DATA_DIR / "issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz",
            ("RotaryLeft_led", "RotaryMiddle_led", "RotaryRight_led", "Right_Btn_4_led"),
        ),
    ],
    ids=["issue68", "issue84"],
)
def test_duplicate_name_channels_match(path: Path, dup_channels: tuple) -> None:
    """For duplicated CHS long_names, both backends expose the same (last,
    data-bearing) CHS index — deterministically."""
    from libxrk.base import ChannelMetadata

    cy, rs = _load_both(path)
    for name in dup_channels:
        cy_meta = ChannelMetadata.from_channel_table(cy.channels[name])
        rs_meta = ChannelMetadata.from_channel_table(rs.channels[name])
        assert cy_meta.source_channel_id == rs_meta.source_channel_id, name
        assert len(cy.channels[name]) == len(rs.channels[name]), name


@pytest.mark.xfail(
    # Not strict: whether these channels come out bit-identical depends on
    # the host's numpy kernels (glibc version, SIMD dispatch) — they match
    # on some machines and differ by 1 ulp on others.
    strict=False,
    reason="GPS float paths differ between numpy and Rust at the ulp level "
    "(module docstring, item 1)",
)
def test_gps_channels_bitwise_identical() -> None:
    cy, rs = _load_both(TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk")
    for name in ("GPS Latitude", "GPS Altitude", "GPS_LateralAcc"):
        cy_v = cy.channels[name].column(name).to_numpy()
        rs_v = rs.channels[name].column(name).to_numpy()
        np.testing.assert_array_equal(cy_v, rs_v, err_msg=name)
