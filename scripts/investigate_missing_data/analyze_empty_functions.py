#!/usr/bin/env python3
"""
Analyze all test XRK files to find channels where function lookup returns empty string.

For each file:
1. Extract all CHS messages
2. Compute (display_format, unit_type_byte, config_flags) triple
3. Show which channels get empty function string from the lookup
4. Group by triple across all files with context
"""

import struct
import sys
from collections import defaultdict
from pathlib import Path

from chs_byte_analysis import extract_chs_messages

# Current function lookup table: (display_format, unit_type_byte) -> function
_function_map = {
    (0, 0x01): "Percent",
    (0, 0x03): "Acceleration",
    (0, 0x05): "Angular Rate",
    (0, 0x0B): "Number",
    (0, 0x0E): "Pressure",
    (0, 0x0F): "Engine RPM",
    (0, 0x10): "Rear Wheel Speed",
    (0, 0x11): "Temperature",
    (0, 0x15): "Voltage",
    (0, 0x91): "Exhaust Temperature",
    (1, 0x95): "Battery Voltage",
    (6, 0x1F): "Gear",
    (9, 0x1E): "Lambda",
    (11, 0x91): "Oil Temperature",
    (13, 0x84): "Steering Angle",
    (14, 0x81): "Percentage Throttle Load",
    (17, 0x03): "Inline Acceleration",
    (17, 0x05): "Roll Rate",
    (17, 0x83): "Lateral Acceleration",
    (17, 0x85): "Pitch Rate",
    (18, 0x03): "Vertical Acceleration",
    (18, 0x05): "Yaw Rate",
    (26, 0x21): "Device Brightness",
    (128, 0x10): "Vehicle Speed",
    (128, 0x91): "Intake Air Temperature",
    (130, 0x0E): "Brake Circuit Pressure",
    (169, 0x8C): "LF Shock Position",
}

# Override: (display_format, unit_type_byte, config_flags) -> function
_function_map_override = {
    (0, 0x11, 1): "Device Temperature",
}

# Unit type -> (unit_string, dec_pts)
_unit_map = {
    1: ("%", 2),
    3: ("g", 2),
    4: ("deg", 1),
    5: ("deg/s", 1),
    6: ("", 0),
    9: ("Hz", 0),
    11: ("", 0),
    12: ("mm", 0),
    14: ("bar", 2),
    15: ("rpm", 0),
    16: ("km/h", 0),
    17: ("C", 1),
    18: ("ms", 0),
    19: ("Nm", 0),
    20: ("km/h", 0),
    21: ("mV", 1),
    22: ("l", 1),
    24: ("l/s", 0),
    26: ("time?", 0),
    27: ("A", 0),
    30: ("lambda", 2),
    31: ("gear", 0),
    33: ("%", 2),
    43: ("kg", 3),
}


def resolve_function(display_format, unit_type_byte, config_flags):
    """Resolve function string, returns '' if not found."""
    override = _function_map_override.get((display_format, unit_type_byte, config_flags))
    if override is not None:
        return override
    return _function_map.get((display_format, unit_type_byte), "")


def get_units(unit_type_byte):
    """Get unit string from unit_type_byte (lower 7 bits)."""
    unit_type_7bit = unit_type_byte & 0x7F
    calibrated = (unit_type_byte >> 7) & 1
    if unit_type_7bit in _unit_map:
        units, dec_pts = _unit_map[unit_type_7bit]
        if calibrated and units == "mV":
            units = "V"
        return units
    return f"??(0x{unit_type_7bit:02x})"


TEST_FILES = [
    "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk",
    "tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk",
    "tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk",
    "tests/test_data/SFJ/CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk",
    "tests/test_data/aim_official/test.xrk",
    "tests/test_data/issue49/badGPSdata.xrk",
]


def main():
    project_root = Path(__file__).parent.parent.parent

    # Collect all channel data from all files
    all_channels = []  # list of dicts with all relevant info

    for rel_path in TEST_FILES:
        file_path = project_root / rel_path
        if not file_path.exists():
            print(f"WARNING: File not found: {file_path}", file=sys.stderr)
            continue

        print(f"Extracting CHS from {rel_path}...", file=sys.stderr)
        msgs = extract_chs_messages(file_path)
        print(f"  Found {len(msgs)} CHS messages", file=sys.stderr)

        for m in msgs:
            raw = m["raw"]
            unit_type_byte = raw[12]
            display_format = raw[13]
            config_flags = struct.unpack_from("<H", raw, 14)[0]
            source_type = raw[16]
            decoder_type = raw[20]
            hardware_id = struct.unpack_from("<H", raw, 4)[0]
            hardware_ref = struct.unpack_from("<I", raw, 8)[0]
            source_channel_id = struct.unpack_from("<H", raw, 6)[0]
            device_tag = raw[76:80]
            device_node_id = raw[80]
            sample_period_us = struct.unpack_from("<I", raw, 64)[0]
            cal_value_1 = struct.unpack_from("<f", raw, 96)[0]
            cal_value_2 = struct.unpack_from("<f", raw, 100)[0]
            display_range_min = struct.unpack_from("<f", raw, 104)[0]
            display_range_max = struct.unpack_from("<f", raw, 108)[0]

            function = resolve_function(display_format, unit_type_byte, config_flags)
            units = get_units(unit_type_byte)

            all_channels.append(
                {
                    "file": rel_path,
                    "long_name": m["long_name"],
                    "short_name": m["short_name"],
                    "display_format": display_format,
                    "unit_type_byte": unit_type_byte,
                    "config_flags": config_flags,
                    "function": function,
                    "units": units,
                    "source_type": source_type,
                    "decoder_type": decoder_type,
                    "hardware_id": hardware_id,
                    "hardware_ref": hardware_ref,
                    "source_channel_id": source_channel_id,
                    "device_tag": device_tag,
                    "device_node_id": device_node_id,
                    "sample_period_us": sample_period_us,
                    "data_size": m["data_size"],
                    "cal_value_1": cal_value_1,
                    "cal_value_2": cal_value_2,
                    "display_range_min": display_range_min,
                    "display_range_max": display_range_max,
                }
            )

    print(f"\nTotal channels across all files: {len(all_channels)}", file=sys.stderr)

    # ============================================================
    # SECTION 1: Per-file summary of all channels
    # ============================================================
    print("=" * 130)
    print("SECTION 1: ALL CHANNELS PER FILE WITH FUNCTION LOOKUP RESULT")
    print("=" * 130)

    for rel_path in TEST_FILES:
        file_channels = [c for c in all_channels if c["file"] == rel_path]
        if not file_channels:
            continue

        print(f"\n--- {rel_path} ({len(file_channels)} channels) ---")
        print(
            f"{'Channel Name':30s}  {'display_fmt':>11s}  {'unit_type':>10s}  {'cfg_flags':>10s}  {'Units':>8s}  {'Function'}"
        )
        print("-" * 120)

        for c in file_channels:
            fn = c["function"] if c["function"] else "** EMPTY **"
            print(
                f"{c['long_name']:30s}  {c['display_format']:>11d}  0x{c['unit_type_byte']:02x}        0x{c['config_flags']:04x}      {c['units']:>8s}  {fn}"
            )

    # ============================================================
    # SECTION 2: Channels with EMPTY function, grouped by triple
    # ============================================================
    print()
    print("=" * 130)
    print("SECTION 2: CHANNELS WITH EMPTY FUNCTION, GROUPED BY (display_format, unit_type_byte)")
    print("=" * 130)

    empty_channels = [c for c in all_channels if c["function"] == ""]
    matched_channels = [c for c in all_channels if c["function"] != ""]

    print(f"\nTotal channels: {len(all_channels)}")
    print(f"Matched (have function): {len(matched_channels)}")
    print(f"Empty (no function): {len(empty_channels)}")

    # Group empty channels by (display_format, unit_type_byte)
    by_triple = defaultdict(list)
    for c in empty_channels:
        key = (c["display_format"], c["unit_type_byte"])
        by_triple[key].append(c)

    print(
        f"\nDistinct (display_format, unit_type_byte) pairs with empty function: {len(by_triple)}"
    )
    print()

    for (df, utb), channels in sorted(by_triple.items()):
        # Collect unique channel names
        unique_names = sorted(set(c["long_name"] for c in channels))
        unique_files = sorted(set(c["file"] for c in channels))
        unique_cfg_flags = sorted(set(c["config_flags"] for c in channels))
        units = channels[0]["units"]

        print(
            f"  (display_format={df}, unit_type_byte=0x{utb:02x})  units=\"{units}\"  config_flags={[f'0x{f:04x}' for f in unique_cfg_flags]}"
        )
        print(
            f"    Appears in {len(channels)} channel instances across {len(unique_files)} file(s)"
        )
        print(f"    Channel names ({len(unique_names)}):")
        for name in unique_names:
            # Find all files this name+triple appears in
            name_files = sorted(
                set(c["file"].split("/")[-1] for c in channels if c["long_name"] == name)
            )
            name_cfg = sorted(set(c["config_flags"] for c in channels if c["long_name"] == name))
            cfg_str = ", ".join(f"0x{f:04x}" for f in name_cfg)
            print(f"      - {name:30s}  cfg_flags=[{cfg_str}]  files: {name_files}")
        print()

    # ============================================================
    # SECTION 3: Detailed context for each empty-function channel
    # ============================================================
    print()
    print("=" * 130)
    print("SECTION 3: DETAILED CONTEXT FOR EMPTY-FUNCTION CHANNELS")
    print("=" * 130)
    print()

    print(
        f"{'Channel Name':30s}  {'df':>3s}  {'utb':>5s}  {'cfg':>6s}  {'units':>6s}  "
        f"{'src_type':>8s}  {'dec':>3s}  {'hw_id':>5s}  {'hw_ref':>8s}  {'src_ch':>6s}  "
        f"{'dev_tag':>8s}  {'node':>4s}  {'period_us':>10s}  {'size':>4s}  "
        f"{'cal1':>10s}  {'cal2':>10s}  {'rng_min':>10s}  {'rng_max':>12s}  {'File'}"
    )
    print("-" * 220)

    for c in sorted(
        empty_channels, key=lambda x: (x["display_format"], x["unit_type_byte"], x["long_name"])
    ):
        dev_tag_str = c["device_tag"].decode("ascii", errors="replace").rstrip("\x00")
        if not dev_tag_str.isprintable():
            dev_tag_str = c["device_tag"].hex()

        print(
            f"{c['long_name']:30s}  {c['display_format']:>3d}  0x{c['unit_type_byte']:02x}  0x{c['config_flags']:04x}  {c['units']:>6s}  "
            f"{c['source_type']:>8d}  {c['decoder_type']:>3d}  {c['hardware_id']:>5d}  {c['hardware_ref']:>8d}  {c['source_channel_id']:>6d}  "
            f"{dev_tag_str:>8s}  {c['device_node_id']:>4d}  {c['sample_period_us']:>10d}  {c['data_size']:>4d}  "
            f"{c['cal_value_1']:>10.3f}  {c['cal_value_2']:>10.3f}  {c['display_range_min']:>10.3f}  {c['display_range_max']:>12.3f}  "
            f"{c['file'].split('/')[-1]}"
        )

    # ============================================================
    # SECTION 4: Analysis and suggestions
    # ============================================================
    print()
    print("=" * 130)
    print("SECTION 4: ANALYSIS AND SUGGESTIONS FOR EMPTY-FUNCTION TRIPLES")
    print("=" * 130)
    print()

    for (df, utb), channels in sorted(by_triple.items()):
        unique_names = sorted(set(c["long_name"] for c in channels))
        unique_cfg = sorted(set(c["config_flags"] for c in channels))
        units = channels[0]["units"]
        unit_type_7bit = utb & 0x7F
        calibrated = (utb >> 7) & 1

        print(f"=== (display_format={df}, unit_type_byte=0x{utb:02x}) ===")
        print(
            f"    unit_type_7bit={unit_type_7bit} (0x{unit_type_7bit:02x}), calibrated_flag={calibrated}"
        )
        print(f"    units=\"{units}\", config_flags={[f'0x{f:04x}' for f in unique_cfg]}")
        print(f"    Channel names: {unique_names}")

        # Try to infer from channel names and units
        name_lower = " ".join(n.lower() for n in unique_names)

        suggestions = []

        # Check if any existing mapping shares the same unit_type_byte with different display_format
        related = [(d, u, fn) for (d, u), fn in _function_map.items() if u == utb and d != df]
        if related:
            print(f"    Related mappings with same unit_type_byte:")
            for d, u, fn in related:
                print(f"      (display_format={d}, unit_type_byte=0x{u:02x}) -> '{fn}'")

        # Check if any existing mapping shares the same display_format with different unit_type_byte
        related_df = [(d, u, fn) for (d, u), fn in _function_map.items() if d == df and u != utb]
        if related_df:
            print(f"    Related mappings with same display_format:")
            for d, u, fn in related_df:
                print(f"      (display_format={d}, unit_type_byte=0x{u:02x}) -> '{fn}'")

        # Name-based heuristics
        if "odometer" in name_lower or "odo" in name_lower:
            suggestions.append("Likely an Odometer/Distance channel")
        if "time" in name_lower or "clk" in name_lower:
            suggestions.append("Likely a Time channel")
        if "gps" in name_lower:
            suggestions.append("Likely a GPS-derived channel")
        if "speed" in name_lower or "spd" in name_lower:
            suggestions.append("Likely a Speed channel")
        if "temp" in name_lower:
            suggestions.append("Likely a Temperature channel")
        if "shock" in name_lower or "pot" in name_lower:
            suggestions.append("Likely a Shock Position channel")
        if "acc" in name_lower:
            suggestions.append("Likely an Acceleration channel")
        if "fuel" in name_lower:
            suggestions.append("Likely a Fuel channel")
        if "press" in name_lower:
            suggestions.append("Likely a Pressure channel")
        if "volt" in name_lower:
            suggestions.append("Likely a Voltage channel")
        if "diff" in name_lower:
            suggestions.append("Likely a Time Difference/Delta channel")
        if "lap" in name_lower:
            suggestions.append("Likely a Lap/Timing channel")
        if "best" in name_lower:
            suggestions.append("Likely a Best Time/Diff channel")
        if "roll" in name_lower:
            suggestions.append("Likely a Rolling/Timing channel")
        if "luminosity" in name_lower or "brightness" in name_lower:
            suggestions.append("Likely a Brightness/Luminosity channel")
        if "predictive" in name_lower:
            suggestions.append("Likely a Predictive Time channel")
        if "oil" in name_lower:
            suggestions.append("Likely an Oil channel (temp or pressure)")
        if "water" in name_lower:
            suggestions.append("Likely a Water Temperature channel")
        if "brake" in name_lower:
            suggestions.append("Likely a Brake channel")
        if "clutch" in name_lower:
            suggestions.append("Likely a Clutch channel")
        if "tire" in name_lower or "tpms" in name_lower or "alm" in name_lower:
            suggestions.append("Likely a Tire/TPMS channel")

        if suggestions:
            print(f"    Suggestions from channel names:")
            for s in suggestions:
                print(f"      - {s}")
        else:
            print(f"    No obvious suggestion from channel names")

        print()

    # ============================================================
    # SECTION 5: Summary table for easy reference
    # ============================================================
    print()
    print("=" * 130)
    print("SECTION 5: SUMMARY TABLE - ALL EMPTY-FUNCTION TRIPLES")
    print("=" * 130)
    print()
    print(
        f"{'(df, utb)':>15s}  {'unit_7bit':>8s}  {'cal':>3s}  {'units':>8s}  {'#ch':>4s}  {'#files':>6s}  {'config_flags':>30s}  {'Channel Names'}"
    )
    print("-" * 160)

    for (df, utb), channels in sorted(by_triple.items()):
        unique_names = sorted(set(c["long_name"] for c in channels))
        unique_files = len(set(c["file"] for c in channels))
        unique_cfg = sorted(set(c["config_flags"] for c in channels))
        units = channels[0]["units"]
        unit_type_7bit = utb & 0x7F
        calibrated = (utb >> 7) & 1
        cfg_str = ", ".join(f"0x{f:04x}" for f in unique_cfg)
        names_str = ", ".join(unique_names[:5])
        if len(unique_names) > 5:
            names_str += f", ... (+{len(unique_names) - 5} more)"

        print(
            f"({df:>3d}, 0x{utb:02x})      0x{unit_type_7bit:02x}     {calibrated:>3d}  {units:>8s}  {len(channels):>4d}  {unique_files:>6d}  {cfg_str:>30s}  {names_str}"
        )


if __name__ == "__main__":
    main()
