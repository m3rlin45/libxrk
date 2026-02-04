#!/usr/bin/env python
"""
Full comparison of DLL vs libxrk.

This script supports two modes:
1. Lap-only comparison (default, legacy mode)
2. Full comparison (--full flag) - compares channels, laps, and metadata

Run from project root.

Usage:
    python full_comparison.py                  # Lap-only comparison (legacy)
    python full_comparison.py --full           # Full data comparison
    python full_comparison.py --full --all     # Full comparison of all test files
    python full_comparison.py --full -v        # Full comparison with verbose output
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TESTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from libxrk import aim_xrk


def get_dll_laps(xrk_path: Path) -> list[tuple[int, int, int, int]]:
    """Get lap times from DLL via Wine subprocess."""
    wine_script = SCRIPT_DIR / "wine_compare.py"
    wine_path = f"Z:{xrk_path.absolute()}"

    # Wine Python installed by setup_dll_comparison.sh
    setup_dir = SCRIPT_DIR / ".setup"
    wine_python = str(setup_dir / "python-embed" / "python.exe")

    result = subprocess.run(
        [
            "/usr/bin/wine",
            wine_python,
            f"Z:{wine_script.absolute()}",
            wine_path,
        ],
        capture_output=True,
        text=True,
        env={"WINEDEBUG": "-all"},
    )

    laps = []
    for line in result.stdout.split("\n"):
        line = line.strip()
        if line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 4:
                lap_num = int(parts[0])
                start_ms = int(parts[1])
                duration_ms = int(parts[2])
                end_ms = int(parts[3])
                laps.append((lap_num, start_ms, end_ms, duration_ms))

    return laps


def get_libxrk_laps(xrk_path: Path) -> list[tuple[int, int, int, int]]:
    """Get lap times from libxrk."""
    log = aim_xrk(str(xrk_path))
    laps = []

    lap_nums = log.laps.column("num").to_pylist()
    start_times = log.laps.column("start_time").to_pylist()
    end_times = log.laps.column("end_time").to_pylist()

    for lap_num, start, end in zip(lap_nums, start_times, end_times):
        laps.append((lap_num, start, end, end - start))

    return laps


def compare_file_laps(xrk_path: Path) -> bool:
    """Compare lap times for a single file. Returns True if match."""
    print(f"\n{'=' * 80}")
    print(f"File: {xrk_path}")
    print("=" * 80)

    dll_laps = get_dll_laps(xrk_path)
    libxrk_laps = get_libxrk_laps(xrk_path)

    print(f"\nDLL: {len(dll_laps)} laps, libxrk: {len(libxrk_laps)} laps")

    count_match = len(dll_laps) == len(libxrk_laps)
    if not count_match:
        print("*** LAP COUNT MISMATCH ***")

    # Header
    print(
        f"\n{'Lap':>4}  {'DLL Start':>10}  {'lib Start':>10}  {'Δ Start':>8}  "
        f"{'DLL Dur':>10}  {'lib Dur':>10}  {'Δ Dur':>8}"
    )
    print("-" * 80)

    # Compare lap by lap (aligned by position, not lap number)
    max_laps = max(len(dll_laps), len(libxrk_laps))
    all_timing_match = True

    for i in range(max_laps):
        dll = dll_laps[i] if i < len(dll_laps) else None
        lib = libxrk_laps[i] if i < len(libxrk_laps) else None

        if dll and lib:
            start_diff = lib[1] - dll[1]
            dur_diff = lib[3] - dll[3]
            print(
                f"{i:>4}  {dll[1]:>10}  {lib[1]:>10}  {start_diff:>+8}  "
                f"{dll[3]:>10}  {lib[3]:>10}  {dur_diff:>+8}"
            )
            # Allow 1ms tolerance
            if abs(start_diff) > 1 or abs(dur_diff) > 1:
                all_timing_match = False
        elif dll:
            print(
                f"{i:>4}  {dll[1]:>10}  {'(none)':>10}  {'---':>8}  {dll[3]:>10}  {'(none)':>10}  {'---':>8}"
            )
            all_timing_match = False
        elif lib:
            print(
                f"{i:>4}  {'(none)':>10}  {lib[1]:>10}  {'---':>8}  {'(none)':>10}  {lib[3]:>10}  {'---':>8}"
            )
            all_timing_match = False

    return count_match and all_timing_match


def run_full_comparison(files: list[Path], verbose: bool = False) -> int:
    """Run full comparison using compare_all.py."""
    from compare_all import compare_file, print_result

    results = []
    for file_path in files:
        result = compare_file(file_path, verbose)
        results.append(result)
        print_result(result, verbose)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    passed = sum(1 for r in results if r.passed)
    total = len(results)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.file_path.name}")

    print(f"\n{passed}/{total} files passed")

    if passed == total:
        print("\nALL FILES MATCH")
        return 0
    else:
        print("\nSOME FILES HAVE MISMATCHES")
        return 1


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
    parser = argparse.ArgumentParser(description="Compare DLL vs libxrk data")
    parser.add_argument("files", nargs="*", help="XRK/XRZ files to compare")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--all", action="store_true", help="Compare all test files")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full comparison (channels, laps, metadata). Default is lap-only comparison.",
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
        # Default test files for lap-only mode
        files = [
            Path("tests/test_data/aim_official/test.xrk"),
            Path("tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"),
            Path("tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"),
        ]
        files = [f for f in files if f.exists()]

    if not files:
        print("No files to compare")
        return 1

    # Verify files exist
    for f in files:
        if not f.exists():
            print(f"File not found: {f}")
            return 1

    if args.full:
        # Full comparison mode
        return run_full_comparison(files, args.verbose)
    else:
        # Legacy lap-only comparison mode
        all_passed = True
        for f in files:
            passed = compare_file_laps(f)
            if not passed:
                all_passed = False

        print("\n" + "=" * 80)
        if all_passed:
            print("ALL FILES MATCH (lap timing)")
            return 0
        else:
            print("SOME FILES HAVE MISMATCHES")
            return 1


if __name__ == "__main__":
    sys.exit(main())
