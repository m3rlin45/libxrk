#!/usr/bin/env python3
"""
XRK Message Scanner - Catalogs ALL message types in XRK files.

This tool scans raw XRK bytes to identify all message tokens,
including unknown/unhandled ones, and reports statistics.
"""

import json
import struct
import sys
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def tokdec(s: str) -> int:
    """Decode a token string like 'GPS' to integer."""
    if s:
        return ord(s[0]) + 256 * tokdec(s[1:])
    return 0


def tokenc(i: int) -> str:
    """Encode an integer token to string like 'GPS'."""
    s = ""
    while i:
        s += chr(i & 255)
        i >>= 8
    return s


@dataclass
class MessageStats:
    """Statistics for a message type."""

    count: int = 0
    sizes: list[int] = field(default_factory=list)
    sample_data: list[bytes] = field(default_factory=list)


@dataclass
class ScanResult:
    """Results from scanning an XRK file."""

    file_path: str
    file_size: int
    bytes_consumed: int = 0
    bad_bytes: int = 0
    h_messages: dict[str, MessageStats] = field(default_factory=lambda: defaultdict(MessageStats))
    s_messages: int = 0  # (S sample messages
    g_messages: int = 0  # (G group messages
    m_messages: int = 0  # (M multi-sample messages
    c_messages: int = 0  # (c expansion channel messages
    unknown_tokens: set = field(default_factory=set)
    decoder_types_seen: dict[int, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )  # decoder_type -> [channel_names]
    channel_metadata: list[dict] = field(default_factory=list)  # Raw channel data for analysis


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


def scan_xrk(file_path: Path, max_samples: int = 3) -> ScanResult:
    """
    Scan an XRK file and catalog all message types.

    Args:
        file_path: Path to XRK/XRZ file
        max_samples: Maximum sample data to keep per message type

    Returns:
        ScanResult with message statistics
    """
    with open(file_path, "rb") as f:
        raw_data = f.read()

    data = decompress_if_needed(raw_data)
    result = ScanResult(file_path=str(file_path), file_size=len(data))

    pos = 0
    bad_bytes = 0

    # Known message tokens
    ord_op = ord("(")
    ord_cp = ord(")")
    ord_lt = ord("<")
    ord_gt = ord(">")

    # Track channel sizes from CHS messages for parsing S/G/M messages
    channel_sizes: dict[int, int] = {}  # channel_index -> size
    group_sizes: dict[int, int] = {}  # group_index -> total size

    while pos < len(data):
        try:
            if pos + 2 > len(data):
                break

            b0, b1 = data[pos], data[pos + 1]

            # <h messages (header messages like GPS, CHS, LAP, etc.)
            if b0 == ord_lt and b1 == ord("h"):
                if pos + 12 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                tok = struct.unpack_from("<I", data, pos + 2)[0]
                hlen = struct.unpack_from("<i", data, pos + 6)[0]
                ver = data[pos + 10]
                cl = data[pos + 11]

                if cl != ord_gt:
                    bad_bytes += 1
                    pos += 1
                    continue

                # Strip trailing space from token
                if (tok >> 24) == 32:
                    tok -= 32 << 24

                token_str = tokenc(tok)

                if pos + 12 + hlen + 8 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                # Verify footer
                ftr_pos = pos + 12 + hlen
                ftr_tok = struct.unpack_from("<I", data, ftr_pos + 1)[0]
                ftr_cl = data[ftr_pos + 7]

                if ftr_tok != tok or ftr_cl != ord_gt:
                    bad_bytes += 1
                    pos += 1
                    continue

                msg_data = data[pos + 12 : pos + 12 + hlen]

                # Record stats
                stats = result.h_messages[token_str]
                stats.count += 1
                stats.sizes.append(hlen)
                if len(stats.sample_data) < max_samples:
                    stats.sample_data.append(msg_data[:256])  # Keep first 256 bytes

                # Parse CNF messages recursively to find CHS/GRP
                if token_str == "CNF":
                    sub_result = scan_xrk_bytes(msg_data, max_samples)
                    for k, v in sub_result.h_messages.items():
                        result.h_messages[k].count += v.count
                        result.h_messages[k].sizes.extend(v.sizes)
                        if len(result.h_messages[k].sample_data) < max_samples:
                            result.h_messages[k].sample_data.extend(
                                v.sample_data[: max_samples - len(result.h_messages[k].sample_data)]
                            )
                    result.channel_metadata.extend(sub_result.channel_metadata)
                    result.decoder_types_seen.update(sub_result.decoder_types_seen)

                # Parse CHS (channel definition) messages
                if token_str == "CHS" and len(msg_data) >= 128:
                    ch_index = struct.unpack_from("<H", msg_data, 0)[0]
                    short_name = msg_data[24:32].split(b"\x00")[0].decode("ascii", errors="replace")
                    long_name = msg_data[32:56].split(b"\x00")[0].decode("ascii", errors="replace")
                    ch_size = msg_data[56]
                    unit_type = msg_data[12] & 127
                    decoder_type = msg_data[20]
                    sample_rate_byte = msg_data[64] & 127

                    channel_sizes[ch_index] = ch_size

                    # Record decoder type usage
                    result.decoder_types_seen[decoder_type].append(long_name)

                    # Record full channel metadata
                    result.channel_metadata.append(
                        {
                            "index": ch_index,
                            "short_name": short_name,
                            "long_name": long_name,
                            "size": ch_size,
                            "unit_type": unit_type,
                            "decoder_type": decoder_type,
                            "sample_rate_byte": sample_rate_byte,
                            "raw_bytes": msg_data.hex(),
                        }
                    )

                # Parse GRP (group definition) messages
                if token_str == "GRP" and len(msg_data) >= 4:
                    grp_index = struct.unpack_from("<H", msg_data, 0)[0]
                    ch_count = struct.unpack_from("<H", msg_data, 2)[0]
                    if len(msg_data) >= 4 + ch_count * 2:
                        channels = struct.unpack_from(f"<{ch_count}H", msg_data, 4)
                        total_size = sum(channel_sizes.get(ch, 0) for ch in channels)
                        group_sizes[grp_index] = total_size

                # Parse ENF (expansion config) messages recursively
                if token_str == "ENF":
                    sub_result = scan_xrk_bytes(msg_data, max_samples)
                    for k, v in sub_result.h_messages.items():
                        # Prefix with ENF/ to distinguish
                        key = f"ENF/{k}"
                        result.h_messages[key].count += v.count
                        result.h_messages[key].sizes.extend(v.sizes)
                        if len(result.h_messages[key].sample_data) < max_samples:
                            result.h_messages[key].sample_data.extend(
                                v.sample_data[
                                    : max_samples - len(result.h_messages[key].sample_data)
                                ]
                            )

                result.bytes_consumed += 12 + hlen + 8
                pos = ftr_pos + 8
                continue

            # (S sample messages
            if b0 == ord_op and b1 == ord("S"):
                if pos + 9 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                timecode = struct.unpack_from("<i", data, pos + 2)[0]
                ch_index = struct.unpack_from("<H", data, pos + 6)[0]

                ch_size = channel_sizes.get(ch_index, 2)  # Default to 2 if unknown
                msg_len = 9 + ch_size
                if pos + msg_len > len(data) or data[pos + msg_len - 1] != ord_cp:
                    bad_bytes += 1
                    pos += 1
                    continue

                result.s_messages += 1
                result.bytes_consumed += msg_len
                pos += msg_len
                continue

            # (G group messages
            if b0 == ord_op and b1 == ord("G"):
                if pos + 9 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                timecode = struct.unpack_from("<i", data, pos + 2)[0]
                grp_index = struct.unpack_from("<H", data, pos + 6)[0]

                grp_size = group_sizes.get(grp_index, 0)
                msg_len = 9 + grp_size
                if pos + msg_len > len(data) or data[pos + msg_len - 1] != ord_cp:
                    bad_bytes += 1
                    pos += 1
                    continue

                result.g_messages += 1
                result.bytes_consumed += msg_len
                pos += msg_len
                continue

            # (M multi-sample messages
            if b0 == ord_op and b1 == ord("M"):
                if pos + 11 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                timecode = struct.unpack_from("<i", data, pos + 2)[0]
                ch_index = struct.unpack_from("<H", data, pos + 6)[0]
                count = struct.unpack_from("<H", data, pos + 8)[0]

                ch_size = channel_sizes.get(ch_index, 2)
                msg_len = 11 + ch_size * count
                if pos + msg_len > len(data) or data[pos + msg_len - 1] != ord_cp:
                    bad_bytes += 1
                    pos += 1
                    continue

                result.m_messages += 1
                result.bytes_consumed += msg_len
                pos += msg_len
                continue

            # (c expansion channel messages
            if b0 == ord_op and b1 == ord("c"):
                if pos + 12 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                # Parse c message header
                unk1 = data[pos + 2]
                ch_field = struct.unpack_from("<H", data, pos + 3)[0]
                ch_index = ch_field >> 3
                timecode = struct.unpack_from("<i", data, pos + 7)[0]

                ch_size = channel_sizes.get(ch_index, 2)
                msg_len = 12 + ch_size
                if pos + msg_len > len(data) or data[pos + msg_len - 1] != ord_cp:
                    bad_bytes += 1
                    pos += 1
                    continue

                result.c_messages += 1
                result.bytes_consumed += msg_len
                pos += msg_len
                continue

            # Unknown byte - skip
            bad_bytes += 1
            pos += 1

        except Exception as e:
            bad_bytes += 1
            pos += 1

    result.bad_bytes = bad_bytes
    return result


def scan_xrk_bytes(data: bytes, max_samples: int = 3) -> ScanResult:
    """Scan XRK data from bytes (for recursive parsing)."""
    result = ScanResult(file_path="<embedded>", file_size=len(data))

    pos = 0
    bad_bytes = 0

    ord_lt = ord("<")
    ord_gt = ord(">")

    while pos < len(data):
        try:
            if pos + 2 > len(data):
                break

            b0, b1 = data[pos], data[pos + 1]

            # <h messages
            if b0 == ord_lt and b1 == ord("h"):
                if pos + 12 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                tok = struct.unpack_from("<I", data, pos + 2)[0]
                hlen = struct.unpack_from("<i", data, pos + 6)[0]
                ver = data[pos + 10]
                cl = data[pos + 11]

                if cl != ord_gt:
                    bad_bytes += 1
                    pos += 1
                    continue

                if (tok >> 24) == 32:
                    tok -= 32 << 24

                token_str = tokenc(tok)

                if pos + 12 + hlen + 8 > len(data):
                    bad_bytes += 1
                    pos += 1
                    continue

                ftr_pos = pos + 12 + hlen
                ftr_tok = struct.unpack_from("<I", data, ftr_pos + 1)[0]
                ftr_cl = data[ftr_pos + 7]

                if ftr_tok != tok or ftr_cl != ord_gt:
                    bad_bytes += 1
                    pos += 1
                    continue

                msg_data = data[pos + 12 : pos + 12 + hlen]

                stats = result.h_messages[token_str]
                stats.count += 1
                stats.sizes.append(hlen)
                if len(stats.sample_data) < max_samples:
                    stats.sample_data.append(msg_data[:256])

                # Parse CHS messages for decoder type tracking
                if token_str == "CHS" and len(msg_data) >= 128:
                    ch_index = struct.unpack_from("<H", msg_data, 0)[0]
                    long_name = msg_data[32:56].split(b"\x00")[0].decode("ascii", errors="replace")
                    decoder_type = msg_data[20]
                    result.decoder_types_seen[decoder_type].append(long_name)
                    result.channel_metadata.append(
                        {
                            "index": ch_index,
                            "long_name": long_name,
                            "decoder_type": decoder_type,
                            "raw_bytes": msg_data.hex(),
                        }
                    )

                result.bytes_consumed += 12 + hlen + 8
                pos = ftr_pos + 8
                continue

            bad_bytes += 1
            pos += 1

        except Exception:
            bad_bytes += 1
            pos += 1

    result.bad_bytes = bad_bytes
    return result


def format_report(result: ScanResult) -> str:
    """Format a scan result as a human-readable report."""
    lines = []
    lines.append(f"=" * 80)
    lines.append(f"XRK Message Scan Report: {result.file_path}")
    lines.append(f"=" * 80)
    lines.append("")
    lines.append(f"File size: {result.file_size:,} bytes")
    lines.append(
        f"Bytes consumed: {result.bytes_consumed:,} bytes ({100*result.bytes_consumed/result.file_size:.1f}%)"
    )
    lines.append(
        f"Bad/unknown bytes: {result.bad_bytes:,} bytes ({100*result.bad_bytes/result.file_size:.1f}%)"
    )
    lines.append("")

    lines.append("SAMPLE DATA MESSAGES:")
    lines.append(f"  (S sample messages: {result.s_messages:,}")
    lines.append(f"  (G group messages: {result.g_messages:,}")
    lines.append(f"  (M multi-sample messages: {result.m_messages:,}")
    lines.append(f"  (c expansion messages: {result.c_messages:,}")
    lines.append("")

    lines.append("HEADER MESSAGES BY TYPE:")
    for token, stats in sorted(result.h_messages.items(), key=lambda x: -x[1].count):
        min_size = min(stats.sizes) if stats.sizes else 0
        max_size = max(stats.sizes) if stats.sizes else 0
        avg_size = sum(stats.sizes) / len(stats.sizes) if stats.sizes else 0
        lines.append(
            f"  {token:8s}: count={stats.count:5d}, size={min_size}-{max_size} (avg {avg_size:.0f})"
        )

    lines.append("")
    lines.append("DECODER TYPES USED:")
    known_decoders = {0, 1, 3, 4, 6, 11, 12, 13, 15, 20, 24}
    for decoder_type, channels in sorted(result.decoder_types_seen.items()):
        status = "KNOWN" if decoder_type in known_decoders else "UNKNOWN - DROPPED"
        unique_channels = sorted(set(channels))
        lines.append(f"  Decoder {decoder_type:2d} [{status:16s}]: {len(unique_channels)} channels")
        if decoder_type not in known_decoders:
            for ch in unique_channels[:10]:
                lines.append(f"    - {ch}")
            if len(unique_channels) > 10:
                lines.append(f"    ... and {len(unique_channels) - 10} more")

    return "\n".join(lines)


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python message_scanner.py <xrk_file> [--json]")
        print("       python message_scanner.py <directory> [--json]")
        sys.exit(1)

    target = Path(sys.argv[1])
    json_output = "--json" in sys.argv

    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = list(target.glob("**/*.xrk")) + list(target.glob("**/*.xrz"))
    else:
        print(f"Error: {target} not found")
        sys.exit(1)

    all_results = []
    for file_path in sorted(files):
        print(f"Scanning {file_path}...", file=sys.stderr)
        result = scan_xrk(file_path)
        all_results.append(result)

        if not json_output:
            print(format_report(result))
            print("")

    if json_output:
        # Convert to JSON-serializable format
        output = []
        for r in all_results:
            output.append(
                {
                    "file": r.file_path,
                    "file_size": r.file_size,
                    "bytes_consumed": r.bytes_consumed,
                    "bad_bytes": r.bad_bytes,
                    "s_messages": r.s_messages,
                    "g_messages": r.g_messages,
                    "m_messages": r.m_messages,
                    "c_messages": r.c_messages,
                    "h_messages": {
                        k: {
                            "count": v.count,
                            "sizes": v.sizes,
                            "samples": [s.hex() for s in v.sample_data],
                        }
                        for k, v in r.h_messages.items()
                    },
                    "decoder_types": {str(k): v for k, v in r.decoder_types_seen.items()},
                    "channel_metadata": r.channel_metadata,
                }
            )
        print(json.dumps(output, indent=2))

    # Summary across all files
    if len(all_results) > 1 and not json_output:
        print("=" * 80)
        print("SUMMARY ACROSS ALL FILES")
        print("=" * 80)

        all_tokens: set[str] = set()
        all_decoders: dict[int, set[str]] = defaultdict(set)
        for r in all_results:
            all_tokens.update(r.h_messages.keys())
            for d, chs in r.decoder_types_seen.items():
                all_decoders[d].update(chs)

        print(f"\nAll message tokens seen: {sorted(all_tokens)}")
        print(f"\nAll decoder types seen:")
        known_decoders = {0, 1, 3, 4, 6, 11, 12, 13, 15, 20, 24}
        for decoder_type, ch_set in sorted(all_decoders.items()):
            status = "KNOWN" if decoder_type in known_decoders else "UNKNOWN"
            print(f"  Decoder {decoder_type:2d} [{status}]: {len(ch_set)} unique channels")


if __name__ == "__main__":
    main()
