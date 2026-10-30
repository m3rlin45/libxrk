#!/usr/bin/env python3
"""Build a small test fixture from a SmartyCam MP4: telemetry track only, time-trimmed.

The output keeps the exact original bytes of ftyp, the leading free box, every
telemetry ('aimd') sample up to --seconds of telemetry media time, the moov
boxes that describe the telemetry track (durations and sample tables rewritten
for the trimmed sample set), the movie-level udta, and the exact trailing
aim_meta_data pseudo-box. Video and audio tracks are dropped.

Usage:
    uv run python scripts/investigate_mp4/make_fixture.py <in.MP4> <out.mp4> [--seconds 210]
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from construct import Container  # noqa: E402

from spec.isobmff import (  # noqa: E402
    EditList,
    MediaHeader,
    MovieHeader,
    TrackHeader,
    children,
    find_child,
    iter_boxes,
    read_payload,
)
from spec.mp4_format import parse_mp4_file  # noqa: E402


def box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def fullbox(typ, version, flags, payload):
    return box(typ, bytes([version]) + flags + payload)


def rebuild_stbl(f, stbl, samples, mdat_payload_offset):
    """Rebuild stsd/stts/stsc/stsz/stco for the trimmed sample list."""
    stsd_raw = box(b"stsd", read_payload(f, find_child(f, stbl, b"stsd")))
    # stts: run-length encode deltas
    deltas = []
    for a, b in zip(samples, samples[1:]):
        deltas.append(b.dts - a.dts)
    deltas.append(deltas[-1] if deltas else 100)
    runs = []
    for d in deltas:
        if runs and runs[-1][1] == d:
            runs[-1][0] += 1
        else:
            runs.append([1, d])
    stts = fullbox(
        b"stts",
        0,
        b"\0\0\0",
        struct.pack(">I", len(runs)) + b"".join(struct.pack(">II", c, d) for c, d in runs),
    )
    per_chunk = 3  # as in the original file
    stsc = fullbox(b"stsc", 0, b"\0\0\0", struct.pack(">IIII", 1, 1, per_chunk, 1))
    stsz = fullbox(
        b"stsz",
        0,
        b"\0\0\0",
        struct.pack(">II", 0, len(samples)) + b"".join(struct.pack(">I", s.size) for s in samples),
    )
    offsets = []
    off = mdat_payload_offset
    for i, s in enumerate(samples):
        if i % per_chunk == 0:
            offsets.append(off)
        off += s.size
    stco = fullbox(
        b"stco",
        0,
        b"\0\0\0",
        struct.pack(">I", len(offsets)) + b"".join(struct.pack(">I", o) for o in offsets),
    )
    return box(b"stbl", stsd_raw + stts + stsc + stsz + stco)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    seconds = float(argv[argv.index("--seconds") + 1]) if "--seconds" in argv else 210.0

    res = parse_mp4_file(src, parse_telemetry_payload=False)
    trk = res.telemetry_track
    assert trk is not None, "no telemetry track"
    keep = [s for s in res.samples if s.index == 0 or s.media_time_us <= seconds * 1e6]
    keep = keep[: len(keep) - (len(keep) % 3)]  # whole chunks of 3
    media_duration = keep[-1].dts + 100  # ms
    scale = res.movie_timescale / trk.media_timescale
    movie_duration = int(round(media_duration * scale))
    print(f"keeping {len(keep)} of {len(res.samples)} samples; media duration {media_duration} ms")

    with open(src, "rb") as f:
        size = src.stat().st_size
        top = list(iter_boxes(f, 0, size))
        ftyp = next(b for b in top if b.type == b"ftyp")
        f.seek(ftyp.offset)
        ftyp_raw = f.read(ftyp.size)
        free_raw = struct.pack(">I4s", 8, b"free")
        moov = next(b for b in top if b.type == b"moov")
        tail_raw = None
        tail_start = top[-1].end
        if tail_start < size:
            f.seek(tail_start)
            tail_raw = f.read(size - tail_start)

        # mdat with just the kept samples
        mdat_payload = bytearray()
        for s in keep:
            f.seek(s.offset)
            mdat_payload += f.read(s.size)
        mdat_header_len = 8
        mdat_payload_offset = len(ftyp_raw) + len(free_raw) + mdat_header_len
        mdat_raw = box(b"mdat", bytes(mdat_payload))

        # mvhd with trimmed duration
        mvhd_box = find_child(f, moov, b"mvhd")
        mv = MovieHeader.parse(read_payload(f, mvhd_box))
        mv.duration = movie_duration
        mvhd_raw = box(b"mvhd", MovieHeader.build(mv))

        # the telemetry trak
        trak = None
        for t in children(f, moov):
            if t.type == b"trak":
                tk = TrackHeader.parse(read_payload(f, find_child(f, t, b"tkhd")))
                if tk.track_id == trk.track_id:
                    trak = t
        assert trak is not None
        tk = TrackHeader.parse(read_payload(f, find_child(f, trak, b"tkhd")))
        tk.duration = movie_duration
        tkhd_raw = box(b"tkhd", TrackHeader.build(tk))

        edts_raw = b""
        edts = find_child(f, trak, b"edts")
        if edts is not None:
            el = EditList.parse(read_payload(f, find_child(f, edts, b"elst")))
            empty = (
                el.entries[0].segment_duration
                if el.entries and el.entries[0].media_time == -1
                else 0
            )
            if len(el.entries) > 1:
                el.entries[-1].segment_duration = movie_duration - empty
            edts_raw = box(b"edts", box(b"elst", EditList.build(el)))

        tref_raw = b""
        tref = find_child(f, trak, b"tref")
        if tref is not None:
            f.seek(tref.offset)
            tref_raw = f.read(tref.size)

        mdia = find_child(f, trak, b"mdia")
        md = MediaHeader.parse(read_payload(f, find_child(f, mdia, b"mdhd")))
        md.duration = media_duration
        mdhd_raw = box(b"mdhd", MediaHeader.build(md))
        hdlr_box = find_child(f, mdia, b"hdlr")
        f.seek(hdlr_box.offset)
        hdlr_raw = f.read(hdlr_box.size)
        minf = find_child(f, mdia, b"minf")
        minf_children = b""
        for c in children(f, minf):
            if c.type == b"stbl":
                minf_children += rebuild_stbl(f, c, keep, mdat_payload_offset)
            else:
                f.seek(c.offset)
                minf_children += f.read(c.size)
        minf_raw = box(b"minf", minf_children)
        mdia_raw = box(b"mdia", mdhd_raw + hdlr_raw + minf_raw)

        udta_raw = b""
        udta = find_child(f, trak, b"udta")
        if udta is not None:
            f.seek(udta.offset)
            udta_raw = f.read(udta.size)
        trak_raw = box(b"trak", tkhd_raw + edts_raw + tref_raw + mdia_raw + udta_raw)

        moov_udta_raw = b""
        mu = find_child(f, moov, b"udta")
        if mu is not None:
            f.seek(mu.offset)
            moov_udta_raw = f.read(mu.size)
        moov_raw = box(b"moov", mvhd_raw + trak_raw + moov_udta_raw)

    out = ftyp_raw + free_raw + mdat_raw + moov_raw + (tail_raw or b"")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    print(f"wrote {dst} ({len(out)} bytes)")

    check = parse_mp4_file(dst)
    assert check.telemetry_track is not None
    assert len(check.samples) == len(keep)
    assert check.telemetry.leftover_bytes == 0
    print(
        f"verified: {len(check.samples)} samples, {len(check.telemetry.messages)} messages, "
        f"{len(check.telemetry.get_laps())} laps, meta tail {'present' if check.meta_data else 'MISSING'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
