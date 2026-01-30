#!/usr/bin/env python3
"""Profile libxrk operations to identify performance bottlenecks.

This script profiles the main operations in libxrk:
- aim_xrk() - File parsing (includes Cython binary parsing)
- get_channels_as_table() - Channel merging with resampling
- resample_to_channel() - Resampling to reference timebase
- filter_by_lap() - Lap filtering

Usage:
    poetry run python scripts/profile_libxrk.py [--detailed]

The --detailed flag enables per-line profiling for specific functions.
"""

import argparse
import cProfile
import io
import os
import pstats
import sys
import time
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from libxrk import aim_xrk


# Test files with different sizes
TEST_FILES = {
    "small": "tests/test_data/aim_official/test.xrk",
    "medium": "tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk",
    "large": "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk",
}


def get_file_size_mb(path: str) -> float:
    """Get file size in MB."""
    return os.path.getsize(path) / (1024 * 1024)


def profile_operation(name: str, func, *args, **kwargs):
    """Profile a single operation and return timing + profile stats."""
    # Warm-up run (to avoid cold cache effects)
    try:
        func(*args, **kwargs)
    except Exception:
        pass  # Ignore errors in warmup

    # Timed run
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start

    # Profiled run
    profiler = cProfile.Profile()
    profiler.enable()
    func(*args, **kwargs)
    profiler.disable()

    return result, elapsed, profiler


def format_profile_stats(profiler: cProfile.Profile, top_n: int = 20) -> str:
    """Format profile stats as a string."""
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.strip_dirs()
    stats.sort_stats("cumulative")
    stats.print_stats(top_n)
    return stream.getvalue()


def profile_file(file_path: str, file_label: str) -> dict:
    """Profile all operations on a single file."""
    results = {}
    file_size = get_file_size_mb(file_path)

    print(f"\n{'=' * 70}")
    print(f"FILE: {file_label} ({file_size:.1f} MB)")
    print(f"Path: {file_path}")
    print("=" * 70)

    # 1. Profile aim_xrk() - File parsing
    print("\n--- aim_xrk() (File Parsing) ---")
    log, elapsed, profiler = profile_operation("aim_xrk", aim_xrk, file_path)
    results["aim_xrk"] = {"time": elapsed, "profiler": profiler}
    print(f"Time: {elapsed:.3f}s")
    print(f"Channels: {len(log.channels)}")
    print(f"Laps: {len(log.laps)}")
    total_samples = sum(t.num_rows for t in log.channels.values())
    print(f"Total samples: {total_samples:,}")

    # 2. Profile get_channels_as_table() - Channel merging
    print("\n--- get_channels_as_table() (Channel Merging) ---")
    _, elapsed, profiler = profile_operation("get_channels_as_table", log.get_channels_as_table)
    results["get_channels_as_table"] = {"time": elapsed, "profiler": profiler}
    print(f"Time: {elapsed:.3f}s")

    # 3. Profile resample_to_channel() - Resampling
    # Find a good reference channel (GPS Speed is common at 25Hz)
    ref_channel = None
    for name in ["GPS Speed", "Engine RPM"]:
        if name in log.channels:
            ref_channel = name
            break

    if ref_channel:
        print(f"\n--- resample_to_channel('{ref_channel}') ---")
        ref_samples = log.channels[ref_channel].num_rows
        print(f"Reference channel samples: {ref_samples:,}")
        _, elapsed, profiler = profile_operation(
            "resample_to_channel", log.resample_to_channel, ref_channel
        )
        results["resample_to_channel"] = {"time": elapsed, "profiler": profiler}
        print(f"Time: {elapsed:.3f}s")
    else:
        print("\n--- resample_to_channel() SKIPPED (no suitable reference channel) ---")

    # 4. Profile filter_by_lap() - Lap filtering
    if len(log.laps) > 0:
        lap_nums = log.laps.column("num").to_pylist()
        # Pick a middle lap if available
        test_lap = lap_nums[len(lap_nums) // 2] if lap_nums else lap_nums[0]
        print(f"\n--- filter_by_lap({test_lap}) ---")
        _, elapsed, profiler = profile_operation("filter_by_lap", log.filter_by_lap, test_lap)
        results["filter_by_lap"] = {"time": elapsed, "profiler": profiler}
        print(f"Time: {elapsed:.3f}s")
    else:
        print("\n--- filter_by_lap() SKIPPED (no laps in file) ---")

    return results


def print_detailed_profiles(all_results: dict, top_n: int = 20):
    """Print detailed profile stats for each operation."""
    print("\n" + "=" * 70)
    print("DETAILED PROFILE STATS (Top {} by cumulative time)".format(top_n))
    print("=" * 70)

    for file_label, results in all_results.items():
        print(f"\n{'#' * 70}")
        print(f"# FILE: {file_label}")
        print("#" * 70)

        for op_name, data in results.items():
            print(f"\n--- {op_name} ---")
            print(format_profile_stats(data["profiler"], top_n))


def print_summary_table(all_results: dict):
    """Print a summary table of all timings."""
    print("\n" + "=" * 70)
    print("SUMMARY TABLE (times in seconds)")
    print("=" * 70)

    # Get all operation names
    all_ops = set()
    for results in all_results.values():
        all_ops.update(results.keys())
    ops = sorted(all_ops)

    # Header
    header = f"{'Operation':<25}"
    for file_label in all_results.keys():
        header += f"{file_label:>12}"
    print(header)
    print("-" * len(header))

    # Rows
    for op in ops:
        row = f"{op:<25}"
        for file_label, results in all_results.items():
            if op in results:
                row += f"{results[op]['time']:>12.3f}"
            else:
                row += f"{'N/A':>12}"
        print(row)


def analyze_bottlenecks(all_results: dict):
    """Analyze and highlight the main bottlenecks."""
    print("\n" + "=" * 70)
    print("BOTTLENECK ANALYSIS")
    print("=" * 70)

    for file_label, results in all_results.items():
        print(f"\n--- {file_label} ---")

        # Sort operations by time
        sorted_ops = sorted(results.items(), key=lambda x: x[1]["time"], reverse=True)

        total_time = sum(r["time"] for r in results.values())
        print(f"Total profiled time: {total_time:.3f}s")

        for op_name, data in sorted_ops:
            pct = (data["time"] / total_time) * 100 if total_time > 0 else 0
            print(f"  {op_name}: {data['time']:.3f}s ({pct:.1f}%)")

            # Show top 5 functions for this operation
            stream = io.StringIO()
            stats = pstats.Stats(data["profiler"], stream=stream)
            stats.strip_dirs()
            stats.sort_stats("cumulative")

            # Get top functions
            print("    Top functions:")
            func_list = []
            for func, (cc, nc, tt, ct, callers) in stats.stats.items():  # type: ignore[attr-defined]
                func_list.append((func, ct))
            func_list.sort(key=lambda x: x[1], reverse=True)

            for func, ct in func_list[:5]:
                filename, lineno, funcname = func
                # Skip built-in and standard library functions
                if funcname.startswith("<") or "site-packages" in filename:
                    continue
                print(f"      {funcname} ({filename}:{lineno}): {ct:.3f}s")


def main():
    parser = argparse.ArgumentParser(description="Profile libxrk operations")
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show detailed profile stats for each operation",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top functions to show in detailed output (default: 20)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        choices=list(TEST_FILES.keys()),
        default=list(TEST_FILES.keys()),
        help="Which test files to profile (default: all)",
    )
    args = parser.parse_args()

    # Change to repo root
    repo_root = Path(__file__).parent.parent
    os.chdir(repo_root)

    # Check that test files exist
    for label in args.files:
        path = TEST_FILES[label]
        if not os.path.exists(path):
            print(f"WARNING: Test file not found: {path}")
            args.files.remove(label)

    if not args.files:
        print("ERROR: No test files available")
        sys.exit(1)

    print("libxrk Performance Profiling")
    print(f"Python: {sys.version}")
    print(f"Working directory: {os.getcwd()}")

    # Profile each file
    all_results = {}
    for label in args.files:
        path = TEST_FILES[label]
        all_results[label] = profile_file(path, label)

    # Print summary
    print_summary_table(all_results)

    # Print bottleneck analysis
    analyze_bottlenecks(all_results)

    # Print detailed profiles if requested
    if args.detailed:
        print_detailed_profiles(all_results, args.top)


if __name__ == "__main__":
    main()
