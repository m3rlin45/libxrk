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

For a stream no fixture resembles (one that ends on a data message, say),
``build_frame()`` and the message builders at the end make one from scratch.
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


def build_frame(tok: str, payload: bytes, ver: int = 0) -> bytes:
    """A complete ``<h`` frame around ``payload``, for a stream built from scratch.

    A three-character token is padded with a space, as on the wire. Nest
    frames by passing the inner frames' bytes as the payload of a CNF.
    """
    raw_tok = tok.encode("latin1")
    wire_tok = raw_tok + b" " if len(raw_tok) == 3 else raw_tok.ljust(4, b"\0")
    return (
        b"<h"
        + wire_tok
        + struct.pack("<iB", len(payload), ver)
        + b">"
        + payload
        + b"<"
        + wire_tok
        + struct.pack("<H", sum(payload) & 0xFFFF)
        + b">"
    )


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


def replace_payload(data: bytes, frame: dict, payload: bytes) -> bytes:
    """``data`` with the frame's payload replaced by one of any length.

    The length and checksum of the frame and of every enclosing frame are
    rewritten, innermost first (a frame's footer is part of its parent's
    payload). Frames found in ``data`` before the call are stale afterwards.
    """
    buf = bytearray(data)
    delta = len(payload) - (frame["pend"] - frame["pstart"])
    buf[frame["pstart"] : frame["pend"]] = payload
    for f in (frame,) + tuple(reversed(frame["parents"])):
        pend = f["pend"] + delta
        struct.pack_into("<i", buf, f["start"] + 6, pend - f["pstart"])
        struct.pack_into("<H", buf, pend + 5, sum(buf[f["pstart"] : pend]) & 0xFFFF)
    return bytes(buf)


def chs_frames(data: bytes) -> dict[str, dict]:
    """Map channel long_name -> its first CHS frame (CHS long_name is bytes [32:56])."""
    out: dict[str, dict] = {}
    for f in frames(data):
        if f["tok"] == "CHS":
            name = data[f["pstart"] + 32 : f["pstart"] + 56].split(b"\0")[0].decode("latin1")
            out.setdefault(name, f)
    return out


def s_message_offsets(data: bytes, channel_index: int, size: int) -> list[int]:
    """Offsets of ``(S`` sample messages for ``channel_index``, in file order.

    An S message is ``(S`` + int32 timecode + uint16 index + ``size`` data
    bytes + ``)``. The sample bytes start 8 bytes into the message. Only
    messages with the closing ``)`` in place are returned.
    """
    out: list[int] = []
    pos = 0
    while True:
        pos = data.find(b"(S", pos)
        if pos < 0 or pos + 9 + size > len(data):
            return out
        if struct.unpack_from("<H", data, pos + 6)[0] == channel_index and data[
            pos + 8 + size
        ] == ord(")"):
            out.append(pos)
        pos += 1


def s_message(timecode: int, channel_index: int, sample: bytes) -> bytes:
    """An ``(S`` message: one sample of ``channel_index`` (``CHS.data_size`` bytes)."""
    return b"(S" + struct.pack("<iH", timecode, channel_index) + sample + b")"


def c_v2_message(channel_field: int, timecode: int, samples: tuple[float, float]) -> bytes:
    """A 16-byte V2 ``(c`` message: two float16 samples under one timecode."""
    return (
        b"(c"
        + struct.pack("<BHBBi", 0, channel_field, 0x84, 8, timecode)
        + struct.pack("<ee", *samples)
        + b")"
    )


def c_v3_message(channel_field: int, sample: float) -> bytes:
    """A 10-byte V3 ``(c`` message: one float16 sample and no timecode."""
    return (
        b"(c" + struct.pack("<BHBB", 1, channel_field, 0x84, 2) + struct.pack("<e", sample) + b")"
    )
