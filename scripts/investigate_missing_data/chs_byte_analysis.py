#!/usr/bin/env python3
"""
CHS (Channel Definition) Unknown Bytes Analyzer.

Analyzes the 112-byte CHS messages across all XRK files to identify
patterns in the ~72 unknown bytes that aren't currently decoded.

Known CHS layout (112 bytes):
  [0:2]   uint16 LE - channel index
  [2:12]  ???
  [12]    unit_type (lower 7 bits)
  [13]    ???
  [14:20] ???
  [20]    decoder_type
  [21:24] ???
  [24:32] short_name (null-terminated ASCII)
  [32:56] long_name (null-terminated ASCII)
  [56:64] ???
  [64:68] uint32 LE - sample_period_us
  [68:72] ???
  [72]    data_size (bytes per sample)
  [73:112] ???
"""

import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from message_scanner import decompress_if_needed, tokdec, tokenc


CHS_SIZE = 112

# Known byte ranges (offset, length, name)
KNOWN_FIELDS = [
    (0, 2, "index"),
    (12, 1, "unit_type"),
    (20, 1, "decoder_type"),
    (24, 8, "short_name"),
    (32, 24, "long_name"),
    (64, 4, "sample_period_us"),
    (72, 1, "data_size"),
]

# Unknown byte ranges to investigate
UNKNOWN_RANGES = [
    (2, 10, "unk_02_11"),
    (13, 1, "unk_13"),
    (14, 6, "unk_14_19"),
    (21, 3, "unk_21_23"),
    (56, 8, "unk_56_63"),
    (68, 4, "unk_68_71"),
    (73, 39, "unk_73_111"),
]


def extract_chs_messages(file_path: Path) -> list[dict]:
    """Extract all CHS messages from an XRK file, including inside CNF containers."""
    with open(file_path, "rb") as f:
        raw_data = f.read()

    data = decompress_if_needed(raw_data)
    messages = []

    def scan_for_chs(data: bytes, source: str):
        pos = 0
        ord_lt = ord("<")
        ord_gt = ord(">")

        while pos < len(data):
            if pos + 2 > len(data):
                break

            b0, b1 = data[pos], data[pos + 1]

            if b0 == ord_lt and b1 == ord("h"):
                if pos + 12 > len(data):
                    pos += 1
                    continue

                tok = struct.unpack_from("<I", data, pos + 2)[0]
                hlen = struct.unpack_from("<i", data, pos + 6)[0]
                cl = data[pos + 11]

                if cl != ord_gt:
                    pos += 1
                    continue

                if (tok >> 24) == 32:
                    tok -= 32 << 24

                token_str = tokenc(tok)

                if pos + 12 + hlen + 8 > len(data):
                    pos += 1
                    continue

                ftr_pos = pos + 12 + hlen
                ftr_tok = struct.unpack_from("<I", data, ftr_pos + 1)[0]
                ftr_cl = data[ftr_pos + 7]

                if ftr_tok != tok or ftr_cl != ord_gt:
                    pos += 1
                    continue

                msg_data = data[pos + 12 : pos + 12 + hlen]

                if token_str == "CNF":
                    scan_for_chs(msg_data, f"{source}/CNF")

                if token_str == "ENF":
                    scan_for_chs(msg_data, f"{source}/ENF")

                if token_str == "CHS" and len(msg_data) >= CHS_SIZE:
                    raw = bytes(msg_data[:CHS_SIZE])
                    ch_index = struct.unpack_from("<H", raw, 0)[0]
                    short_name = raw[24:32].split(b"\x00")[0].decode("ascii", errors="replace")
                    long_name = raw[32:56].split(b"\x00")[0].decode("ascii", errors="replace")
                    unit_type = raw[12] & 127
                    unit_high_bit = (raw[12] >> 7) & 1
                    decoder_type = raw[20]
                    sample_period_us = struct.unpack_from("<I", raw, 64)[0]
                    data_size = raw[72]

                    messages.append(
                        {
                            "file": str(file_path.name),
                            "source": source,
                            "raw": raw,
                            "index": ch_index,
                            "short_name": short_name,
                            "long_name": long_name,
                            "unit_type": unit_type,
                            "unit_high_bit": unit_high_bit,
                            "decoder_type": decoder_type,
                            "sample_period_us": sample_period_us,
                            "data_size": data_size,
                        }
                    )

                pos = ftr_pos + 8
                continue

            pos += 1

    scan_for_chs(data, str(file_path.name))
    return messages


def entropy(values: list[int]) -> float:
    """Shannon entropy of a list of byte values."""
    if not values:
        return 0.0
    counts = Counter(values)
    total = len(values)
    ent = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            ent -= p * math.log2(p)
    return ent


def per_byte_analysis(all_msgs: list[dict]):
    """Analyze each byte position across all CHS messages."""
    print("=" * 100)
    print("PER-BYTE ANALYSIS (112 bytes)")
    print("=" * 100)
    print()

    n = len(all_msgs)

    # Build known-byte set for highlighting
    known_bytes = set()
    for offset, length, _ in KNOWN_FIELDS:
        for i in range(offset, offset + length):
            known_bytes.add(i)

    print(f"Total CHS messages analyzed: {n}")
    print()
    print(
        f"{'Offset':>6}  {'Status':8}  {'Always0':>7}  {'Const':>5}  "
        f"{'Unique':>6}  {'Min':>5}  {'Max':>5}  {'Entropy':>7}  "
        f"{'Most Common (count)':30}  Notes"
    )
    print("-" * 130)

    for byte_pos in range(CHS_SIZE):
        values = [m["raw"][byte_pos] for m in all_msgs]
        counts = Counter(values)
        unique = len(counts)
        most_common = counts.most_common(5)
        ent = entropy(values)
        min_v = min(values)
        max_v = max(values)
        all_zero = all(v == 0 for v in values)
        constant = unique == 1

        status = "KNOWN" if byte_pos in known_bytes else "???"
        mc_str = ", ".join(f"0x{v:02x}({c})" for v, c in most_common[:3])

        notes = []
        if all_zero:
            notes.append("ALWAYS ZERO")
        elif constant:
            notes.append(f"CONSTANT=0x{values[0]:02x}")
        elif unique <= 3:
            notes.append(f"LOW VARIETY ({unique} vals)")

        print(
            f"  [{byte_pos:3d}]  {status:8}  {'yes' if all_zero else 'no':>7}  "
            f"{'yes' if constant else 'no':>5}  {unique:>6}  "
            f"0x{min_v:02x}  0x{max_v:02x}  {ent:>7.3f}  "
            f"{mc_str:30}  {'; '.join(notes)}"
        )

    print()


def unknown_range_analysis(all_msgs: list[dict]):
    """Detailed analysis of each unknown byte range."""
    print("=" * 100)
    print("UNKNOWN RANGE ANALYSIS")
    print("=" * 100)

    for offset, length, name in UNKNOWN_RANGES:
        print()
        print(f"--- {name}: bytes [{offset}:{offset + length}] ({length} bytes) ---")
        print()

        # Extract raw bytes for this range
        ranges = [m["raw"][offset : offset + length] for m in all_msgs]

        # Check if always zero
        if all(all(b == 0 for b in r) for r in ranges):
            print("  => ALL ZEROS across all channels/files")
            continue

        # Check if constant
        unique_ranges = set(r for r in ranges)
        if len(unique_ranges) == 1:
            val = ranges[0]
            print(f"  => CONSTANT: {val.hex()}")
            continue

        print(f"  Unique patterns: {len(unique_ranges)} / {len(ranges)} total")

        # Try multi-type interpretations
        print()
        print("  Multi-type interpretations:")

        # uint16 LE pairs
        if length >= 2 and length % 2 == 0:
            for sub_off in range(0, length, 2):
                abs_off = offset + sub_off
                vals = [struct.unpack_from("<H", m["raw"], abs_off)[0] for m in all_msgs]
                svals = [struct.unpack_from("<h", m["raw"], abs_off)[0] for m in all_msgs]
                unique_u = sorted(set(vals))
                unique_s = sorted(set(svals))
                if len(unique_u) == 1 and unique_u[0] == 0:
                    print(f"    uint16 LE [{abs_off}:{abs_off+2}]: always 0")
                else:
                    print(
                        f"    uint16 LE [{abs_off}:{abs_off+2}]: "
                        f"{len(unique_u)} unique, range [{min(vals)}, {max(vals)}]"
                        f" | top: {Counter(vals).most_common(5)}"
                    )
                    if any(v < 0 for v in svals):
                        print(
                            f"    int16  LE [{abs_off}:{abs_off+2}]: "
                            f"range [{min(svals)}, {max(svals)}]"
                        )

        # uint32 LE
        if length >= 4 and length % 4 == 0:
            for sub_off in range(0, length, 4):
                abs_off = offset + sub_off
                vals = [struct.unpack_from("<I", m["raw"], abs_off)[0] for m in all_msgs]
                fvals = [struct.unpack_from("<f", m["raw"], abs_off)[0] for m in all_msgs]
                unique_u = sorted(set(vals))
                if len(unique_u) == 1 and unique_u[0] == 0:
                    print(f"    uint32 LE [{abs_off}:{abs_off+4}]: always 0")
                else:
                    print(
                        f"    uint32 LE [{abs_off}:{abs_off+4}]: "
                        f"{len(unique_u)} unique, range [{min(vals)}, {max(vals)}]"
                        f" | top: {Counter(vals).most_common(5)}"
                    )

                # float32 - only show if values look reasonable (not NaN/inf)
                valid_floats = [v for v in fvals if not (math.isnan(v) or math.isinf(v))]
                if valid_floats and not (len(unique_u) == 1 and unique_u[0] == 0):
                    print(
                        f"    float32 LE [{abs_off}:{abs_off+4}]: "
                        f"range [{min(valid_floats):.6g}, {max(valid_floats):.6g}]"
                        f" | top: {Counter(fvals).most_common(5)}"
                    )

        # ASCII interpretation
        raw_concat = b"".join(ranges)
        printable_ratio = sum(1 for b in raw_concat if 32 <= b < 127) / max(len(raw_concat), 1)
        if printable_ratio > 0.5:
            print(f"    ASCII (printable ratio {printable_ratio:.0%}):")
            seen = set()
            for m in all_msgs:
                s = m["raw"][offset : offset + length].decode("ascii", errors="replace")
                if s not in seen:
                    seen.add(s)
                    print(f"      '{s}' ({m['long_name']})")
                    if len(seen) >= 10:
                        print(f"      ... and {len(unique_ranges) - 10} more")
                        break

        # Show values grouped by channel name
        print()
        print("  Values by channel (sample):")
        by_name = defaultdict(list)
        for m in all_msgs:
            by_name[m["long_name"]].append(m["raw"][offset : offset + length])

        shown = 0
        for ch_name, vals in sorted(by_name.items()):
            if shown >= 20:
                print(f"  ... and {len(by_name) - shown} more channels")
                break
            unique_for_ch = set(v.hex() for v in vals)
            if len(unique_for_ch) == 1:
                print(f"    {ch_name:30s}: {vals[0].hex(sep=' ')}")
            else:
                print(f"    {ch_name:30s}: {len(unique_for_ch)} different across files")
                for v in sorted(unique_for_ch)[:3]:
                    print(f"      {v}")
            shown += 1

    print()


def group_by_analysis(all_msgs: list[dict]):
    """Analyze unknown bytes grouped by decoder_type and unit_type."""
    print("=" * 100)
    print("GROUP-BY ANALYSIS")
    print("=" * 100)

    # Group by decoder_type
    print()
    print("--- By decoder_type ---")
    by_decoder = defaultdict(list)
    for m in all_msgs:
        by_decoder[m["decoder_type"]].append(m)

    for dt, msgs in sorted(by_decoder.items()):
        ch_names = sorted(set(m["long_name"] for m in msgs))
        print(
            f"\n  Decoder {dt} ({len(msgs)} channels: {', '.join(ch_names[:5])}"
            f"{'...' if len(ch_names) > 5 else ''})"
        )

        for offset, length, name in UNKNOWN_RANGES:
            ranges = [m["raw"][offset : offset + length] for m in msgs]
            unique = set(r.hex() for r in ranges)
            if len(unique) == 1:
                val = ranges[0]
                if all(b == 0 for b in val):
                    print(f"    {name:15s}: constant (all zeros)")
                else:
                    print(f"    {name:15s}: constant = {val.hex(sep=' ')}")
            else:
                print(f"    {name:15s}: {len(unique)} unique patterns")

    # Group by unit_type
    print()
    print("--- By unit_type ---")
    by_unit = defaultdict(list)
    for m in all_msgs:
        by_unit[m["unit_type"]].append(m)

    for ut, msgs in sorted(by_unit.items()):
        ch_names = sorted(set(m["long_name"] for m in msgs))
        print(
            f"\n  Unit type {ut} ({len(msgs)} channels: {', '.join(ch_names[:5])}"
            f"{'...' if len(ch_names) > 5 else ''})"
        )

        for offset, length, name in UNKNOWN_RANGES:
            ranges = [m["raw"][offset : offset + length] for m in msgs]
            unique = set(r.hex() for r in ranges)
            if len(unique) == 1:
                val = ranges[0]
                if all(b == 0 for b in val):
                    print(f"    {name:15s}: constant (all zeros)")
                else:
                    print(f"    {name:15s}: constant = {val.hex(sep=' ')}")
            else:
                print(f"    {name:15s}: {len(unique)} unique patterns")

    print()


def correlation_analysis(all_msgs: list[dict]):
    """Check if unknown bytes correlate with known properties."""
    print("=" * 100)
    print("CORRELATION ANALYSIS")
    print("=" * 100)
    print()

    # For each unknown range, check if its value is fully determined by decoder_type
    for offset, length, name in UNKNOWN_RANGES:
        all_zero = all(all(b == 0 for b in m["raw"][offset : offset + length]) for m in all_msgs)
        if all_zero:
            continue

        print(f"--- {name} [{offset}:{offset+length}] ---")

        # Check correlation with decoder_type
        by_decoder = defaultdict(set)
        for m in all_msgs:
            by_decoder[m["decoder_type"]].add(m["raw"][offset : offset + length].hex())

        all_unique_in_decoder = all(len(v) == 1 for v in by_decoder.values())
        if all_unique_in_decoder:
            print(f"  FULLY DETERMINED by decoder_type!")
            for dt, vals in sorted(by_decoder.items()):
                print(f"    decoder {dt}: {list(vals)[0]}")
        else:
            varying = {dt: vals for dt, vals in by_decoder.items() if len(vals) > 1}
            print(f"  Varies within decoder_type for: " f"{sorted(varying.keys())}")

        # Check correlation with unit_type
        by_unit = defaultdict(set)
        for m in all_msgs:
            by_unit[m["unit_type"]].add(m["raw"][offset : offset + length].hex())

        all_unique_in_unit = all(len(v) == 1 for v in by_unit.values())
        if all_unique_in_unit:
            print(f"  FULLY DETERMINED by unit_type!")
            for ut, vals in sorted(by_unit.items()):
                print(f"    unit {ut}: {list(vals)[0]}")
        else:
            varying = {ut: vals for ut, vals in by_unit.items() if len(vals) > 1}
            print(f"  Varies within unit_type for: " f"{sorted(varying.keys())}")

        # Check correlation with (decoder_type, unit_type) pair
        by_pair = defaultdict(set)
        for m in all_msgs:
            key = (m["decoder_type"], m["unit_type"])
            by_pair[key].add(m["raw"][offset : offset + length].hex())

        all_unique_in_pair = all(len(v) == 1 for v in by_pair.values())
        if all_unique_in_pair and not all_unique_in_decoder:
            print(f"  FULLY DETERMINED by (decoder_type, unit_type) pair!")

        # Check correlation with data_size
        by_size = defaultdict(set)
        for m in all_msgs:
            by_size[m["data_size"]].add(m["raw"][offset : offset + length].hex())

        all_unique_in_size = all(len(v) == 1 for v in by_size.values())
        if all_unique_in_size and not all_unique_in_decoder:
            print(f"  FULLY DETERMINED by data_size!")
            for ds, vals in sorted(by_size.items()):
                print(f"    size {ds}: {list(vals)[0]}")

        print()


def cross_file_analysis(all_msgs: list[dict]):
    """Compare unknown bytes for same channel across different files."""
    print("=" * 100)
    print("CROSS-FILE COMPARISON")
    print("=" * 100)
    print()

    # Group by channel name across files
    by_name = defaultdict(list)
    for m in all_msgs:
        by_name[m["long_name"]].append(m)

    # Only look at channels appearing in multiple files
    multi_file = {
        name: msgs for name, msgs in by_name.items() if len(set(m["file"] for m in msgs)) > 1
    }

    if not multi_file:
        print("No channels found across multiple files.")
        return

    print(f"Channels appearing in multiple files: {len(multi_file)}")
    print()

    for ch_name, msgs in sorted(multi_file.items()):
        files = sorted(set(m["file"] for m in msgs))
        print(f"  {ch_name} (in {len(files)} files)")

        # Compare unknown bytes across files
        all_identical = True
        for offset, length, name in UNKNOWN_RANGES:
            ranges = set(m["raw"][offset : offset + length].hex() for m in msgs)
            if len(ranges) > 1:
                all_identical = False
                print(f"    {name}: DIFFERS across files")
                for m in msgs:
                    val = m["raw"][offset : offset + length].hex(sep=" ")
                    print(f"      {m['file'][:40]:40s}: {val}")

        if all_identical:
            print(f"    All unknown bytes IDENTICAL across files")

    print()


def byte_12_high_bit_analysis(all_msgs: list[dict]):
    """Specifically analyze byte 12 high bit (currently masked out by & 127)."""
    print("=" * 100)
    print("BYTE 12 HIGH BIT ANALYSIS")
    print("=" * 100)
    print()

    for m in all_msgs:
        high_bit = m["unit_high_bit"]
        if high_bit:
            print(
                f"  {m['long_name']:30s} unit_type={m['unit_type']:3d} "
                f"high_bit=1 decoder={m['decoder_type']} "
                f"file={m['file']}"
            )

    if not any(m["unit_high_bit"] for m in all_msgs):
        print("  High bit of byte 12 is NEVER SET across all channels.")

    print()


def full_hex_dump(all_msgs: list[dict]):
    """Dump all CHS messages with annotations for review."""
    print("=" * 100)
    print("ANNOTATED HEX DUMP (first 5 unique channels per decoder type)")
    print("=" * 100)

    by_decoder = defaultdict(list)
    for m in all_msgs:
        by_decoder[m["decoder_type"]].append(m)

    for dt, msgs in sorted(by_decoder.items()):
        print(f"\n--- Decoder type {dt} ---")
        seen_names = set()
        for m in msgs:
            if m["long_name"] in seen_names:
                continue
            seen_names.add(m["long_name"])
            if len(seen_names) > 5:
                print(f"  ... and {len(set(mm['long_name'] for mm in msgs)) - 5} more")
                break

            raw = m["raw"]
            print(
                f"\n  Channel: {m['long_name']} ({m['short_name']})"
                f"  idx={m['index']} unit={m['unit_type']} "
                f"size={m['data_size']} period={m['sample_period_us']}us"
            )
            print(f"  File: {m['file']}")

            # Print hex with offset annotations
            for row_start in range(0, CHS_SIZE, 16):
                row_end = min(row_start + 16, CHS_SIZE)
                hex_vals = " ".join(f"{raw[i]:02x}" for i in range(row_start, row_end))

                # Annotate known fields
                annotations = []
                for koff, klen, kname in KNOWN_FIELDS:
                    if row_start <= koff < row_end or row_start < koff + klen <= row_end:
                        annotations.append(kname)

                ann_str = f"  <- {', '.join(annotations)}" if annotations else ""
                print(f"    [{row_start:3d}] {hex_vals:48s}{ann_str}")

    print()


def summary(all_msgs: list[dict]):
    """Print a summary of findings."""
    print("=" * 100)
    print("SUMMARY OF FINDINGS")
    print("=" * 100)
    print()

    # Categorize each unknown range
    for offset, length, name in UNKNOWN_RANGES:
        all_zero = all(all(b == 0 for b in m["raw"][offset : offset + length]) for m in all_msgs)
        if all_zero:
            status = "ALWAYS ZERO (padding)"
        else:
            unique = set(m["raw"][offset : offset + length].hex() for m in all_msgs)
            if len(unique) == 1:
                status = f"CONSTANT = {list(unique)[0]}"
            else:
                # Check if determined by decoder_type
                by_dt = defaultdict(set)
                for m in all_msgs:
                    by_dt[m["decoder_type"]].add(m["raw"][offset : offset + length].hex())
                if all(len(v) == 1 for v in by_dt.values()):
                    status = f"VARIES but determined by decoder_type ({len(unique)} patterns)"
                else:
                    # Check by (decoder, unit) pair
                    by_pair = defaultdict(set)
                    for m in all_msgs:
                        key = (m["decoder_type"], m["unit_type"])
                        by_pair[key].add(m["raw"][offset : offset + length].hex())
                    if all(len(v) == 1 for v in by_pair.values()):
                        status = f"VARIES but determined by (decoder_type, unit_type) ({len(unique)} patterns)"
                    else:
                        status = f"VARIES per channel ({len(unique)} unique patterns)"

        print(f"  [{offset:3d}:{offset+length:3d}] {name:15s}: {status}")

    print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python chs_byte_analysis.py <xrk_file_or_directory>")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(target.glob("**/*.xrk")) + sorted(target.glob("**/*.xrz"))
    else:
        print(f"Error: {target} not found")
        sys.exit(1)

    if not files:
        print(f"No XRK/XRZ files found in {target}")
        sys.exit(1)

    all_msgs = []
    for file_path in sorted(files):
        print(f"Extracting CHS from {file_path}...", file=sys.stderr)
        msgs = extract_chs_messages(file_path)
        all_msgs.extend(msgs)
        print(f"  Found {len(msgs)} CHS messages", file=sys.stderr)

    print(f"\nTotal CHS messages: {len(all_msgs)}", file=sys.stderr)
    print(
        f"Unique channels: {len(set(m['long_name'] for m in all_msgs))}",
        file=sys.stderr,
    )
    print(
        f"Files: {len(set(m['file'] for m in all_msgs))}",
        file=sys.stderr,
    )
    print(file=sys.stderr)

    # Run all analyses
    per_byte_analysis(all_msgs)
    unknown_range_analysis(all_msgs)
    byte_12_high_bit_analysis(all_msgs)
    group_by_analysis(all_msgs)
    correlation_analysis(all_msgs)
    cross_file_analysis(all_msgs)
    full_hex_dump(all_msgs)
    summary(all_msgs)


if __name__ == "__main__":
    main()
