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
    ]  # Each channel is a PyArrow table with columns: timecodes (int64), values (float/int)
    # Metadata stored in schema.field('values').metadata:
    # name, units, dec_pts, interpolate
    laps: pa.Table  # PyArrow table with columns: num (int), start_time (int), end_time (int)
    metadata: typing.Dict[str, str]
    key_channel_map: typing.List[typing.Optional[str]]  # speed, lat, long, alt
    file_name: str  # move to metadata?
