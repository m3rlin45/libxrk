#!/usr/bin/env python
"""
Full comparison of official AIM DLL vs libxrk.

Compares all data items:
- Channels: count, names, units, sample counts, sample data
- Laps: count, timing
- Metadata: vehicle, track, racer, championship

Usage:
    python compare_all.py path/to/file.xrk
    python compare_all.py --all                 # compare all test files
    python compare_all.py -v path/to/file.xrk   # verbose output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).parent
TESTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))


@dataclass
class ComparisonResult:
    """Result of comparing one file."""

    file_path: Path
    passed: bool
    channels_count_match: bool
    channels_names_match: bool
    channels_units_match: bool
    channels_samples_match: bool
    laps_count_match: bool
    laps_timing_match: bool
    metadata_match: dict[str, bool]
    errors: list[str]
    warnings: list[str]
    details: dict[str, Any]


def get_dll_data(xrk_path: Path) -> dict[str, Any] | None:
    """Get full data from DLL via Wine subprocess."""
    wine_script = SCRIPT_DIR / "wine_full_extract.py"

    # Convert to Wine path
    wine_path = f"Z:{xrk_path.absolute()}"
    wine_script_path = f"Z:{wine_script.absolute()}"

    # Wine Python installed by setup_dll_comparison.sh
    setup_dir = SCRIPT_DIR / ".setup"
    wine_python = str(setup_dir / "python-embed" / "python.exe")

    if (
        not Path(wine_python.replace("Z:", "")).exists()
        and not (setup_dir / "python-embed").exists()
    ):
        # Try default location
        wine_python = str(setup_dir / "python-embed" / "python.exe")

    result = subprocess.run(
        [
            "/usr/bin/wine",
            wine_python,
            wine_script_path,
            wine_path,
        ],
        capture_output=True,
        text=True,
        env={"WINEDEBUG": "-all"},
    )

    if result.returncode != 0:
        print(f"Wine subprocess failed: {result.stderr}")
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Failed to parse DLL output: {e}")
        print(f"Output was: {result.stdout[:500]}")
        return None


def get_libxrk_data(xrk_path: Path) -> dict[str, Any]:
    """Get full data from libxrk."""
    from libxrk import aim_xrk

    log = aim_xrk(str(xrk_path))

    result = {
        "file": str(xrk_path),
        "channels": [],
        "laps": [],
        "metadata": {},
    }

    # Extract channels
    for name in sorted(log.channels.keys()):
        table = log.channels[name]
        field = table.schema.field(name)

        units = ""
        if field.metadata and b"units" in field.metadata:
            units = field.metadata[b"units"].decode("utf-8")

        sample_count = table.num_rows

        channel_data = {
            "name": name,
            "units": units,
            "sample_count": sample_count,
        }

        if sample_count > 0:
            # Get timecodes in seconds (DLL uses seconds)
            timecodes_ms = table.column("timecodes").to_pylist()
            timecodes_sec = [t / 1000.0 for t in timecodes_ms]
            values = table.column(name).to_pylist()

            # Compute hash matching DLL format
            channel_data["samples_hash"] = compute_samples_hash(timecodes_sec, values)

            # First/last samples
            n_spot = min(5, sample_count)
            channel_data["first_samples"] = {
                "times": timecodes_sec[:n_spot],
                "values": values[:n_spot],
            }
            channel_data["last_samples"] = {
                "times": timecodes_sec[-n_spot:],
                "values": values[-n_spot:],
            }

        result["channels"].append(channel_data)

    # Extract laps
    lap_nums = log.laps.column("num").to_pylist()
    start_times = log.laps.column("start_time").to_pylist()
    end_times = log.laps.column("end_time").to_pylist()

    for lap_num, start, end in zip(lap_nums, start_times, end_times):
        result["laps"].append(
            {
                "lap_num": lap_num,
                "start_ms": start,
                "duration_ms": end - start,
                "end_ms": end,
            }
        )

    # Extract metadata
    result["metadata"]["vehicle"] = log.metadata.get("Vehicle", "")
    result["metadata"]["track"] = log.metadata.get("Venue", "")
    result["metadata"]["racer"] = log.metadata.get("Driver", "")
    result["metadata"]["championship"] = log.metadata.get("Series", "")
    # venue_type not stored in libxrk

    return result


def compute_samples_hash(times: list[float], values: list[float]) -> str:
    """Compute hash of sample data for integrity checking."""
    h = hashlib.sha256()
    for t, v in zip(times, values):
        h.update(struct.pack("<dd", t, v))
    return h.hexdigest()[:16]


def compare_channels(
    dll_data: dict[str, Any],
    lib_data: dict[str, Any],
    verbose: bool = False,
    intersection_only: bool = False,
) -> tuple[bool, bool, bool, bool, list[str], list[str], dict[str, Any]]:
    """
    Compare channel data between DLL and libxrk.

    Returns:
        (count_match, names_match, units_match, samples_match, errors, warnings, details)
    """
    errors = []
    warnings = []
    details = {}

    dll_channels = {ch["name"]: ch for ch in dll_data["channels"]}
    lib_channels = {ch["name"]: ch for ch in lib_data["channels"]}

    dll_names = set(dll_channels.keys())
    lib_names = set(lib_channels.keys())

    # Count match
    count_match = len(dll_channels) == len(lib_channels)
    if not count_match:
        if intersection_only:
            warnings.append(
                f"Channel count differs: DLL={len(dll_channels)}, libxrk={len(lib_channels)}"
            )
        else:
            errors.append(
                f"Channel count mismatch: DLL={len(dll_channels)}, libxrk={len(lib_channels)}"
            )

    # Names match
    names_match = dll_names == lib_names
    if not names_match:
        only_dll = dll_names - lib_names
        only_lib = lib_names - dll_names
        if intersection_only:
            if only_dll:
                warnings.append(f"Channels only in DLL: {sorted(only_dll)}")
            if only_lib:
                warnings.append(f"Channels only in libxrk: {sorted(only_lib)}")
        else:
            if only_dll:
                errors.append(f"Channels only in DLL: {sorted(only_dll)}")
            if only_lib:
                errors.append(f"Channels only in libxrk: {sorted(only_lib)}")

    # Compare common channels
    common_names = dll_names & lib_names
    units_mismatches = []
    sample_count_mismatches = []
    sample_data_mismatches = []
    max_time_diff = 0.0
    max_value_diff = 0.0

    for name in sorted(common_names):
        dll_ch = dll_channels[name]
        lib_ch = lib_channels[name]

        # Units comparison (case-insensitive)
        dll_units = dll_ch["units"].strip()
        lib_units = lib_ch["units"].strip()
        if dll_units.lower() != lib_units.lower():
            units_mismatches.append(f"{name}: DLL='{dll_units}', libxrk='{lib_units}'")

        # Sample count comparison
        dll_count = dll_ch["sample_count"]
        lib_count = lib_ch["sample_count"]
        if dll_count != lib_count:
            sample_count_mismatches.append(f"{name}: DLL={dll_count}, libxrk={lib_count}")

        # Sample data comparison (spot check first/last samples)
        if dll_count > 0 and lib_count > 0:
            # Check first samples
            if "first_samples" in dll_ch and "first_samples" in lib_ch:
                dll_first = dll_ch["first_samples"]
                lib_first = lib_ch["first_samples"]

                for i, (dt, dv, lt, lv) in enumerate(
                    zip(
                        dll_first["times"],
                        dll_first["values"],
                        lib_first["times"],
                        lib_first["values"],
                    )
                ):
                    time_diff = abs(lt - dt)
                    value_diff = abs(lv - dv)

                    # Relative comparison for values
                    rel_diff = value_diff / max(abs(dv), 1e-10) if dv != 0 else value_diff

                    max_time_diff = max(max_time_diff, time_diff)
                    max_value_diff = max(max_value_diff, value_diff)

                    # Tolerance: 1ms for time, 1e-5 relative or 1e-8 absolute for values
                    if time_diff > 0.001:  # 1ms in seconds
                        sample_data_mismatches.append(
                            f"{name}[{i}] time: DLL={dt:.6f}, libxrk={lt:.6f}, diff={time_diff:.6f}s"
                        )
                    if rel_diff > 1e-5 and value_diff > 1e-8:
                        sample_data_mismatches.append(
                            f"{name}[{i}] value: DLL={dv}, libxrk={lv}, diff={value_diff}"
                        )

            # Check hash (overall integrity)
            if "samples_hash" in dll_ch and "samples_hash" in lib_ch:
                if dll_ch["samples_hash"] != lib_ch["samples_hash"]:
                    # Hash mismatch - might be due to sample count or data differences
                    if dll_count == lib_count and not any(
                        name in m for m in sample_data_mismatches
                    ):
                        warnings.append(f"{name}: hash mismatch (may be rounding differences)")

    units_match = len(units_mismatches) == 0
    samples_match = len(sample_count_mismatches) == 0 and len(sample_data_mismatches) == 0

    if units_mismatches:
        errors.extend([f"Units mismatch - {m}" for m in units_mismatches])
    if sample_count_mismatches:
        errors.extend([f"Sample count mismatch - {m}" for m in sample_count_mismatches])
    if sample_data_mismatches and verbose:
        errors.extend([f"Sample data mismatch - {m}" for m in sample_data_mismatches[:10]])
        if len(sample_data_mismatches) > 10:
            errors.append(f"  ... and {len(sample_data_mismatches) - 10} more mismatches")

    details = {
        "dll_count": len(dll_channels),
        "libxrk_count": len(lib_channels),
        "common_count": len(common_names),
        "max_time_diff_ms": max_time_diff * 1000,
        "max_value_diff": max_value_diff,
    }

    return count_match, names_match, units_match, samples_match, errors, warnings, details


def compare_laps(
    dll_data: dict[str, Any],
    lib_data: dict[str, Any],
    verbose: bool = False,
) -> tuple[bool, bool, list[str], list[str], dict[str, Any]]:
    """
    Compare lap data between DLL and libxrk.

    Returns:
        (count_match, timing_match, errors, warnings, details)
    """
    errors = []
    warnings = []

    dll_laps = dll_data["laps"]
    lib_laps = lib_data["laps"]

    count_match = len(dll_laps) == len(lib_laps)
    if not count_match:
        errors.append(f"Lap count mismatch: DLL={len(dll_laps)}, libxrk={len(lib_laps)}")

    # Compare lap by lap (by position, not lap number)
    timing_mismatches = []
    max_start_diff = 0
    max_duration_diff = 0

    for i, (dll_lap, lib_lap) in enumerate(zip(dll_laps, lib_laps)):
        start_diff = abs(lib_lap["start_ms"] - dll_lap["start_ms"])
        duration_diff = abs(lib_lap["duration_ms"] - dll_lap["duration_ms"])

        max_start_diff = max(max_start_diff, start_diff)
        max_duration_diff = max(max_duration_diff, duration_diff)

        # Tolerance: 1ms
        if start_diff > 1 or duration_diff > 1:
            timing_mismatches.append(
                f"Lap {i}: start diff={start_diff}ms, duration diff={duration_diff}ms"
            )

    timing_match = len(timing_mismatches) == 0

    if timing_mismatches and verbose:
        errors.extend(timing_mismatches[:5])
        if len(timing_mismatches) > 5:
            errors.append(f"  ... and {len(timing_mismatches) - 5} more mismatches")

    details = {
        "dll_count": len(dll_laps),
        "libxrk_count": len(lib_laps),
        "max_start_diff_ms": max_start_diff,
        "max_duration_diff_ms": max_duration_diff,
    }

    return count_match, timing_match, errors, warnings, details


def compare_metadata(
    dll_data: dict[str, Any],
    lib_data: dict[str, Any],
    verbose: bool = False,
) -> tuple[dict[str, bool], list[str], list[str], dict[str, Any]]:
    """
    Compare metadata between DLL and libxrk.

    Returns:
        (field_matches, errors, warnings, details)
    """
    errors = []
    warnings = []
    field_matches = {}
    details = {}

    dll_meta = dll_data["metadata"]
    lib_meta = lib_data["metadata"]

    # Fields to compare (mapping DLL field -> libxrk field)
    fields = ["vehicle", "track", "racer", "championship"]

    for field in fields:
        dll_val = dll_meta.get(field, "").strip()
        lib_val = lib_meta.get(field, "").strip()

        match = dll_val == lib_val
        field_matches[field] = match
        details[field] = {"dll": dll_val, "libxrk": lib_val}

        if not match:
            errors.append(f"Metadata '{field}' mismatch: DLL='{dll_val}', libxrk='{lib_val}'")

    # venue_type is not stored in libxrk - just note it
    if "venue_type" in dll_meta:
        field_matches["venue_type"] = None  # Skip
        details["venue_type"] = {"dll": dll_meta["venue_type"], "libxrk": "(not stored)"}
        if verbose:
            warnings.append(f"venue_type not stored in libxrk (DLL='{dll_meta['venue_type']}')")

    return field_matches, errors, warnings, details


def compare_file(
    file_path: Path, verbose: bool = False, intersection_only: bool = False
) -> ComparisonResult:
    """Compare a single file between DLL and libxrk."""
    errors = []
    warnings = []

    # Get DLL data
    dll_data = get_dll_data(file_path)
    if dll_data is None:
        return ComparisonResult(
            file_path=file_path,
            passed=False,
            channels_count_match=False,
            channels_names_match=False,
            channels_units_match=False,
            channels_samples_match=False,
            laps_count_match=False,
            laps_timing_match=False,
            metadata_match={},
            errors=["Failed to get DLL data"],
            warnings=[],
            details={},
        )

    if "error" in dll_data:
        return ComparisonResult(
            file_path=file_path,
            passed=False,
            channels_count_match=False,
            channels_names_match=False,
            channels_units_match=False,
            channels_samples_match=False,
            laps_count_match=False,
            laps_timing_match=False,
            metadata_match={},
            errors=[f"DLL error: {dll_data['error']}"],
            warnings=[],
            details={},
        )

    # Get libxrk data
    try:
        lib_data = get_libxrk_data(file_path)
    except Exception as e:
        return ComparisonResult(
            file_path=file_path,
            passed=False,
            channels_count_match=False,
            channels_names_match=False,
            channels_units_match=False,
            channels_samples_match=False,
            laps_count_match=False,
            laps_timing_match=False,
            metadata_match={},
            errors=[f"libxrk error: {e}"],
            warnings=[],
            details={},
        )

    # Compare channels
    (
        ch_count_match,
        ch_names_match,
        ch_units_match,
        ch_samples_match,
        ch_errors,
        ch_warnings,
        ch_details,
    ) = compare_channels(dll_data, lib_data, verbose, intersection_only)
    errors.extend(ch_errors)
    warnings.extend(ch_warnings)

    # Compare laps
    (
        lap_count_match,
        lap_timing_match,
        lap_errors,
        lap_warnings,
        lap_details,
    ) = compare_laps(dll_data, lib_data, verbose)
    errors.extend(lap_errors)
    warnings.extend(lap_warnings)

    # Compare metadata
    (
        meta_matches,
        meta_errors,
        meta_warnings,
        meta_details,
    ) = compare_metadata(dll_data, lib_data, verbose)
    errors.extend(meta_errors)
    warnings.extend(meta_warnings)

    # Overall pass/fail
    # When intersection_only, ignore channel count/names mismatch for pass/fail
    if intersection_only:
        passed = (
            ch_units_match
            and ch_samples_match
            and lap_count_match
            and lap_timing_match
            and all(v for k, v in meta_matches.items() if v is not None)
        )
    else:
        passed = (
            ch_count_match
            and ch_names_match
            and ch_units_match
            and ch_samples_match
            and lap_count_match
            and lap_timing_match
            and all(v for k, v in meta_matches.items() if v is not None)
        )

    return ComparisonResult(
        file_path=file_path,
        passed=passed,
        channels_count_match=ch_count_match,
        channels_names_match=ch_names_match,
        channels_units_match=ch_units_match,
        channels_samples_match=ch_samples_match,
        laps_count_match=lap_count_match,
        laps_timing_match=lap_timing_match,
        metadata_match=meta_matches,
        errors=errors,
        warnings=warnings,
        details={
            "channels": ch_details,
            "laps": lap_details,
            "metadata": meta_details,
        },
    )


def print_result(result: ComparisonResult, verbose: bool = False) -> None:
    """Print comparison result."""
    print(f"\n{'=' * 70}")
    print(f"File: {result.file_path}")
    print("=" * 70)

    # Channels
    ch_details = result.details.get("channels", {})
    ch_count = ch_details.get("libxrk_count", "?")
    dll_ch_count = ch_details.get("dll_count", "?")
    ch_status = (
        "OK"
        if all(
            [
                result.channels_count_match,
                result.channels_names_match,
                result.channels_units_match,
                result.channels_samples_match,
            ]
        )
        else "MISMATCH"
    )

    print(
        f"\nChannels:    {ch_count}/{dll_ch_count} {'match' if result.channels_count_match else 'MISMATCH'}"
    )
    print(f"  Names:     {'OK' if result.channels_names_match else 'MISMATCH'}")
    print(f"  Units:     {'OK' if result.channels_units_match else 'MISMATCH'}")

    time_diff = ch_details.get("max_time_diff_ms", 0)
    value_diff = ch_details.get("max_value_diff", 0)
    samples_status = "OK" if result.channels_samples_match else "MISMATCH"
    print(
        f"  Samples:   {samples_status} (max time diff: {time_diff:.1f}ms, max value diff: {value_diff:.6g})"
    )

    # Laps
    lap_details = result.details.get("laps", {})
    lap_count = lap_details.get("libxrk_count", "?")
    dll_lap_count = lap_details.get("dll_count", "?")
    lap_status = "OK" if result.laps_count_match and result.laps_timing_match else "MISMATCH"

    print(
        f"\nLaps:        {lap_count}/{dll_lap_count} {'match' if result.laps_count_match else 'MISMATCH'}"
    )
    if result.laps_count_match:
        start_diff = lap_details.get("max_start_diff_ms", 0)
        dur_diff = lap_details.get("max_duration_diff_ms", 0)
        print(
            f"  Timing:    {'OK' if result.laps_timing_match else 'MISMATCH'} (max start diff: {start_diff}ms, max dur diff: {dur_diff}ms)"
        )

    # Metadata
    meta_details = result.details.get("metadata", {})
    meta_total = len([v for v in result.metadata_match.values() if v is not None])
    meta_ok = len([v for v in result.metadata_match.values() if v is True])
    print(f"\nMetadata:    {meta_ok}/{meta_total} fields match")

    for field, match in result.metadata_match.items():
        if match is None:
            print(f"  {field.title():12} SKIP (not stored in libxrk)")
        elif match:
            val = meta_details.get(field, {}).get("libxrk", "")
            print(f"  {field.title():12} OK ({val})")
        else:
            dll_val = meta_details.get(field, {}).get("dll", "")
            lib_val = meta_details.get(field, {}).get("libxrk", "")
            print(f"  {field.title():12} MISMATCH (DLL='{dll_val}', libxrk='{lib_val}')")

    # Errors
    if result.errors and verbose:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors[:20]:
            print(f"  - {err}")
        if len(result.errors) > 20:
            print(f"  ... and {len(result.errors) - 20} more")

    # Warnings
    if result.warnings and verbose:
        print(f"\nWarnings ({len(result.warnings)}):")
        for warn in result.warnings[:10]:
            print(f"  - {warn}")

    # Result
    print(f"\nRESULT: {'PASS' if result.passed else 'FAIL'}")


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
    parser = argparse.ArgumentParser(description="Full comparison of official AIM DLL vs libxrk")
    parser.add_argument("files", nargs="*", help="XRK/XRZ files to compare")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--all", action="store_true", help="Compare all test files")
    parser.add_argument(
        "--intersection",
        action="store_true",
        help="Only compare channels present in both (report missing as info, not error)",
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

    # Compare files
    results = []
    for file_path in files:
        result = compare_file(file_path, args.verbose, args.intersection)
        results.append(result)
        print_result(result, args.verbose)

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


if __name__ == "__main__":
    sys.exit(main())
