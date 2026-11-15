"""Streams whose last bytes are a 10-byte data message (issue #163).

A recording ends with header frames (the issue68 fixture's last frame is a
LAP), so no fixture has a data message as the very last thing in the file.
These streams do. Each is a CNF defining one channel, then that channel's
samples, built in memory with ``xrk_patch``; the channel definition is CHS 82
(``LR_Shock_Pot``) of the issue68 fixture.

Ten bytes is the shortest data message a logger writes: a V3 ``(c`` message,
or an ``(S`` message with a 1-byte sample. Both are here. ``tests`` checks
what the two backends make of them (``test_end_of_stream.py``) and
``spec/tests`` checks the spec against each backend.
"""

from __future__ import annotations

import functools
import struct
from pathlib import Path
from typing import NamedTuple

from . import xrk_patch

ISSUE68_XRZ = (
    Path(__file__).parent / "test_data/issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz"
)
CHANNEL_INDEX = 82
# The (c channel_field pair the issue68 logger uses for CHS 82.
BASE_FIELD, PLUS4_FIELD = 0x1290, 0x1294


class Stream(NamedTuple):
    """One stream and what a parser must return for its only channel."""

    data: bytes
    channel: str
    #: Logger-clock timecodes of the samples. Nothing else is in the stream,
    #: so the session starts at the first one.
    timecodes: list[int]
    values: list[float]
    #: Size of the last message, the one that ends at the end of ``data``.
    last_message_size: int


@functools.lru_cache(maxsize=None)
def _chs() -> tuple[bytes, int, int]:
    """(payload, CHS frame version, CNF frame version) of the fixture's CHS 82."""
    data = xrk_patch.load_bytes(ISSUE68_XRZ)
    for frame in xrk_patch.frames(data):
        payload = data[frame["pstart"] : frame["pend"]]
        if frame["tok"] == "CHS" and struct.unpack_from("<H", payload)[0] == CHANNEL_INDEX:
            return payload, frame["ver"], frame["parents"][-1]["ver"]
    raise AssertionError(f"{ISSUE68_XRZ.name} lost its CHS {CHANNEL_INDEX}")


def _cnf(chs_payload: bytes) -> bytes:
    """A CNF frame holding one CHS frame."""
    _, chs_ver, cnf_ver = _chs()
    return xrk_patch.build_frame("CNF", xrk_patch.build_frame("CHS", chs_payload, chs_ver), cnf_ver)


def ends_on_v3_c() -> Stream:
    """Two 10 ms cycles of a 500 Hz shock pot, as the issue68 logger writes them.

    A cycle is V2(+4 field), V2(base field), V3(+4 field): five samples 2 ms
    apart, the V3's in fourth place. The stream ends on the second V3. (A V3
    after a V2 on the base field alone, the stream in the issue, also shows
    the fault, but the spec and the backends resolve that layout differently:
    issue #173.)
    """
    messages = [
        xrk_patch.c_v2_message(PLUS4_FIELD, 1004, (2.0, 1.0)),  # 1002, 1000
        xrk_patch.c_v2_message(BASE_FIELD, 1008, (5.0, 3.0)),  # 1008, 1004
        xrk_patch.c_v3_message(PLUS4_FIELD, 4.0),  # 1006
        xrk_patch.c_v2_message(PLUS4_FIELD, 1014, (7.0, 6.0)),  # 1012, 1010
        xrk_patch.c_v2_message(BASE_FIELD, 1018, (10.0, 8.0)),  # 1018, 1014
        xrk_patch.c_v3_message(PLUS4_FIELD, 9.0),  # 1016
    ]
    return Stream(
        data=_cnf(_chs()[0]) + b"".join(messages),
        channel="LR_Shock_Pot",
        timecodes=list(range(1000, 1020, 2)),
        values=[float(v) for v in range(1, 11)],
        last_message_size=len(messages[-1]),
    )


def ends_on_1_byte_s() -> Stream:
    """Three ``(S`` messages of a 1-byte channel; the stream ends on the third.

    No fixture has a 1-byte channel, so the CHS is rewritten as one:
    decoder_type 13 (uint8) at [20], data_size 1 at [72].
    """
    payload = bytearray(_chs()[0])
    payload[20] = 13
    payload[72] = 1
    payload[32:56] = b"Status".ljust(24, b"\0")
    samples = {1000: 7, 1002: 8, 1004: 9}
    messages = [xrk_patch.s_message(tc, CHANNEL_INDEX, bytes([v])) for tc, v in samples.items()]
    return Stream(
        data=_cnf(bytes(payload)) + b"".join(messages),
        channel="Status",
        timecodes=list(samples),
        values=[float(v) for v in samples.values()],
        last_message_size=len(messages[-1]),
    )


STREAMS = {"V3 (c": ends_on_v3_c, "1-byte (S": ends_on_1_byte_s}
