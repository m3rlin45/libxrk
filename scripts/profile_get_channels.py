#!/usr/bin/env python3
"""Focused profiling of get_channels_as_table() to understand the bottleneck."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import os

os.chdir(Path(__file__).parent.parent)

from libxrk import aim_xrk
import heapq
from itertools import groupby
import pyarrow as pa
import numpy as np


# Large file for profiling
TEST_FILE = "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"


def profile_timecode_extraction(log):
    """Profile different ways to extract timecodes from channels."""
    print("\n=== Timecode Extraction Methods ===")

    # Method 1: Current approach (to_pylist)
    start = time.perf_counter()
    for _ in range(3):
        timecode_iterators = [
            channel_table.column("timecodes").to_pylist() for channel_table in log.channels.values()
        ]
    elapsed1 = (time.perf_counter() - start) / 3
    print(f"to_pylist() x {len(log.channels)}: {elapsed1:.3f}s")

    # Method 2: to_numpy
    start = time.perf_counter()
    for _ in range(3):
        timecode_arrays = [
            channel_table.column("timecodes").to_numpy() for channel_table in log.channels.values()
        ]
    elapsed2 = (time.perf_counter() - start) / 3
    print(f"to_numpy() x {len(log.channels)}: {elapsed2:.3f}s")

    # Method 3: combine_chunks().to_numpy()
    start = time.perf_counter()
    for _ in range(3):
        timecode_arrays = [
            channel_table.column("timecodes").combine_chunks().to_numpy()
            for channel_table in log.channels.values()
        ]
    elapsed3 = (time.perf_counter() - start) / 3
    print(f"combine_chunks().to_numpy() x {len(log.channels)}: {elapsed3:.3f}s")

    return timecode_iterators, timecode_arrays


def profile_k_way_merge_approaches(log):
    """Profile different approaches to k-way merge."""
    print("\n=== K-way Merge Approaches ===")

    # Get numpy arrays for all timecodes
    timecode_arrays = [
        channel_table.column("timecodes").to_numpy() for channel_table in log.channels.values()
    ]

    # Also get lists for heapq
    timecode_lists = [arr.tolist() for arr in timecode_arrays]

    total_samples = sum(len(arr) for arr in timecode_arrays)
    print(f"Total samples to merge: {total_samples:,}")

    # Method 1: heapq.merge (current approach)
    start = time.perf_counter()
    merged = heapq.merge(*timecode_lists)
    unique_timecodes_1 = [k for k, _ in groupby(merged)]
    elapsed1 = time.perf_counter() - start
    print(f"heapq.merge + groupby: {elapsed1:.3f}s -> {len(unique_timecodes_1):,} unique")

    # Method 2: numpy concatenate + unique (sorted=True gives us sorted result)
    start = time.perf_counter()
    concatenated = np.concatenate(timecode_arrays)
    unique_timecodes_2 = np.unique(concatenated)
    elapsed2 = time.perf_counter() - start
    print(f"np.concatenate + np.unique: {elapsed2:.3f}s -> {len(unique_timecodes_2):,} unique")

    # Method 3: PyArrow approach
    start = time.perf_counter()
    chunks = [channel_table.column("timecodes") for channel_table in log.channels.values()]
    combined = pa.chunked_array(chunks)
    # PyArrow unique returns deduplicated but not sorted
    unique_arr = pa.compute.unique(combined)
    # Need to sort
    sorted_arr = pa.compute.sort_indices(unique_arr)
    unique_timecodes_3 = unique_arr.take(sorted_arr)
    elapsed3 = time.perf_counter() - start
    print(f"PyArrow unique + sort: {elapsed3:.3f}s -> {len(unique_timecodes_3):,} unique")

    # Method 4: numpy union1d reduce (uses sorting internally)
    start = time.perf_counter()
    from functools import reduce

    unique_timecodes_4 = reduce(np.union1d, timecode_arrays)
    elapsed4 = time.perf_counter() - start
    print(f"reduce(np.union1d): {elapsed4:.3f}s -> {len(unique_timecodes_4):,} unique")

    # Method 5: heapq.merge with numpy arrays directly (iter)
    start = time.perf_counter()
    merged = heapq.merge(*[iter(arr) for arr in timecode_arrays])
    unique_timecodes_5 = [k for k, _ in groupby(merged)]
    elapsed5 = time.perf_counter() - start
    print(f"heapq.merge(iter(numpy)): {elapsed5:.3f}s -> {len(unique_timecodes_5):,} unique")

    return unique_timecodes_2


def profile_full_get_channels_as_table(log):
    """Profile the full get_channels_as_table with detailed timing."""
    print("\n=== Full get_channels_as_table() Breakdown ===")

    # Step 1: Timecode extraction
    start = time.perf_counter()
    timecode_iterators = [
        channel_table.column("timecodes").to_pylist() for channel_table in log.channels.values()
    ]
    step1_time = time.perf_counter() - start
    print(f"1. to_pylist() extraction: {step1_time:.3f}s")

    # Step 2: K-way merge
    start = time.perf_counter()
    merged = heapq.merge(*timecode_iterators)
    unique_timecodes = [k for k, _ in groupby(merged)]
    step2_time = time.perf_counter() - start
    print(f"2. heapq.merge + groupby: {step2_time:.3f}s -> {len(unique_timecodes):,} unique")

    # Step 3: Convert to PyArrow array
    start = time.perf_counter()
    union_timecodes = pa.array(unique_timecodes, type=pa.int64())
    step3_time = time.perf_counter() - start
    print(f"3. pa.array creation: {step3_time:.3f}s")

    # Step 4: Resampling
    start = time.perf_counter()
    resampled = log.resample_to_timecodes(union_timecodes)
    step4_time = time.perf_counter() - start
    print(f"4. resample_to_timecodes: {step4_time:.3f}s")

    # Step 5: Build merged table
    start = time.perf_counter()
    channel_names = sorted(resampled.channels.keys())
    channel_metadata = {}
    for name in channel_names:
        field = resampled.channels[name].schema.field(name)
        if field.metadata:
            channel_metadata[name] = field.metadata
    columns_dict = {"timecodes": union_timecodes}
    for name in channel_names:
        columns_dict[name] = resampled.channels[name].column(name)
    result = pa.table(columns_dict)
    step5_time = time.perf_counter() - start
    print(f"5. Build result table: {step5_time:.3f}s")

    # Step 6: Restore metadata
    start = time.perf_counter()
    if channel_metadata:
        new_fields = []
        for field in result.schema:
            if field.name in channel_metadata:
                new_fields.append(field.with_metadata(channel_metadata[field.name]))
            else:
                new_fields.append(field)
        new_schema = pa.schema(new_fields)
        result = result.cast(new_schema)
    step6_time = time.perf_counter() - start
    print(f"6. Restore metadata: {step6_time:.3f}s")

    total = step1_time + step2_time + step3_time + step4_time + step5_time + step6_time
    print(f"\nTotal breakdown: {total:.3f}s")
    print(
        f"  Timecode merge (1+2+3): {step1_time + step2_time + step3_time:.3f}s ({(step1_time + step2_time + step3_time)/total*100:.1f}%)"
    )
    print(f"  Resampling (4): {step4_time:.3f}s ({step4_time/total*100:.1f}%)")
    print(
        f"  Table building (5+6): {step5_time + step6_time:.3f}s ({(step5_time + step6_time)/total*100:.1f}%)"
    )


def main():
    print("Loading test file...")
    log = aim_xrk(TEST_FILE)
    print(
        f"Loaded: {len(log.channels)} channels, {sum(t.num_rows for t in log.channels.values()):,} total samples"
    )

    # Run profiling
    profile_timecode_extraction(log)
    profile_k_way_merge_approaches(log)
    profile_full_get_channels_as_table(log)


if __name__ == "__main__":
    main()
