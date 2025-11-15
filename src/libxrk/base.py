# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

from dataclasses import dataclass
import sys
import typing
import pyarrow as pa
import pyarrow.compute as pc
import numpy as np

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

        For channels with interpolate="True" metadata, performs linear interpolation for null values.
        For other channels, fills nulls with the previous non-null value (forward fill).
        After filling, any remaining leading nulls are backward filled with the first available value.

        Returns:
            A PyArrow table with a 'timecodes' column and one column per channel.
            Missing values are interpolated or forward-filled based on channel metadata.
            Leading nulls are backward filled to ensure no nulls remain.
            Column metadata is preserved.
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

        # Fill nulls based on interpolate metadata
        # Process each channel column (skip timecodes)
        columns_dict = {}
        columns_dict["timecodes"] = result.column("timecodes")

        timecodes_np = result.column("timecodes").to_numpy()

        for field in result.schema:
            if field.name == "timecodes":
                continue

            column = result.column(field.name)

            # Check if we should interpolate
            should_interpolate = False
            if field.metadata:
                interpolate_value = field.metadata.get(b"interpolate", b"").decode("utf-8")
                should_interpolate = interpolate_value == "True"

            if should_interpolate:
                # Linear interpolation using numpy for efficiency
                column_np = column.to_numpy(zero_copy_only=False)

                # Find non-null indices
                valid_mask = (
                    ~np.isnan(column_np)
                    if np.issubdtype(column_np.dtype, np.floating)
                    else column is not None
                )

                if isinstance(valid_mask, bool):
                    # All values are null or non-null
                    columns_dict[field.name] = column
                else:
                    valid_indices = np.where(valid_mask)[0]

                    if len(valid_indices) > 0:
                        # Perform linear interpolation
                        # Use numpy.interp which handles extrapolation by extending edge values
                        interpolated = np.interp(
                            timecodes_np,
                            timecodes_np[valid_indices],
                            column_np[valid_indices],
                        )
                        columns_dict[field.name] = pa.array(interpolated, type=field.type)
                    else:
                        # All nulls, keep as is
                        columns_dict[field.name] = column
            else:
                # Forward fill (use previous non-null value)
                # PyArrow's fill_null with forward fill
                filled = pc.fill_null_forward(column)
                columns_dict[field.name] = filled

        # Reconstruct table with filled values
        result = pa.table(columns_dict)

        # Backward fill any remaining leading nulls (nulls before first value in each column)
        columns_dict_final = {}
        columns_dict_final["timecodes"] = result.column("timecodes")
        for field in result.schema:
            if field.name == "timecodes":
                continue
            column = result.column(field.name)
            # Backward fill to handle leading nulls
            filled = pc.fill_null_backward(column)
            columns_dict_final[field.name] = filled

        # Reconstruct table after backward fill
        result = pa.table(columns_dict_final)

        # Restore schema with metadata (fill operations lose metadata)
        if channel_metadata:
            new_fields = []
            for field in result.schema:
                if field.name in channel_metadata:
                    new_fields.append(field.with_metadata(channel_metadata[field.name]))
                else:
                    new_fields.append(field)
            new_schema = pa.schema(new_fields)
            result = result.cast(new_schema)

        return result
