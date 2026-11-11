"""Channels report the type their samples have on disk (``source_dtype``).

Float16 samples (CHS decoder type 20: the IMU, the gyro, most CAN channels)
are returned as float32. The widening adds no information, so a consumer that
wants to store a channel at its real precision needs to know which float32
columns were float16 in the file. ``ChannelMetadata.source_dtype`` says so,
for every channel read from the file, on both backends, without changing any
value or column type. Issue #115.

The rule itself (decoder type -> on-disk type) is the spec's
``channel_source_dtype()``; ``spec/tests/test_spec.py`` compares it against
both backends for every channel of every fixture. These tests cover what a
caller sees.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from libxrk import GPS_CHANNEL_NAMES, ChannelMetadata
from libxrk._aim_xrk_cy import aim_xrk as decode_cython
from libxrk._aim_xrk_rs import aim_xrk as decode_rust

from . import xrk_patch

TEST_DATA = Path(__file__).parent / "test_data"
SFJ_XRK = TEST_DATA / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
FILE_86_XRK = TEST_DATA / "86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
KK_SII_XRZ = TEST_DATA / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz"
AIM_OFFICIAL = TEST_DATA / "aim_official/test.xrk"

# The three loggers of the issue (a Super FJ, an Inferno 86 / SW4, a KK-SII)
# and the official AIM sample.
FIXTURES = pytest.mark.parametrize(
    "path", [SFJ_XRK, FILE_86_XRK, KK_SII_XRZ, AIM_OFFICIAL], ids=["sfj", "86", "kk-sii", "aim"]
)
BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])

SOURCE_DTYPES = {"uint8", "uint16", "int16", "int32", "uint64", "float16", "float32"}


def _meta(log, name) -> ChannelMetadata:
    return ChannelMetadata.from_channel_table(log.channels[name])


def _values(log, name) -> np.ndarray:
    return np.asarray(log.channels[name].column(name).to_numpy(zero_copy_only=False))


def _float16_exact(values: np.ndarray) -> bool:
    """The issue's repro: every finite value survives float32 -> float16 -> float32."""
    finite = values[np.isfinite(values)]
    return bool(np.array_equal(finite.astype(np.float16).astype(np.float32), finite))


@BACKENDS
@FIXTURES
def test_every_file_channel_has_a_source_dtype_and_gps_channels_have_none(decode, path):
    log = decode(str(path))
    for name in log.channels:
        source_dtype = _meta(log, name).source_dtype
        if name in GPS_CHANNEL_NAMES:
            # Computed from NAV-SOL records: no single on-disk sample type.
            assert source_dtype == "", name
        else:
            assert source_dtype in SOURCE_DTYPES, f"{name}: {source_dtype!r}"
    assert set(GPS_CHANNEL_NAMES) <= set(log.channels)


@BACKENDS
@FIXTURES
def test_float16_channels_are_float32_columns_that_narrow_back_exactly(decode, path):
    """The issue's repro, driven by the field instead of by guessing from the
    values: a channel that says float16 is still a float32 column, and every
    finite value is float16-representable."""
    log = decode(str(path))
    checked = 0
    for name, table in log.channels.items():
        meta = _meta(log, name)
        if meta.source_dtype != "float16":
            continue
        assert table.schema.field(name).type == pa.float32(), name
        values = _values(log, name)
        if meta.units == "V":
            # Stored in millivolts and returned divided by 1000: the stored
            # float16 is the millivolt value, and it gives the volts back.
            millivolts = (values * np.float32(1000)).astype(np.float16)
            restored = millivolts.astype(np.float32) / np.float32(1000)
            assert np.array_equal(restored, values, equal_nan=True), name
        else:
            assert _float16_exact(values), name
            narrowed = values.astype(np.float16)
            assert np.array_equal(narrowed.astype(np.float32), values, equal_nan=True), name
        checked += 1
    assert checked >= 10, "fixture lost its float16 channels"


IMU_AND_GYRO = ["LateralAcc", "InlineAcc", "VerticalAcc", "YawRate", "PitchRate", "RollRate"]


@BACKENDS
@pytest.mark.parametrize(
    "path, float16_channels, float32_channels",
    [
        (SFJ_XRK, IMU_AND_GYRO + ["BRK", "steering", "ACCEL"], ["Lateral Grip"]),
        (FILE_86_XRK, IMU_AND_GYRO, ["BrakePress", "SteerAngle", "PPS", "FL_Ch1"]),
        (
            KK_SII_XRZ,
            # The KK-SII config truncates the name to "VerticalAc".
            [n for n in IMU_AND_GYRO if n != "VerticalAcc"]
            + ["VerticalAc", "F_BRK", "Steering", "Throttle"],
            ["Brake_Bias", "TPMS_Press_LF"],
        ),
    ],
    ids=["sfj", "86", "kk-sii"],
)
def test_the_issues_channels(decode, path, float16_channels, float32_channels):
    """The channels the issue names: the IMU, the gyro and the CAN
    brake/steer/throttle are float16; the analog channels, IR temperatures
    and pressures are genuine float32, and their values show it (they are not
    float16-representable)."""
    log = decode(str(path))
    for name in float16_channels:
        assert _meta(log, name).source_dtype == "float16", name
        assert _float16_exact(_values(log, name)), name
    for name in float32_channels:
        assert _meta(log, name).source_dtype == "float32", name
        assert not _float16_exact(_values(log, name)), name


@BACKENDS
def test_values_alone_cannot_tell_the_source_type(decode):
    """Why the field is needed: a genuine float32 channel can hold nothing but
    float16-representable values (whole-degree TPMS temperatures here), so
    the issue's value test would call it float16. The field does not."""
    log = decode(str(FILE_86_XRK))
    assert _meta(log, "TPMS_Temp_LF").source_dtype == "float32"
    assert _float16_exact(_values(log, "TPMS_Temp_LF"))


@BACKENDS
def test_integer_channels_report_their_stored_type(decode):
    """Integer samples are returned in the type they are stored in."""
    log = decode(str(AIM_OFFICIAL))
    seen = set()
    for name, table in log.channels.items():
        source_dtype = _meta(log, name).source_dtype
        if source_dtype.startswith(("int", "uint")):
            assert str(table.schema.field(name).type) == source_dtype, name
            seen.add(source_dtype)
    assert {"int16", "uint16"} <= seen


@BACKENDS
@pytest.mark.parametrize("gear_name", ["Calculated_Gear", "PreCalcGear"])
def test_gear_channels_decoded_by_name_are_uint64(decode, gear_name):
    """Both backends read these two channels as uint64 by name, whatever the
    CHS decoder type says. No fixture has one with data, so the 86's ``Gear``
    channel (8-byte samples) is renamed in memory."""
    data = bytearray(xrk_patch.load_bytes(FILE_86_XRK))
    frame = xrk_patch.chs_frames(bytes(data))["Gear"]
    assert data[frame["pstart"] + 72] == 8, "fixture changed: Gear no longer has 8-byte samples"
    xrk_patch.patch(data, frame, 32, gear_name.encode().ljust(24, b"\0"))

    log = decode(bytes(data))
    assert _meta(log, gear_name).source_dtype == "uint64"


@BACKENDS
def test_source_dtype_survives_logfile_operations(decode):
    """select / filter / resample / merge keep the field like the other metadata."""
    log = decode(str(SFJ_XRK))
    names = ["LateralAcc", "Lateral Grip", "GPS Speed"]
    expected = {"LateralAcc": "float16", "Lateral Grip": "float32", "GPS Speed": ""}
    assert {n: _meta(log, n).source_dtype for n in names} == expected

    lap = int(log.laps.column("num")[1].as_py())
    derived = {
        "select_channels": log.select_channels(names),
        "filter_by_time_range": log.filter_by_time_range(10_000, 20_000, names),
        "filter_by_lap": log.filter_by_lap(lap, names),
        "resample_to_channel": log.resample_to_channel("GPS Speed", names),
        "resample_to_timecodes": log.resample_to_timecodes(
            pa.array(range(0, 20_000, 50), type=pa.int64()), names
        ),
    }
    for operation, result in derived.items():
        for name in names:
            assert _meta(result, name) == _meta(log, name), f"{operation}: {name}"

    merged = log.select_channels(names).get_channels_as_table()
    for name in names:
        assert ChannelMetadata.from_field(merged.schema.field(name)) == _meta(log, name), name

    # Selecting and filtering keep the stored samples, so the narrowing still holds.
    filtered = derived["filter_by_lap"]
    assert filtered.channels["LateralAcc"].num_rows > 0
    assert _float16_exact(_values(filtered, "LateralAcc"))
