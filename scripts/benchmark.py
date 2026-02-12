#!/usr/bin/env python3
"""Benchmark Cython vs Rust parser performance.

Usage:
    poetry run python scripts/benchmark.py
"""

import gc
import os
import statistics
import sys
import time

# Ensure we can import both backends
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

TEST_DATA = os.path.join(os.path.dirname(__file__), "..", "tests", "test_data")

FILES = [
    (
        "SFJ (7.9 MB)",
        os.path.join(TEST_DATA, "SFJ", "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"),
    ),
    (
        "86 (40 MB)",
        os.path.join(TEST_DATA, "86", "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"),
    ),
]


def benchmark_backend(parse_fn, fname, warmup=1, iterations=10):
    """Run benchmark and return list of elapsed times in seconds."""
    # Warmup
    for _ in range(warmup):
        parse_fn(fname)

    times = []
    for _ in range(iterations):
        gc.collect()
        gc.disable()
        t0 = time.perf_counter()
        parse_fn(fname)
        t1 = time.perf_counter()
        gc.enable()
        times.append(t1 - t0)
    return times


def main():
    # Import backends
    try:
        from libxrk.aim_xrk import aim_xrk as cython_parse

        has_cython = True
    except ImportError:
        has_cython = False
        print("WARNING: Cython backend not available")

    try:
        from libxrk._aim_xrk_rs import aim_xrk as rust_parse

        has_rust = True
    except ImportError:
        has_rust = False
        print("WARNING: Rust backend not available")

    if not has_cython and not has_rust:
        print("ERROR: No backends available")
        sys.exit(1)

    iterations = 10
    print(f"Benchmarking {iterations} iterations per file per backend\n")
    print(
        f"{'File':<16} {'Backend':<10} {'Mean':>8} {'Median':>8} {'StdDev':>8} {'Min':>8} {'Max':>8}"
    )
    print("-" * 74)

    for label, fname in FILES:
        if not os.path.exists(fname):
            print(f"{label:<16} SKIPPED (file not found)")
            continue

        results = {}

        if has_cython:
            times = benchmark_backend(cython_parse, fname, iterations=iterations)
            results["Cython"] = times
            mean = statistics.mean(times)
            med = statistics.median(times)
            sd = statistics.stdev(times) if len(times) > 1 else 0
            print(
                f"{label:<16} {'Cython':<10} {mean:>7.3f}s {med:>7.3f}s {sd:>7.3f}s {min(times):>7.3f}s {max(times):>7.3f}s"
            )

        if has_rust:
            times = benchmark_backend(rust_parse, fname, iterations=iterations)
            results["Rust"] = times
            mean = statistics.mean(times)
            med = statistics.median(times)
            sd = statistics.stdev(times) if len(times) > 1 else 0
            print(
                f"{'':<16} {'Rust':<10} {mean:>7.3f}s {med:>7.3f}s {sd:>7.3f}s {min(times):>7.3f}s {max(times):>7.3f}s"
            )

        if has_cython and has_rust:
            speedup = statistics.median(results["Cython"]) / statistics.median(results["Rust"])
            print(f"{'':<16} {'Speedup':<10} {speedup:>7.2f}x")

        print()


if __name__ == "__main__":
    main()
