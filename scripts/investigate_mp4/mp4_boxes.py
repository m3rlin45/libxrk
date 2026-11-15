#!/usr/bin/env python3
"""Print the ISO BMFF box tree of a SmartyCam MP4 and dump the boxes the spec relies on.

Deliberately self-contained (stdlib only) so it can cross-check spec/mp4_format.py.
Reads only box headers plus the small moov children; never loads mdat.

Usage:
    uv run python scripts/investigate_mp4/mp4_boxes.py <file.MP4> [--hex]
"""

import datetime
import struct
import sys

CONTAINERS = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"udta",
    b"edts",
    b"dinf",
    b"mvex",
    b"tref",
}
FULLBOX_CONTAINERS = {b"meta"}
EPOCH = datetime.datetime(1904, 1, 1)


def hexdump(data, base=0, width=16, max_lines=8):
    lines = 0
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        txt = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print(f"      {base + i:08x}  {chunk.hex(' '):<48}  {txt}")
        lines += 1
        if lines >= max_lines and i + width < len(data):
            print(f"      ... ({len(data) - i - width} more bytes)")
            break


def read_header(f, off, end):
    """Return (size, type, header_size, malformed) or None."""
    if off + 8 > end:
        return None
    f.seek(off)
    hdr = f.read(16)
    if len(hdr) < 8:
        return None
    size, typ = struct.unpack(">I4s", hdr[:8])
    hs = 8
    if size == 1:
        size = struct.unpack(">Q", hdr[8:16])[0]
        hs = 16
    elif size == 0:
        size = end - off
    malformed = size < hs or off + size > end
    return size, typ, hs, malformed


def walk(f, start, end, depth, out):
    off = start
    while off < end:
        r = read_header(f, off, end)
        if r is None:
            return
        size, typ, hs, malformed = r
        if malformed:
            f.seek(off)
            raw = f.read(8)
            print(
                f"{'  ' * depth}!! malformed box at {off}: size field {raw[:4]!r} "
                f"(= {struct.unpack('>I', raw[:4])[0]}), type {raw[4:8]!r}; {end - off} bytes to EOF"
            )
            out.append((depth, off, end - off, b"<tail>", 8))
            return
        print(f"{'  ' * depth}{typ.decode('latin1')!r} off={off} size={size}")
        out.append((depth, off, size, typ, hs))
        if typ in CONTAINERS or typ in FULLBOX_CONTAINERS:
            sub = off + hs + (4 if typ in FULLBOX_CONTAINERS else 0)
            walk(f, sub, off + size, depth + 1, out)
        off += size


def fullbox_times(payload):
    ver = payload[0]
    if ver == 0:
        ct, mt = struct.unpack(">II", payload[4:12])
        rest = 12
    else:
        ct, mt = struct.unpack(">QQ", payload[4:20])
        rest = 20
    return ver, ct, mt, rest


def describe(f, boxes, want_hex):
    track = 0
    for depth, off, size, typ, hs in boxes:
        f.seek(off + hs)
        payload = f.read(min(size - hs, 4096))
        if typ == b"mvhd":
            ver, ct, mt, rest = fullbox_times(payload)
            ts = struct.unpack(">I", payload[rest : rest + 4])[0]
            dur = struct.unpack(
                ">Q" if ver else ">I", payload[rest + 4 : rest + (12 if ver else 8)]
            )[0]
            print(
                f"  mvhd: timescale={ts} duration={dur} ({dur / ts:.3f}s) "
                f"creation={EPOCH + datetime.timedelta(seconds=ct)} "
                f"modification={EPOCH + datetime.timedelta(seconds=mt)}"
            )
        elif typ == b"trak":
            track += 1
            print(f"  --- track #{track} (trak at {off})")
        elif typ == b"tkhd":
            ver, ct, mt, rest = fullbox_times(payload)
            tid = struct.unpack(">I", payload[rest : rest + 4])[0]
            dur = struct.unpack(
                ">Q" if ver else ">I", payload[rest + 8 : rest + (16 if ver else 12)]
            )[0]
            w, h = struct.unpack(">II", payload[-8:])
            print(
                f"  tkhd: track_id={tid} duration={dur} (movie ts) width={w >> 16} height={h >> 16}"
            )
        elif typ == b"mdhd":
            ver, ct, mt, rest = fullbox_times(payload)
            ts = struct.unpack(">I", payload[rest : rest + 4])[0]
            dur = struct.unpack(
                ">Q" if ver else ">I", payload[rest + 4 : rest + (12 if ver else 8)]
            )[0]
            print(f"  mdhd: timescale={ts} duration={dur} ({dur / ts:.3f}s)")
        elif typ == b"hdlr":
            htype = payload[8:12]
            name = payload[24:].split(b"\0")[0]
            print(f"  hdlr: handler_type={htype!r} name={name!r}")
        elif typ == b"elst":
            ver = payload[0]
            n = struct.unpack(">I", payload[4:8])[0]
            ents = []
            p = 8
            for _ in range(n):
                if ver == 0:
                    sd, mt_ = struct.unpack(">Ii", payload[p : p + 8])
                    p += 12
                else:
                    sd, mt_ = struct.unpack(">Qq", payload[p : p + 16])
                    p += 20
                ents.append((sd, mt_))
            print(f"  elst: {ents}  (segment_duration in movie ts; media_time -1 = empty edit)")
        elif typ == b"tref":
            print(f"  tref: children follow")
        elif depth >= 2 and typ not in CONTAINERS and typ in (b"tmcd",):
            ids = struct.unpack(f">{(size - 8) // 4}I", payload[: size - 8])
            print(f"  tref/{typ.decode()}: track_ids={list(ids)}")
        elif typ == b"stsd":
            n = struct.unpack(">I", payload[4:8])[0]
            esize, fmt = struct.unpack(">I4s", payload[8:16])
            print(f"  stsd: entries={n} first: size={esize} format={fmt!r}")
            if want_hex:
                hexdump(payload, off + hs)
        elif typ == b"stts":
            n = struct.unpack(">I", payload[4:8])[0]
            f.seek(off + hs + 8)
            raw = f.read(8 * n)
            ents = struct.unpack(f">{2 * n}I", raw)
            from collections import Counter

            hist = Counter()
            total = 0
            count = 0
            for i in range(n):
                hist[ents[2 * i + 1]] += ents[2 * i]
                total += ents[2 * i] * ents[2 * i + 1]
                count += ents[2 * i]
            print(
                f"  stts: entries={n} samples={count} total_duration={total} deltas={dict(hist.most_common(6))}"
            )
        elif typ == b"stsc":
            n = struct.unpack(">I", payload[4:8])[0]
            f.seek(off + hs + 8)
            ents = struct.unpack(f">{3 * n}I", f.read(12 * n))
            print(
                f"  stsc: entries={n} first={[tuple(ents[i:i + 3]) for i in range(0, 3 * n, 3)][:4]}"
            )
        elif typ == b"stsz":
            ss, n = struct.unpack(">II", payload[4:12])
            print(f"  stsz: sample_size={ss} sample_count={n}")
        elif typ in (b"stco", b"co64"):
            n = struct.unpack(">I", payload[4:8])[0]
            w = 4 if typ == b"stco" else 8
            first = struct.unpack(">I" if w == 4 else ">Q", payload[8 : 8 + w])[0]
            print(f"  {typ.decode()}: chunks={n} first_offset={first}")
        elif typ == b"ftyp":
            print(
                f"  ftyp: major={payload[:4]!r} minor={payload[4:8].hex()} compatible={[payload[i:i+4] for i in range(8, len(payload), 4)]}"
            )
        elif typ == b"<tail>":
            f.seek(off)
            raw = f.read(size)
            print(f"  tail: {size} bytes; header bytes {raw[:8]!r}; XML follows:")
            print("      " + raw[8:].decode("utf-8", "replace").replace("\n", "\n      "))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    want_hex = "--hex" in argv
    with open(path, "rb") as f:
        f.seek(0, 2)
        end = f.tell()
        print(f"file: {path}\nsize: {end}")
        boxes = []
        walk(f, 0, end, 0, boxes)
        print()
        describe(f, boxes, want_hex)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
