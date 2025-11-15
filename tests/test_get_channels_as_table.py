"""Unit tests for LogFile.get_channels_as_table() method."""

import unittest
import pyarrow as pa
from libxrk.base import LogFile


class TestChannelMerge(unittest.TestCase):
    """Tests for merging channels into a single table."""

    def test_empty_channels(self):
        """Test merging with no channels returns empty table."""
        log = LogFile(
            channels={},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        self.assertEqual(result.num_columns, 1)
        self.assertEqual(result.num_rows, 0)
        self.assertIn("timecodes", result.column_names)

    def test_single_channel(self):
        """Test merging a single channel returns the channel unchanged."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100, 200, 300], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0, 3.0, 4.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={"ChannelA": channel_a},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        self.assertEqual(result.num_columns, 2)
        self.assertEqual(result.num_rows, 4)
        self.assertEqual(result.column_names, ["timecodes", "ChannelA"])
        self.assertEqual(result.column("timecodes").to_pylist(), [0, 100, 200, 300])
        self.assertEqual(result.column("ChannelA").to_pylist(), [1.0, 2.0, 3.0, 4.0])

    def test_two_channels_same_timestamps(self):
        """Test merging two channels with identical timestamps."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100, 200], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0, 3.0], type=pa.float32()),
            }
        )
        channel_b = pa.table(
            {
                "timecodes": pa.array([0, 100, 200], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0, 30.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={"ChannelA": channel_a, "ChannelB": channel_b},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        self.assertEqual(result.num_columns, 3)
        self.assertEqual(result.num_rows, 3)
        self.assertEqual(result.column_names, ["timecodes", "ChannelA", "ChannelB"])
        self.assertEqual(result.column("timecodes").to_pylist(), [0, 100, 200])
        self.assertEqual(result.column("ChannelA").to_pylist(), [1.0, 2.0, 3.0])
        self.assertEqual(result.column("ChannelB").to_pylist(), [10.0, 20.0, 30.0])

    def test_two_channels_disjoint_timestamps(self):
        """Test merging two channels with completely different timestamps."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100, 200], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0, 3.0], type=pa.float32()),
            }
        )
        channel_b = pa.table(
            {
                "timecodes": pa.array([50, 150, 250], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0, 30.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={"ChannelA": channel_a, "ChannelB": channel_b},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Should have 6 rows (3 from each channel, no overlap)
        self.assertEqual(result.num_columns, 3)
        self.assertEqual(result.num_rows, 6)
        self.assertEqual(result.column_names, ["timecodes", "ChannelA", "ChannelB"])

        # Check timecodes are sorted and include all unique values
        timecodes = result.column("timecodes").to_pylist()
        self.assertEqual(timecodes, [0, 50, 100, 150, 200, 250])

        # Check null handling
        channel_a_values = result.column("ChannelA").to_pylist()
        channel_b_values = result.column("ChannelB").to_pylist()

        # ChannelA has values at 0, 100, 200; nulls at 50, 150, 250
        self.assertEqual(channel_a_values, [1.0, None, 2.0, None, 3.0, None])

        # ChannelB has values at 50, 150, 250; nulls at 0, 100, 200
        self.assertEqual(channel_b_values, [None, 10.0, None, 20.0, None, 30.0])

    def test_two_channels_overlapping_timestamps(self):
        """Test merging two channels with partially overlapping timestamps."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100, 200, 300], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0, 3.0, 4.0], type=pa.float32()),
            }
        )
        channel_b = pa.table(
            {
                "timecodes": pa.array([100, 200, 400], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0, 30.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={"ChannelA": channel_a, "ChannelB": channel_b},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Should have 5 unique timestamps: 0, 100, 200, 300, 400
        self.assertEqual(result.num_columns, 3)
        self.assertEqual(result.num_rows, 5)

        timecodes = result.column("timecodes").to_pylist()
        self.assertEqual(timecodes, [0, 100, 200, 300, 400])

        channel_a_values = result.column("ChannelA").to_pylist()
        channel_b_values = result.column("ChannelB").to_pylist()

        # ChannelA: has values at 0, 100, 200, 300; null at 400
        self.assertEqual(channel_a_values, [1.0, 2.0, 3.0, 4.0, None])

        # ChannelB: has values at 100, 200, 400; nulls at 0, 300
        self.assertEqual(channel_b_values, [None, 10.0, 20.0, None, 30.0])

    def test_three_channels_mixed_timestamps(self):
        """Test merging three channels with various overlapping patterns."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 200], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0], type=pa.float32()),
            }
        )
        channel_b = pa.table(
            {
                "timecodes": pa.array([100, 200, 300], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0, 30.0], type=pa.float32()),
            }
        )
        channel_c = pa.table(
            {
                "timecodes": pa.array([0, 100, 300], type=pa.int64()),
                "ChannelC": pa.array([100.0, 200.0, 300.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={
                "ChannelA": channel_a,
                "ChannelB": channel_b,
                "ChannelC": channel_c,
            },
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Should have 4 unique timestamps: 0, 100, 200, 300
        self.assertEqual(result.num_columns, 4)
        self.assertEqual(result.num_rows, 4)

        timecodes = result.column("timecodes").to_pylist()
        self.assertEqual(timecodes, [0, 100, 200, 300])

        channel_a_values = result.column("ChannelA").to_pylist()
        channel_b_values = result.column("ChannelB").to_pylist()
        channel_c_values = result.column("ChannelC").to_pylist()

        # ChannelA: at 0, 200
        self.assertEqual(channel_a_values, [1.0, None, 2.0, None])

        # ChannelB: at 100, 200, 300
        self.assertEqual(channel_b_values, [None, 10.0, 20.0, 30.0])

        # ChannelC: at 0, 100, 300
        self.assertEqual(channel_c_values, [100.0, 200.0, None, 300.0])

    def test_null_count_calculation(self):
        """Test that null counts are correctly calculated in merged table."""
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0], type=pa.float32()),
            }
        )
        channel_b = pa.table(
            {
                "timecodes": pa.array([200, 300], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={"ChannelA": channel_a, "ChannelB": channel_b},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Should have 4 rows total
        self.assertEqual(result.num_rows, 4)

        # Timecodes should have no nulls
        self.assertEqual(result.column("timecodes").null_count, 0)

        # Each channel should have 2 nulls (50%)
        self.assertEqual(result.column("ChannelA").null_count, 2)
        self.assertEqual(result.column("ChannelB").null_count, 2)

    def test_channels_sorted_alphabetically(self):
        """Test that channels are processed in alphabetical order."""
        # Create channels in non-alphabetical order
        channel_z = pa.table(
            {
                "timecodes": pa.array([0], type=pa.int64()),
                "ZChannel": pa.array([1.0], type=pa.float32()),
            }
        )
        channel_a = pa.table(
            {
                "timecodes": pa.array([0], type=pa.int64()),
                "AChannel": pa.array([2.0], type=pa.float32()),
            }
        )
        channel_m = pa.table(
            {
                "timecodes": pa.array([0], type=pa.int64()),
                "MChannel": pa.array([3.0], type=pa.float32()),
            }
        )

        log = LogFile(
            channels={
                "ZChannel": channel_z,
                "AChannel": channel_a,
                "MChannel": channel_m,
            },
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Columns should be sorted alphabetically (after timecodes)
        self.assertEqual(result.column_names, ["timecodes", "AChannel", "MChannel", "ZChannel"])

    def test_column_metadata_preserved(self):
        """Test that column metadata is preserved in the merged table."""
        # Create channels with metadata
        channel_a = pa.table(
            {
                "timecodes": pa.array([0, 100], type=pa.int64()),
                "ChannelA": pa.array([1.0, 2.0], type=pa.float32()),
            }
        )
        # Add metadata to ChannelA
        channel_a_field = channel_a.schema.field("ChannelA")
        channel_a_field_with_meta = channel_a_field.with_metadata(
            {b"units": b"m/s", b"dec_pts": b"2", b"interpolate": b"linear"}
        )
        new_schema_a = pa.schema(
            [
                channel_a.schema.field("timecodes"),
                channel_a_field_with_meta,
            ]
        )
        channel_a = channel_a.cast(new_schema_a)

        channel_b = pa.table(
            {
                "timecodes": pa.array([50, 150], type=pa.int64()),
                "ChannelB": pa.array([10.0, 20.0], type=pa.float32()),
            }
        )
        # Add different metadata to ChannelB
        channel_b_field = channel_b.schema.field("ChannelB")
        channel_b_field_with_meta = channel_b_field.with_metadata(
            {b"units": b"rpm", b"dec_pts": b"0", b"interpolate": b"step"}
        )
        new_schema_b = pa.schema(
            [
                channel_b.schema.field("timecodes"),
                channel_b_field_with_meta,
            ]
        )
        channel_b = channel_b.cast(new_schema_b)

        log = LogFile(
            channels={"ChannelA": channel_a, "ChannelB": channel_b},
            laps=pa.table({"num": [], "start_time": [], "end_time": []}),
            metadata={},
            file_name="test.xrk",
        )

        result = log.get_channels_as_table()

        # Check that metadata is preserved for ChannelA
        schema = result.schema
        channel_a_field = schema.field("ChannelA")
        channel_a_metadata = channel_a_field.metadata or {}

        self.assertEqual(channel_a_metadata.get(b"units"), b"m/s")
        self.assertEqual(channel_a_metadata.get(b"dec_pts"), b"2")
        self.assertEqual(channel_a_metadata.get(b"interpolate"), b"linear")

        # Check that metadata is preserved for ChannelB
        channel_b_field = schema.field("ChannelB")
        channel_b_metadata = channel_b_field.metadata or {}

        self.assertEqual(channel_b_metadata.get(b"units"), b"rpm")
        self.assertEqual(channel_b_metadata.get(b"dec_pts"), b"0")
        self.assertEqual(channel_b_metadata.get(b"interpolate"), b"step")


if __name__ == "__main__":
    unittest.main()
