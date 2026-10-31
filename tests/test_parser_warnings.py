"""The Rust backend reports on stderr what it could not interpret.

Fragments of messages the logger wrote over are expected and must not be
reported; a framed message that fails to parse must be. See
spec/docs/companion.md section 10.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TEST_DATA_DIR = Path(__file__).parent / "test_data"

# Files whose only unparseable bytes are fragments of messages the logger
# wrote over (the trailer, the ODO record closing the header block, or the
# following message). The last two also carry a short tail of a sample
# message mid-stream.
FRAGMENTS_ONLY = [
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrk",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0101.xrz",
    TEST_DATA_DIR / "issue68/CMD_KK-SII_Tsukuba_Car_Generic testing_a_0101.xrz",
    TEST_DATA_DIR / "ek9/CMD_EK9_Motegi_Generic testing_a_0351.xrz",
    TEST_DATA_DIR / "SFJ/CMD_SFJ_Suzuka Car_Generic testing_a_0090.xrk",
    TEST_DATA_DIR / "issue84/CMD_KK-SII_Tsukuba_Car_Qualifying testing_a_0159.xrz",
]


def _bad_byte_lines(path: Path) -> list[str]:
    """The "Bad bytes(...)" lines a fresh interpreter prints while parsing with Rust."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from libxrk import aim_xrk; aim_xrk(sys.argv[1])",
            str(path),
        ],
        env=dict(os.environ, LIBXRK_BACKEND="rust"),
        capture_output=True,
        text=True,
        check=True,
    )
    return [l for l in result.stderr.splitlines() if l.startswith("Bad bytes(")]


@pytest.mark.parametrize("path", FRAGMENTS_ONLY, ids=lambda p: p.name)
def test_fragments_of_overwritten_messages_are_not_reported(path):
    assert _bad_byte_lines(path) == []


def test_a_message_that_does_not_parse_is_still_reported(tmp_path):
    """Damage one S message in the middle of the file. It is still framed like
    a message but fails to parse, which is also what an unhandled message
    variant looks like, so it must stay visible."""
    stream = bytearray(FRAGMENTS_ONLY[0].read_bytes())  # an uncompressed .xrk
    # An S message past the midpoint that is followed by more sample data, not
    # by a header record (a damaged message right before a header record is
    # indistinguishable from a head fragment, by design).
    start = len(stream) // 2
    while True:
        start = stream.index(b")(S", start) + 1
        if b"<h" not in stream[start : start + 64]:
            break
    # (S + timecode(4) + channel index(2) + data + ): point the index at a
    # channel that does not exist.
    stream[start + 6 : start + 8] = b"\xff\xff"
    damaged = tmp_path / "damaged.xrk"
    damaged.write_bytes(stream)

    assert len(_bad_byte_lines(damaged)) == 1
