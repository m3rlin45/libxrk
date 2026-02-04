#!/usr/bin/env python
"""
Print lap times from a XRK/XRZ file using libxrk.

This is a simple utility to inspect lap times without needing the official DLL.

Usage:
    poetry run python tests/reference_dll/print_laps.py path/to/file.xrk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).parent
TESTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from libxrk import aim_xrk


def format_time_ms(ms: int) -> str:
    """Format milliseconds as MM:SS.mmm."""
    minutes = ms // 60000
    seconds = (ms % 60000) / 1000
    return f"{minutes:02d}:{seconds:06.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print lap times from XRK/XRZ file using libxrk")
    parser.add_argument("file", type=Path, help="XRK/XRZ file to analyze")

    args = parser.parse_args()

    if not args.file.exists():
        print(f"File not found: {args.file}")
        return 1

    print(f"Loading: {args.file}")
    log = aim_xrk(str(args.file))

    # Print metadata
    print(f"\nMetadata:")
    for key, value in sorted(log.metadata.items()):
        print(f"  {key}: {value}")

    # Print laps
    lap_nums = log.laps.column("num").to_pylist()
    start_times = log.laps.column("start_time").to_pylist()
    end_times = log.laps.column("end_time").to_pylist()

    print(f"\nLaps ({len(lap_nums)} total):")
    print("-" * 80)
    print(f"{'Lap':>4}  {'Start':>12}  {'End':>12}  {'Duration':>12}  {'Duration (s)':>14}")
    print("-" * 80)

    for lap_num, start_time, end_time in zip(lap_nums, start_times, end_times):
        duration = end_time - start_time
        print(
            f"{lap_num:>4}  "
            f"{format_time_ms(start_time):>12}  "
            f"{format_time_ms(end_time):>12}  "
            f"{format_time_ms(duration):>12}  "
            f"{duration / 1000:>14.3f}"
        )

    print("-" * 80)

    # Print channel info
    print(f"\nChannels ({len(log.channels)} total):")
    for name in sorted(log.channels.keys()):
        table = log.channels[name]
        print(f"  {name}: {table.num_rows} samples")

    return 0


if __name__ == "__main__":
    sys.exit(main())
