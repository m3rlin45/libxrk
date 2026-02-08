#!/usr/bin/env python3
"""
Investigate unknown message types: CAL, GPSR, iSLV, RACM, VET.

Extracts raw data from test XRK files and attempts to decode each
unknown message type by interpreting binary patterns.
"""

import struct
import zlib
from pathlib import Path


def tokdec(s: str) -> int:
    if s:
        return ord(s[0]) + 256 * tokdec(s[1:])
    return 0


def tokenc(i: int) -> str:
    s = ""
    while i:
        s += chr(i & 255)
        i >>= 8
    return s


def decompress_if_needed(data: bytes) -> bytes:
    if len(data) < 2:
        return data
    if data[0] == 0x78 and data[1] in (0x01, 0x9C, 0xDA):
        try:
            return zlib.decompress(data)
        except zlib.error:
            return data
    return data


def extract_h_messages(data: bytes) -> dict[str, list[bytes]]:
    """Extract all <h> message payloads grouped by token string."""
    messages: dict[str, list[bytes]] = {}
    pos = 0
    ord_lt = ord("<")
    ord_gt = ord(">")

    while pos < len(data) - 12:
        if data[pos] == ord_lt and data[pos + 1] == ord("h"):
            tok = struct.unpack_from("<I", data, pos + 2)[0]
            hlen = struct.unpack_from("<i", data, pos + 6)[0]
            cl = data[pos + 11]

            if cl != ord_gt or hlen < 0 or pos + 12 + hlen + 8 > len(data):
                pos += 1
                continue

            if (tok >> 24) == 32:
                tok -= 32 << 24

            ftr_pos = pos + 12 + hlen
            ftr_tok = struct.unpack_from("<I", data, ftr_pos + 1)[0]
            ftr_cl = data[ftr_pos + 7]

            if ftr_tok != tok or ftr_cl != ord_gt:
                pos += 1
                continue

            token_str = tokenc(tok)
            msg_data = data[pos + 12 : pos + 12 + hlen]
            messages.setdefault(token_str, []).append(msg_data)

            # Recurse into CNF/ENF containers
            if token_str in ("CNF", "ENF"):
                sub = extract_h_messages(msg_data)
                for k, v in sub.items():
                    prefix = f"{token_str}/{k}" if token_str == "ENF" else k
                    messages.setdefault(prefix, []).extend(v)

            pos = ftr_pos + 8
        else:
            pos += 1

    return messages


def hex_dump(data: bytes, width: int = 32) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:4d}: {hex_part:<{width*3}}  {ascii_part}")
    return "\n".join(lines)


def analyze_racm(messages: list[bytes]) -> None:
    print("\n" + "=" * 80)
    print("RACM - Race Mode")
    print("=" * 80)
    for i, msg in enumerate(messages):
        if len(msg) == 1:
            print(f"  [{i}] value = {msg[0]} (single byte flag)")
        else:
            text = msg.split(b"\x00")[0].decode("ascii", errors="replace")
            print(f"  [{i}] {len(msg)} bytes: {text!r} (null-terminated string)")
            print(hex_dump(msg))


def analyze_vet(messages: list[bytes]) -> None:
    print("\n" + "=" * 80)
    print("VET - Vehicle Electronics Type")
    print("=" * 80)
    for i, msg in enumerate(messages):
        print(f"  [{i}] value = {msg[0]} ({len(msg)} byte{'s' if len(msg)!=1 else ''})")


def analyze_islv(messages: list[bytes], chs_messages: list[bytes] | None = None) -> None:
    print("\n" + "=" * 80)
    print("iSLV - Slave Device Configuration")
    print("=" * 80)

    for i, msg in enumerate(messages):
        print(f"\n  --- iSLV message [{i}] ({len(msg)} bytes) ---")
        print(hex_dump(msg))

        # Check if it starts with embedded "idn" message
        if len(msg) >= 6 and msg[:3] == b"idn":
            ver = msg[3]
            payload_len = struct.unpack_from("<H", msg, 4)[0]
            print(f"\n  Embedded idn: version={ver}, payload_len={payload_len}")

            if len(msg) >= 6 + 10:
                idn = msg[6 : 6 + payload_len]
                model_id = struct.unpack_from("<H", idn, 0)[0]
                # bytes 2-5 unknown
                logger_id = struct.unpack_from("<I", idn, 6)[0]
                print(f"  Model ID:  {model_id} (0x{model_id:04x})")
                print(f"  Logger ID: {logger_id} (0x{logger_id:08x})")

                if len(idn) >= 20:
                    # Try to decode more fields from the idn payload
                    unk_2_3 = struct.unpack_from("<H", idn, 2)[0]
                    unk_4_5 = struct.unpack_from("<H", idn, 4)[0]
                    print(f"  idn[2:4]:  {unk_2_3} (0x{unk_2_3:04x})")
                    print(f"  idn[4:6]:  {unk_4_5} (0x{unk_4_5:04x})")

                    # Remaining bytes after idn header
                    remaining = idn[10:]
                    if remaining:
                        print(f"  idn[10:] ({len(remaining)} bytes):")
                        # Try to interpret as version/firmware info
                        pairs = []
                        for j in range(0, min(len(remaining), 20), 4):
                            if j + 4 <= len(remaining):
                                val = struct.unpack_from("<I", remaining, j)[0]
                                hi = (val >> 16) & 0xFFFF
                                lo = val & 0xFFFF
                                pairs.append(f"    +{j}: 0x{val:08x} = {val} (hi={hi}, lo={lo})")
                        print("\n".join(pairs))


def analyze_gpsr(messages: list[bytes], islv_messages: list[bytes] | None = None) -> None:
    print("\n" + "=" * 80)
    print("GPSR - GPS Receiver Configuration")
    print("=" * 80)

    for i, msg in enumerate(messages):
        print(f"\n  --- GPSR message [{i}] ({len(msg)} bytes) ---")
        print(hex_dump(msg))

        if len(msg) >= 36:
            # Parse as structured fields
            field_0 = struct.unpack_from("<I", msg, 0)[0]  # unknown/counter
            token_bytes = msg[4:8]  # looks like "GPS\0" or "iGPS"
            token_str = token_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

            print(f"\n  Interpretation:")
            print(f"  offset  0: {field_0} (0x{field_0:08x}) - unknown ID/counter")
            print(f"  offset  4: {token_str!r} - GPS type identifier")

            # Try various interpretations for remaining bytes
            for offset in range(8, min(len(msg), 36), 4):
                val_u32 = struct.unpack_from("<I", msg, offset)[0]
                val_i32 = struct.unpack_from("<i", msg, offset)[0]
                val_f32 = struct.unpack_from("<f", msg, offset)[0]
                val_u16 = struct.unpack_from("<HH", msg, offset)
                parts = []
                if val_u32 != 0:
                    parts.append(f"u32={val_u32}")
                    parts.append(f"i32={val_i32}")
                    if abs(val_f32) > 1e-10 and abs(val_f32) < 1e10:
                        parts.append(f"f32={val_f32:.6g}")
                    parts.append(f"u16=({val_u16[0]}, {val_u16[1]})")
                else:
                    parts.append("0")
                print(f"  offset {offset:2d}: {' | '.join(parts)}")

            # Look for logger_id pattern (we know from idn parsing)
            for offset in range(0, len(msg) - 4):
                val = struct.unpack_from("<I", msg, offset)[0]
                if val == 0x0084F084:  # Known logger ID from 86 file
                    print(f"\n  ** Logger ID found at offset {offset}: 0x{val:08x}")


def analyze_cal(messages: list[bytes], chs_messages: list[bytes] | None = None) -> None:
    print("\n" + "=" * 80)
    print("CAL - Calibration Data")
    print("=" * 80)
    print(f"  Total CAL messages: {len(messages)}")

    # Parse CHS messages to correlate channel indices
    channels: dict[int, str] = {}
    if chs_messages:
        for chs in chs_messages:
            if len(chs) >= 56:
                ch_idx = struct.unpack_from("<H", chs, 0)[0]
                long_name = chs[32:56].split(b"\x00")[0].decode("ascii", errors="replace")
                decoder_type = chs[20]
                channels[ch_idx] = f"{long_name} (decoder={decoder_type})"

    for i, msg in enumerate(messages):
        print(f"\n  --- CAL message [{i}] ({len(msg)} bytes) ---")
        print(hex_dump(msg))

        if len(msg) >= 144:
            # Header section - try various interpretations
            b0 = msg[0]
            b1 = msg[1]
            b2 = msg[2]
            b3 = msg[3]

            # First 2 bytes could be a channel-related field
            u16_0 = struct.unpack_from("<H", msg, 0)[0]
            # Byte 2 might be flags, byte 3 might be decoder type
            print(f"\n  Structural interpretation:")
            print(f"  bytes[0:2]: 0x{u16_0:04x} = {u16_0}")
            print(f"    byte[0] = 0x{b0:02x} = {b0}")
            print(f"    byte[1] = 0x{b1:02x} = {b1}")
            print(f"  byte[2]:    0x{b2:02x} = {b2} (flags?)")
            print(f"  byte[3]:    0x{b3:02x} = {b3} (decoder type?)")

            # uint32 fields
            for off in (4, 8, 12, 16, 20):
                v = struct.unpack_from("<I", msg, off)[0]
                print(f"  uint32[{off}:]: {v}")

            # Float32 section (offsets 24-63ish seem to contain floats)
            print(f"\n  Float32 fields:")
            for off in range(24, min(len(msg), 72), 4):
                v = struct.unpack_from("<f", msg, off)[0]
                if v != 0.0:
                    print(f"    offset {off:3d}: {v:>15.6g}")
                else:
                    print(f"    offset {off:3d}: {v}")

            # Check if remaining bytes (72+) are all zeros
            trailing = msg[72:]
            nonzero = sum(1 for b in trailing if b != 0)
            print(f"\n  Trailing bytes[72:144]: {nonzero}/{len(trailing)} non-zero bytes")
            if nonzero > 0:
                print(f"  Non-zero trailing data:")
                print(hex_dump(trailing))


def analyze_file(path: Path) -> None:
    print(f"\n{'#' * 80}")
    print(f"# FILE: {path.name}")
    print(f"{'#' * 80}")

    with open(path, "rb") as f:
        raw = f.read()
    data = decompress_if_needed(raw)

    messages = extract_h_messages(data)

    # Show what tokens we found
    print(f"\n  All tokens: {sorted(messages.keys())}")
    for tok in ("RACM", "VET", "iSLV", "GPSR", "CAL"):
        if tok in messages:
            print(f"  {tok}: {len(messages[tok])} messages")

    chs = messages.get("CHS", [])

    if "RACM" in messages:
        analyze_racm(messages["RACM"])
    if "VET" in messages:
        analyze_vet(messages["VET"])
    if "iSLV" in messages:
        analyze_islv(messages["iSLV"], chs)
    if "GPSR" in messages:
        analyze_gpsr(messages["GPSR"], messages.get("iSLV"))
    if "CAL" in messages:
        analyze_cal(messages["CAL"], chs)


def main() -> None:
    test_data = Path(__file__).resolve().parents[2] / "tests" / "test_data"

    # Analyze only .xrk files (skip .xrz duplicates)
    xrk_files = sorted(test_data.glob("**/*.xrk"))
    for f in xrk_files:
        analyze_file(f)

    # Cross-file comparison summary
    print(f"\n\n{'=' * 80}")
    print("CROSS-FILE COMPARISON")
    print(f"{'=' * 80}")

    all_cal_first_bytes: list[tuple[str, list[tuple[int, int, int, int]]]] = []
    all_gpsr: list[tuple[str, bytes]] = []

    for f in xrk_files:
        with open(f, "rb") as fh:
            raw = fh.read()
        data = decompress_if_needed(raw)
        messages = extract_h_messages(data)

        if "CAL" in messages:
            first_bytes = []
            for msg in messages["CAL"]:
                if len(msg) >= 4:
                    first_bytes.append((msg[0], msg[1], msg[2], msg[3]))
            all_cal_first_bytes.append((f.name, first_bytes))

        if "GPSR" in messages:
            for msg in messages["GPSR"]:
                all_gpsr.append((f.name, msg))

    print("\n  CAL first 4 bytes across files:")
    for fname, fb_list in all_cal_first_bytes:
        print(f"\n  {fname}:")
        for j, (b0, b1, b2, b3) in enumerate(fb_list):
            u16 = b0 | (b1 << 8)
            print(
                f"    CAL[{j}]: byte0=0x{b0:02x} byte1=0x{b1:02x} byte2=0x{b2:02x} byte3=0x{b3:02x}  (u16_0={u16})"
            )

    print("\n  GPSR comparison:")
    for fname, msg in all_gpsr:
        token_str = msg[4:8].split(b"\x00")[0].decode("ascii", errors="replace")
        field_0 = struct.unpack_from("<I", msg, 0)[0]
        last_4 = struct.unpack_from("<I", msg, 32)[0] if len(msg) >= 36 else 0
        print(f"    {fname}: type={token_str!r}, field0={field_0}, last4={last_4}")


if __name__ == "__main__":
    main()
