#!/usr/bin/env python3
"""
Investigate ODO fuel record mapping.

The parser extracts time/distance for non-fuel ODO records but skips fuel
entries because the conversion formula is unknown. The developer left a clue:
"Fuel Used channel claims 8.56l used (2046.0-2037.4), Fuel Used odo says 70689."

This script extracts ALL ODO records (including fuel) from test XRK files
and tries to decode the 40 unknown bytes in each 64-byte record.
"""

import struct
from pathlib import Path

from unknown_messages import decompress_if_needed, extract_h_messages, hex_dump, tokenc


# Known validation values for non-fuel ODO records:
# 86 "System": time=286193s (79:29:53), dist=5313420m (5313.42km)
# SFJ "System": time=5105s (1:25:05), dist=165858m (165.858km)


def parse_odo_records(odo_data: bytes) -> list[dict]:
    """Parse all 64-byte ODO records from raw ODO message data."""
    records = []
    for i in range(0, len(odo_data), 64):
        chunk = odo_data[i : i + 64]
        if len(chunk) < 64:
            break

        name = chunk[:16].split(b"\x00")[0].decode("ascii", errors="replace")
        is_fuel = name.startswith("Fuel")

        # Known fields (bytes 16-23): two uint32 LE values (time_s, dist_m)
        time_u32 = struct.unpack_from("<I", chunk, 16)[0]
        dist_u32 = struct.unpack_from("<I", chunk, 20)[0]

        # Also try float32 interpretation of bytes 16-23
        time_f32 = struct.unpack_from("<f", chunk, 16)[0]
        dist_f32 = struct.unpack_from("<f", chunk, 20)[0]

        # Parse unknown bytes 24-63 (40 bytes) with multiple interpretations
        unknown_bytes = chunk[24:64]
        uint32_vals = struct.unpack_from("<10I", unknown_bytes, 0)
        int32_vals = struct.unpack_from("<10i", unknown_bytes, 0)
        float32_vals = struct.unpack_from("<10f", unknown_bytes, 0)
        float64_vals = struct.unpack_from("<5d", unknown_bytes, 0)

        records.append(
            {
                "name": name,
                "is_fuel": is_fuel,
                "raw": chunk,
                "time_u32": time_u32,
                "dist_u32": dist_u32,
                "time_f32": time_f32,
                "dist_f32": dist_f32,
                "unknown_bytes": unknown_bytes,
                "uint32_vals": uint32_vals,
                "int32_vals": int32_vals,
                "float32_vals": float32_vals,
                "float64_vals": float64_vals,
            }
        )
    return records


def format_time(seconds: int) -> str:
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def print_record(rec: dict, idx: int) -> None:
    tag = " [FUEL]" if rec["is_fuel"] else ""
    print(f"\n  --- Record [{idx}]: {rec['name']!r}{tag} ---")
    print(hex_dump(rec["raw"]))

    # Known interpretation (uint32 pair at bytes 16-23)
    print(f"\n  Bytes 16-23 as uint32 pair:")
    print(f"    time = {rec['time_u32']} ({format_time(rec['time_u32'])})")
    print(f"    dist = {rec['dist_u32']} ({rec['dist_u32'] / 1000:.3f} km)")

    # Float32 interpretation of bytes 16-23
    print(f"  Bytes 16-23 as float32 pair:")
    print(f"    [16] = {rec['time_f32']:.6g}")
    print(f"    [20] = {rec['dist_f32']:.6g}")

    # Unknown bytes 24-63
    print(f"\n  Unknown bytes 24-63 ({len(rec['unknown_bytes'])} bytes):")
    print(f"    uint32:  {rec['uint32_vals']}")
    print(f"    int32:   {rec['int32_vals']}")
    print(f"    float32: {tuple(f'{v:.6g}' for v in rec['float32_vals'])}")
    print(f"    float64: {tuple(f'{v:.6g}' for v in rec['float64_vals'])}")

    # Detailed per-offset analysis of unknown bytes
    print(f"\n  Per-offset analysis (bytes 24-63):")
    for j in range(0, 40, 4):
        abs_off = 24 + j
        u32 = rec["uint32_vals"][j // 4]
        i32 = rec["int32_vals"][j // 4]
        f32 = rec["float32_vals"][j // 4]
        parts = []
        if u32 != 0:
            parts.append(f"u32={u32}")
            if i32 != u32:
                parts.append(f"i32={i32}")
            if abs(f32) > 1e-10 and abs(f32) < 1e10:
                parts.append(f"f32={f32:.6g}")
            # Also show as u16 pair
            lo16 = u32 & 0xFFFF
            hi16 = (u32 >> 16) & 0xFFFF
            parts.append(f"u16=({lo16}, {hi16})")
        else:
            parts.append("0")
        print(f"    offset {abs_off:2d}: {' | '.join(parts)}")


def try_fuel_conversions(rec: dict) -> None:
    """Try various conversion hypotheses for fuel records."""
    print(f"\n  Fuel conversion hypotheses for {rec['name']!r}:")

    # The developer clue: "Fuel Used odo says 70689" and "8.56l used"
    val = rec["time_u32"]  # This is where the uint32 at offset 16 lives
    if val > 0:
        print(f"    uint32[16] = {val}")
        print(f"      as milliliters:    {val / 1000:.3f} L")
        print(f"      / 8256.19 = {val / 8256.19:.6f} (trying to get 8.56)")
        if val != 0:
            factor = val / 8.56
            print(f"      factor to get 8.56L: {factor:.2f}")

    val2 = rec["dist_u32"]
    if val2 > 0:
        print(f"    uint32[20] = {val2}")
        print(f"      as milliliters:    {val2 / 1000:.3f} L")
        if val2 != 0:
            factor2 = val2 / 8.56
            print(f"      factor to get 8.56L: {factor2:.2f}")

    # Check all uint32 fields in the unknown region too
    for j, val_u in enumerate(rec["uint32_vals"]):
        if val_u == 70689:
            print(f"    ** Found 70689 at unknown offset {24 + j*4}!")
        if val_u > 0:
            # Check if dividing gives something close to 8.56
            ratio = val_u / 8.56
            if 8000 < ratio < 9000:
                print(
                    f"    uint32[{24 + j*4}] = {val_u}: "
                    f"/ 8.56 = {ratio:.1f} (near ~8260 factor)"
                )

    # Check if 70689 appears anywhere as uint32 in the known fields
    if rec["time_u32"] == 70689:
        print(f"    ** 70689 found at offset 16 (time field)!")
    if rec["dist_u32"] == 70689:
        print(f"    ** 70689 found at offset 20 (dist field)!")

    # Try float64 interpretations
    for j, val_f64 in enumerate(rec["float64_vals"]):
        if abs(val_f64) > 1e-10 and abs(val_f64) < 1e10:
            print(f"    float64[{24 + j*8}] = {val_f64:.6g}")


def compare_fuel_vs_nonfuel(fuel_records: list[dict], nonfuel_records: list[dict]) -> None:
    """Compare fuel vs non-fuel records byte-by-byte."""
    if not fuel_records or not nonfuel_records:
        return

    print("\n  Byte-by-byte comparison (fuel vs non-fuel):")
    print("  Offset | Fuel (first) | Non-fuel (first) | Same?")
    print("  " + "-" * 60)

    fuel = fuel_records[0]["raw"]
    nonfuel = nonfuel_records[0]["raw"]

    # Skip name bytes (0-15), compare bytes 16-63
    diff_ranges = []
    in_diff = False
    diff_start = 0

    for off in range(16, 64):
        same = fuel[off] == nonfuel[off]
        if not same and not in_diff:
            diff_start = off
            in_diff = True
        elif same and in_diff:
            diff_ranges.append((diff_start, off))
            in_diff = False
    if in_diff:
        diff_ranges.append((diff_start, 64))

    for start, end in diff_ranges:
        fuel_hex = " ".join(f"{fuel[i]:02x}" for i in range(start, end))
        nonfuel_hex = " ".join(f"{nonfuel[i]:02x}" for i in range(start, end))
        fuel_u32s = []
        nonfuel_u32s = []
        for off in range(start, end, 4):
            if off + 4 <= 64:
                fuel_u32s.append(struct.unpack_from("<I", fuel, off)[0])
                nonfuel_u32s.append(struct.unpack_from("<I", nonfuel, off)[0])
        print(f"  Diff at bytes {start}-{end-1}:")
        print(f"    Fuel:     {fuel_hex}")
        print(f"    Non-fuel: {nonfuel_hex}")
        if fuel_u32s:
            print(f"    Fuel u32:     {fuel_u32s}")
            print(f"    Non-fuel u32: {nonfuel_u32s}")

    if not diff_ranges:
        print("  No differences in bytes 16-63!")


def analyze_file(path: Path) -> list[dict]:
    print(f"\n{'#' * 80}")
    print(f"# FILE: {path.name}")
    print(f"{'#' * 80}")

    with open(path, "rb") as f:
        raw = f.read()
    data = decompress_if_needed(raw)

    messages = extract_h_messages(data)

    odo_token = "ODO"
    if odo_token not in messages:
        print("  No ODO messages found.")
        return []

    print(f"  ODO messages: {len(messages[odo_token])}")

    all_records = []
    for msg_idx, msg_data in enumerate(messages[odo_token]):
        print(
            f"\n  === ODO message [{msg_idx}] ({len(msg_data)} bytes, "
            f"{len(msg_data) // 64} records) ==="
        )

        records = parse_odo_records(msg_data)
        all_records.extend(records)

        fuel_records = [r for r in records if r["is_fuel"]]
        nonfuel_records = [r for r in records if not r["is_fuel"]]

        print(
            f"  Total records: {len(records)} "
            f"(fuel: {len(fuel_records)}, non-fuel: {len(nonfuel_records)})"
        )

        for i, rec in enumerate(records):
            print_record(rec, i)
            if rec["is_fuel"]:
                try_fuel_conversions(rec)

        # Byte-by-byte comparison
        if fuel_records and nonfuel_records:
            compare_fuel_vs_nonfuel(fuel_records, nonfuel_records)

    return all_records


def cross_file_summary(all_results: dict[str, list[dict]]) -> None:
    print(f"\n\n{'=' * 80}")
    print("CROSS-FILE COMPARISON")
    print(f"{'=' * 80}")

    print("\n  ODO record summary by file:")
    for fname, records in all_results.items():
        print(f"\n  {fname}:")
        for rec in records:
            tag = " [FUEL]" if rec["is_fuel"] else ""
            print(
                f"    {rec['name']:20s}{tag:8s} "
                f"time_u32={rec['time_u32']:>10d} "
                f"dist_u32={rec['dist_u32']:>10d}"
            )

    # Show all non-zero unknown bytes across files
    print("\n  Non-zero unknown fields (bytes 24-63) across all records:")
    for fname, records in all_results.items():
        for rec in records:
            nonzero_offsets = []
            for j in range(0, 40, 4):
                if rec["uint32_vals"][j // 4] != 0:
                    nonzero_offsets.append(f"off={24+j}:u32={rec['uint32_vals'][j // 4]}")
            if nonzero_offsets:
                tag = " [FUEL]" if rec["is_fuel"] else ""
                print(f"    {fname} / {rec['name']}{tag}: {', '.join(nonzero_offsets)}")
            else:
                tag = " [FUEL]" if rec["is_fuel"] else ""
                print(f"    {fname} / {rec['name']}{tag}: (all zeros)")


def main() -> None:
    test_data = Path(__file__).resolve().parents[2] / "tests" / "test_data"

    xrk_files = sorted(test_data.glob("**/*.xrk"))
    all_results: dict[str, list[dict]] = {}

    for f in xrk_files:
        records = analyze_file(f)
        if records:
            all_results[f.name] = records

    cross_file_summary(all_results)


if __name__ == "__main__":
    main()
