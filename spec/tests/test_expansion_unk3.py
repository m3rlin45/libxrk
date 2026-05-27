"""Tests for (c) messages with unk3=0x04 (AIM expansion-module loggers).

AIM expansion-module loggers (brake PSI sensors, rotor temperature sensors,
EGT modules, steering sensors, etc.) emit (c) data records with unk3=0x04
rather than the 0x84 seen on standard AIM loggers such as MXP and MXm.

The (unk1, unk4) pair is the true variant discriminator for (c) messages.
unk3 is a secondary flag. Both 0x84 and 0x04 belong to the same record
family and must be accepted.

These tests use synthetic byte streams — no real XRK fixture is needed.
The wire layout follows spec/xrk_format.py and is described in
spec/docs/unknown_regions.md § (c) Expansion Data Messages.
"""

import struct
import unittest

from construct import Container

from spec.xrk_format import (
    cMessageV1Compiled,
    cMessageV2Compiled,
    cMessageV3Compiled,
    _C_MSG_UNK3_VALID,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic (c) frames
# ---------------------------------------------------------------------------


def _make_v1_frame(channel_field: int, unk3: int, timecode: int, payload: bytes) -> bytes:
    """Build a raw V1 (c) frame: (c + unk1 + channel_field + unk3 + unk4 + tc + payload + )"""
    return (
        b"(c"
        + struct.pack("<BHHBI", 0x00, channel_field, unk3, 0x06, timecode)[:7]
        + payload
        + b")"
    )


def _v1_frame(channel_field: int, unk3: int, timecode: int, payload: bytes) -> bytes:
    """Pack a V1 frame using the same byte layout the parser expects.

    Layout (12 + len(payload) bytes):
      [0:2]  b'(c'
      [2]    unk1 = 0x00
      [3:5]  channel_field (LE uint16)
      [5]    unk3
      [6]    unk4 = 0x06
      [7:11] timecode (LE int32)
      [11:]  payload (len = channel_size)
      [-1]   b')'
    """
    return (
        b"(c"
        + struct.pack("B", 0x00)
        + struct.pack("<H", channel_field)
        + struct.pack("B", unk3)
        + struct.pack("B", 0x06)
        + struct.pack("<i", timecode)
        + payload
        + b")"
    )


def _v2_frame(channel_field: int, unk3: int, timecode: int, fp16_a: bytes, fp16_b: bytes) -> bytes:
    """Pack a V2 frame (16 bytes).

    Layout:
      [0:2]  b'(c'
      [2]    unk1 = 0x00
      [3:5]  channel_field (LE uint16)
      [5]    unk3
      [6]    unk4 = 0x08
      [7:11] timecode (LE int32)
      [11:13] fp16 sample A
      [13:15] fp16 sample B
      [15]   b')'
    """
    return (
        b"(c"
        + struct.pack("B", 0x00)
        + struct.pack("<H", channel_field)
        + struct.pack("B", unk3)
        + struct.pack("B", 0x08)
        + struct.pack("<i", timecode)
        + fp16_a
        + fp16_b
        + b")"
    )


def _v3_frame(channel_field: int, unk3: int, fp16: bytes) -> bytes:
    """Pack a V3 frame (10 bytes).

    Layout:
      [0:2]  b'(c'
      [2]    unk1 = 0x01
      [3:5]  channel_field (LE uint16)
      [5]    unk3
      [6]    unk4 = 0x02
      [7:9]  fp16 sample
      [9]    b')'
    """
    return (
        b"(c"
        + struct.pack("B", 0x01)
        + struct.pack("<H", channel_field)
        + struct.pack("B", unk3)
        + struct.pack("B", 0x02)
        + fp16
        + b")"
    )


# ---------------------------------------------------------------------------
# Unit tests — spec structs parse unk3=0x04
# ---------------------------------------------------------------------------


class TestUnk3ValidValues(unittest.TestCase):
    """_C_MSG_UNK3_VALID must include both 0x84 and 0x04."""

    def test_valid_set_contains_0x84(self):
        self.assertIn(b"\x84", _C_MSG_UNK3_VALID)

    def test_valid_set_contains_0x04(self):
        self.assertIn(b"\x04", _C_MSG_UNK3_VALID)

    def test_valid_set_does_not_contain_other_bytes(self):
        for bad in (b"\x00", b"\x01", b"\x85", b"\x08", b"\xff"):
            self.assertNotIn(bad, _C_MSG_UNK3_VALID, f"{bad!r} should not be in _C_MSG_UNK3_VALID")


class TestV1Unk3(unittest.TestCase):
    """cMessageV1Compiled must accept unk3 in {0x84, 0x04} and reject others."""

    # channel_field >> 3 gives the channel index; low 3 bits must be 4.
    # channel_field = (0 << 3) | 4 = 4  (channel index 0, payload = 4 bytes)
    _CHANNEL_FIELD = 4
    _PAYLOAD = b"\x00\x00\x00\x00"  # 4-byte int32 sample
    _CHANNEL_SIZES = {0: 4}
    _TC = 1000

    def _parse_v1(self, unk3: int):
        frame = _v1_frame(self._CHANNEL_FIELD, unk3, self._TC, self._PAYLOAD)
        return cMessageV1Compiled.parse(frame, channel_sizes=self._CHANNEL_SIZES)

    def test_unk3_0x84_accepted(self):
        """Standard value 0x84 must be accepted."""
        result = self._parse_v1(0x84)
        self.assertEqual(result.unk3, b"\x84")
        self.assertEqual(result.channel_field, self._CHANNEL_FIELD)
        self.assertEqual(result.timecode, self._TC)
        self.assertEqual(bytes(result.data), self._PAYLOAD)

    def test_unk3_0x04_accepted(self):
        """Expansion-module value 0x04 must be accepted."""
        result = self._parse_v1(0x04)
        self.assertEqual(result.unk3, b"\x04")
        self.assertEqual(result.channel_field, self._CHANNEL_FIELD)
        self.assertEqual(result.timecode, self._TC)
        self.assertEqual(bytes(result.data), self._PAYLOAD)

    def test_unk3_0x85_rejected(self):
        """Any value outside {0x84, 0x04} must raise on parse."""
        from construct import ConstructError

        frame = _v1_frame(self._CHANNEL_FIELD, 0x85, self._TC, self._PAYLOAD)
        with self.assertRaises(ConstructError):
            cMessageV1Compiled.parse(frame, channel_sizes=self._CHANNEL_SIZES)

    def test_v1_unk3_0x04_round_trip(self):
        """A frame with unk3=0x04 must survive parse → build byte-identically."""
        frame = _v1_frame(self._CHANNEL_FIELD, 0x04, self._TC, self._PAYLOAD)
        parsed = cMessageV1Compiled.parse(frame, channel_sizes=self._CHANNEL_SIZES)
        rebuilt = cMessageV1Compiled.build(
            Container(
                channel_field=parsed.channel_field,
                unk3=parsed.unk3,
                timecode=parsed.timecode,
                data=parsed.data,
            ),
            channel_sizes=self._CHANNEL_SIZES,
        )
        self.assertEqual(rebuilt, frame, "V1 unk3=0x04 round-trip must be byte-identical")

    def test_v1_unk3_0x84_round_trip(self):
        """A frame with unk3=0x84 must survive parse → build byte-identically."""
        frame = _v1_frame(self._CHANNEL_FIELD, 0x84, self._TC, self._PAYLOAD)
        parsed = cMessageV1Compiled.parse(frame, channel_sizes=self._CHANNEL_SIZES)
        rebuilt = cMessageV1Compiled.build(
            Container(
                channel_field=parsed.channel_field,
                unk3=parsed.unk3,
                timecode=parsed.timecode,
                data=parsed.data,
            ),
            channel_sizes=self._CHANNEL_SIZES,
        )
        self.assertEqual(rebuilt, frame, "V1 unk3=0x84 round-trip must be byte-identical")


class TestV2Unk3(unittest.TestCase):
    """cMessageV2Compiled must accept unk3 in {0x84, 0x04}."""

    _CHANNEL_FIELD = 0x10  # arbitrary
    _TC = 2000
    _FP16_A = b"\x00\x42"  # arbitrary fp16
    _FP16_B = b"\x00\x41"

    def _parse_v2(self, unk3: int):
        frame = _v2_frame(self._CHANNEL_FIELD, unk3, self._TC, self._FP16_A, self._FP16_B)
        return cMessageV2Compiled.parse(frame, channel_sizes={})

    def test_unk3_0x84_accepted(self):
        result = self._parse_v2(0x84)
        self.assertEqual(result.unk3, b"\x84")
        self.assertEqual(result.timecode, self._TC)

    def test_unk3_0x04_accepted(self):
        result = self._parse_v2(0x04)
        self.assertEqual(result.unk3, b"\x04")
        self.assertEqual(result.timecode, self._TC)

    def test_v2_unk3_0x04_round_trip(self):
        frame = _v2_frame(self._CHANNEL_FIELD, 0x04, self._TC, self._FP16_A, self._FP16_B)
        parsed = cMessageV2Compiled.parse(frame, channel_sizes={})
        rebuilt = cMessageV2Compiled.build(
            Container(
                channel_field=parsed.channel_field,
                unk3=parsed.unk3,
                timecode=parsed.timecode,
                data=parsed.data,
            ),
            channel_sizes={},
        )
        self.assertEqual(rebuilt, frame, "V2 unk3=0x04 round-trip must be byte-identical")


class TestV3Unk3(unittest.TestCase):
    """cMessageV3Compiled must accept unk3 in {0x84, 0x04}."""

    _CHANNEL_FIELD = 0x14
    _FP16 = b"\x80\x3c"

    def _parse_v3(self, unk3: int):
        frame = _v3_frame(self._CHANNEL_FIELD, unk3, self._FP16)
        return cMessageV3Compiled.parse(frame, channel_sizes={})

    def test_unk3_0x84_accepted(self):
        result = self._parse_v3(0x84)
        self.assertEqual(result.unk3, b"\x84")

    def test_unk3_0x04_accepted(self):
        result = self._parse_v3(0x04)
        self.assertEqual(result.unk3, b"\x04")

    def test_v3_unk3_0x04_round_trip(self):
        frame = _v3_frame(self._CHANNEL_FIELD, 0x04, self._FP16)
        parsed = cMessageV3Compiled.parse(frame, channel_sizes={})
        rebuilt = cMessageV3Compiled.build(
            Container(
                channel_field=parsed.channel_field,
                unk3=parsed.unk3,
                data=parsed.data,
            ),
            channel_sizes={},
        )
        self.assertEqual(rebuilt, frame, "V3 unk3=0x04 round-trip must be byte-identical")


if __name__ == "__main__":
    unittest.main()
