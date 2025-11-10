"""Test file to verify type checking works with aim_xrk stub file."""

from libxrk.data import AIMXRK, aim_track


def process_xrk_file(filename: str) -> None:
    """Process an XRK file and access its data."""
    # Test AIMXRK with type hints
    log = AIMXRK(filename, progress=None)
    
    # These should all type-check correctly
    channels = log.channels
    laps = log.laps
    metadata = log.metadata
    
    for channel_name, channel in channels.items():
        timecodes = channel.timecodes
        values = channel.values
        print(f"{channel_name}: {len(timecodes)} samples")
    
    for lap in laps:
        lap_num = lap.num
        start = lap.start_time
        end = lap.end_time
        print(f"Lap {lap_num}: {start} - {end}")


def process_track_data(filename: str) -> None:
    """Process track data from XRK file."""
    track_data = aim_track(filename)
    
    # Should be a dict
    for key, value in track_data.items():
        print(f"{key}: {value}")


# Test with progress callback
def my_progress(current: int, total: int) -> None:
    """Progress callback."""
    print(f"Progress: {current}/{total}")


def process_with_progress(filename: str) -> None:
    """Process file with progress callback."""
    log = AIMXRK(filename, progress=my_progress)
    print(f"Loaded {len(log.channels)} channels")


if __name__ == "__main__":
    # This would fail at runtime but should type-check fine
    print("Type checking example - not meant to be executed")
