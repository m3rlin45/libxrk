# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

from dataclasses import dataclass
import sys
import typing
import pyarrow as pa

# We use array and memoryview for efficient operations, but that
# assumes the sizes we expect match the file format.  Lets assert a
# few of those assumptions here.  Our use of struct is safe since it
# has tighter control over byte order and sizing.
assert sys.byteorder == "little"


@dataclass(eq=False)
class LogFile:
    channels: typing.Dict[
        str, pa.Table
    ]  # Each channel is a PyArrow table with columns: timecodes (int64), <channel_name> (float/int)
    # Metadata stored in schema.field(<channel_name>).metadata:
    # units, dec_pts, interpolate
    laps: pa.Table  # PyArrow table with columns: num (int), start_time (int), end_time (int)
    metadata: typing.Dict[str, str]
    file_name: str  # move to metadata?

    def get_channels_as_table(self) -> pa.Table:
        """
        Merge all channels into a single PyArrow table with full outer join on timestamps.

        Returns:
            A PyArrow table with a 'timecodes' column and one column per channel.
            Missing values are represented as null. Column metadata is preserved.
        """
        if not self.channels:
            # Return an empty table with just timecodes column if no channels
            return pa.table({"timecodes": pa.array([], type=pa.int64())})

        # Collect metadata from all channels before joining
        # PyArrow join() doesn't preserve field metadata, so we need to save and restore it
        channel_metadata = {}
        for channel_name, channel_table in self.channels.items():
            field = channel_table.schema.field(channel_name)
            if field.metadata:
                channel_metadata[channel_name] = field.metadata

        # Start with the first channel
        channel_names = sorted(self.channels.keys())
        result = self.channels[channel_names[0]]

        # Perform full outer joins with remaining channels
        for channel_name in channel_names[1:]:
            channel_table = self.channels[channel_name]

            # Perform full outer join on timecodes
            result = result.join(
                channel_table, keys="timecodes", right_keys="timecodes", join_type="full outer"
            )

        # Sort by timecodes to maintain temporal order
        result = result.sort_by([("timecodes", "ascending")])

        # Restore column metadata that was lost during join operations
        if channel_metadata:
            new_fields = []
            for field in result.schema:
                if field.name in channel_metadata:
                    # Restore the metadata for this channel
                    new_fields.append(field.with_metadata(channel_metadata[field.name]))
                else:
                    new_fields.append(field)
            new_schema = pa.schema(new_fields)
            result = result.cast(new_schema)

        return result
