#!/usr/bin/env python3
"""
CHS Function Field Investigation.

Extracts all CHS fields for every channel in the 86 test file,
correlates with known RS3 function values to identify which CHS
field(s) encode the "function" metadata.

Reuses extract_chs_messages() from chs_byte_analysis.py.
"""

import csv
import io
import math
import struct
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from chs_byte_analysis import extract_chs_messages

# Known function values from RS3 for the 86 test file
KNOWN_FUNCTIONS = {
    "ECT": "Temperature",
    "CAT1": "Exhaust Temperature",
    "AmbientTemp": "Temperature",
    "OilTemp": "Oil Temperature",
    "IntakeAirT": "Intake Air Temperature",
    "LoggerTemp": "Device Temperature",
    "TPMS_Temp_LF": "Temperature",
    "FL_Ch1": "Temperature",
    "LateralAcc": "Lateral Acceleration",
    "InlineAcc": "Inline Acceleration",
    "VerticalAcc": "Vertical Acceleration",
    "YawRate": "Yaw Rate",
    "PitchRate": "Pitch Rate",
    "RollRate": "Roll Rate",
    "RPM": "Engine RPM",
    "Gear": "Gear",
    "BrakePress": "Brake Circuit Pressure",
    "WheelSpdFL": "Rear Wheel Speed",
    "SteerAngle": "Steering Angle",
    "Lambda": "Lambda",
    "TPS": "Percent",
    "External Voltage": "Battery Voltage",
    "MAP": "Pressure",
    "LF_Shock_Pot": "LF Shock Position",
    "Baro": "Pressure",
    "Luminosity": "Device Brightness",
    "SpeedAverage": "Vehicle Speed",
    "TPMS_Press_LF": "Pressure",
    "PPS": "Percentage Throttle Load",
    "ClutchSw": "Number",
    "BrakeSw": "Number",
    "CH": "Number",
    "TPMS_Volt_LF": "Voltage",
    "TPMS_ALM_LF": "Number",
    "Predictive Time": "",  # Not shown in RS3
}

# CHS field extraction definitions: (name, offset, fmt_or_size)
# fmt_or_size: struct format string, or int for raw bytes
CHS_FIELDS = [
    ("index", 0, "<H"),
    ("pad_2_4", 2, 2),
    ("hardware_id", 4, "<H"),
    ("source_channel_id", 6, "<H"),
    ("hardware_ref", 8, "<I"),
    ("unit_type_byte", 12, "<B"),
    ("maybe_display_format", 13, "<B"),
    ("maybe_config_flags", 14, "<H"),
    ("source_type", 16, "<B"),
    ("pad_17_20", 17, 3),
    ("decoder_type", 20, "<B"),
    ("pad_21_24", 21, 3),
    # 24:32 short_name, 32:56 long_name - handled separately
    ("pad_56_64", 56, 8),
    ("sample_period_us", 64, "<I"),
    ("data_offset", 68, "<H"),
    ("pad_70_72", 70, 2),
    ("data_size", 72, "<B"),
    ("pad_73_76", 73, 3),
    ("device_tag", 76, 4),
    ("device_node_id", 80, "<B"),
    ("maybe_device_flags", 81, "<B"),
    ("unknown_82_84", 82, 2),
    ("maybe_output_type", 84, "<B"),
    ("pad_85_88", 85, 3),
    ("display_index", 88, "<I"),
    ("maybe_output_size", 92, "<B"),
    ("pad_93_96", 93, 3),
    ("cal_value_1", 96, "<f"),
    ("cal_value_2", 100, "<f"),
    ("display_range_min", 104, "<f"),
    ("display_range_max", 108, "<f"),
]


def extract_field(raw: bytes, name: str, offset: int, fmt_or_size) -> object:
    """Extract a single field from raw CHS bytes."""
    if isinstance(fmt_or_size, int):
        return raw[offset : offset + fmt_or_size].hex()
    else:
        size = struct.calcsize(fmt_or_size)
        (val,) = struct.unpack_from(fmt_or_size, raw, offset)
        return val


def extract_all_fields(msg: dict) -> dict:
    """Extract all CHS fields from a message."""
    raw = msg["raw"]
    fields = {}
    fields["long_name"] = msg["long_name"]
    fields["short_name"] = msg["short_name"]
    fields["file"] = msg["file"]
    fields["source"] = msg["source"]

    for name, offset, fmt_or_size in CHS_FIELDS:
        fields[name] = extract_field(raw, name, offset, fmt_or_size)

    # Also extract some derived/sub-fields that might be relevant
    fields["unit_type_7bit"] = raw[12] & 0x7F
    fields["calibrated_flag"] = (raw[12] >> 7) & 1
    fields["config_flags_lo"] = raw[14]
    fields["config_flags_hi"] = raw[15]

    return fields


def mutual_information(xs: list, ys: list) -> float:
    """Compute mutual information I(X;Y) in bits."""
    n = len(xs)
    if n == 0:
        return 0.0

    xy_counts = Counter(zip(xs, ys))
    x_counts = Counter(xs)
    y_counts = Counter(ys)

    mi = 0.0
    for (x, y), c_xy in xy_counts.items():
        p_xy = c_xy / n
        p_x = x_counts[x] / n
        p_y = y_counts[y] / n
        if p_xy > 0 and p_x > 0 and p_y > 0:
            mi += p_xy * math.log2(p_xy / (p_x * p_y))
    return mi


def entropy(xs: list) -> float:
    """Compute Shannon entropy H(X) in bits."""
    n = len(xs)
    if n == 0:
        return 0.0
    counts = Counter(xs)
    h = 0.0
    for c in counts.values():
        p = c / n
        if p > 0:
            h -= p * math.log2(p)
    return h


def normalized_mi(xs: list, ys: list) -> float:
    """Compute normalized mutual information NMI = I(X;Y) / H(Y)."""
    h_y = entropy(ys)
    if h_y == 0:
        return 0.0
    return mutual_information(xs, ys) / h_y


def check_determines(field_vals: list, function_vals: list) -> bool:
    """Check if field values uniquely determine function values.

    Returns True if every distinct field value maps to exactly one function.
    """
    mapping = defaultdict(set)
    for fv, fn in zip(field_vals, function_vals):
        mapping[fv].add(fn)
    return all(len(fns) == 1 for fns in mapping.values())


def main():
    test_file_86 = Path(__file__).parent.parent.parent / "tests" / "test_data" / "86"
    xrk_files_86 = sorted(test_file_86.glob("*.xrk"))

    if not xrk_files_86:
        print("ERROR: No 86 test XRK files found", file=sys.stderr)
        sys.exit(1)

    # Also scan all test files for cross-file analysis
    test_data = Path(__file__).parent.parent.parent / "tests" / "test_data"
    all_xrk = sorted(test_data.glob("**/*.xrk"))

    print(f"=== Extracting CHS from 86 test file ===", file=sys.stderr)
    msgs_86 = []
    for f in xrk_files_86:
        print(f"  {f.name}", file=sys.stderr)
        msgs_86.extend(extract_chs_messages(f))
    print(f"  Total CHS messages: {len(msgs_86)}", file=sys.stderr)

    # Extract all fields
    all_fields_86 = [extract_all_fields(m) for m in msgs_86]

    # Add known function labels
    for f in all_fields_86:
        f["function"] = KNOWN_FUNCTIONS.get(f["long_name"], "UNKNOWN")

    # Print CSV of all fields
    print()
    print("=" * 120)
    print("ALL CHS FIELDS FOR 86 TEST FILE")
    print("=" * 120)

    # Select interesting columns for display
    display_cols = [
        "long_name",
        "function",
        "source_channel_id",
        "maybe_display_format",
        "maybe_config_flags",
        "config_flags_lo",
        "config_flags_hi",
        "unit_type_7bit",
        "calibrated_flag",
        "source_type",
        "decoder_type",
        "device_node_id",
        "maybe_device_flags",
        "maybe_output_type",
        "maybe_output_size",
        "hardware_id",
        "hardware_ref",
        "display_index",
    ]

    # Print as aligned table
    header = " | ".join(f"{c:>22s}" for c in display_cols)
    print(header)
    print("-" * len(header))
    for f in sorted(all_fields_86, key=lambda x: x["long_name"]):
        row = " | ".join(f"{str(f.get(c, '')):>22s}" for c in display_cols)
        print(row)

    # ============================================================
    # CORRELATION ANALYSIS
    # ============================================================
    print()
    print("=" * 120)
    print("CORRELATION ANALYSIS: Which field(s) determine 'function'?")
    print("=" * 120)

    # Only use channels with known functions
    labeled = [f for f in all_fields_86 if f["function"] != "UNKNOWN"]
    functions = [f["function"] for f in labeled]

    print(f"\nLabeled channels: {len(labeled)}")
    print(f"Distinct functions: {len(set(functions))}")
    print(f"Function entropy: {entropy(functions):.3f} bits")
    print()

    # Candidate fields to test
    candidate_fields = [
        "source_channel_id",
        "maybe_display_format",
        "maybe_config_flags",
        "config_flags_lo",
        "config_flags_hi",
        "unit_type_7bit",
        "calibrated_flag",
        "source_type",
        "decoder_type",
        "device_node_id",
        "maybe_device_flags",
        "maybe_output_type",
        "maybe_output_size",
        "hardware_id",
        "display_index",
    ]

    print("--- Single-field analysis ---")
    print(f"{'Field':>25s}  {'Unique':>6s}  {'MI':>8s}  {'NMI':>8s}  {'Determines?':>12s}")
    print("-" * 70)

    single_results = []
    for field_name in candidate_fields:
        vals = [f[field_name] for f in labeled]
        mi = mutual_information(vals, functions)
        nmi = normalized_mi(vals, functions)
        determines = check_determines(vals, functions)
        n_unique = len(set(vals))
        single_results.append((field_name, n_unique, mi, nmi, determines))
        print(
            f"{field_name:>25s}  {n_unique:>6d}  {mi:>8.4f}  {nmi:>8.4f}  "
            f"{'YES' if determines else 'no':>12s}"
        )

    # Sort by NMI descending
    print()
    print("--- Ranked by NMI (normalized mutual information) ---")
    for field_name, n_unique, mi, nmi, determines in sorted(
        single_results, key=lambda x: -x[3]
    ):
        det_str = " ** DETERMINES **" if determines else ""
        print(f"  {nmi:.4f}  {field_name} ({n_unique} unique){det_str}")

    # ============================================================
    # 2-FIELD COMBINATIONS
    # ============================================================
    print()
    print("--- 2-field combination analysis ---")
    print("(Only showing combinations that determine function)")
    print()

    found_any = False
    for f1, f2 in combinations(candidate_fields, 2):
        vals = [(f[f1], f[f2]) for f in labeled]
        if check_determines(vals, functions):
            mi = mutual_information(vals, functions)
            nmi = normalized_mi(vals, functions)
            found_any = True
            print(f"  ({f1}, {f2}): NMI={nmi:.4f}, MI={mi:.4f}")

    if not found_any:
        print("  No 2-field combination fully determines function!")

    # ============================================================
    # DETAILED MAPPING for top candidates
    # ============================================================
    print()
    print("=" * 120)
    print("DETAILED MAPPINGS FOR TOP CANDIDATES")
    print("=" * 120)

    # Show mapping for source_channel_id
    for field_name in candidate_fields:
        vals = [f[field_name] for f in labeled]
        if not check_determines(vals, functions):
            # Still show if NMI > 0.5
            nmi = normalized_mi(vals, functions)
            if nmi < 0.5:
                continue
            print(f"\n--- {field_name} (does NOT fully determine, NMI={nmi:.4f}) ---")
        else:
            print(f"\n--- {field_name} (DETERMINES function) ---")

        mapping = defaultdict(set)
        examples = defaultdict(list)
        for f in labeled:
            mapping[f[field_name]].add(f["function"])
            if len(examples[f[field_name]]) < 3:
                examples[f[field_name]].append(f["long_name"])

        for val in sorted(mapping.keys(), key=lambda x: (str(type(x)), x)):
            fns = sorted(mapping[val])
            exs = examples[val]
            fn_str = ", ".join(fns)
            ex_str = ", ".join(exs)
            ambiguous = " ** AMBIGUOUS **" if len(fns) > 1 else ""
            print(f"  {field_name}={val!r:>8s} -> {fn_str:40s} (e.g. {ex_str}){ambiguous}")

    # ============================================================
    # CROSS-FILE STABILITY
    # ============================================================
    print()
    print("=" * 120)
    print("CROSS-FILE STABILITY CHECK")
    print("=" * 120)

    print(f"\nScanning all test files...", file=sys.stderr)
    all_msgs = []
    for f in all_xrk:
        print(f"  {f}", file=sys.stderr)
        all_msgs.extend(extract_chs_messages(f))
    print(f"  Total CHS messages: {len(all_msgs)}", file=sys.stderr)

    all_fields = [extract_all_fields(m) for m in all_msgs]

    # For each candidate field, check if same channel name always has same field value
    print()
    for field_name in candidate_fields:
        by_name = defaultdict(set)
        for f in all_fields:
            by_name[f["long_name"]].add(f[field_name])

        varying = {
            name: vals for name, vals in by_name.items() if len(vals) > 1
        }
        if varying:
            print(
                f"  {field_name}: {len(varying)} channels vary across files"
            )
            for name, vals in sorted(varying.items())[:5]:
                print(f"    {name}: {sorted(vals)}")
        else:
            print(f"  {field_name}: STABLE across all files (same channel -> same value)")

    # ============================================================
    # RAW HEX DUMP of key bytes for labeled channels
    # ============================================================
    print()
    print("=" * 120)
    print("RAW HEX FOR BYTES [6:16] (source_channel_id, hw_ref, unit_type, display_fmt, config_flags, source_type)")
    print("=" * 120)
    print()
    for f in sorted(all_fields_86, key=lambda x: x["long_name"]):
        raw = msgs_86[[m["long_name"] for m in msgs_86].index(f["long_name"])]["raw"]
        fn = KNOWN_FUNCTIONS.get(f["long_name"], "UNKNOWN")
        hex_6_16 = " ".join(f"{raw[i]:02x}" for i in range(6, 16))
        hex_76_96 = " ".join(f"{raw[i]:02x}" for i in range(76, 96))
        print(
            f"  {f['long_name']:25s}  [6:16]={hex_6_16}  "
            f"[76:96]={hex_76_96}  fn={fn}"
        )


if __name__ == "__main__":
    main()
