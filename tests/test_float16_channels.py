"""Float16 samples are returned as float16, not widened to float32.

Most analog, IMU, gyro and CAN channels are stored as IEEE half floats (CHS
decoder type 20). Their value column is float16 (Arrow ``halffloat``) on both
backends: widening to float32 added no information and doubled the column.
The one exception is a channel in volts, stored in millivolts and divided by
1000: the quotient is not a float16, so it is float32. Issue #115.

The rule is stated in ``spec/docs/companion.md`` section 3, and the spec tests
compare every value of every fixture against both backends.
These tests cover what a caller sees: the types, that no value changed, the
LogFile operations on float16 columns, and the cast to use where pyarrow has
no half-float kernel.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from libxrk import ChannelMetadata
from libxrk._aim_xrk_cy import aim_xrk as decode_cython
from libxrk._aim_xrk_rs import aim_xrk as decode_rust

TEST_DATA = Path(__file__).parent / "test_data"
SFJ_XRK = TEST_DATA / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
FILE_86_XRK = TEST_DATA / "86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
KK_SII_XRZ = TEST_DATA / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz"
AIM_OFFICIAL = TEST_DATA / "aim_official/test.xrk"

BACKENDS = pytest.mark.parametrize("decode", [decode_cython, decode_rust], ids=["cython", "rust"])

IMU_AND_GYRO = ["LateralAcc", "InlineAcc", "VerticalAcc", "YawRate", "PitchRate", "RollRate"]

# Per logger: the channels issue #115 names as float16, the float16 channel
# in volts, genuine float32 channels, and how many float16 columns the file has.
LOGGERS = pytest.mark.parametrize(
    "path, float16_channels, volt_channel, float32_channels, float16_count",
    [
        (
            SFJ_XRK,
            IMU_AND_GYRO + ["BRK", "steering", "ACCEL", "RPM"],
            "External Voltage",
            ["Lateral Grip"],
            14,
        ),
        (
            FILE_86_XRK,
            IMU_AND_GYRO + ["LF_Shock_Pot"],
            "External Voltage",
            ["BrakePress", "SteerAngle", "PPS", "FL_Ch1", "RPM"],
            12,
        ),
        (
            KK_SII_XRZ,
            # The KK-SII config truncates the name to "VerticalAc".
            [n for n in IMU_AND_GYRO if n != "VerticalAcc"]
            + ["VerticalAc", "F_BRK", "Steering", "Throttle"],
            "Ext_Battery",
            ["Brake_Bias", "TPMS_Press_LF"],
            24,
        ),
        (
            AIM_OFFICIAL,
            ["Logger Temperature", "AccelerometerX", "Water Temp"],
            "Int Batt Voltage",
            ["GyroX", "GyroY", "GyroZ"],
            9,
        ),
    ],
    ids=["sfj", "86", "kk-sii", "aim"],
)


def _type(log, name) -> pa.DataType:
    return log.channels[name].schema.field(name).type


def _values(log, name) -> np.ndarray:
    return np.asarray(log.channels[name].column(name).to_numpy(zero_copy_only=False))


@BACKENDS
@LOGGERS
def test_float16_samples_are_float16_columns(
    decode, path, float16_channels, volt_channel, float32_channels, float16_count
):
    log = decode(str(path))
    for name in float16_channels:
        assert _type(log, name) == pa.float16(), name
        assert _values(log, name).dtype == np.float16, name
    for name in float32_channels:
        assert _type(log, name) == pa.float32(), name
    assert sum(_type(log, name) == pa.float16() for name in log.channels) == float16_count


@BACKENDS
@LOGGERS
def test_volt_channels_stay_float32(
    decode, path, float16_channels, volt_channel, float32_channels, float16_count
):
    """Stored as float16 millivolts, returned as float32 volts: the stored
    sample is recovered from the value, and widening it and dividing by 1000
    in float32 gives the value back bit for bit. A float16 column could not
    hold these values."""
    log = decode(str(path))
    assert ChannelMetadata.from_channel_table(log.channels[volt_channel]).units == "V"
    assert _type(log, volt_channel) == pa.float32()

    volts = _values(log, volt_channel)
    millivolts = (volts * np.float32(1000)).astype(np.float16)
    restored = millivolts.astype(np.float32) / np.float32(1000)
    assert restored.tobytes() == volts.tobytes()

    finite = volts[np.isfinite(volts)]
    if len(finite):  # the AIM sample's External Voltage is NaN throughout
        assert not np.array_equal(finite.astype(np.float16).astype(np.float32), finite)


@BACKENDS
def test_a_float16_channel_table_is_10_bytes_per_row(decode):
    """int64 timecode + float16 value; it was 12 with a float32 value."""
    table = decode(str(SFJ_XRK)).channels["LateralAcc"]
    assert table.column("LateralAcc").nbytes == 2 * table.num_rows
    assert table.nbytes == 10 * table.num_rows


@BACKENDS
def test_cast_to_float32_is_exact_and_feeds_pyarrow_compute(decode):
    """pyarrow has no half-float kernels for most of pyarrow.compute. The
    documented way out is a cast, which is exact, by either route."""
    log = decode(str(SFJ_XRK))
    column = log.channels["LateralAcc"].column("LateralAcc")
    values = _values(log, "LateralAcc")

    widened = pc.cast(column, pa.float32())
    assert widened.type == pa.float32()
    via_numpy = values.astype(np.float32)
    assert widened.to_numpy().tobytes() == via_numpy.tobytes()
    assert np.array_equal(via_numpy.astype(np.float16), values)  # nothing was added

    low_high = pc.min_max(widened).as_py()
    assert (low_high["min"], low_high["max"]) == (float(values.min()), float(values.max()))
    assert pc.mean(widened).as_py() == pytest.approx(float(via_numpy.mean(dtype=np.float64)))


@BACKENDS
def test_select_and_filter_keep_float16_and_the_stored_samples(decode):
    log = decode(str(SFJ_XRK))
    names = ["LateralAcc", "RPM", "External Voltage", "Lateral Grip", "GPS Speed"]
    lap = log.laps.slice(1, 1).to_pylist()[0]

    for operation, result in {
        "select_channels": log.select_channels(names),
        "filter_by_time_range": log.filter_by_time_range(10_000, 20_000, names),
        "filter_by_lap": log.filter_by_lap(lap["num"], names),
    }.items():
        for name in names:
            assert _type(result, name) == _type(log, name), f"{operation}: {name}"
            assert ChannelMetadata.from_channel_table(
                result.channels[name]
            ) == ChannelMetadata.from_channel_table(log.channels[name]), f"{operation}: {name}"

    filtered = log.filter_by_lap(lap["num"], names).channels["LateralAcc"]
    whole = log.channels["LateralAcc"]
    timecodes = whole.column("timecodes").to_numpy()
    inside = (timecodes >= lap["start_time"]) & (timecodes < lap["end_time"])
    assert filtered.num_rows == int(inside.sum()) > 0
    assert filtered.schema.field("LateralAcc").type == pa.float16()
    assert np.array_equal(
        filtered.column("LateralAcc").to_numpy(), _values(log, "LateralAcc")[inside]
    )


def _widened(log):
    """The same LogFile with every float16 column cast to float32: what the
    backends returned before float16 columns."""
    channels = {}
    for name, table in log.channels.items():
        field = table.schema.field(name)
        if field.type == pa.float16():
            schema = pa.schema([table.schema.field("timecodes"), field.with_type(pa.float32())])
            table = table.cast(schema)
        channels[name] = table
    return type(log)(
        channels=channels, laps=log.laps, metadata=log.metadata, file_name=log.file_name
    )


@BACKENDS
def test_resampling_gives_float32_identical_to_resampling_the_widened_channels(decode):
    """Interpolated values are not float16 values, so an interpolated float16
    channel resamples to float32. Resampled and merged tables are therefore
    exactly what they were when the backends widened: same types, same bits."""
    log = decode(str(SFJ_XRK))
    names = ["LateralAcc", "RPM", "External Voltage", "Lateral Grip", "GPS Speed", "StartRec"]
    assert _type(log, "LateralAcc") == _type(log, "RPM") == pa.float16()
    before = _widened(log)

    target = pa.array(range(0, 60_000, 37), type=pa.int64())
    pairs = {
        "resample_to_timecodes": (
            log.resample_to_timecodes(target, names),
            before.resample_to_timecodes(target, names),
        ),
        "resample_to_channel": (
            log.resample_to_channel("GPS Speed", names),
            before.resample_to_channel("GPS Speed", names),
        ),
    }
    for operation, (now, then) in pairs.items():
        for name in names:
            assert now.channels[name].equals(
                then.channels[name], check_metadata=True
            ), f"{operation}: {name}"
        assert _type(now, "LateralAcc") == _type(now, "RPM") == pa.float32(), operation

    merged = log.select_channels(names).get_channels_as_table()
    assert merged.equals(before.select_channels(names).get_channels_as_table(), check_metadata=True)
    assert merged.schema.field("LateralAcc").type == pa.float32()


@BACKENDS
def test_merging_a_whole_file_leaves_no_float16_column(decode):
    """Every float16 channel a logger writes is interpolated, so the merged
    table of a parsed file has none: pyarrow compute works on all of it."""
    merged = decode(str(SFJ_XRK)).get_channels_as_table()
    assert pa.float16() not in set(merged.schema.types)
    assert pc.mean(merged.column("LateralAcc")).as_py() is not None
