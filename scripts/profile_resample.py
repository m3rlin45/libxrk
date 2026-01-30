#!/usr/bin/env python3
"""Focused profiling of resample_to_channel() to understand its performance."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import os

os.chdir(Path(__file__).parent.parent)

from libxrk import aim_xrk
import numpy as np
import pyarrow as pa


TEST_FILE = "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"


def profile_resample_breakdown(log, ref_channel="GPS Speed"):
    """Profile resample_to_channel with detailed timing."""
    print(f"\n=== resample_to_channel('{ref_channel}') Breakdown ===")

    ref_timecodes = log.channels[ref_channel].column("timecodes").combine_chunks()
    target_timecodes_np = ref_timecodes.to_numpy()
    print(f"Target samples: {len(target_timecodes_np):,}")
    print(f"Source channels: {len(log.channels)}")

    # Count interpolated vs forward-fill channels
    interp_channels = []
    ffill_channels = []
    for name, channel_table in log.channels.items():
        field = channel_table.schema.field(name)
        should_interpolate = False
        if field.metadata:
            interpolate_value = field.metadata.get(b"interpolate", b"").decode("utf-8")
            should_interpolate = interpolate_value == "True"
        if should_interpolate:
            interp_channels.append(name)
        else:
            ffill_channels.append(name)

    print(f"Interpolated channels: {len(interp_channels)}")
    print(f"Forward-fill channels: {len(ffill_channels)}")

    # Time the full operation
    start = time.perf_counter()
    resampled = log.resample_to_channel(ref_channel)
    total_time = time.perf_counter() - start
    print(f"\nTotal time: {total_time:.4f}s")

    # Now break down the internals
    print("\n--- Internal Breakdown ---")

    # 1. to_numpy conversions
    start = time.perf_counter()
    for name, channel_table in log.channels.items():
        _ = channel_table.column("timecodes").to_numpy()
        _ = channel_table.column(name).to_numpy(zero_copy_only=False)
    numpy_time = time.perf_counter() - start
    print(f"to_numpy() conversions: {numpy_time:.4f}s ({numpy_time/total_time*100:.1f}%)")

    # 2. np.interp for interpolated channels
    start = time.perf_counter()
    for name in interp_channels:
        channel_table = log.channels[name]
        channel_timecodes = channel_table.column("timecodes").to_numpy()
        channel_values = channel_table.column(name).to_numpy(zero_copy_only=False)
        _ = np.interp(target_timecodes_np, channel_timecodes, channel_values)
    interp_time = time.perf_counter() - start
    print(
        f"np.interp ({len(interp_channels)} channels): {interp_time:.4f}s ({interp_time/total_time*100:.1f}%)"
    )

    # 3. searchsorted for forward-fill channels
    start = time.perf_counter()
    for name in ffill_channels:
        channel_table = log.channels[name]
        channel_timecodes = channel_table.column("timecodes").to_numpy()
        channel_values = channel_table.column(name).to_numpy(zero_copy_only=False)
        indices = np.searchsorted(channel_timecodes, target_timecodes_np, side="right") - 1
        indices = np.clip(indices, 0, len(channel_values) - 1)
        _ = channel_values[indices]
    ffill_time = time.perf_counter() - start
    print(
        f"searchsorted ({len(ffill_channels)} channels): {ffill_time:.4f}s ({ffill_time/total_time*100:.1f}%)"
    )

    # 4. PyArrow table creation (simulate with dummy resampled data)
    start = time.perf_counter()
    for name, channel_table in log.channels.items():
        field = channel_table.schema.field(name)
        # Create dummy resampled values of correct size
        dummy_values = np.zeros(len(target_timecodes_np), dtype=np.float64)
        new_table = pa.table(
            {
                "timecodes": ref_timecodes,
                name: pa.array(dummy_values),
            }
        )
        if field.metadata:
            new_field = new_table.schema.field(name).with_metadata(field.metadata)
            new_schema = pa.schema([new_table.schema.field("timecodes"), new_field])
            new_table = new_table.cast(new_schema)
    table_time = time.perf_counter() - start
    print(f"PyArrow table creation: {table_time:.4f}s ({table_time/total_time*100:.1f}%)")


def profile_different_reference_sizes(log):
    """Profile resampling with different target sizes."""
    print("\n=== Resampling to Different Target Sizes ===")

    # Find channels with different sample rates
    channels_by_size = {}
    for name, table in log.channels.items():
        size = table.num_rows
        if size not in channels_by_size:
            channels_by_size[size] = name

    # Sort by size
    sizes = sorted(channels_by_size.keys())

    print(f"{'Reference Channel':<30} {'Samples':>10} {'Time':>10}")
    print("-" * 52)

    for size in sizes[:10]:  # Top 10 different sizes
        ref_name = channels_by_size[size]
        start = time.perf_counter()
        _ = log.resample_to_channel(ref_name)
        elapsed = time.perf_counter() - start
        print(f"{ref_name:<30} {size:>10,} {elapsed:>10.4f}s")


def profile_batch_interp_potential(log, ref_channel="GPS Speed"):
    """Check if batching np.interp calls could help."""
    print("\n=== Batch Interpolation Analysis ===")

    ref_timecodes = log.channels[ref_channel].column("timecodes").to_numpy()

    # Get all interpolated channels
    interp_data = []
    for name, channel_table in log.channels.items():
        field = channel_table.schema.field(name)
        if field.metadata and field.metadata.get(b"interpolate", b"").decode("utf-8") == "True":
            tc = channel_table.column("timecodes").to_numpy()
            vals = channel_table.column(name).to_numpy(zero_copy_only=False)
            interp_data.append((name, tc, vals))

    print(f"Interpolated channels: {len(interp_data)}")

    # Current approach: individual np.interp calls
    start = time.perf_counter()
    for name, tc, vals in interp_data:
        _ = np.interp(ref_timecodes, tc, vals)
    individual_time = time.perf_counter() - start
    print(f"Individual np.interp: {individual_time:.4f}s")

    # Check if channels share timecodes (could batch them)
    unique_timecode_shapes: dict[tuple[int, int, int], list[str]] = {}
    for name, tc, vals in interp_data:
        key = (len(tc), tc[0] if len(tc) > 0 else 0, tc[-1] if len(tc) > 0 else 0)
        if key not in unique_timecode_shapes:
            unique_timecode_shapes[key] = []
        unique_timecode_shapes[key].append(name)

    print(f"\nUnique timecode patterns: {len(unique_timecode_shapes)}")
    for key, names in sorted(unique_timecode_shapes.items(), key=lambda x: -len(x[1]))[:5]:
        print(f"  {key[0]:,} samples ({key[1]}-{key[2]}): {len(names)} channels")


def main():
    print("Loading test file...")
    log = aim_xrk(TEST_FILE)
    print(
        f"Loaded: {len(log.channels)} channels, {sum(t.num_rows for t in log.channels.values()):,} total samples"
    )

    profile_resample_breakdown(log)
    profile_different_reference_sizes(log)
    profile_batch_interp_potential(log)


if __name__ == "__main__":
    main()
