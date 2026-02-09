#!/usr/bin/env python
"""
Exhaustive value-by-value comparison of official AIM DLL vs libxrk.

Compares every single sample value from the DLL against libxrk output.
Handles three channel categories:
  - Regular channels: exact sample-by-sample comparison
  - GPS derived channels: compared at matching timecodes (DLL interpolates to higher rate)
  - GPS raw channels: compared if libxrk exposes equivalent data

Usage:
    # Extract DLL data first (via Wine):
    WINEDEBUG=-all /usr/lib/wine/wine64 .setup/python-embed/python.exe \\
        Z:/path/to/wine_exhaustive_extract.py \\
        Z:/path/to/file.xrk Z:/tmp/output.bin

    # Then compare:
    poetry run python tests/reference_dll/exhaustive_comparison.py \\
        tests/test_data/SFJ/file.xrk /tmp/output.bin

    # Or extract + compare in one step:
    poetry run python tests/reference_dll/exhaustive_comparison.py \\
        tests/test_data/SFJ/file.xrk --extract

    # Compare all test files:
    poetry run python tests/reference_dll/exhaustive_comparison.py --all
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# Add parent directories to path for imports
SCRIPT_DIR = Path(__file__).parent
TESTS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TESTS_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

WINE_PYTHON = str(SCRIPT_DIR / ".setup" / "python-embed" / "python.exe")
WINE_BIN = "/usr/lib/wine/wine64"
EXTRACT_SCRIPT = str(SCRIPT_DIR / "wine_exhaustive_extract.py")


@dataclass
class DllChannel:
    """Channel data extracted from the DLL."""

    name: str
    units: str
    sample_count: int
    times: np.ndarray  # float64, seconds
    values: np.ndarray  # float64


@dataclass
class DllData:
    """All data extracted from the DLL."""

    regular_channels: list[DllChannel]
    gps_derived_channels: list[DllChannel]
    gps_raw_channels: list[DllChannel]
    laps: list[tuple[float, float]]  # (start_seconds, duration_seconds)
    metadata: dict[str, str]


@dataclass
class ChannelComparison:
    """Result of comparing one channel."""

    name: str
    dll_name: str
    category: str  # "regular", "gps_derived", "gps_raw"
    dll_units: str
    lib_units: str
    units_match: bool
    dll_samples: int
    lib_samples: int
    compared_samples: int  # number of samples actually compared
    max_time_diff_s: float
    max_value_diff: float
    max_rel_diff: float
    mismatched_samples: int  # number of samples exceeding tolerance
    first_mismatches: list[dict[str, Any]]  # first few mismatches for reporting
    passed: bool


@dataclass
class ComparisonReport:
    """Full comparison report for one file."""

    file_path: str
    channel_results: list[ChannelComparison] = field(default_factory=list)
    dll_only_channels: list[str] = field(default_factory=list)
    lib_only_channels: list[str] = field(default_factory=list)
    laps_match: bool = True
    lap_details: str = ""
    metadata_match: bool = True
    metadata_details: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def regular_results(self) -> list[ChannelComparison]:
        return [cr for cr in self.channel_results if cr.category == "regular"]

    @property
    def gps_results(self) -> list[ChannelComparison]:
        return [cr for cr in self.channel_results if cr.category != "regular"]

    @property
    def regular_passed(self) -> bool:
        """Did all regular (non-GPS) channels pass?"""
        return all(cr.passed for cr in self.regular_results)

    @property
    def passed(self) -> bool:
        """Overall pass requires regular channels + laps + metadata to match.
        GPS derived channels use different algorithms and are reported separately."""
        return self.regular_passed and self.laps_match and self.metadata_match and not self.errors


def read_dll_binary(path: Path) -> DllData:
    """Read the binary file produced by wine_exhaustive_extract.py."""
    with open(path, "rb") as f:
        # Header
        magic = f.read(4)
        if magic != b"XDLL":
            raise ValueError(f"Invalid magic: {magic!r}")
        (version,) = struct.unpack("<I", f.read(4))
        if version != 1:
            raise ValueError(f"Unsupported version: {version}")

        def read_str() -> str:
            (length,) = struct.unpack("<I", f.read(4))
            return f.read(length).decode("utf-8")

        def read_channel_section() -> list[DllChannel]:
            (num_channels,) = struct.unpack("<I", f.read(4))
            channels = []
            for _ in range(num_channels):
                name = read_str()
                units = read_str()
                (sample_count,) = struct.unpack("<I", f.read(4))
                if sample_count > 0:
                    times = np.frombuffer(f.read(sample_count * 8), dtype="<f8").copy()
                    values = np.frombuffer(f.read(sample_count * 8), dtype="<f8").copy()
                else:
                    times = np.array([], dtype=np.float64)
                    values = np.array([], dtype=np.float64)
                channels.append(DllChannel(name, units, sample_count, times, values))
            return channels

        regular = read_channel_section()
        gps_derived = read_channel_section()
        gps_raw = read_channel_section()

        # Laps
        (num_laps,) = struct.unpack("<I", f.read(4))
        laps = []
        for _ in range(num_laps):
            start, duration = struct.unpack("<dd", f.read(16))
            laps.append((start, duration))

        # Metadata
        (num_fields,) = struct.unpack("<I", f.read(4))
        metadata = {}
        for _ in range(num_fields):
            key = read_str()
            val = read_str()
            metadata[key] = val

    return DllData(regular, gps_derived, gps_raw, laps, metadata)


# Mapping from DLL GPS derived channel names to libxrk channel names
GPS_DERIVED_NAME_MAP = {
    "GPS Speed": "GPS Speed",
    "GPS Nsat": "GPS_Satellites",
    "GPS LatAcc": "GPS_LateralAcc",
    "GPS LonAcc": "GPS_InlineAcc",
    "GPS Gyro": "GPS_Yaw_Rate",
    "GPS Altitude": "GPS Altitude",
    "GPS PosAccuracy": "GPS_Position_Accuracy",
    "GPS SpdAccuracy": "GPS_Velocity_Accuracy",
    "GPS Latitude": "GPS Latitude",
    "GPS Longitude": "GPS Longitude",
    # These are not computed by libxrk:
    # "GPS Slope", "GPS Heading", "GPS Radius", "GPS East", "GPS North"
}

# Channels that the DLL lists as regular but libxrk computes from GPS
# (the DLL also returns these as GPS derived with different names)
GPS_COMPUTED_REGULAR = {"GPS_LateralAcc", "GPS_InlineAcc", "GPS_Yaw_Rate"}


def units_equivalent(a: str, b: str) -> bool:
    """Check if two unit strings are equivalent, handling known aliases."""
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return True
    # '#' and '' are both used for dimensionless quantities
    if {a, b} <= {"#", ""}:
        return True
    # lambda symbol encoding differences - DLL may use special chars for lambda
    lambda_aliases = {"lambda", "\xeb", "\u03bb", "\xbb", "\u00bb", "\u00eb"}
    if a in lambda_aliases and b in lambda_aliases:
        return True
    return False


def compare_channel_values(
    dll_ch: DllChannel,
    lib_times_s: np.ndarray,
    lib_values: np.ndarray,
    lib_units: str,
    category: str,
    lib_name: str,
    time_tol_s: float = 0.0005,  # 0.5ms
    value_rtol: float = 1e-4,
    value_atol: float = 1e-6,
) -> ChannelComparison:
    """Compare a DLL channel against libxrk channel data, value by value."""
    units_match = units_equivalent(dll_ch.units, lib_units)

    dll_times = dll_ch.times
    dll_values = dll_ch.values
    lib_times = lib_times_s
    lib_vals = lib_values

    # For channels with the same sample count, compare directly
    if len(dll_times) == len(lib_times):
        compared = len(dll_times)
        time_diffs = np.abs(dll_times - lib_times)
        max_time_diff = float(np.max(time_diffs)) if compared > 0 else 0.0

        value_diffs = np.abs(dll_values - lib_vals)
        max_value_diff = float(np.max(value_diffs)) if compared > 0 else 0.0

        # Relative difference (avoid divide by zero)
        with np.errstate(divide="ignore", invalid="ignore"):
            denom = np.maximum(np.abs(dll_values), value_atol)
            rel_diffs = value_diffs / denom
        max_rel_diff = float(np.max(rel_diffs)) if compared > 0 else 0.0

        # Find mismatches
        time_bad = time_diffs > time_tol_s
        value_bad = (rel_diffs > value_rtol) & (value_diffs > value_atol)
        bad_mask = time_bad | value_bad
        mismatched = int(np.sum(bad_mask))

        # Collect first few mismatches
        first_mismatches = []
        if mismatched > 0:
            bad_indices = np.where(bad_mask)[0][:10]
            for i in bad_indices:
                first_mismatches.append(
                    {
                        "index": int(i),
                        "dll_time": float(dll_times[i]),
                        "lib_time": float(lib_times[i]),
                        "time_diff_ms": float(time_diffs[i]) * 1000,
                        "dll_value": float(dll_values[i]),
                        "lib_value": float(lib_vals[i]),
                        "value_diff": float(value_diffs[i]),
                        "rel_diff": float(rel_diffs[i]),
                    }
                )
    else:
        # Different sample counts - match by timecodes
        # Find DLL samples that have a matching libxrk timecode within tolerance
        compared = 0
        mismatched = 0
        max_time_diff = 0.0
        max_value_diff = 0.0
        max_rel_diff = 0.0
        first_mismatches = []

        if len(dll_times) > 0 and len(lib_times) > 0:
            # Use searchsorted to find closest lib timecodes for each dll timecode
            insert_idx = np.searchsorted(lib_times, dll_times)
            insert_idx = np.clip(insert_idx, 0, len(lib_times) - 1)

            # Check both insert_idx and insert_idx-1 for closest match
            diffs_at = np.abs(dll_times - lib_times[insert_idx])
            prev_idx = np.clip(insert_idx - 1, 0, len(lib_times) - 1)
            diffs_prev = np.abs(dll_times - lib_times[prev_idx])

            use_prev = diffs_prev < diffs_at
            best_idx = np.where(use_prev, prev_idx, insert_idx)
            best_time_diff = np.where(use_prev, diffs_prev, diffs_at)

            # Only compare samples within time tolerance
            match_mask = best_time_diff <= time_tol_s
            compared = int(np.sum(match_mask))

            if compared > 0:
                matched_dll_idx = np.where(match_mask)[0]
                matched_lib_idx = best_idx[match_mask]
                matched_time_diffs = best_time_diff[match_mask]

                max_time_diff = float(np.max(matched_time_diffs))

                matched_dll_vals = dll_values[matched_dll_idx]
                matched_lib_vals = lib_vals[matched_lib_idx]
                value_diffs = np.abs(matched_dll_vals - matched_lib_vals)
                max_value_diff = float(np.max(value_diffs))

                with np.errstate(divide="ignore", invalid="ignore"):
                    denom = np.maximum(np.abs(matched_dll_vals), value_atol)
                    rel_diffs = value_diffs / denom
                max_rel_diff = float(np.max(rel_diffs))

                value_bad = (rel_diffs > value_rtol) & (value_diffs > value_atol)
                mismatched = int(np.sum(value_bad))

                if mismatched > 0:
                    bad_local = np.where(value_bad)[0][:10]
                    for bi in bad_local:
                        di = matched_dll_idx[bi]
                        li = matched_lib_idx[bi]
                        first_mismatches.append(
                            {
                                "dll_index": int(di),
                                "lib_index": int(li),
                                "dll_time": float(dll_times[di]),
                                "lib_time": float(lib_times[li]),
                                "time_diff_ms": float(matched_time_diffs[bi]) * 1000,
                                "dll_value": float(dll_values[di]),
                                "lib_value": float(lib_vals[li]),
                                "value_diff": float(value_diffs[bi]),
                                "rel_diff": float(rel_diffs[bi]),
                            }
                        )

    passed = mismatched == 0 and units_match

    return ChannelComparison(
        name=lib_name,
        dll_name=dll_ch.name,
        category=category,
        dll_units=dll_ch.units,
        lib_units=lib_units,
        units_match=units_match,
        dll_samples=len(dll_times),
        lib_samples=len(lib_times),
        compared_samples=compared,
        max_time_diff_s=max_time_diff,
        max_value_diff=max_value_diff,
        max_rel_diff=max_rel_diff,
        mismatched_samples=mismatched,
        first_mismatches=first_mismatches,
        passed=passed,
    )


def compare_file(
    xrk_path: Path,
    dll_data: DllData,
    verbose: bool = False,
) -> ComparisonReport:
    """Compare DLL data against libxrk for a single file."""
    from libxrk import aim_xrk

    report = ComparisonReport(file_path=str(xrk_path))

    try:
        log = aim_xrk(str(xrk_path))
    except Exception as e:
        report.errors.append(f"Failed to load with libxrk: {e}")
        return report

    lib_channel_names = set(log.channels.keys())

    # Build lookup of libxrk channels
    def get_lib_channel(name: str) -> tuple[np.ndarray, np.ndarray, str] | None:
        """Get libxrk channel data as (times_seconds, values, units)."""
        if name not in log.channels:
            return None
        table = log.channels[name]
        times_ms = table.column("timecodes").to_numpy().astype(np.float64)
        times_s = times_ms / 1000.0
        values = table.column(name).to_numpy().astype(np.float64)
        field = table.schema.field(name)
        units = ""
        if field.metadata and b"units" in field.metadata:
            units = field.metadata[b"units"].decode("utf-8")
        return times_s, values, units

    matched_lib_channels = set()

    # 1. Compare regular channels
    for dll_ch in dll_data.regular_channels:
        if dll_ch.sample_count == 0:
            # DLL reports 0 samples - skip but note
            if verbose:
                report.dll_only_channels.append(f"{dll_ch.name} (0 samples)")
            continue

        lib_name = dll_ch.name
        lib_data = get_lib_channel(lib_name)

        if lib_data is None:
            report.dll_only_channels.append(dll_ch.name)
            continue

        matched_lib_channels.add(lib_name)
        times_s, values, units = lib_data

        result = compare_channel_values(dll_ch, times_s, values, units, "regular", lib_name)
        report.channel_results.append(result)

    # 2. Compare GPS derived channels
    # NOTE: DLL GPS derived channels return timecodes in milliseconds (not seconds
    # like regular channels). We convert to seconds for comparison.
    for dll_ch in dll_data.gps_derived_channels:
        if dll_ch.sample_count == 0:
            continue

        lib_name = GPS_DERIVED_NAME_MAP.get(dll_ch.name)
        if lib_name is None:
            report.dll_only_channels.append(f"GPS:{dll_ch.name}")
            continue

        # Skip if already compared as a regular channel
        if lib_name in matched_lib_channels:
            # Compare GPS derived separately since sample count differs
            pass

        lib_data = get_lib_channel(lib_name)
        if lib_data is None:
            report.dll_only_channels.append(f"GPS:{dll_ch.name} -> {lib_name}")
            continue

        matched_lib_channels.add(lib_name)
        times_s, values, units = lib_data

        # Convert DLL GPS times from ms to seconds
        gps_dll_ch = DllChannel(
            name=dll_ch.name,
            units=dll_ch.units,
            sample_count=dll_ch.sample_count,
            times=dll_ch.times / 1000.0,
            values=dll_ch.values,
        )

        # GPS channels have different sample rates (DLL interpolates to higher rate)
        # Use broader tolerance for timecodes (5ms) since rates don't align exactly
        is_computed = lib_name in GPS_COMPUTED_REGULAR
        result = compare_channel_values(
            gps_dll_ch,
            times_s,
            values,
            units,
            "gps_derived",
            lib_name,
            time_tol_s=0.005,  # 5ms tolerance for GPS timecode matching
            value_rtol=5e-3 if is_computed else 1e-4,
            value_atol=1e-4 if is_computed else 1e-6,
        )
        report.channel_results.append(result)

    # 3. Compare GPS raw channels
    # DLL GPS raw channels also use millisecond timecodes
    for dll_ch in dll_data.gps_raw_channels:
        if dll_ch.sample_count == 0:
            continue

        # Map GPS raw channel names to libxrk equivalents
        # libxrk doesn't directly expose ECEF values, but we can check N Satellites
        gps_raw_name_map = {
            "N Satellites": "GPS_Satellites",
        }
        lib_name = gps_raw_name_map.get(dll_ch.name)
        if lib_name is None:
            report.dll_only_channels.append(f"GPS_RAW:{dll_ch.name}")
            continue

        lib_data = get_lib_channel(lib_name)
        if lib_data is None:
            report.dll_only_channels.append(f"GPS_RAW:{dll_ch.name} -> {lib_name}")
            continue

        # Don't duplicate if already matched via GPS derived
        if lib_name in matched_lib_channels:
            continue

        matched_lib_channels.add(lib_name)
        times_s, values, units = lib_data

        gps_raw_dll_ch = DllChannel(
            name=dll_ch.name,
            units=dll_ch.units,
            sample_count=dll_ch.sample_count,
            times=dll_ch.times / 1000.0,
            values=dll_ch.values,
        )

        result = compare_channel_values(
            gps_raw_dll_ch,
            times_s,
            values,
            units,
            "gps_raw",
            lib_name,
            time_tol_s=0.005,
        )
        report.channel_results.append(result)

    # 4. Note libxrk-only channels
    for name in sorted(lib_channel_names - matched_lib_channels):
        report.lib_only_channels.append(name)

    # 5. Compare laps
    lib_laps = log.laps
    lib_lap_nums = lib_laps.column("num").to_pylist()
    lib_starts = lib_laps.column("start_time").to_pylist()
    lib_ends = lib_laps.column("end_time").to_pylist()

    dll_laps = dll_data.laps
    if len(dll_laps) != len(lib_lap_nums):
        report.laps_match = False
        report.lap_details = f"Lap count mismatch: DLL={len(dll_laps)}, libxrk={len(lib_lap_nums)}"
    else:
        lap_issues = []
        for i, ((dll_start_s, dll_dur_s), lib_start, lib_end) in enumerate(
            zip(dll_laps, lib_starts, lib_ends)
        ):
            dll_start_ms = dll_start_s * 1000
            dll_dur_ms = dll_dur_s * 1000
            lib_dur_ms = lib_end - lib_start

            start_diff = abs(lib_start - dll_start_ms)
            dur_diff = abs(lib_dur_ms - dll_dur_ms)

            if start_diff > 1 or dur_diff > 1:
                lap_issues.append(
                    f"Lap {i}: start diff={start_diff:.1f}ms, "
                    f"dur diff={dur_diff:.1f}ms "
                    f"(DLL: {dll_start_ms:.0f}/{dll_dur_ms:.0f}, "
                    f"lib: {lib_start:.0f}/{lib_dur_ms:.0f})"
                )

        if lap_issues:
            report.laps_match = False
            report.lap_details = "\n".join(lap_issues)

    # 6. Compare metadata
    meta_issues = []
    lib_meta = log.metadata
    meta_map = {
        "vehicle": "Vehicle",
        "track": "Venue",
        "racer": "Driver",
        "championship": "Series",
    }
    for dll_key, lib_key in meta_map.items():
        dll_val = dll_data.metadata.get(dll_key, "").strip()
        lib_val = lib_meta.get(lib_key, "").strip()
        if dll_val != lib_val:
            meta_issues.append(f"{dll_key}: DLL='{dll_val}', libxrk='{lib_val}'")

    if meta_issues:
        report.metadata_match = False
        report.metadata_details = "\n".join(meta_issues)

    return report


def print_report(report: ComparisonReport, verbose: bool = False) -> None:
    """Print a comparison report."""
    print(f"\n{'=' * 78}")
    print(f"File: {report.file_path}")
    print("=" * 78)

    # Channel results summary - separate regular from GPS
    reg = report.regular_results
    gps = report.gps_results
    reg_passed = sum(1 for cr in reg if cr.passed)
    gps_passed = sum(1 for cr in gps if cr.passed)

    print(
        f"\nRegular channels: {len(reg)} compared, {reg_passed} passed, {len(reg) - reg_passed} failed"
    )
    print(
        f"GPS channels:     {len(gps)} compared, {gps_passed} passed, {len(gps) - gps_passed} differ"
    )
    print(f"  (GPS differences are expected - DLL and libxrk use different algorithms)")

    def print_channel_results(results: list[ChannelComparison], header: str) -> None:
        print(f"\n--- {header} ---")
        for cr in results:
            status = "PASS" if cr.passed else "FAIL"
            marker = " " if cr.passed else "!"

            samples_info = f"{cr.compared_samples}/{cr.dll_samples}"
            if cr.dll_samples != cr.lib_samples:
                samples_info += f" (lib:{cr.lib_samples})"

            units_info = ""
            if not cr.units_match:
                units_info = f" UNITS: dll='{cr.dll_units}' lib='{cr.lib_units}'"

            if cr.passed and not verbose:
                print(
                    f" {marker} [{status}] {cr.name:30s} "
                    f"samples={samples_info:>20s}  "
                    f"max_diff={cr.max_value_diff:.2e}"
                )
            else:
                extra = ""
                if cr.category != "regular":
                    extra = f"  (dll: {cr.dll_name})"
                print(
                    f" {marker} [{status}] {cr.name:30s} "
                    f"samples={samples_info:>20s}  "
                    f"max_diff={cr.max_value_diff:.2e}  "
                    f"max_rel={cr.max_rel_diff:.2e}  "
                    f"time_diff={cr.max_time_diff_s * 1000:.3f}ms"
                    f"{units_info}{extra}"
                )

            if cr.mismatched_samples > 0 and verbose:
                print(f"   {cr.mismatched_samples} mismatched samples:")
                for m in cr.first_mismatches[:5]:
                    idx_str = (
                        f"[{m['index']}]"
                        if "index" in m
                        else f"[dll:{m['dll_index']},lib:{m['lib_index']}]"
                    )
                    print(
                        f"     {idx_str} "
                        f"t={m['dll_time']:.4f}s/{m['lib_time']:.4f}s "
                        f"(dt={m['time_diff_ms']:.3f}ms) "
                        f"v={m['dll_value']:.6g}/{m['lib_value']:.6g} "
                        f"(dv={m['value_diff']:.6g}, rel={m['rel_diff']:.6g})"
                    )
                remaining = cr.mismatched_samples - min(5, len(cr.first_mismatches))
                if remaining > 0:
                    print(f"     ... and {remaining} more")

    print_channel_results(reg, "Regular Channels (parser correctness)")
    if gps:
        print_channel_results(gps, "GPS Derived Channels (different algorithms expected)")

    # DLL-only channels
    if report.dll_only_channels:
        print(f"\nDLL-only channels ({len(report.dll_only_channels)}):")
        for name in report.dll_only_channels:
            print(f"  - {name}")

    # libxrk-only channels
    if report.lib_only_channels:
        print(f"\nlibxrk-only channels ({len(report.lib_only_channels)}):")
        for name in report.lib_only_channels:
            print(f"  - {name}")

    # Laps
    if report.laps_match:
        print(f"\nLaps: MATCH")
    else:
        print(f"\nLaps: MISMATCH")
        print(f"  {report.lap_details}")

    # Metadata
    if report.metadata_match:
        print(f"Metadata: MATCH")
    else:
        print(f"Metadata: MISMATCH")
        print(f"  {report.metadata_details}")

    # Errors
    if report.errors:
        print(f"\nErrors:")
        for err in report.errors:
            print(f"  - {err}")

    # Overall
    overall = "PASS" if report.passed else "FAIL"
    print(f"\nOVERALL: {overall}")


def extract_dll_data(xrk_path: Path) -> Path:
    """Run wine_exhaustive_extract.py to extract DLL data, return path to binary file."""
    output_path = Path(tempfile.mktemp(suffix=".bin", prefix="dll_extract_"))
    wine_xrk = f"Z:{xrk_path.absolute()}"
    wine_output = f"Z:{output_path}"
    wine_script = f"Z:{Path(EXTRACT_SCRIPT).absolute()}"

    result = subprocess.run(
        [WINE_BIN, WINE_PYTHON, wine_script, wine_xrk, wine_output],
        capture_output=True,
        text=True,
        env={"WINEDEBUG": "-all"},
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Wine extraction failed (rc={result.returncode}):\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

    if not output_path.exists():
        raise RuntimeError(f"Output file not created: {output_path}")

    return output_path


def find_test_files() -> list[Path]:
    """Find all XRK test files."""
    test_data = TESTS_DIR / "test_data"
    files = sorted(test_data.glob("**/*.xrk"))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exhaustive value-by-value comparison of AIM DLL vs libxrk"
    )
    parser.add_argument("xrk_file", nargs="?", help="XRK file to compare")
    parser.add_argument("bin_file", nargs="?", help="Pre-extracted DLL binary file")
    parser.add_argument("--extract", action="store_true", help="Auto-extract DLL data via Wine")
    parser.add_argument("--all", action="store_true", help="Compare all test files")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.all:
        files = find_test_files()
        if not files:
            print("No test files found")
            return 1

        all_passed = True
        for xrk_path in files:
            print(f"\nExtracting DLL data for: {xrk_path.name}...")
            try:
                bin_path = extract_dll_data(xrk_path)
                dll_data = read_dll_binary(bin_path)
                report = compare_file(xrk_path, dll_data, args.verbose)
                print_report(report, args.verbose)
                if not report.passed:
                    all_passed = False
                os.unlink(bin_path)
            except Exception as e:
                print(f"\nFailed to process {xrk_path}: {e}")
                all_passed = False

        print(f"\n{'=' * 78}")
        print(f"SUMMARY: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
        return 0 if all_passed else 1

    elif args.xrk_file:
        xrk_path = Path(args.xrk_file)

        if args.bin_file:
            bin_path = Path(args.bin_file)
        elif args.extract:
            print(f"Extracting DLL data for: {xrk_path.name}...")
            bin_path = extract_dll_data(xrk_path)
        else:
            parser.error("Either provide a .bin file or use --extract")
            return 1

        dll_data = read_dll_binary(bin_path)
        report = compare_file(xrk_path, dll_data, args.verbose)
        print_report(report, args.verbose)

        if args.extract and args.bin_file is None:
            os.unlink(bin_path)

        return 0 if report.passed else 1

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
