"""Extract test data from 86 XRK file for test suite."""

from pathlib import Path
from libxrk import AIMXRK

# Load the test file
test_file = Path("tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk")
print(f"Loading: {test_file}")

log = AIMXRK(str(test_file), progress=None)

# Extract metadata
print("\n=== FILE-LEVEL METADATA ===")
print(f"Number of metadata entries: {len(log.metadata)}")
print("\nMetadata key-value pairs:")

for key, value in sorted(log.metadata.items()):
    print(f"  '{key}': {repr(value)}")

# Print in a format easy to copy to markdown
print("\n\n=== MARKDOWN FORMAT - METADATA ===")
print("| Key | Value |")
print("|-----|-------|")
for key, value in sorted(log.metadata.items()):
    # Handle empty strings
    display_value = value if value != "" else "(empty)"
    print(f"| {key} | {display_value} |")

# Print metadata for test
print("\n\n=== PYTHON TEST FORMAT - METADATA ===")
print("expected_metadata = {")
for key, value in sorted(log.metadata.items()):
    if isinstance(value, str):
        print(f'    "{key}": "{value}",')
    else:
        print(f'    "{key}": {repr(value)},')
print("}")

# Extract lap information
print("\n\n=== LAPS ===")
print(f"Number of laps: {len(log.laps)}")
print("\nLap details:")
lap_data = []
for i in range(len(log.laps)):
    lap_num = log.laps.column("num")[i].as_py()
    start_time = log.laps.column("start_time")[i].as_py()
    end_time = log.laps.column("end_time")[i].as_py()
    lap_data.append({"num": lap_num, "start_time": start_time, "end_time": end_time})
    duration = end_time - start_time
    print(f"  Lap {lap_num}: start={start_time:.3f}, end={end_time:.3f}, duration={duration:.3f}")

# Print lap data for test
print("\n\n=== PYTHON TEST FORMAT - LAPS ===")
print("expected_laps = [")
for lap in lap_data:
    print(f"    ({lap['num']}, {lap['start_time']}, {lap['end_time']}),")
print("]")

# Extract channel information
print("\n\n=== CHANNELS ===")
print(f"Number of channels: {len(log.channels)}")
print("\nChannel details:")

channel_data = []
for channel_name, channel_table in sorted(log.channels.items()):
    # Get the schema metadata
    schema = channel_table.schema
    data_field = schema.field(channel_name)
    metadata = data_field.metadata or {}

    # Decode metadata
    units = metadata.get(b"units", b"").decode("utf-8")
    dec_pts = metadata.get(b"dec_pts", b"").decode("utf-8")
    interpolate = metadata.get(b"interpolate", b"").decode("utf-8")

    # Get first and last values
    data_column = channel_table.column(channel_name)
    num_rows = len(data_column)
    first_value = data_column[0].as_py()
    last_value = data_column[-1].as_py()

    channel_info = {
        "name": channel_name,
        "num_rows": num_rows,
        "first_value": first_value,
        "last_value": last_value,
        "units": units,
        "dec_pts": dec_pts,
        "interpolate": interpolate,
    }
    channel_data.append(channel_info)

    print(f"\n  {channel_name}:")
    print(f"    Rows: {num_rows}")
    print(f"    First value: {first_value}")
    print(f"    Last value: {last_value}")
    print(f"    Units: '{units}'")
    print(f"    Decimal points: '{dec_pts}'")
    print(f"    Interpolation: '{interpolate}'")

# Print channel names as set for test
print("\n\n=== PYTHON TEST FORMAT - CHANNEL NAMES ===")
print("expected_channels = {")
for channel_info in channel_data:
    print(f'    "{channel_info["name"]}",')
print("}")

# Print row counts for test
print("\n\n=== PYTHON TEST FORMAT - CHANNEL ROW COUNTS ===")
print("expected_row_counts = {")
for channel_info in channel_data:
    print(f'    "{channel_info["name"]}": {channel_info["num_rows"]},')
print("}")
