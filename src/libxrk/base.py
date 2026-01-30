# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

from collections.abc import Sequence
from dataclasses import dataclass
import sys
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
    """
    Container for parsed XRK/XRZ telemetry data.

    Attributes:
        channels: Dict mapping channel names to PyArrow tables. Each table has
            'timecodes' (int64, ms) and '<channel_name>' columns. Channel metadata
            (units, dec_pts, interpolate) stored in schema.field.metadata with bytes keys.
        laps: PyArrow table with columns: num (int), start_time (int), end_time (int).
            Times are in milliseconds.
        metadata: Dict of session metadata (racer, vehicle, venue, etc.)
        file_name: Original filename or "<bytes>" if loaded from bytes.

    Example:
        >>> log = aim_xrk('file.xrk')
        >>> log.channels['Engine RPM'].to_pandas()  # Single channel
        >>> log.get_channels_as_table().to_pandas()  # All merged
    """

    channels: dict[str, pa.Table]
    laps: pa.Table
    metadata: dict[str, str]
    file_name: str

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

        # Compute union of all channel timecodes using numpy concatenate + unique
        # This is faster than k-way merge with heapq due to optimized C implementation
        timecode_arrays = [
            channel_table.column("timecodes").to_numpy() for channel_table in self.channels.values()
        ]
        union_timecodes = pa.array(np.unique(np.concatenate(timecode_arrays)), type=pa.int64())

        # Resample all channels to the union timecodes
        resampled = self.resample_to_timecodes(union_timecodes)

        # Build merged table from resampled channels (simple horizontal concatenation)
        channel_names = sorted(resampled.channels.keys())

        # Collect metadata for restoration
        channel_metadata = {}
        for name in channel_names:
            field = resampled.channels[name].schema.field(name)
            if field.metadata:
                channel_metadata[name] = field.metadata

        # Build the result table
        columns_dict = {"timecodes": union_timecodes}
        for name in channel_names:
            columns_dict[name] = resampled.channels[name].column(name)

        result = pa.table(columns_dict)

        # Restore schema with metadata
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

    def select_channels(self, channel_names: Sequence[str]) -> "LogFile":
        """
        Create a new LogFile with only the specified channels.

        Args:
            channel_names: Sequence of channel names to include.

        Returns:
            New LogFile containing only the specified channels.

        Raises:
            KeyError: If any channel name is not found.

        Example:
            >>> log = aim_xrk('session.xrk')
            >>> gps_log = log.select_channels(['GPS Latitude', 'GPS Longitude', 'GPS Speed'])
            >>> print(gps_log.channels.keys())
        """
        missing = set(channel_names) - set(self.channels.keys())
        if missing:
            raise KeyError(f"Channels not found: {sorted(missing)}")

        new_channels = {name: self.channels[name] for name in channel_names}
        return LogFile(
            channels=new_channels,
            laps=self.laps,
            metadata=self.metadata,
            file_name=self.file_name,
        )

    def filter_by_time_range(
        self,
        start_time: int,
        end_time: int,
        channel_names: Sequence[str] | None = None,
    ) -> "LogFile":
        """
        Filter channels to a time range [start_time, end_time) at native sample rates.

        Args:
            start_time: Start time in milliseconds (inclusive).
            end_time: End time in milliseconds (exclusive).
            channel_names: Optional sequence of channel names to include. If None, all channels.

        Returns:
            New LogFile with channels filtered to the time range.

        Example:
            >>> log = aim_xrk('session.xrk')
            >>> segment = log.filter_by_time_range(60000, 120000)
            >>> print(segment.channels['Engine RPM'].num_rows)
        """
        source = self.select_channels(channel_names) if channel_names is not None else self

        new_channels = {}
        for name, channel_table in source.channels.items():
            timecodes = channel_table.column("timecodes")

            # Filter to [start_time, end_time)
            mask = pc.and_(
                pc.greater_equal(timecodes, start_time),
                pc.less(timecodes, end_time),
            )
            new_channels[name] = channel_table.filter(mask)

        # Filter laps to only those overlapping with the time range
        laps_start = self.laps.column("start_time")
        laps_end = self.laps.column("end_time")

        # A lap overlaps if: lap_start < end_time AND lap_end > start_time
        laps_mask = pc.and_(
            pc.less(laps_start, end_time),
            pc.greater(laps_end, start_time),
        )
        new_laps = self.laps.filter(laps_mask)

        return LogFile(
            channels=new_channels,
            laps=new_laps,
            metadata=self.metadata,
            file_name=self.file_name,
        )

    def filter_by_lap(
        self,
        lap_num: int,
        channel_names: Sequence[str] | None = None,
    ) -> "LogFile":
        """
        Filter channels to a specific lap's time range.

        Args:
            lap_num: The lap number to filter to.
            channel_names: Optional sequence of channel names to include. If None, all channels.

        Returns:
            New LogFile with channels filtered to the lap's time range.

        Raises:
            ValueError: If lap_num is not found in the laps table.

        Example:
            >>> log = aim_xrk('session.xrk')
            >>> lap5 = log.filter_by_lap(5, ['GPS Speed', 'Engine RPM'])
            >>> df = lap5.get_channels_as_table().to_pandas()
        """
        lap_nums = self.laps.column("num").to_pylist()
        if lap_num not in lap_nums:
            raise ValueError(f"Lap {lap_num} not found. Available laps: {lap_nums}")

        lap_idx = lap_nums.index(lap_num)
        start_time = self.laps.column("start_time")[lap_idx].as_py()
        end_time = self.laps.column("end_time")[lap_idx].as_py()

        return self.filter_by_time_range(int(start_time), int(end_time), channel_names)

    def resample_to_timecodes(
        self,
        timecodes: pa.Array,
        channel_names: Sequence[str] | None = None,
    ) -> "LogFile":
        """
        Resample all channels to a target timebase.

        For channels with interpolate="True" metadata, performs linear interpolation.
        For other channels, uses forward-fill then backward-fill for leading nulls.

        Args:
            timecodes: Target timecodes array (int64, milliseconds) to resample to.
            channel_names: Optional sequence of channel names to include. If None, all channels.

        Returns:
            New LogFile with all channels resampled to the target timecodes.

        Example:
            >>> log = aim_xrk('session.xrk')
            >>> target = pa.array(range(0, 100000, 100), type=pa.int64())
            >>> resampled = log.resample_to_timecodes(target)
        """
        source = self.select_channels(channel_names) if channel_names is not None else self

        target_timecodes_np = timecodes.to_numpy()
        new_channels = {}

        for name, channel_table in source.channels.items():
            field = channel_table.schema.field(name)
            channel_timecodes = channel_table.column("timecodes").to_numpy()
            channel_values = channel_table.column(name).to_numpy(zero_copy_only=False)

            # Check if we should interpolate
            should_interpolate = False
            if field.metadata:
                interpolate_value = field.metadata.get(b"interpolate", b"").decode("utf-8")
                should_interpolate = interpolate_value == "True"

            if should_interpolate:
                # Linear interpolation using numpy
                # np.interp handles extrapolation by extending edge values
                resampled_values = np.interp(
                    target_timecodes_np,
                    channel_timecodes,
                    channel_values,
                )
            else:
                # Forward-fill approach using searchsorted
                # Find the index of the largest timecode <= each target timecode
                indices = np.searchsorted(channel_timecodes, target_timecodes_np, side="right") - 1

                # Handle leading nulls: where target is before first source timecode
                leading_mask = indices < 0

                # Clamp indices to valid range
                indices = np.clip(indices, 0, len(channel_values) - 1)

                resampled_values = channel_values[indices]

                # For leading values (before first source timecode), backward fill
                if np.any(leading_mask):
                    resampled_values = resampled_values.copy()
                    resampled_values[leading_mask] = channel_values[0]

            # Build new channel table preserving metadata
            new_table = pa.table(
                {
                    "timecodes": timecodes,
                    name: pa.array(resampled_values, type=field.type),
                }
            )

            # Restore metadata
            if field.metadata:
                new_field = new_table.schema.field(name).with_metadata(field.metadata)
                new_schema = pa.schema([new_table.schema.field("timecodes"), new_field])
                new_table = new_table.cast(new_schema)

            new_channels[name] = new_table

        return LogFile(
            channels=new_channels,
            laps=self.laps,
            metadata=self.metadata,
            file_name=self.file_name,
        )

    def resample_to_channel(
        self,
        reference_channel: str,
        channel_names: Sequence[str] | None = None,
    ) -> "LogFile":
        """
        Resample all channels to match a reference channel's timebase.

        Args:
            reference_channel: Name of the channel whose timecodes will be used.
            channel_names: Optional sequence of channel names to include. If None, all channels.

        Returns:
            New LogFile with all channels resampled to the reference channel's timecodes.

        Raises:
            KeyError: If reference_channel is not found.

        Example:
            >>> log = aim_xrk('session.xrk')
            >>> aligned = log.resample_to_channel('GPS Speed')
            >>> df = aligned.get_channels_as_table().to_pandas()
        """
        if reference_channel not in self.channels:
            raise KeyError(f"Reference channel not found: {reference_channel}")

        ref_timecodes = self.channels[reference_channel].column("timecodes").combine_chunks()

        return self.resample_to_timecodes(ref_timecodes, channel_names)
