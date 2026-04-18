#!/usr/bin/env python3
"""
(c) Expansion-channel message variant scanner for libxrk issue #68.

Background
----------
On newer AIM loggers (observed KK-SII car, 2026-04-17 session), shock-pot
channels are silently dropped by libxrk. Raw-byte inspection showed three
distinct c-message variants in the file, only one of which the parser
handles today.

Variants
--------
All (c) messages begin with the 7-byte header:

    (c unk1 ch_field ch_field unk3 unk4

where ch_field is little-endian uint16. The full layout varies by
(unk1, unk4):

| Variant | unk1 | unk3 | unk4 | total bytes | payload            | tc source                 |
|---------|------|------|------|-------------|--------------------|---------------------------|
| V1      | 0x00 | 0x84 | 0x06 | 12 + data   | CHS-sized          | embedded at offset 7 (4B) |
| V2 long | 0x00 | 0x84 | 0x08 | 16          | 4 bytes (2 x fp16) | embedded at offset 7 (4B) |
| V3 short| 0x01 | 0x84 | 0x02 | 10          | 2 bytes (1 x fp16) | inherited (see below)     |

V1 matches the format documented in spec/docs/unknown_regions.md and is
what spec/xrk_format.py:cMessage currently handles. V2 and V3 are new.

channel_field mapping (U1) — resolved against AIM DLL
------------------------------------------------------
V1 uses `channel_index = channel_field >> 3`, with `channel_field & 7 == 4`.
V2/V3 `channel_field` values are NOT valid channel indices under the V1 rule.
In file 0095 we see values 0x1290, 0x1294, 0x1298, 0x129c, 0x12e8, 0x12ec,
0x12f0, 0x12f4 — eight distinct channel_fields.

These come in **pairs** (base, base+4):

    base           base+4
    0x1290         0x1294   → LR_Shock_Pot  (CHS hw_ref=0x005ea28a, src_ch=2)
    0x1298         0x129c   → RR_Shock_Pot  (CHS hw_ref=0x005ea28a, src_ch=3)
    0x12e8         0x12ec   → LF_Shock_Pot  (CHS hw_ref=0x005ea3b1, src_ch=2)
    0x12f0         0x12f4   → RF_Shock_Pot  (CHS hw_ref=0x005ea3b1, src_ch=3)

Pair base values sort identically to the CHS channels they represent,
ordered by (hw_ref, source_channel_id) ascending. The 4 channels are
"expansion" shock pots (CHS source_type=1, decoder_type=20, display_format=169
which is "LF Shock Position" per `_function_map`).

**Working mapping rule (per-file, empirical):**
  1. At CHS time, collect CHS channels with decoder_type=20 and
     display_format=169 (shock-pot-style). Sort by (hw_ref, source_channel_id).
  2. During data parsing, collect the set of distinct V2/V3 `channel_field`
     values observed. Form pairs (base, base+4) where `base & 0xF` ∈ {0, 8}.
  3. Assign the i-th sorted pair to the i-th sorted CHS channel.

V2 payload (U2) — validated against AIM DLL
--------------------------------------------
Each V2 message carries **two fp16 samples of the same logical channel**,
one in bytes [0:2] (call it `V2[0]`) and one in bytes [2:4] (`V2[1]`).
Their sample timecodes (in logger-clock) depend on which side of the
pair the `channel_field` is on:

  - base ch_field (low nibble 0 or 8): V2[0] at `tc`, V2[1] at `tc − 4ms`.
  - +4 ch_field (low nibble 4 or c):   V2[0] at `tc − 2ms`, V2[1] at `tc − 4ms`.
  - orphan (accelerometers):           V2[0] at `tc`, V2[1] at `tc − 4ms`.

The base/+4 offset difference reflects that the two `channel_field`
values in a pair interleave in the device's sampling schedule: V2(+4)
covers the "earlier half" of a 10ms cycle and V2(base) covers the
"later half". Together with V3 they produce 5 samples at 2ms intervals.
Validated against the AIM DLL to 99.97% exact per-sample match on the
issue68 fixture (file 0101, ~213k LR_Shock_Pot samples).

On the very first V2 message on a `channel_field`, V2[1] may contain a
stale pre-roll value not in DLL output — a sub-percent boundary artifact,
not a decode bug.

V3 timecode (U3)
----------------
V3 has no embedded timecode. It always appears on `base+4`. By count,
V3 contributes 1 sample per burst on top of the 4 samples from V2(base)
and V2(base+4). The combination gives ~5 samples per 10ms burst which
matches the ~487Hz aggregate rate seen in DLL output (DLL samples have
mostly 2ms spacing with occasional 3ms gaps).

V3's tc is synthesized from whichever V2 (base or +4) was most recently
seen on the same pair, in file order:

  - if preceding V2 was on `base`:  V3.tc = last_V2(base).tc − mms
  - otherwise (preceding V2 on +4): V3.tc = last_V2(+4).tc + mms

Both formulas give the same cycle-center tc when cycles are uniformly
10ms apart, but the branch handles 11ms "long" cycles where a single
rule would produce V3 tcs off by 1-2ms from DLL output.

Observed total sample counts (file 0095, empirical ≈ DLL ±110):

  | channel | empirical (2*V2b+2*V2+4+V3+4) | DLL reported |
  |---------|-------------------------------:|-------------:|
  | LR      |                        431846 |       431736 |
  | RR      |                        431848 |       431739 |
  | LF      |                        432470 |       432358 |
  | RF      |                        432470 |       432358 |

The ~110-sample discrepancy per channel is consistent with DLL trimming
session boundaries. Parser tests should allow ±150 samples tolerance.

IMU / rate channels (accels + rates) — file 0099
-------------------------------------------------
Files 0099/0101/0102 exercise the IMU. Confirmed findings:

- **Rate gyros** (`RollRate`, `PitchRate`, `YawRate`, mms=20 → 50Hz): use
  **V1** (the existing c-message format) with `channel_field = (idx<<3)|4`.
  Already handled by the current parser — not part of issue #68.
- **Accelerometers** (`LateralAcc`, `InlineAcc`, `VerticalAc`, mms=10 → 100Hz):
  use **V2 only**, no V3 partner. One channel_field per accel, each V2
  carries 2 fp16 samples. The channel_fields observed in 0099 are
  0x12ac, 0x12b4, 0x12bc — **not** paired with any +4 counterpart.

So there are TWO modes of V2/V3 usage:

  | pattern          | what                        | example           |
  |------------------|-----------------------------|-------------------|
  | paired (V2+V3)   | 500Hz fp16 (shock pots)     | pairs like (0x1290, 0x1294) |
  | orphan V2 only   | 100Hz fp16 (accelerometers) | 0x12ac, 0x12b4, 0x12bc |

**Revised channel_field → channel_index mapping rule:**
  1. At CHS time, partition expansion fp16 channels (decoder_type=20,
     source_type=1, hw_id≠0) by sample period:
     - **paired group**: mms ≤ 5 (500Hz-class: shock pots)
     - **orphan group**: 5 < mms ≤ 15 (100Hz-class: accels)
     Sort each group by (hw_ref, source_channel_id).
  2. During data parsing, discover all V2/V3 channel_fields:
     - If ch_field CF has a partner CF±4 also with V2: they form a pair,
       assign the pair to the next paired-group CHS channel.
     - Otherwise CF is an orphan; assign to the next orphan-group CHS channel.

Accelerometers are straightforward: each orphan V2 emits 2 fp16 samples
per message. V3 timecode inheritance logic from the paired case does not
apply here — accels have no V3.

IMU / rate channels
-------------------
CHS in these files declares 6 IMU channels (`LateralAcc`, `InlineAcc`,
`VerticalAc`, `RollRate`, `PitchRate`, `YawRate`) but the official AIM
DLL reports 0 samples for all of them — confirmed against
tests/reference_dll/wine_full_extract.py output. They are empty in the
file, not a parser miss. Only the 4 shock pots carry data this parser
drops.

Smoke test
----------
Running this scanner against tests/test_data/86/*.xrk,
tests/test_data/SFJ/*.xrk, etc. must report zero V2 and zero V3 messages
(older loggers don't emit them).

Usage
-----
    python scripts/investigate_missing_data/c_variant_scanner.py <file.xrk> [<file2.xrk> ...]
    python scripts/investigate_missing_data/c_variant_scanner.py --smoke-test
"""

from __future__ import annotations

import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from message_scanner import decompress_if_needed, tokdec, tokenc


CHS_SIZE = 112


@dataclass
class CHSRecord:
    index: int
    hw_id: int
    source_channel_id: int
    hw_ref: int
    unit_type: int
    display_format: int
    decoder_type: int
    data_size: int
    sample_period_us: int
    short_name: str
    long_name: str

    @property
    def mms(self) -> int:
        return self.sample_period_us // 1000


def _parse_chs(raw: bytes) -> CHSRecord:
    if len(raw) < CHS_SIZE:
        raise ValueError(f"CHS too short: {len(raw)} bytes")
    return CHSRecord(
        index=struct.unpack_from("<H", raw, 0)[0],
        hw_id=struct.unpack_from("<H", raw, 4)[0],
        source_channel_id=struct.unpack_from("<H", raw, 6)[0],
        hw_ref=struct.unpack_from("<I", raw, 8)[0],
        unit_type=raw[12] & 0x7F,
        display_format=raw[13],
        decoder_type=raw[20],
        data_size=raw[72],
        sample_period_us=struct.unpack_from("<I", raw, 64)[0],
        short_name=raw[24:32].split(b"\x00", 1)[0].decode("latin1"),
        long_name=raw[32:56].split(b"\x00", 1)[0].decode("latin1"),
    )


def _extract_chs(data: bytes) -> dict[int, CHSRecord]:
    """Walk <h...> header messages (and CNF/ENF recursive containers)
    to collect every CHS record keyed by channel index."""
    records: dict[int, CHSRecord] = {}

    def scan(buf: bytes) -> None:
        pos = 0
        n = len(buf)
        while pos + 12 <= n:
            if buf[pos] != 0x3C or buf[pos + 1] != 0x68:  # '<h'
                pos += 1
                continue
            tok = struct.unpack_from("<I", buf, pos + 2)[0]
            hlen = struct.unpack_from("<i", buf, pos + 6)[0]
            if hlen < 0 or pos + 12 + hlen + 8 > n:
                pos += 1
                continue
            if buf[pos + 11] != 0x3E:  # '>'
                pos += 1
                continue
            ftr_tok = struct.unpack_from("<I", buf, pos + 12 + hlen + 1)[0]
            if ftr_tok != tok:
                pos += 1
                continue
            if (tok >> 24) == 0x20:
                tok -= 0x20 << 24
            tok_str = tokenc(tok)
            payload = buf[pos + 12 : pos + 12 + hlen]
            if tok_str == "CHS" and len(payload) >= CHS_SIZE:
                rec = _parse_chs(payload[:CHS_SIZE])
                records.setdefault(rec.index, rec)
            elif tok_str in ("CNF", "ENF"):
                scan(payload)
            pos += 12 + hlen + 8

    scan(data)
    return records


@dataclass
class VariantStats:
    count: int = 0
    channel_field_hist: Counter = field(default_factory=Counter)
    sample_messages: list[tuple[int, int, bytes]] = field(default_factory=list)
    """(channel_field, timecode_or_-1, payload) — trimmed to MAX_SAMPLES."""


MAX_SAMPLES_PER_VARIANT = 3


@dataclass
class ScanOutput:
    file_path: Path
    chs: dict[int, CHSRecord]
    v1: VariantStats = field(default_factory=VariantStats)
    v2_long: VariantStats = field(default_factory=VariantStats)
    v3_short: VariantStats = field(default_factory=VariantStats)
    unknown: VariantStats = field(default_factory=VariantStats)


def scan_c_messages(path: Path) -> ScanOutput:
    raw = Path(path).read_bytes()
    data = decompress_if_needed(raw)
    chs = _extract_chs(data)
    out = ScanOutput(file_path=Path(path), chs=chs)

    i = 0
    n = len(data)
    while True:
        i = data.find(b"(c", i)
        if i < 0:
            break
        if i + 7 > n:
            break
        unk1 = data[i + 2]
        ch_field = struct.unpack_from("<H", data, i + 3)[0]
        unk3 = data[i + 5]
        unk4 = data[i + 6]

        stats: VariantStats
        total: int | None = None
        tc: int = -1
        payload: bytes = b""

        if unk3 == 0x84 and unk1 == 0 and unk4 == 6:
            # V1 — length derived from CHS via channel_field >> 3
            ch_idx = ch_field >> 3
            rec = chs.get(ch_idx)
            if rec is None or (ch_field & 7) != 4:
                stats = out.unknown
            else:
                total = rec.data_size + 12
                if i + total <= n and data[i + total - 1] == 0x29:
                    tc = struct.unpack_from("<i", data, i + 7)[0]
                    payload = bytes(data[i + 11 : i + total - 1])
                    stats = out.v1
                else:
                    stats = out.unknown
                    total = None
        elif unk3 == 0x84 and unk1 == 0 and unk4 == 8:
            total = 16
            if i + total <= n and data[i + total - 1] == 0x29:
                tc = struct.unpack_from("<i", data, i + 7)[0]
                payload = bytes(data[i + 11 : i + 15])
                stats = out.v2_long
            else:
                stats = out.unknown
                total = None
        elif unk3 == 0x84 and unk1 == 1 and unk4 == 2:
            total = 10
            if i + total <= n and data[i + total - 1] == 0x29:
                payload = bytes(data[i + 7 : i + 9])
                stats = out.v3_short
            else:
                stats = out.unknown
                total = None
        else:
            stats = out.unknown

        stats.count += 1
        stats.channel_field_hist[ch_field] += 1
        if len(stats.sample_messages) < MAX_SAMPLES_PER_VARIANT:
            stats.sample_messages.append((ch_field, tc, payload))

        i += total if total else 1

    return out


def _find_chs_for_field(chs: dict[int, CHSRecord], ch_field: int) -> list[CHSRecord]:
    """Candidate CHS records for a V2/V3 ch_field. Ranks candidates by
    how close (hw_ref, source_channel_id) packs into the observed bits."""
    # Current working hypothesis: ch_field pairs (base, base+4) correspond
    # to one (hw_ref, source_channel_id) tuple. We don't know the exact
    # encoding yet — this function returns all CHS channels whose source
    # device context matches any observed pattern, for further triage.
    candidates: list[CHSRecord] = []
    base_field = ch_field & ~7
    for rec in chs.values():
        if rec.source_channel_id in (
            (base_field >> 3) & 0xFF,
            (base_field >> 4) & 0xFF,
            (base_field >> 5) & 0xFF,
        ):
            candidates.append(rec)
    return candidates


def report(out: ScanOutput) -> None:
    print(f"\n=== {out.file_path.name} ===")
    print(f"CHS records: {len(out.chs)}")
    print(
        f"(c) messages — V1: {out.v1.count}, V2 long: {out.v2_long.count}, "
        f"V3 short: {out.v3_short.count}, unknown: {out.unknown.count}"
    )

    for label, stats in (
        ("V1", out.v1),
        ("V2 long", out.v2_long),
        ("V3 short", out.v3_short),
        ("unknown", out.unknown),
    ):
        if not stats.count:
            continue
        print(f"\n  {label} — top channel_field values:")
        for cf, cnt in stats.channel_field_hist.most_common(20):
            print(f"    0x{cf:04x} ({cf >> 3:5d})  × {cnt}")

    # For V2/V3, try to match ch_field pairs (base, base+4) and print
    # associated CHS candidates.
    if out.v2_long.count or out.v3_short.count:
        print("\n  Variant ch_field pair analysis:")
        all_fields = set(out.v2_long.channel_field_hist) | set(out.v3_short.channel_field_hist)
        bases = sorted(f for f in all_fields if (f & 0xF) in (0x0, 0x8))
        for base in bases:
            plus4 = base + 4
            v2_base = out.v2_long.channel_field_hist.get(base, 0)
            v2_plus4 = out.v2_long.channel_field_hist.get(plus4, 0)
            v3_plus4 = out.v3_short.channel_field_hist.get(plus4, 0)
            total_samples = 2 * (v2_base + v2_plus4) + v3_plus4
            print(
                f"    pair base=0x{base:04x} / +4=0x{plus4:04x}: "
                f"V2(base)={v2_base} V2(+4)={v2_plus4} V3(+4)={v3_plus4}"
                f"  → expected samples = {total_samples}"
            )


def smoke_test() -> int:
    """Assert old files have zero V2/V3. Exit 0 on success."""
    fixtures = [
        p for p in Path(__file__).resolve().parent.parent.parent.glob("tests/test_data/86/*.xrk")
    ]
    fixtures += [
        p for p in Path(__file__).resolve().parent.parent.parent.glob("tests/test_data/SFJ/*.xrk")
    ]
    fixtures += [
        p
        for p in Path(__file__)
        .resolve()
        .parent.parent.parent.glob("tests/test_data/aim_official/*.xrk")
    ]
    fixtures += [
        p
        for p in Path(__file__).resolve().parent.parent.parent.glob("tests/test_data/issue49/*.xrk")
    ]
    if not fixtures:
        print("smoke test: no fixtures found", file=sys.stderr)
        return 1
    failures = 0
    for p in fixtures:
        out = scan_c_messages(p)
        if out.v2_long.count or out.v3_short.count:
            print(
                f"SMOKE FAIL: {p.name} has V2={out.v2_long.count}, "
                f"V3={out.v3_short.count} (expected 0,0)",
                file=sys.stderr,
            )
            failures += 1
        else:
            print(f"ok: {p.name}: V1={out.v1.count} V2=0 V3=0 " f"unknown={out.unknown.count}")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "--help":
        print(__doc__)
        return 0
    if argv[0] == "--smoke-test":
        return smoke_test()
    for path in argv:
        out = scan_c_messages(Path(path))
        report(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
