"""Unit tests for LogFile filtering and resampling methods."""

import unittest
import pyarrow as pa
from libxrk.base import ChannelMetadata, LogFile


def create_channel_with_metadata(
    timecodes: list,
    values: list,
    name: str,
    units: str = "",
    dec_pts: str = "",
    interpolate: str = "",
) -> pa.Table:
    """Helper to create a channel table with metadata."""
    table = pa.table(
        {
            "timecodes": pa.array(timecodes, type=pa.int64()),
            name: pa.array(values, type=pa.float32()),
        }
    )

    meta = ChannelMetadata(
        units=units,
        dec_pts=int(dec_pts) if dec_pts else 0,
        interpolate=interpolate == "True",
    )
    metadata = meta.to_field_metadata()
    field = table.schema.field(name)
    new_field = field.with_metadata(metadata)
    new_schema = pa.schema([table.schema.field("timecodes"), new_field])
    table = table.cast(new_schema)

    return table


def create_test_logfile(
    channels: dict[str, pa.Table],
    laps: list[tuple[int, int, int]] | None = None,
) -> LogFile:
    """Helper to create a test LogFile."""
    if laps is None:
        laps_table = pa.table({"num": [], "start_time": [], "end_time": []})
    else:
        laps_table = pa.table(
            {
                "num": [lap[0] for lap in laps],
                "start_time": [float(lap[1]) for lap in laps],
                "end_time": [float(lap[2]) for lap in laps],
            }
        )

    return LogFile(
        channels=channels,
        laps=laps_table,
        metadata={},
        file_name="test.xrk",
    )


class TestSelectChannels(unittest.TestCase):
    """Tests for LogFile.select_channels()."""

    def test_select_channels_basic(self):
        """Test selecting a subset of channels."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
            "ChannelB": create_channel_with_metadata([0, 100], [10.0, 20.0], "ChannelB"),
            "ChannelC": create_channel_with_metadata([0, 100], [100.0, 200.0], "ChannelC"),
        }
        log = create_test_logfile(channels)

        result = log.select_channels(["ChannelA", "ChannelC"])

        self.assertEqual(set(result.channels.keys()), {"ChannelA", "ChannelC"})
        self.assertEqual(result.channels["ChannelA"].num_rows, 2)
        self.assertEqual(result.channels["ChannelC"].num_rows, 2)

    def test_select_channels_missing_raises(self):
        """Test that selecting missing channels raises KeyError."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        with self.assertRaises(KeyError) as ctx:
            log.select_channels(["ChannelA", "NotExist"])

        self.assertIn("NotExist", str(ctx.exception))

    def test_select_channels_preserves_metadata(self):
        """Test that selecting channels preserves their metadata."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100], [1.0, 2.0], "ChannelA", units="m/s", dec_pts="2", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        result = log.select_channels(["ChannelA"])

        meta = ChannelMetadata.from_channel_table(result.channels["ChannelA"], "ChannelA")
        self.assertEqual(meta.units, "m/s")
        self.assertEqual(meta.dec_pts, 2)
        self.assertTrue(meta.interpolate)


class TestFilterByTimeRange(unittest.TestCase):
    """Tests for LogFile.filter_by_time_range()."""

    def test_filter_by_time_range_basic(self):
        """Test basic time range filtering."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 50, 100, 150, 200], [1.0, 2.0, 3.0, 4.0, 5.0], "ChannelA"
            ),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(50, 150)

        # Should include 50 and 100, but not 150 (exclusive end)
        self.assertEqual(result.channels["ChannelA"].num_rows, 2)
        timecodes = result.channels["ChannelA"].column("timecodes").to_pylist()
        self.assertEqual(timecodes, [50, 100])

    def test_filter_by_time_range_with_channel_selection(self):
        """Test filtering with specific channel names."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100, 200], [1.0, 2.0, 3.0], "ChannelA"),
            "ChannelB": create_channel_with_metadata([0, 100, 200], [10.0, 20.0, 30.0], "ChannelB"),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(0, 150, channel_names=["ChannelA"])

        self.assertEqual(set(result.channels.keys()), {"ChannelA"})
        self.assertEqual(result.channels["ChannelA"].num_rows, 2)

    def test_filter_by_time_range_preserves_metadata(self):
        """Test that filtering preserves channel metadata."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200], [1.0, 2.0, 3.0], "ChannelA", units="rpm", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(0, 150)

        meta = ChannelMetadata.from_channel_table(result.channels["ChannelA"], "ChannelA")
        self.assertEqual(meta.units, "rpm")
        self.assertTrue(meta.interpolate)

    def test_filter_by_time_range_empty_result(self):
        """Test filtering that results in empty channels."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100, 200], [1.0, 2.0, 3.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(500, 600)

        self.assertEqual(result.channels["ChannelA"].num_rows, 0)

    def test_filter_by_time_range_filters_laps(self):
        """Test that laps are filtered to overlapping ones."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200, 300], [1.0, 2.0, 3.0, 4.0], "ChannelA"
            ),
        }
        laps = [
            (0, 0, 100),
            (1, 100, 200),
            (2, 200, 300),
        ]
        log = create_test_logfile(channels, laps)

        result = log.filter_by_time_range(50, 150)

        # Laps 0 and 1 overlap with [50, 150)
        self.assertEqual(result.laps.num_rows, 2)
        lap_nums = result.laps.column("num").to_pylist()
        self.assertEqual(lap_nums, [0, 1])

    def test_filter_by_time_range_missing_channel_raises(self):
        """Test that filtering with missing channels raises KeyError."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        with self.assertRaises(KeyError):
            log.filter_by_time_range(0, 100, channel_names=["NotExist"])


class TestFilterByLap(unittest.TestCase):
    """Tests for LogFile.filter_by_lap()."""

    def test_filter_by_lap_basic(self):
        """Test filtering by a specific lap."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 50, 100, 150, 200, 250], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "ChannelA"
            ),
        }
        laps = [
            (0, 0, 100),
            (1, 100, 200),
            (2, 200, 300),
        ]
        log = create_test_logfile(channels, laps)

        result = log.filter_by_lap(1)

        # Lap 1 is [100, 200)
        timecodes = result.channels["ChannelA"].column("timecodes").to_pylist()
        self.assertEqual(timecodes, [100, 150])

    def test_filter_by_lap_invalid_raises(self):
        """Test that filtering by invalid lap raises ValueError."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
        }
        laps = [(0, 0, 100)]
        log = create_test_logfile(channels, laps)

        with self.assertRaises(ValueError) as ctx:
            log.filter_by_lap(99)

        self.assertIn("99", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_filter_by_lap_with_channel_names(self):
        """Test filtering by lap with specific channel names."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100, 200], [1.0, 2.0, 3.0], "ChannelA"),
            "ChannelB": create_channel_with_metadata([0, 100, 200], [10.0, 20.0, 30.0], "ChannelB"),
        }
        laps = [(0, 0, 150)]
        log = create_test_logfile(channels, laps)

        result = log.filter_by_lap(0, channel_names=["ChannelA"])

        self.assertEqual(set(result.channels.keys()), {"ChannelA"})


class TestResampleToTimecodes(unittest.TestCase):
    """Tests for LogFile.resample_to_timecodes()."""

    def test_resample_to_timecodes_interpolation(self):
        """Test linear interpolation for channels with interpolate=True."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200], [0.0, 10.0, 20.0], "ChannelA", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100, 150, 200], type=pa.int64())
        result = log.resample_to_timecodes(target)

        values = result.channels["ChannelA"].column("ChannelA").to_pylist()
        # 0 -> 0.0, 50 -> 5.0 (interpolated), 100 -> 10.0, 150 -> 15.0, 200 -> 20.0
        self.assertAlmostEqual(values[0], 0.0, places=5)
        self.assertAlmostEqual(values[1], 5.0, places=5)
        self.assertAlmostEqual(values[2], 10.0, places=5)
        self.assertAlmostEqual(values[3], 15.0, places=5)
        self.assertAlmostEqual(values[4], 20.0, places=5)

    def test_resample_to_timecodes_forward_fill(self):
        """Test forward-fill for channels without interpolate=True."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200], [1.0, 2.0, 3.0], "ChannelA", interpolate="False"
            ),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100, 150, 200], type=pa.int64())
        result = log.resample_to_timecodes(target)

        values = result.channels["ChannelA"].column("ChannelA").to_pylist()
        # 0 -> 1.0, 50 -> 1.0 (forward fill), 100 -> 2.0, 150 -> 2.0 (forward fill), 200 -> 3.0
        self.assertEqual(values, [1.0, 1.0, 2.0, 2.0, 3.0])

    def test_resample_to_timecodes_backward_fill_leading(self):
        """Test backward-fill for target timecodes before source data."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [100, 200], [10.0, 20.0], "ChannelA", interpolate="False"
            ),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100, 150, 200], type=pa.int64())
        result = log.resample_to_timecodes(target)

        values = result.channels["ChannelA"].column("ChannelA").to_pylist()
        # 0 -> 10.0 (backward fill), 50 -> 10.0 (backward fill), 100 -> 10.0, 150 -> 10.0, 200 -> 20.0
        self.assertEqual(values, [10.0, 10.0, 10.0, 10.0, 20.0])

    def test_resample_to_timecodes_preserves_metadata(self):
        """Test that resampling preserves channel metadata."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100], [1.0, 2.0], "ChannelA", units="m/s", dec_pts="2", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100], type=pa.int64())
        result = log.resample_to_timecodes(target)

        meta = ChannelMetadata.from_channel_table(result.channels["ChannelA"], "ChannelA")
        self.assertEqual(meta.units, "m/s")
        self.assertEqual(meta.dec_pts, 2)
        self.assertTrue(meta.interpolate)

    def test_resample_to_timecodes_with_channel_names(self):
        """Test resampling specific channels only."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
            "ChannelB": create_channel_with_metadata([0, 100], [10.0, 20.0], "ChannelB"),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100], type=pa.int64())
        result = log.resample_to_timecodes(target, channel_names=["ChannelA"])

        self.assertEqual(set(result.channels.keys()), {"ChannelA"})

    def test_resample_to_timecodes_missing_channel_raises(self):
        """Test that resampling with missing channels raises KeyError."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        target = pa.array([0, 50, 100], type=pa.int64())
        with self.assertRaises(KeyError):
            log.resample_to_timecodes(target, channel_names=["NotExist"])


class TestResampleToChannel(unittest.TestCase):
    """Tests for LogFile.resample_to_channel()."""

    def test_resample_to_channel_delegates(self):
        """Test that resample_to_channel delegates to resample_to_timecodes."""
        channels = {
            "Reference": create_channel_with_metadata([0, 50, 100], [1.0, 2.0, 3.0], "Reference"),
            "Other": create_channel_with_metadata(
                [0, 100], [10.0, 20.0], "Other", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        result = log.resample_to_channel("Reference")

        # Other should be resampled to Reference's timecodes
        other_timecodes = result.channels["Other"].column("timecodes").to_pylist()
        self.assertEqual(other_timecodes, [0, 50, 100])

        other_values = result.channels["Other"].column("Other").to_pylist()
        # Interpolated: 0 -> 10.0, 50 -> 15.0, 100 -> 20.0
        self.assertAlmostEqual(other_values[0], 10.0, places=5)
        self.assertAlmostEqual(other_values[1], 15.0, places=5)
        self.assertAlmostEqual(other_values[2], 20.0, places=5)

    def test_resample_to_channel_missing_raises(self):
        """Test that resample_to_channel with missing reference raises KeyError."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100], [1.0, 2.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        with self.assertRaises(KeyError) as ctx:
            log.resample_to_channel("NotExist")

        self.assertIn("NotExist", str(ctx.exception))


class TestGetChannelsAsTableRefactored(unittest.TestCase):
    """Tests to verify refactored get_channels_as_table behavior matches original."""

    def test_get_channels_as_table_single_channel(self):
        """Test that single channel works correctly."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100, 200], [1.0, 2.0, 3.0], "ChannelA"),
        }
        log = create_test_logfile(channels)

        result = log.get_channels_as_table()

        self.assertEqual(result.num_columns, 2)
        self.assertEqual(result.column_names, ["timecodes", "ChannelA"])
        self.assertEqual(result.column("ChannelA").to_pylist(), [1.0, 2.0, 3.0])

    def test_get_channels_as_table_merges_timecodes(self):
        """Test that channels with different timecodes are merged."""
        channels = {
            "ChannelA": create_channel_with_metadata([0, 100, 200], [1.0, 2.0, 3.0], "ChannelA"),
            "ChannelB": create_channel_with_metadata([50, 150], [10.0, 20.0], "ChannelB"),
        }
        log = create_test_logfile(channels)

        result = log.get_channels_as_table()

        # Should have union of timecodes
        timecodes = result.column("timecodes").to_pylist()
        self.assertEqual(timecodes, [0, 50, 100, 150, 200])

    def test_get_channels_as_table_preserves_metadata(self):
        """Test that metadata is preserved in merged table."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100], [1.0, 2.0], "ChannelA", units="m/s", interpolate="True"
            ),
        }
        log = create_test_logfile(channels)

        result = log.get_channels_as_table()

        meta = ChannelMetadata.from_field(result.schema.field("ChannelA"))
        self.assertEqual(meta.units, "m/s")
        self.assertTrue(meta.interpolate)

    def test_get_channels_as_table_empty(self):
        """Test that empty channels returns empty table."""
        log = create_test_logfile({})

        result = log.get_channels_as_table()

        self.assertEqual(result.num_columns, 1)
        self.assertEqual(result.num_rows, 0)
        self.assertIn("timecodes", result.column_names)


class TestMethodChaining(unittest.TestCase):
    """Tests for method chaining pattern."""

    def test_filter_and_select_chain(self):
        """Test chaining filter_by_time_range and select_channels."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200, 300], [1.0, 2.0, 3.0, 4.0], "ChannelA"
            ),
            "ChannelB": create_channel_with_metadata(
                [0, 100, 200, 300], [10.0, 20.0, 30.0, 40.0], "ChannelB"
            ),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(50, 250).select_channels(["ChannelA"])

        self.assertEqual(set(result.channels.keys()), {"ChannelA"})
        timecodes = result.channels["ChannelA"].column("timecodes").to_pylist()
        self.assertEqual(timecodes, [100, 200])

    def test_filter_resample_chain(self):
        """Test chaining filter and resample operations."""
        channels = {
            "ChannelA": create_channel_with_metadata(
                [0, 100, 200, 300], [0.0, 10.0, 20.0, 30.0], "ChannelA", interpolate="True"
            ),
            "ChannelB": create_channel_with_metadata(
                [0, 50, 100, 150, 200, 250, 300],
                [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
                "ChannelB",
            ),
        }
        log = create_test_logfile(channels)

        result = log.filter_by_time_range(0, 200).resample_to_channel("ChannelB")

        # ChannelA should be resampled to ChannelB's filtered timecodes
        a_values = result.channels["ChannelA"].column("ChannelA").to_pylist()
        # After filter [0, 200):
        #   ChannelA has timecodes [0, 100] with values [0.0, 10.0] (200 excluded)
        #   ChannelB has timecodes [0, 50, 100, 150]
        # ChannelA interpolated to ChannelB's timecodes:
        #   0 -> 0.0, 50 -> 5.0, 100 -> 10.0, 150 -> 10.0 (extrapolated edge value)
        self.assertEqual(len(a_values), 4)
        self.assertAlmostEqual(a_values[0], 0.0, places=5)
        self.assertAlmostEqual(a_values[1], 5.0, places=5)
        self.assertAlmostEqual(a_values[2], 10.0, places=5)
        self.assertAlmostEqual(a_values[3], 10.0, places=5)  # np.interp extrapolates edge value


if __name__ == "__main__":
    unittest.main()
