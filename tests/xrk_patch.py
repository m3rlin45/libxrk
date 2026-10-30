"""Byte-level helpers for building test inputs from the shipped fixtures.

Some behaviours have no fixture that exhibits them (a unit code no shipped
logger uses, an all-zero GPS record at the start of a stream, a non-finite
sample). Rather than commit a new multi-megabyte file for each, tests take an
existing fixture and patch a few bytes in memory. ``aim_xrk()`` accepts
``bytes``, so nothing is written to disk.

Header frames carry a 16-bit checksum (sum of payload bytes) and CNF/ENF
frames nest further frames inside their payload, so a patch must recompute
the checksum of the patched frame and of every enclosing frame. ``frames()``
locates frames with their absolute offsets and parent chain; ``patch()``
applies a replacement and fixes the checksums. The frame layout is the
``HeaderMessage`` struct in ``spec/xrk_format.py``.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterator


def load_bytes(path) -> bytes:
    """Read an XRK, or an XRZ (zlib) decompressed to its XRK bytes."""
    data = open(path, "rb").read()
    if data[:1] == b"\x78":
        deco = zlib.decompressobj()
        try:
            return deco.decompress(data)
        except zlib.error:
            return deco.flush()
    return data


def token_str(tok: int) -> str:
    return tok.to_bytes(4, "little").decode("latin1").rstrip(" \x00")


def frames(data: bytes, lo: int = 0, hi: int | None = None, parents: tuple = ()) -> Iterator[dict]:
    """Yield every checksum-valid ``<h`` frame in ``data[lo:hi]``.

    Each frame is a dict with ``start`` (offset of ``<h``), ``tok`` (string
    token, padding stripped), ``ver``, ``pstart``/``pend`` (payload span) and
    ``parents`` (enclosing CNF/ENF frames, outermost first). Frames inside a
    CNF or ENF payload are yielded after their parent.
    """
    hi = len(data) if hi is None else hi
    pos = lo
    while True:
        pos = data.find(b"<h", pos, hi)
        if pos < 0 or pos + 12 > hi:
            return
        tok, plen, ver, close = struct.unpack_from("<IiBB", data, pos + 2)
        pstart = pos + 12
        pend = pstart + plen
        ok = close == ord(">") and plen >= 0 and pend + 8 <= hi and data[pend] == ord("<")
        if ok:
            ftok, csum, fclose = struct.unpack_from("<IHB", data, pend + 1)
            ok = ftok == tok and fclose == ord(">") and csum == (sum(data[pstart:pend]) & 0xFFFF)
        if not ok:
            pos += 1
            continue
        frame = dict(
            start=pos, tok=token_str(tok), ver=ver, pstart=pstart, pend=pend, parents=parents
        )
        yield frame
        if frame["tok"] in ("CNF", "ENF"):
            yield from frames(data, pstart, pend, parents + (frame,))
        pos = pend + 8


def fix_checksums(buf: bytearray, frame: dict) -> None:
    """Recompute the footer checksum of ``frame`` and of every enclosing frame."""
    for f in (frame,) + tuple(frame["parents"]):
        struct.pack_into("<H", buf, f["pend"] + 5, sum(buf[f["pstart"] : f["pend"]]) & 0xFFFF)


def patch(buf: bytearray, frame: dict, offset: int, new_bytes: bytes) -> None:
    """Overwrite ``new_bytes`` at ``offset`` within the frame's payload, fixing checksums."""
    start = frame["pstart"] + offset
    assert start + len(new_bytes) <= frame["pend"], "patch runs past the payload"
    buf[start : start + len(new_bytes)] = new_bytes
    fix_checksums(buf, frame)


def chs_frames(data: bytes) -> dict[str, dict]:
    """Map channel long_name -> its first CHS frame (CHS long_name is bytes [32:56])."""
    out: dict[str, dict] = {}
    for f in frames(data):
        if f["tok"] == "CHS":
            name = data[f["pstart"] + 32 : f["pstart"] + 56].split(b"\0")[0].decode("latin1")
            out.setdefault(name, f)
    return out
