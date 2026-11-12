"""What a parser does with the last bytes of a stream (issue #163).

The Cython backend dropped a 10-byte data message that ended exactly at the
end of the input: its loop wanted an eleventh byte to be there. The Rust
backend and the spec kept it. With one more byte or a header frame after the
message, as in every recording seen so far, the backends already agreed.

The streams are built in memory (``tests/end_of_stream.py``).
"""

from __future__ import annotations

import numpy as np
import pytest

from libxrk._aim_xrk_cy import aim_xrk as cython_aim_xrk
from libxrk._aim_xrk_rs import aim_xrk as rust_aim_xrk

from . import xrk_patch
from .end_of_stream import STREAMS, Stream

BACKENDS = {"cython": cython_aim_xrk, "rust": rust_aim_xrk}

# What may follow the last data message: nothing (the case that was lost), a
# stray byte, or a header frame as in a real recording.
TRAILING_FRAME = xrk_patch.build_frame("RCR", b"A. Driver\0")
TAILS = {"nothing": b"", "one byte": b"\0", "header frame": TRAILING_FRAME}


@pytest.fixture(params=list(BACKENDS))
def decode(request):
    return BACKENDS[request.param]


@pytest.fixture(params=list(STREAMS))
def stream(request) -> Stream:
    return STREAMS[request.param]()


def _assert_every_sample(log, stream: Stream) -> None:
    """The stream's one channel holds every sample, the last message's included."""
    assert list(log.channels) == [stream.channel]
    table = log.channels[stream.channel]
    session_start = stream.timecodes[0]
    np.testing.assert_array_equal(
        table.column("timecodes").to_numpy(),
        np.array(stream.timecodes) - session_start,
    )
    np.testing.assert_array_equal(
        table.column(stream.channel).to_numpy().astype(np.float64), stream.values
    )


def test_the_streams_end_on_a_10_byte_message(stream):
    """The premise: the shortest data message is the last thing in the stream."""
    assert stream.last_message_size == 10
    last = stream.data[-10:]
    assert last[:1] == b"(" and last[-1:] == b")"
    # Nothing after the CNF is a header frame.
    assert [f["tok"] for f in xrk_patch.frames(stream.data)] == ["CNF", "CHS"]


@pytest.mark.parametrize("tail", list(TAILS))
def test_last_message_is_kept(decode, stream, tail):
    """Every sample is returned whatever follows the last message, nothing included.

    With nothing after it, Cython returned one row fewer than Rust: the
    sample of the last message (the fourth of the second cycle for the V3
    stream, since a V3's sample is not the newest of its cycle).
    """
    log = decode(stream.data + TAILS[tail])
    _assert_every_sample(log, stream)
    if tail == "header frame":
        # The frame ends at the end of the input too, and is read.
        assert log.metadata["Driver"] == "A. Driver"


def test_header_frame_cut_by_the_end_of_the_stream_is_skipped(decode, stream):
    """A header frame cut short at any byte costs nothing but itself.

    Cut after 10 bytes it leaves exactly what the loop requires before it
    looks at a message; the version byte, the closing ``>`` and the footer
    are not there to be read.
    """
    for cut in range(1, len(TRAILING_FRAME)):
        log = decode(stream.data + TRAILING_FRAME[:cut])
        _assert_every_sample(log, stream)
        assert "Driver" not in log.metadata, cut
