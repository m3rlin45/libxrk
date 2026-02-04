#!/usr/bin/env python
"""
Compare lap times between official AIM DLL and libxrk.

This script loads an XRK/XRZ file with both the official AIM DLL and libxrk,
then compares the lap timing data to identify any discrepancies.

Usage:
    python compare_laps.py path/to/file.xrk
    python compare_laps.py -v path/to/file.xrk  # verbose output
    python compare_laps.py --all                 # compare all test files

On Linux, run via Wine:
    wine64 python compare_laps.py path/to/file.xrk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).parent
TESTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from aim_dll_wrapper import AimDll, AimDllError  # noqa: E402


class LapTiming(NamedTuple):
    """Lap timing data."""

    lap_num: int
    start_ms: int
    end_ms: int
    duration_ms: int


class ComparisonResult(NamedTuple):
    """Result of comparing a single lap."""

    lap_num: int
    dll_start_ms: int
    dll_end_ms: int
    dll_duration_ms: int
    libxrk_start_ms: int
    libxrk_end_ms: int
    libxrk_duration_ms: int

    @property
    def start_diff_ms(self) -> int:
        return self.libxrk_start_ms - self.dll_start_ms

    @property
    def end_diff_ms(self) -> int:
        return self.libxrk_end_ms - self.dll_end_ms

    @property
    def duration_diff_ms(self) -> int:
        return self.libxrk_duration_ms - self.dll_duration_ms

    @property
    def matches(self) -> bool:
        # Allow 1ms tolerance for floating point conversion
        return (
            abs(self.start_diff_ms) <= 1
            and abs(self.end_diff_ms) <= 1
            and abs(self.duration_diff_ms) <= 1
        )


def get_dll_laps(dll: AimDll, idx: int) -> list[LapTiming]:
    """Extract lap timings from official DLL."""
    laps = []
    lap_count = dll.get_laps_count(idx)

    for lap_num in range(lap_count):
        start_sec, duration_sec = dll.get_lap_info(idx, lap_num)
        start_ms = int(round(start_sec * 1000))
        duration_ms = int(round(duration_sec * 1000))
        end_ms = start_ms + duration_ms
        laps.append(LapTiming(lap_num, start_ms, end_ms, duration_ms))

    return laps


def get_libxrk_laps(file_path: Path) -> list[LapTiming]:
    """Extract lap timings from libxrk."""
    from libxrk import aim_xrk

    log = aim_xrk(str(file_path))
    laps = []

    # laps is a PyArrow table with columns: num, start_time, end_time
    lap_nums = log.laps.column("num").to_pylist()
    start_times = log.laps.column("start_time").to_pylist()
    end_times = log.laps.column("end_time").to_pylist()

    for lap_num, start_time, end_time in zip(lap_nums, start_times, end_times):
        duration = end_time - start_time
        laps.append(LapTiming(lap_num, start_time, end_time, duration))

    return laps


def compare_laps(
    dll_laps: list[LapTiming], libxrk_laps: list[LapTiming]
) -> tuple[list[ComparisonResult], list[str]]:
    """
    Compare lap timings between DLL and libxrk.

    Returns:
        Tuple of (comparison_results, error_messages)
    """
    results = []
    errors = []

    # Check lap count
    if len(dll_laps) != len(libxrk_laps):
        errors.append(f"Lap count mismatch: DLL={len(dll_laps)}, libxrk={len(libxrk_laps)}")

    # Compare each lap (up to the minimum count)
    for dll_lap, lib_lap in zip(dll_laps, libxrk_laps):
        if dll_lap.lap_num != lib_lap.lap_num:
            errors.append(f"Lap number mismatch: DLL={dll_lap.lap_num}, libxrk={lib_lap.lap_num}")

        results.append(
            ComparisonResult(
                lap_num=dll_lap.lap_num,
                dll_start_ms=dll_lap.start_ms,
                dll_end_ms=dll_lap.end_ms,
                dll_duration_ms=dll_lap.duration_ms,
                libxrk_start_ms=lib_lap.start_ms,
                libxrk_end_ms=lib_lap.end_ms,
                libxrk_duration_ms=lib_lap.duration_ms,
            )
        )

    # Report extra laps
    if len(dll_laps) > len(libxrk_laps):
        for lap in dll_laps[len(libxrk_laps) :]:
            errors.append(f"Extra lap in DLL: lap {lap.lap_num}")
    elif len(libxrk_laps) > len(dll_laps):
        for lap in libxrk_laps[len(dll_laps) :]:
            errors.append(f"Extra lap in libxrk: lap {lap.lap_num}")

    return results, errors


def format_time_ms(ms: int) -> str:
    """Format milliseconds as MM:SS.mmm."""
    minutes = ms // 60000
    seconds = (ms % 60000) / 1000
    return f"{minutes:02d}:{seconds:06.3f}"


def print_comparison_table(results: list[ComparisonResult], verbose: bool = False) -> None:
    """Print comparison results as a table."""
    if not results:
        print("No laps to compare")
        return

    # Header
    print()
    print("=" * 100)
    print(
        f"{'Lap':>4}  {'DLL Start':>12}  {'libxrk Start':>12}  {'Δ Start':>8}  "
        f"{'DLL Dur':>10}  {'libxrk Dur':>10}  {'Δ Dur':>8}  {'Status':>8}"
    )
    print("=" * 100)

    # Data rows
    mismatches = 0
    for r in results:
        status = "OK" if r.matches else "MISMATCH"
        if not r.matches:
            mismatches += 1

        # Format differences with sign
        start_diff = f"{r.start_diff_ms:+d}ms" if r.start_diff_ms != 0 else "0"
        dur_diff = f"{r.duration_diff_ms:+d}ms" if r.duration_diff_ms != 0 else "0"

        print(
            f"{r.lap_num:>4}  "
            f"{format_time_ms(r.dll_start_ms):>12}  "
            f"{format_time_ms(r.libxrk_start_ms):>12}  "
            f"{start_diff:>8}  "
            f"{r.dll_duration_ms:>10}ms  "
            f"{r.libxrk_duration_ms:>10}ms  "
            f"{dur_diff:>8}  "
            f"{status:>8}"
        )

    print("=" * 100)

    # Summary
    total = len(results)
    matched = total - mismatches
    print(f"\nSummary: {matched}/{total} laps match ({mismatches} mismatches)")


def print_detailed_diff(results: list[ComparisonResult]) -> None:
    """Print detailed diff for mismatched laps."""
    mismatches = [r for r in results if not r.matches]
    if not mismatches:
        return

    print("\n" + "=" * 60)
    print("DETAILED MISMATCHES")
    print("=" * 60)

    for r in mismatches:
        print(f"\nLap {r.lap_num}:")
        print(f"  Start time:")
        print(f"    DLL:    {r.dll_start_ms}ms ({format_time_ms(r.dll_start_ms)})")
        print(f"    libxrk: {r.libxrk_start_ms}ms ({format_time_ms(r.libxrk_start_ms)})")
        print(f"    Diff:   {r.start_diff_ms:+d}ms")
        print(f"  End time:")
        print(f"    DLL:    {r.dll_end_ms}ms ({format_time_ms(r.dll_end_ms)})")
        print(f"    libxrk: {r.libxrk_end_ms}ms ({format_time_ms(r.libxrk_end_ms)})")
        print(f"    Diff:   {r.end_diff_ms:+d}ms")
        print(f"  Duration:")
        print(f"    DLL:    {r.dll_duration_ms}ms ({r.dll_duration_ms / 1000:.3f}s)")
        print(f"    libxrk: {r.libxrk_duration_ms}ms ({r.libxrk_duration_ms / 1000:.3f}s)")
        print(f"    Diff:   {r.duration_diff_ms:+d}ms")


def compare_file(file_path: Path, dll: AimDll, verbose: bool = False) -> bool:
    """
    Compare a single file.

    Returns:
        True if all laps match, False otherwise.
    """
    print(f"\n{'=' * 60}")
    print(f"File: {file_path}")
    print("=" * 60)

    try:
        # Get DLL laps
        idx = dll.open_file(file_path)
        try:
            dll_laps = get_dll_laps(dll, idx)
            print(f"DLL: {len(dll_laps)} laps")
        finally:
            dll.close_file(idx)

        # Get libxrk laps
        libxrk_laps = get_libxrk_laps(file_path)
        print(f"libxrk: {len(libxrk_laps)} laps")

        # Compare
        results, errors = compare_laps(dll_laps, libxrk_laps)

        # Print errors
        for error in errors:
            print(f"ERROR: {error}")

        # Print comparison table
        print_comparison_table(results, verbose)

        # Print detailed diff if verbose
        if verbose:
            print_detailed_diff(results)

        # Return success status
        all_match = all(r.matches for r in results) and not errors
        return all_match

    except AimDllError as e:
        print(f"DLL Error: {e}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


def find_test_files() -> list[Path]:
    """Find all XRK/XRZ test files."""
    test_data = TESTS_DIR / "test_data"
    if not test_data.exists():
        return []

    files: list[Path] = []
    for pattern in ["**/*.xrk", "**/*.xrz", "**/*.XRK", "**/*.XRZ"]:
        files.extend(test_data.glob(pattern))

    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare lap times between official AIM DLL and libxrk"
    )
    parser.add_argument("files", nargs="*", help="XRK/XRZ files to compare")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed diffs")
    parser.add_argument("--all", action="store_true", help="Compare all test files")
    parser.add_argument(
        "--dll",
        type=Path,
        default=None,
        help="Path to DLL (default: look in script directory)",
    )

    args = parser.parse_args()

    # Determine files to compare
    if args.all:
        files = find_test_files()
        if not files:
            print("No test files found in tests/test_data/")
            return 1
        print(f"Found {len(files)} test files")
    elif args.files:
        files = [Path(f) for f in args.files]
    else:
        parser.print_help()
        return 1

    # Verify files exist
    for f in files:
        if not f.exists():
            print(f"File not found: {f}")
            return 1

    # Load DLL
    try:
        dll = AimDll(args.dll)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nPlease download the DLL from:")
        print("  https://www.aim-sportline.com/aim-software-betas/DLL/")
        print(f"\nAnd place it in: {SCRIPT_DIR}/")
        return 1

    # Compare files
    all_passed = True
    with dll:
        for file_path in files:
            passed = compare_file(file_path, dll, args.verbose)
            if not passed:
                all_passed = False

    # Final summary
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL FILES MATCH")
        return 0
    else:
        print("SOME FILES HAVE MISMATCHES")
        return 1


if __name__ == "__main__":
    sys.exit(main())
