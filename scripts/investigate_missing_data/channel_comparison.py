#!/usr/bin/env python3
"""
Channel Comparison Tool - Compares DLL channels vs libxrk channels.

This tool uses the expanded DLL wrapper to enumerate ALL channels
(regular, GPS derived, GPS raw) and compare against libxrk output.
"""

import json
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "tests" / "reference_dll"))


def load_libxrk_channels(xrk_path: Path) -> dict[str, dict]:
    """Load channel info from libxrk."""
    from libxrk import aim_xrk

    log = aim_xrk(str(xrk_path))

    channels = {}
    for name, table in log.channels.items():
        field = table.schema.field(name)
        metadata = field.metadata or {}
        channels[name] = {
            "samples_count": table.num_rows,
            "units": metadata.get(b"units", b"").decode("utf-8"),
            "source": "libxrk",
        }

    return channels


def compare_channels_native(xrk_path: Path) -> dict:
    """
    Compare channels when running natively (Windows or with DLL access).

    Returns dict with comparison results.
    """
    from aim_dll_wrapper import AimDll

    dll_regular_channels: dict[str, dict] = {}
    dll_gps_derived_channels: dict[str, dict] = {}
    dll_gps_raw_channels: dict[str, dict] = {}
    libxrk_channels: dict[str, dict] = {}
    missing_in_libxrk: list[str] = []
    missing_in_dll: list[str] = []
    common_channels: list[str] = []

    result: dict = {
        "file": str(xrk_path),
        "dll_regular_channels": dll_regular_channels,
        "dll_gps_derived_channels": dll_gps_derived_channels,
        "dll_gps_raw_channels": dll_gps_raw_channels,
        "libxrk_channels": libxrk_channels,
        "missing_in_libxrk": missing_in_libxrk,
        "missing_in_dll": missing_in_dll,
        "common_channels": common_channels,
    }

    # Load libxrk channels
    try:
        libxrk_channels.update(load_libxrk_channels(xrk_path))
    except Exception as e:
        result["libxrk_error"] = str(e)

    # Load DLL channels
    try:
        with AimDll() as dll:
            idx = dll.open_file(xrk_path)

            # Regular channels
            ch_count = dll.get_channels_count(idx)
            for ch in range(ch_count):
                name = dll.get_channel_name(idx, ch)
                dll_regular_channels[name] = {
                    "units": dll.get_channel_units(idx, ch),
                    "samples_count": dll.get_channel_samples_count(idx, ch),
                    "source": "dll_regular",
                }

            # GPS derived channels
            gps_count = dll.get_GPS_channels_count(idx)
            for ch in range(gps_count):
                name = dll.get_GPS_channel_name(idx, ch)
                dll_gps_derived_channels[name] = {
                    "units": dll.get_GPS_channel_units(idx, ch),
                    "samples_count": dll.get_GPS_channel_samples_count(idx, ch),
                    "source": "dll_gps_derived",
                }

            # GPS raw channels
            raw_count = dll.get_GPS_raw_channels_count(idx)
            for ch in range(raw_count):
                name = dll.get_GPS_raw_channel_name(idx, ch)
                dll_gps_raw_channels[name] = {
                    "units": dll.get_GPS_raw_channel_units(idx, ch),
                    "samples_count": dll.get_GPS_raw_channel_samples_count(idx, ch),
                    "source": "dll_gps_raw",
                }

            dll.close_file(idx)

    except FileNotFoundError as e:
        result["dll_error"] = str(e)
    except Exception as e:
        result["dll_error"] = str(e)

    # Compute differences
    if "dll_error" not in result and "libxrk_error" not in result:
        all_dll_channels = set(dll_regular_channels.keys())
        all_dll_channels.update(dll_gps_derived_channels.keys())
        all_dll_channels.update(dll_gps_raw_channels.keys())

        libxrk_set = set(libxrk_channels.keys())

        missing_in_libxrk.extend(sorted(all_dll_channels - libxrk_set))
        missing_in_dll.extend(sorted(libxrk_set - all_dll_channels))
        common_channels.extend(sorted(all_dll_channels & libxrk_set))

    return result


def generate_wine_comparison_script() -> str:
    """
    Generate a standalone Python script that can run in Wine.

    This script extracts ALL channel info from the DLL and outputs JSON.
    """
    return '''#!/usr/bin/env python
"""
Wine script to extract ALL channels from DLL (regular, GPS derived, GPS raw).
Run with: wine python.exe wine_extract_all_channels.py <xrk_file>
"""

import ctypes
import json
import sys
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_wchar_p

def main():
    if len(sys.argv) < 2:
        print("Usage: wine python.exe wine_extract_all_channels.py <xrk_file>")
        sys.exit(1)

    xrk_path = sys.argv[1]
    dll_path = __file__.rsplit("\\\\", 1)[0] + "\\\\MatLabXRK-2017-64-ReleaseU.dll"

    dll = ctypes.CDLL(dll_path)

    # Setup function signatures
    dll.open_file_w.argtypes = [c_wchar_p]
    dll.open_file_w.restype = c_int
    dll.close_file_i.argtypes = [c_int]
    dll.close_file_i.restype = None

    # Regular channels
    dll.get_channels_count.argtypes = [c_int]
    dll.get_channels_count.restype = c_int
    dll.get_channel_name.argtypes = [c_int, c_int]
    dll.get_channel_name.restype = c_char_p
    dll.get_channel_units.argtypes = [c_int, c_int]
    dll.get_channel_units.restype = c_char_p
    dll.get_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_channel_samples_count.restype = c_int

    # GPS derived channels
    dll.get_GPS_channels_count.argtypes = [c_int]
    dll.get_GPS_channels_count.restype = c_int
    dll.get_GPS_channel_name.argtypes = [c_int, c_int]
    dll.get_GPS_channel_name.restype = c_char_p
    dll.get_GPS_channel_units.argtypes = [c_int, c_int]
    dll.get_GPS_channel_units.restype = c_char_p
    dll.get_GPS_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_GPS_channel_samples_count.restype = c_int

    # GPS raw channels
    dll.get_GPS_raw_channels_count.argtypes = [c_int]
    dll.get_GPS_raw_channels_count.restype = c_int
    dll.get_GPS_raw_channel_name.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_name.restype = c_char_p
    dll.get_GPS_raw_channel_units.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_units.restype = c_char_p
    dll.get_GPS_raw_channel_samples_count.argtypes = [c_int, c_int]
    dll.get_GPS_raw_channel_samples_count.restype = c_int

    idx = dll.open_file_w(xrk_path)
    if idx < 0:
        print(json.dumps({"error": "Failed to open file"}))
        sys.exit(1)

    result = {
        "file": xrk_path,
        "regular_channels": {},
        "gps_derived_channels": {},
        "gps_raw_channels": {},
    }

    # Regular channels
    ch_count = dll.get_channels_count(idx)
    for ch in range(ch_count):
        name = dll.get_channel_name(idx, ch)
        name = name.decode("latin-1") if name else ""
        units = dll.get_channel_units(idx, ch)
        units = units.decode("latin-1") if units else ""
        samples = dll.get_channel_samples_count(idx, ch)
        result["regular_channels"][name] = {"units": units, "samples_count": samples}

    # GPS derived channels
    gps_count = dll.get_GPS_channels_count(idx)
    for ch in range(gps_count):
        name = dll.get_GPS_channel_name(idx, ch)
        name = name.decode("latin-1") if name else ""
        units = dll.get_GPS_channel_units(idx, ch)
        units = units.decode("latin-1") if units else ""
        samples = dll.get_GPS_channel_samples_count(idx, ch)
        result["gps_derived_channels"][name] = {"units": units, "samples_count": samples}

    # GPS raw channels
    raw_count = dll.get_GPS_raw_channels_count(idx)
    for ch in range(raw_count):
        name = dll.get_GPS_raw_channel_name(idx, ch)
        name = name.decode("latin-1") if name else ""
        units = dll.get_GPS_raw_channel_units(idx, ch)
        units = units.decode("latin-1") if units else ""
        samples = dll.get_GPS_raw_channel_samples_count(idx, ch)
        result["gps_raw_channels"][name] = {"units": units, "samples_count": samples}

    dll.close_file_i(idx)

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
'''


def format_report(result: dict) -> str:
    """Format comparison result as human-readable report."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"Channel Comparison Report: {result['file']}")
    lines.append("=" * 80)
    lines.append("")

    if "dll_error" in result:
        lines.append(f"DLL ERROR: {result['dll_error']}")
        lines.append("")
        lines.append("To run comparison, use Wine with the DLL:")
        lines.append("  1. Run: wine python.exe wine_extract_all_channels.py <xrk_file>")
        lines.append("  2. Compare JSON output with libxrk channels")
        lines.append("")

    if "libxrk_error" in result:
        lines.append(f"LIBXRK ERROR: {result['libxrk_error']}")
        lines.append("")

    # Channel counts
    lines.append("CHANNEL COUNTS:")
    lines.append(f"  DLL Regular:      {len(result.get('dll_regular_channels', {}))}")
    lines.append(f"  DLL GPS Derived:  {len(result.get('dll_gps_derived_channels', {}))}")
    lines.append(f"  DLL GPS Raw:      {len(result.get('dll_gps_raw_channels', {}))}")
    lines.append(f"  libxrk:           {len(result.get('libxrk_channels', {}))}")
    lines.append("")

    # GPS Derived channels (key missing data)
    if result.get("dll_gps_derived_channels"):
        lines.append("GPS DERIVED CHANNELS (from DLL):")
        for name, info in sorted(result["dll_gps_derived_channels"].items()):
            in_libxrk = "✓" if name in result.get("libxrk_channels", {}) else "✗ MISSING"
            lines.append(
                f"  {name:30s} {info['units']:10s} {info['samples_count']:6d} samples  {in_libxrk}"
            )
        lines.append("")

    # GPS Raw channels
    if result.get("dll_gps_raw_channels"):
        lines.append("GPS RAW CHANNELS (from DLL):")
        for name, info in sorted(result["dll_gps_raw_channels"].items()):
            in_libxrk = "✓" if name in result.get("libxrk_channels", {}) else "✗ MISSING"
            lines.append(
                f"  {name:30s} {info['units']:10s} {info['samples_count']:6d} samples  {in_libxrk}"
            )
        lines.append("")

    # Missing channels
    if result.get("missing_in_libxrk"):
        lines.append("CHANNELS MISSING IN LIBXRK:")
        for name in result["missing_in_libxrk"]:
            source = "unknown"
            if name in result.get("dll_regular_channels", {}):
                source = "regular"
            elif name in result.get("dll_gps_derived_channels", {}):
                source = "gps_derived"
            elif name in result.get("dll_gps_raw_channels", {}):
                source = "gps_raw"
            lines.append(f"  {name} (DLL source: {source})")
        lines.append("")

    # Extra channels in libxrk
    if result.get("missing_in_dll"):
        lines.append("CHANNELS ONLY IN LIBXRK (synthesized or different names):")
        for name in result["missing_in_dll"]:
            lines.append(f"  {name}")
        lines.append("")

    return "\n".join(lines)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Compare DLL channels vs libxrk channels")
    parser.add_argument("xrk_file", nargs="?", help="XRK file to compare")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of report")
    parser.add_argument(
        "--generate-wine-script",
        action="store_true",
        help="Generate Wine extraction script",
    )
    parser.add_argument(
        "--libxrk-only",
        action="store_true",
        help="Only show libxrk channels (no DLL comparison)",
    )
    args = parser.parse_args()

    if args.generate_wine_script:
        print(generate_wine_comparison_script())
        return

    if not args.xrk_file:
        parser.print_help()
        sys.exit(1)

    xrk_path = Path(args.xrk_file)
    if not xrk_path.exists():
        print(f"Error: File not found: {xrk_path}")
        sys.exit(1)

    if args.libxrk_only:
        # Just show libxrk channels
        channels = load_libxrk_channels(xrk_path)
        if args.json:
            print(json.dumps({"libxrk_channels": channels}, indent=2))
        else:
            print(f"libxrk channels for {xrk_path}:")
            print("-" * 60)
            for name, info in sorted(channels.items()):
                print(f"  {name:35s} {info['units']:10s} {info['samples_count']:6d} samples")
    else:
        # Full comparison
        result = compare_channels_native(xrk_path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(format_report(result))


if __name__ == "__main__":
    main()
