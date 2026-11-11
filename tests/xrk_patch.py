"""Byte-level helpers for building test inputs from the shipped fixtures.

Some behaviours have no fixture that exhibits them (a unit code no shipped
logger uses, an all-zero GPS record at the start of a stream, a non-finite
sample). Rather than commit a new multi-megabyte file for each, tests take an
existing fixture and patch a few bytes in memory. ``aim_xrk()`` accepts
``bytes``, so nothing is written to disk.

Whole messages can be spliced in the same way: ``split_at_cnf()`` cuts a
stream around a mid-file CNF and hands back the earlier data messages, ready
to be replayed after it.

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
from typing import Iterator, NamedTuple


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


def data_messages(data: bytes, lo: int, hi: int) -> bytes:
    """``data[lo:hi]`` with every header frame cut out: its data messages.

    Between header frames a stream holds only ``(S``/``(G``/``(M``/``(c``
    messages, which have no enclosing checksum, so the result can be spliced
    into a stream as is. ``lo`` and ``hi`` must sit on message boundaries
    outside any CNF/ENF payload (the start or end of a top-level header frame
    is the easy choice).
    """
    out = bytearray()
    pos = lo
    for f in frames(data, lo, hi):
        if not f["parents"]:
            out += data[pos : f["start"]]
            pos = f["pend"] + 8
    out += data[pos:hi]
    return bytes(out)


def _boundary(data: bytes, start: int, pos: int) -> int:
    """Offset of the first top-level header frame at or after ``pos``.

    Walks from ``start``, itself the start or end of a top-level frame, so
    frames nested in a CNF/ENF on the way are not mistaken for boundaries.
    The end of the data when there is no such frame.
    """
    for f in frames(data, start):
        if not f["parents"] and f["start"] >= pos:
            return int(f["start"])
    return len(data)


class CnfSplit(NamedTuple):
    """A stream cut around a mid-file CNF; see ``split_at_cnf``."""

    pre: bytes  # the stream up to the CNF frame
    cnf: bytes  # the CNF frame itself
    post: bytes  # what follows it, truncated at a message boundary
    replay: bytes  # the data messages of ``pre`` since the previous CNF
    replay_head: bytes  # roughly the first half of ``replay``


def split_at_cnf(data: bytes, nth: int = 1, tail: int = 1 << 17) -> CnfSplit:
    """Cut a stream around its ``nth`` top-level CNF frame.

    Top-level CNF 0 is the configuration block that opens a file. Loggers
    write later ones mid-stream, between data messages: a short one that
    re-defines a single channel (SFJ, 86), or the whole configuration again
    (the official AIM sample). ``pre + cnf + post`` is the original stream,
    cut off about ``tail`` bytes after the CNF to keep the input small.

    ``replay`` holds a copy of every data message between the previous
    top-level CNF and this one, so ``pre + cnf + replay + post`` is a stream
    whose timecodes step back across the CNF: every replayed message carries
    a timecode at or before the last one sent before the CNF. No shipped
    fixture does that. ``replay_head`` is the first half of the same span.
    """
    cnfs = []
    for f in frames(data):
        if f["tok"] == "CNF" and not f["parents"]:
            cnfs.append(f)
            if len(cnfs) > nth:
                break
    prev_end, cnf = cnfs[nth - 1]["pend"] + 8, cnfs[nth]
    start, end = cnf["start"], cnf["pend"] + 8
    return CnfSplit(
        pre=data[:start],
        cnf=data[start:end],
        post=data[end : _boundary(data, end, end + tail)],
        replay=data_messages(data, prev_end, start),
        replay_head=data_messages(
            data, prev_end, _boundary(data, prev_end, (prev_end + start) // 2)
        ),
    )
