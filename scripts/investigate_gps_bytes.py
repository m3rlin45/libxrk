#!/usr/bin/env python3
"""Investigate unknown bytes in XRK GPS messages against u-blox NAV-SOL fields.

The GPS message in XRK files is 56 bytes: a 4-byte AIM timecode followed by a
52-byte u-blox NAV-SOL payload. This script validates that hypothesis by reading
the unknown byte regions and checking whether their values match NAV-SOL expectations.

Complete 56-byte layout (XRK GPS message = AIM timecode + NAV-SOL):

  XRK Off  NAV-SOL Off  Field         Type     Notes
  ------   -----------  -----         ----     -----
  0        -            timecode      int32    AIM logger ms
  4        0            iTOW          uint32   GPS time of week [ms]
  8        4            fTOW          int32    Fractional TOW [ns], range +/-500000
  12       8            week          uint16   GPS week number
  14       10           gpsFix        uint8    Fix type (0-5)
  15       11           flags         uint8    Validity bitmask
  16       12           ecefX         int32    [cm]
  20       16           ecefY         int32    [cm]
  24       20           ecefZ         int32    [cm]
  28       24           pAcc          uint32   Position accuracy [cm]
  32       28           ecefVX        int32    [cm/s]
  36       32           ecefVY        int32    [cm/s]
  40       36           ecefVZ        int32    [cm/s]
  44       40           sAcc          uint32   Speed accuracy [cm/s]
  48       44           pDOP          uint16   Dilution of Precision [*0.01]
  50       46           reserved1     uint8    u-blox reserved
  51       47           numSV         uint8    Satellite count
  52       48           reserved2     uint32   u-blox reserved
"""

import struct
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def extract_gps_messages(filepath):
    """Extract raw 56-byte GPS messages from an XRK file.

    This reimplements the minimal parsing needed to find GPS message payloads
    without loading the full library (avoids needing the Cython build).
    """
    import mmap
    import zlib

    with open(filepath, "rb") as f:
        raw = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

    # Check for zlib compression (XRZ)
    data: bytes | mmap.mmap
    if len(raw) >= 2 and raw[0] == 0x78 and raw[1] in (0x01, 0x9C, 0xDA):
        data = zlib.decompress(raw[:])
    else:
        data = raw

    # Scan for GPS header messages and collect their payloads
    gps_messages = []
    pos = 0
    data_len = len(data)

    # GPS token as bytes: 'GPS' or 'GPS '
    tok_GPS = ord("G") + 256 * ord("P") + 256 * 256 * ord("S")
    tok_GPS1 = tok_GPS + 256 * 256 * 256 * ord("1")
    tok_GPS_sp = tok_GPS + 256 * 256 * 256 * ord(" ")

    while pos < data_len - 20:
        # Look for header start: '<h'
        if data[pos] == ord("<") and data[pos + 1] == ord("h"):
            try:
                tok = struct.unpack_from("<I", data, pos + 2)[0]
                hlen = struct.unpack_from("<i", data, pos + 6)[0]

                if hlen < 0 or hlen > 1000000:
                    pos += 1
                    continue

                payload_start = pos + 12
                payload_end = payload_start + hlen

                if payload_end + 8 > data_len:
                    pos += 1
                    continue

                # Strip trailing space from token
                check_tok = tok
                if (check_tok >> 24) == 32:
                    check_tok -= 32 << 24

                if check_tok == tok_GPS or check_tok == tok_GPS1:
                    payload = bytes(data[payload_start:payload_end])
                    if len(payload) == 56:
                        gps_messages.append(payload)

                pos = payload_end + 8
            except (struct.error, IndexError):
                pos += 1
        else:
            pos += 1

    return gps_messages


def analyze_gps_messages(messages):
    """Analyze unknown bytes in GPS messages against NAV-SOL expectations."""
    if not messages:
        print("No GPS messages found!")
        return

    print(f"Found {len(messages)} GPS messages (56 bytes each)")
    print()

    # Collect field values
    ftow_values = []
    gps_fix_values = []
    flags_values = []
    pdop_values = []
    reserved1_values = []
    reserved2_values = []
    numSV_values = []

    for msg in messages:
        # fTOW: offset 8, int32 (fractional TOW in nanoseconds)
        ftow = struct.unpack_from("<i", msg, 8)[0]
        ftow_values.append(ftow)

        # gpsFix: offset 14, uint8
        gps_fix = msg[14]
        gps_fix_values.append(gps_fix)

        # flags: offset 15, uint8
        flags = msg[15]
        flags_values.append(flags)

        # pDOP: offset 48, uint16 (scaled by 0.01)
        pdop = struct.unpack_from("<H", msg, 48)[0]
        pdop_values.append(pdop)

        # reserved1: offset 50, uint8
        reserved1 = msg[50]
        reserved1_values.append(reserved1)

        # numSV: offset 51, uint8
        numSV = msg[51]
        numSV_values.append(numSV)

        # reserved2: offset 52, uint32
        reserved2 = struct.unpack_from("<I", msg, 52)[0]
        reserved2_values.append(reserved2)

    # Print analysis
    print("=" * 70)
    print("FIELD ANALYSIS - Checking NAV-SOL hypothesis")
    print("=" * 70)

    # fTOW analysis
    print(f"\nfTOW (offset 8, int32) - Fractional TOW [ns]")
    print(f"  Expected range: -500000 to +500000")
    print(f"  Actual range:   {min(ftow_values)} to {max(ftow_values)}")
    in_range = sum(1 for v in ftow_values if -500000 <= v <= 500000)
    print(f"  In range: {in_range}/{len(ftow_values)} ({100*in_range/len(ftow_values):.1f}%)")
    verdict = "PASS" if in_range == len(ftow_values) else "FAIL"
    print(f"  Verdict: {verdict}")

    # gpsFix analysis
    print(f"\ngpsFix (offset 14, uint8) - Fix type")
    print(f"  Expected values: 0=no fix, 2=2D, 3=3D, 4=GPS+DR, 5=time only")
    unique_fix = sorted(set(gps_fix_values))
    print(f"  Unique values: {unique_fix}")
    for v in unique_fix:
        count = gps_fix_values.count(v)
        pct = 100 * count / len(gps_fix_values)
        label = {
            0: "No fix",
            1: "Dead reckoning",
            2: "2D fix",
            3: "3D fix",
            4: "GPS+DR",
            5: "Time only",
        }.get(v, "UNKNOWN")
        print(f"    {v} ({label}): {count} ({pct:.1f}%)")
    valid_fix = all(0 <= v <= 5 for v in gps_fix_values)
    print(f"  Verdict: {'PASS' if valid_fix else 'FAIL'}")

    # flags analysis
    print(f"\nflags (offset 15, uint8) - Validity bitmask")
    print(f"  Bit 0: gpsFixOk, Bit 1: diffSoln, Bit 2: wknSet, Bit 3: towSet")
    unique_flags = sorted(set(flags_values))
    print(f"  Unique values: {[f'0x{v:02x}' for v in unique_flags]}")
    for v in unique_flags:
        count = flags_values.count(v)
        pct = 100 * count / len(flags_values)
        bits = []
        if v & 1:
            bits.append("gpsFixOk")
        if v & 2:
            bits.append("diffSoln")
        if v & 4:
            bits.append("wknSet")
        if v & 8:
            bits.append("towSet")
        print(f"    0x{v:02x} [{', '.join(bits) or 'none'}]: {count} ({pct:.1f}%)")
    # Upper nibble should typically be 0
    upper_nibble_clean = all(v < 16 for v in flags_values)
    print(f"  Upper nibble all zero: {upper_nibble_clean}")
    print(f"  Verdict: {'PASS' if upper_nibble_clean else 'FAIL'}")

    # pDOP analysis
    print(f"\npDOP (offset 48, uint16) - Position DOP [*0.01]")
    print(f"  Expected range: ~100-2000 (1.00-20.00)")
    pdop_scaled = [v / 100 for v in pdop_values]
    print(
        f"  Actual range:   {min(pdop_values)} to {max(pdop_values)} "
        f"({min(pdop_scaled):.2f} to {max(pdop_scaled):.2f})"
    )
    reasonable = sum(1 for v in pdop_values if 50 <= v <= 10000)
    print(
        f"  In reasonable range: {reasonable}/{len(pdop_values)} "
        f"({100*reasonable/len(pdop_values):.1f}%)"
    )
    print(f"  Verdict: {'PASS' if reasonable > len(pdop_values) * 0.9 else 'FAIL'}")

    # reserved1 analysis
    print(f"\nreserved1 (offset 50, uint8) - u-blox reserved")
    print(f"  Expected: always 0")
    unique_r1 = sorted(set(reserved1_values))
    print(f"  Unique values: {unique_r1}")
    all_zero_r1 = all(v == 0 for v in reserved1_values)
    print(f"  All zero: {all_zero_r1}")
    print(f"  Verdict: {'PASS' if all_zero_r1 else 'FAIL'}")

    # numSV (known field, included for completeness)
    print(f"\nnumSV (offset 51, uint8) - Satellite count")
    print(f"  Range: {min(numSV_values)} to {max(numSV_values)}")
    avg_sv = sum(numSV_values) / len(numSV_values)
    print(f"  Average: {avg_sv:.1f}")

    # reserved2 analysis
    print(f"\nreserved2 (offset 52, uint32) - u-blox reserved")
    print(f"  Expected: always 0")
    unique_r2 = sorted(set(reserved2_values))
    print(f"  Unique values: {unique_r2}")
    all_zero_r2 = all(v == 0 for v in reserved2_values)
    print(f"  All zero: {all_zero_r2}")
    print(f"  Verdict: {'PASS' if all_zero_r2 else 'FAIL'}")

    # Overall summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    all_pass = (
        in_range == len(ftow_values)
        and valid_fix
        and upper_nibble_clean
        and reasonable > len(pdop_values) * 0.9
        and all_zero_r1
        and all_zero_r2
    )
    if all_pass:
        print("ALL CHECKS PASSED - GPS message is confirmed as AIM timecode + NAV-SOL payload")
    else:
        print("SOME CHECKS FAILED - Hypothesis may need revision")


def main():
    test_data = Path(__file__).parent.parent / "tests" / "test_data"
    xrk_files = sorted(test_data.glob("**/*.xrk"))

    if not xrk_files:
        print("No XRK test files found!")
        return

    for filepath in xrk_files:
        print(f"\n{'#' * 70}")
        print(f"# File: {filepath.name}")
        print(f"{'#' * 70}")

        messages = extract_gps_messages(filepath)
        analyze_gps_messages(messages)


if __name__ == "__main__":
    main()
