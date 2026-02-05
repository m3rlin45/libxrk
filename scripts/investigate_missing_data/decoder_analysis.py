#!/usr/bin/env python3
"""
Decoder Analysis - Analyzes decoder types and finds skipped channels.

This tool parses XRK files and identifies:
1. Which decoder types are used
2. Which channels are being skipped due to unknown decoder types
3. Patterns in channel metadata that might reveal decoder type meanings
"""

import json
import struct
import sys
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# Known decoder types from aim_xrk.pyx
KNOWN_DECODERS = {
    0: {"type": "i", "desc": "Master Clock on M4GT4?"},
    1: {"type": "H", "desc": "float16, interpolate=True"},
    3: {"type": "i", "desc": "Master Clock on ScottE46?"},
    4: {"type": "h", "desc": "int16"},
    6: {"type": "f", "desc": "float32, interpolate=True"},
    11: {"type": "h", "desc": "int16"},
    12: {"type": "i", "desc": "Predictive Time?"},
    13: {"type": "B", "desc": "uint8, status field?"},
    15: {"type": "H", "desc": "uint16, gear table lookup"},
    20: {"type": "H", "desc": "float16, interpolate=True"},
    24: {"type": "i", "desc": "Best Run Diff?"},
}

# Manual decoder overrides by channel name
MANUAL_DECODERS = {
    "Calculated_Gear",
    "PreCalcGear",
}


def decompress_if_needed(data: bytes) -> bytes:
    """Decompress zlib-compressed data if detected."""
    if len(data) < 2:
        return data
    if data[0] == 0x78 and data[1] in (0x01, 0x9C, 0xDA):
        try:
            return zlib.decompress(data)
        except zlib.error:
            deco = zlib.decompressobj()
            try:
                return deco.decompress(data)
            except zlib.error:
                return deco.flush()
    return data


@dataclass
class ChannelInfo:
    """Detailed channel information."""

    index: int
    short_name: str
    long_name: str
    size: int
    unit_type: int
    unit_str: str
    decoder_type: int
    sample_rate_byte: int
    raw_bytes: bytes
    is_skipped: bool = False
    skip_reason: str = ""


@dataclass
class DecoderAnalysis:
    """Analysis results for decoder types."""

    file_path: str
    channels: list[ChannelInfo] = field(default_factory=list)
    decoder_usage: dict[int, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    skipped_channels: list[ChannelInfo] = field(default_factory=list)


def tokdec(s: str) -> int:
    """Decode a token string to integer."""
    if s:
        return ord(s[0]) + 256 * tokdec(s[1:])
    return 0


def tokenc(i: int) -> str:
    """Encode an integer token to string."""
    s = ""
    while i:
        s += chr(i & 255)
        i >>= 8
    return s


# Unit map from aim_xrk.pyx
UNIT_MAP = {
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
    21: ("V", 1),
    22: ("l", 1),
    24: ("l/s", 0),
    26: ("time?", 0),
    27: ("A", 0),
    30: ("lambda", 2),
    31: ("gear", 0),
    33: ("%", 2),
    43: ("kg", 3),
}


def analyze_xrk(file_path: Path) -> DecoderAnalysis:
    """
    Analyze decoder types in an XRK file.

    Parses the file and identifies all channels, their decoder types,
    and whether they would be skipped by libxrk.
    """
    with open(file_path, "rb") as f:
        raw_data = f.read()

    data = decompress_if_needed(raw_data)
    result = DecoderAnalysis(file_path=str(file_path))

    pos = 0
    ord_lt = ord("<")
    ord_gt = ord(">")

    while pos < len(data):
        if pos + 12 > len(data):
            break

        b0, b1 = data[pos], data[pos + 1]

        # <h messages
        if b0 == ord_lt and b1 == ord("h"):
            tok = struct.unpack_from("<I", data, pos + 2)[0]
            hlen = struct.unpack_from("<i", data, pos + 6)[0]
            cl = data[pos + 11]

            if cl != ord_gt or pos + 12 + hlen + 8 > len(data):
                pos += 1
                continue

            if (tok >> 24) == 32:
                tok -= 32 << 24

            token_str = tokenc(tok)
            msg_data = data[pos + 12 : pos + 12 + hlen]

            # Parse CNF messages recursively
            if token_str == "CNF":
                sub_result = analyze_xrk_bytes(msg_data)
                result.channels.extend(sub_result.channels)
                for d, channels in sub_result.decoder_usage.items():
                    result.decoder_usage[d].extend(channels)
                result.skipped_channels.extend(sub_result.skipped_channels)

            # Parse CHS (channel definition) messages
            if token_str == "CHS" and len(msg_data) >= 128:
                ch_info = parse_channel_info(msg_data)
                result.channels.append(ch_info)
                result.decoder_usage[ch_info.decoder_type].append(ch_info.long_name)

                if ch_info.is_skipped:
                    result.skipped_channels.append(ch_info)

            pos = pos + 12 + hlen + 8
        else:
            pos += 1

    return result


def analyze_xrk_bytes(data: bytes) -> DecoderAnalysis:
    """Analyze decoder types in embedded XRK data."""
    result = DecoderAnalysis(file_path="<embedded>")

    pos = 0
    ord_lt = ord("<")
    ord_gt = ord(">")

    while pos < len(data):
        if pos + 12 > len(data):
            break

        b0, b1 = data[pos], data[pos + 1]

        if b0 == ord_lt and b1 == ord("h"):
            tok = struct.unpack_from("<I", data, pos + 2)[0]
            hlen = struct.unpack_from("<i", data, pos + 6)[0]
            cl = data[pos + 11]

            if cl != ord_gt or pos + 12 + hlen + 8 > len(data):
                pos += 1
                continue

            if (tok >> 24) == 32:
                tok -= 32 << 24

            token_str = tokenc(tok)
            msg_data = data[pos + 12 : pos + 12 + hlen]

            if token_str == "CHS" and len(msg_data) >= 128:
                ch_info = parse_channel_info(msg_data)
                result.channels.append(ch_info)
                result.decoder_usage[ch_info.decoder_type].append(ch_info.long_name)

                if ch_info.is_skipped:
                    result.skipped_channels.append(ch_info)

            pos = pos + 12 + hlen + 8
        else:
            pos += 1

    return result


def parse_channel_info(msg_data: bytes) -> ChannelInfo:
    """Parse a CHS message into ChannelInfo."""
    ch_index = struct.unpack_from("<H", msg_data, 0)[0]
    short_name = msg_data[24:32].split(b"\x00")[0].decode("ascii", errors="replace")
    long_name = msg_data[32:56].split(b"\x00")[0].decode("ascii", errors="replace")
    ch_size = msg_data[56]
    unit_type = msg_data[12] & 127
    decoder_type = msg_data[20]
    sample_rate_byte = msg_data[64] & 127

    # Get unit string
    unit_str = ""
    if unit_type in UNIT_MAP:
        unit_str = UNIT_MAP[unit_type][0]

    # Determine if channel would be skipped
    is_skipped = False
    skip_reason = ""

    if long_name in MANUAL_DECODERS:
        # Has manual decoder, not skipped
        pass
    elif decoder_type not in KNOWN_DECODERS:
        is_skipped = True
        skip_reason = f"Unknown decoder type {decoder_type}"

    return ChannelInfo(
        index=ch_index,
        short_name=short_name,
        long_name=long_name,
        size=ch_size,
        unit_type=unit_type,
        unit_str=unit_str,
        decoder_type=decoder_type,
        sample_rate_byte=sample_rate_byte,
        raw_bytes=msg_data,
        is_skipped=is_skipped,
        skip_reason=skip_reason,
    )


def format_report(result: DecoderAnalysis) -> str:
    """Format analysis result as human-readable report."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"Decoder Analysis Report: {result.file_path}")
    lines.append("=" * 80)
    lines.append("")

    lines.append(f"Total channels: {len(result.channels)}")
    lines.append(f"Skipped channels: {len(result.skipped_channels)}")
    lines.append("")

    # Decoder type summary
    lines.append("DECODER TYPE USAGE:")
    lines.append("-" * 60)
    for decoder_type in sorted(result.decoder_usage.keys()):
        channels = result.decoder_usage[decoder_type]
        known = decoder_type in KNOWN_DECODERS
        status = "KNOWN" if known else "UNKNOWN"
        desc = KNOWN_DECODERS.get(decoder_type, {}).get("desc", "")

        lines.append(f"\n  Decoder {decoder_type:2d} [{status}] {desc}")
        lines.append(f"  Channels ({len(channels)}):")
        for ch in sorted(set(channels)):
            lines.append(f"    - {ch}")

    # Skipped channels detail
    if result.skipped_channels:
        lines.append("")
        lines.append("=" * 60)
        lines.append("SKIPPED CHANNELS (DATA LOSS)")
        lines.append("=" * 60)
        for ch in result.skipped_channels:
            lines.append(f"\n  {ch.long_name}")
            lines.append(f"    Short name: {ch.short_name}")
            lines.append(f"    Decoder type: {ch.decoder_type}")
            lines.append(f"    Size: {ch.size} bytes")
            lines.append(f"    Units: {ch.unit_str} (type {ch.unit_type})")
            lines.append(f"    Sample rate byte: {ch.sample_rate_byte}")
            lines.append(f"    Skip reason: {ch.skip_reason}")

            # Show relevant bytes for reverse engineering
            lines.append(f"    Raw metadata bytes [20]: {ch.raw_bytes[20]:02x}")
            lines.append(f"    Raw metadata bytes [84]: {ch.raw_bytes[84]:02x}")
            lines.append(f"    Raw bytes [12-24]: {ch.raw_bytes[12:24].hex()}")

    # Channel metadata analysis for skipped channels
    if result.skipped_channels:
        lines.append("")
        lines.append("=" * 60)
        lines.append("METADATA PATTERN ANALYSIS FOR UNKNOWN DECODERS")
        lines.append("=" * 60)

        # Group by decoder type
        by_decoder: dict[int, list[ChannelInfo]] = defaultdict(list)
        for ch in result.skipped_channels:
            by_decoder[ch.decoder_type].append(ch)

        for decoder_type, channels in sorted(by_decoder.items()):
            lines.append(f"\n  Decoder {decoder_type} ({len(channels)} channels):")

            # Analyze common byte patterns
            if len(channels) >= 2:
                # Find bytes that are constant across all channels with this decoder
                constant_bytes = []
                varying_bytes = []
                for i in range(min(len(ch.raw_bytes) for ch in channels)):
                    values = set(ch.raw_bytes[i] for ch in channels)
                    if len(values) == 1:
                        constant_bytes.append((i, list(values)[0]))
                    else:
                        varying_bytes.append((i, values))

                if constant_bytes:
                    lines.append(f"    Constant bytes: {constant_bytes[:10]}")
                if varying_bytes:
                    lines.append(f"    Varying byte positions: {[v[0] for v in varying_bytes[:10]]}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Analyze decoder types in XRK files")
    parser.add_argument("files", nargs="+", help="XRK files to analyze")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of report")
    parser.add_argument("--skipped-only", action="store_true", help="Only show skipped channels")
    args = parser.parse_args()

    all_results = []
    all_skipped: dict[int, list[str]] = defaultdict(list)

    for file_path in args.files:
        path = Path(file_path)
        if path.is_dir():
            files = list(path.glob("**/*.xrk")) + list(path.glob("**/*.xrz"))
        else:
            files = [path]

        for f in files:
            print(f"Analyzing {f}...", file=sys.stderr)
            result = analyze_xrk(f)
            all_results.append(result)

            for ch in result.skipped_channels:
                all_skipped[ch.decoder_type].append(ch.long_name)

            if not args.json and not args.skipped_only:
                print(format_report(result))
                print()

    # Summary across all files
    if len(all_results) > 1:
        print("=" * 80, file=sys.stderr if args.json else sys.stdout)
        print("SUMMARY ACROSS ALL FILES", file=sys.stderr if args.json else sys.stdout)
        print("=" * 80, file=sys.stderr if args.json else sys.stdout)

        total_skipped = sum(len(r.skipped_channels) for r in all_results)
        total_channels = sum(len(r.channels) for r in all_results)
        print(f"Total channels: {total_channels}", file=sys.stderr if args.json else sys.stdout)
        print(f"Total skipped: {total_skipped}", file=sys.stderr if args.json else sys.stdout)

        if all_skipped:
            print("\nUnknown decoder types found:", file=sys.stderr if args.json else sys.stdout)
            for decoder, channels in sorted(all_skipped.items()):
                unique = sorted(set(channels))
                print(f"  Decoder {decoder}: {len(unique)} unique channels", file=sys.stderr if args.json else sys.stdout)
                for ch in unique[:5]:
                    print(f"    - {ch}", file=sys.stderr if args.json else sys.stdout)
                if len(unique) > 5:
                    print(f"    ... and {len(unique) - 5} more", file=sys.stderr if args.json else sys.stdout)

    if args.json:
        output = []
        for r in all_results:
            output.append(
                {
                    "file": r.file_path,
                    "total_channels": len(r.channels),
                    "skipped_channels": len(r.skipped_channels),
                    "decoder_usage": {
                        str(k): sorted(set(v)) for k, v in r.decoder_usage.items()
                    },
                    "skipped": [
                        {
                            "name": ch.long_name,
                            "decoder_type": ch.decoder_type,
                            "size": ch.size,
                            "units": ch.unit_str,
                            "reason": ch.skip_reason,
                        }
                        for ch in r.skipped_channels
                    ],
                }
            )
        print(json.dumps(output, indent=2))

    if args.skipped_only:
        print("\nSKIPPED CHANNELS SUMMARY:")
        for decoder, channels in sorted(all_skipped.items()):
            unique = sorted(set(channels))
            print(f"\nDecoder {decoder}:")
            for ch in unique:
                print(f"  - {ch}")


if __name__ == "__main__":
    main()
