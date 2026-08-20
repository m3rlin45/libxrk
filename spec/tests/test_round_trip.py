"""Round-trip tests: parse -> build -> compare for all message types.

For each message type, extract raw bytes from a real file, parse with Construct,
build back, and verify byte-identical output.
"""

import io
import struct

import pytest


from construct import ConstructError

from spec.xrk_format import (
    CHSPayload,
    GRPPayload,
    GPSPayload,
    GNFIPayload,
    LAPPayload,
    CDEPayload,
    GPSRPayload,
    HeaderMessage,
    _C_MSG_VARIANT_STRUCTS,
    _DATA_MSG_STRUCTS,
    _container_to_dict,
    build_header_frame,
    _tokdec,
    cMessageV1Compiled,
    cMessageV2Compiled,
    cMessageV3Compiled,
)
from spec.tests.conftest import ISSUE68_XRZ


# ---------------------------------------------------------------------------
# CHS round-trip (112 bytes)
# ---------------------------------------------------------------------------


class TestCHSRoundTrip:
    """CHS payloads should survive parse -> build round-trip."""

    def test_chs_round_trip(self, parsed_only):
        """Every CHS payload should round-trip exactly."""
        chs_msgs = parsed_only.find_nested_messages("CHS")
        assert len(chs_msgs) > 0
        for msg in chs_msgs:
            raw = msg.raw_payload
            assert len(raw) == 112
            parsed = CHSPayload.parse(raw)
            rebuilt = CHSPayload.build(parsed)
            assert rebuilt == raw, f"CHS round-trip failed for {parsed.name!r}"


# ---------------------------------------------------------------------------
# GRP round-trip
# ---------------------------------------------------------------------------


class TestGRPRoundTrip:
    """GRP payloads should survive parse -> build round-trip."""

    def test_86_grp_round_trip(self, file_86_parsed):
        """Every GRP payload from 86 should round-trip exactly."""
        grp_msgs = file_86_parsed.find_nested_messages("GRP")
        assert len(grp_msgs) > 0
        for msg in grp_msgs:
            raw = msg.raw_payload
            parsed = GRPPayload.parse(raw)
            rebuilt = GRPPayload.build(parsed)
            assert rebuilt == raw, f"GRP round-trip failed for index {parsed.index}"


# ---------------------------------------------------------------------------
# GPS round-trip (56 bytes)
# ---------------------------------------------------------------------------


class TestGPSRoundTrip:
    """GPS payloads should survive parse -> build round-trip."""

    def test_gps_round_trip(self, parsed_only):
        """First 10 GPS payloads should round-trip."""
        for raw in parsed_only.gps_payloads[:10]:
            assert len(raw) == 56
            parsed = GPSPayload.parse(raw)
            rebuilt = GPSPayload.build(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# LAP round-trip (20 bytes for v0/v1, 32 bytes for v2)
# ---------------------------------------------------------------------------


class TestLAPRoundTrip:
    """LAP payloads should survive parse -> build round-trip."""

    def test_lap_round_trip(self, parsed_only):
        """Every LAP payload should round-trip."""
        lap_msgs = parsed_only.messages_by_token("LAP")
        assert len(lap_msgs) > 0
        for msg in lap_msgs:
            raw = msg.raw_payload
            assert len(raw) == 20
            parsed = LAPPayload.parse(raw)
            rebuilt = LAPPayload.build(parsed)
            assert rebuilt == raw

    def test_lap_v2_round_trip(self, aim_official_parsed):
        """Every v2 (32-byte) LAP payload should round-trip byte-identically,
        and the derived end_time must come from offset [28:32]."""
        lap_msgs = aim_official_parsed.messages_by_token("LAP")
        assert len(lap_msgs) > 0
        for msg in lap_msgs:
            raw = msg.raw_payload
            assert len(raw) == 32
            assert msg.version == 2
            parsed = LAPPayload.parse(raw)
            assert parsed.end_time == int.from_bytes(raw[28:32], "little")
            rebuilt = LAPPayload.build(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# CDE round-trip (6 bytes)
# ---------------------------------------------------------------------------


class TestCDERoundTrip:
    """CDE payloads should survive parse -> build round-trip."""

    def test_sfj_cde_round_trip(self, sfj_parsed):
        """Every CDE payload from SFJ should round-trip."""
        cde_msgs = sfj_parsed.messages_by_token("CDE")
        if not cde_msgs:
            pytest.skip("No CDE messages in SFJ")
        for msg in cde_msgs:
            raw = msg.raw_payload
            assert len(raw) == 6
            parsed = CDEPayload.parse(raw)
            rebuilt = CDEPayload.build(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# GNFI round-trip (32 bytes)
# ---------------------------------------------------------------------------


class TestGNFIRoundTrip:
    """GNFI payloads should survive parse -> build round-trip."""

    def test_sfj_gnfi_round_trip(self, sfj_parsed):
        """First 10 GNFI payloads from SFJ should round-trip."""
        if not sfj_parsed.gnfi_payloads:
            pytest.skip("No GNFI messages in SFJ")
        for raw in sfj_parsed.gnfi_payloads[:10]:
            assert len(raw) == 32
            parsed = GNFIPayload.parse(raw)
            rebuilt = GNFIPayload.build(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# GPSR round-trip (36 bytes)
# ---------------------------------------------------------------------------


class TestGPSRRoundTrip:
    """GPSR payloads should survive parse -> build round-trip."""

    def test_sfj_gpsr_round_trip(self, sfj_parsed):
        """GPSR payload from SFJ should round-trip."""
        gpsr_msgs = sfj_parsed.messages_by_token("GPSR")
        assert len(gpsr_msgs) > 0
        for msg in gpsr_msgs:
            raw = msg.raw_payload
            assert len(raw) == 36
            parsed = GPSRPayload.parse(raw)
            rebuilt = GPSRPayload.build(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# String messages round-trip
# ---------------------------------------------------------------------------


class TestStringRoundTrip:
    """String message payloads are just null-terminated ASCII."""

    def test_sfj_string_messages_round_trip(self, sfj_parsed):
        """String message payloads should be unmodified bytes."""
        string_tokens = ["RCR", "VEH", "TMD", "TMT", "VTY", "CMP", "NTE", "NDV"]
        for tok_str in string_tokens:
            msgs = sfj_parsed.messages_by_token(tok_str)
            for msg in msgs:
                raw = msg.raw_payload
                # raw_payload should be the original bytes
                assert isinstance(raw, (bytes, bytearray, memoryview))
                # The parsed string should be derivable from the raw payload
                parsed_str = msg.payload
                if parsed_str is not None:
                    # Null-terminate and compare
                    zero = raw.find(0)
                    if zero >= 0:
                        assert parsed_str == raw[:zero].decode("ascii", errors="replace")
                    else:
                        assert parsed_str == raw.decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# Header message framing round-trip
# ---------------------------------------------------------------------------


class TestHeaderFrameRoundTrip:
    """Header message framing should survive build -> parse round-trip."""

    def test_build_parse_chs_frame(self, sfj_parsed):
        """Build a CHS header frame and parse it back."""
        chs_msgs = sfj_parsed.find_nested_messages("CHS")
        msg = chs_msgs[0]
        # Build a complete framed message
        frame = build_header_frame("CHS", msg.raw_payload, version=msg.version)
        # Parse it back using HeaderMessage Struct directly
        parsed = HeaderMessage.parse(frame)
        assert parsed.token == _tokdec("CHS")
        assert parsed.version == msg.version
        assert bytes(parsed.raw_payload) == msg.raw_payload

    def test_build_parse_lap_frame(self, sfj_parsed):
        """Build a LAP header frame and parse it back."""
        lap_msgs = sfj_parsed.messages_by_token("LAP")
        msg = lap_msgs[0]
        frame = build_header_frame("LAP", msg.raw_payload, version=msg.version)
        parsed = HeaderMessage.parse(frame)
        assert parsed.token == _tokdec("LAP")
        assert bytes(parsed.raw_payload) == msg.raw_payload

    def test_build_parse_gps_frame(self, sfj_parsed):
        """Build a GPS header frame and parse it back."""
        raw = sfj_parsed.gps_payloads[0]
        frame = build_header_frame("GPS", raw, version=1)
        parsed = HeaderMessage.parse(frame)
        # GPS is a 3-char token, so it gets padded and then stripped
        assert parsed.token == _tokdec("GPS")
        assert bytes(parsed.raw_payload) == raw

    def test_build_parse_4char_token(self, sfj_parsed):
        """Build a GNFI header frame (4-char token) and parse it back."""
        if not sfj_parsed.gnfi_payloads:
            pytest.skip("No GNFI data")
        raw = sfj_parsed.gnfi_payloads[0]
        frame = build_header_frame("GNFI", raw, version=1)
        parsed = HeaderMessage.parse(frame)
        assert parsed.token == _tokdec("GNFI")
        assert bytes(parsed.raw_payload) == raw


# ---------------------------------------------------------------------------
# Data message framing round-trip
# ---------------------------------------------------------------------------


class TestDataMessageRoundTrip:
    """Data messages should survive build -> parse -> compare round-trip."""

    def _build_s_message(self, timecode, channel_index, data):
        """Build an (S data message."""
        return struct.pack("<HiH", 0x5328, timecode, channel_index) + data + b")"

    def _build_g_message(self, timecode, group_index, data):
        """Build a (G data message."""
        return struct.pack("<HiH", 0x4728, timecode, group_index) + data + b")"

    def _build_m_message(self, timecode, channel_index, count, data):
        """Build an (M data message."""
        return struct.pack("<HiHH", 0x4D28, timecode, channel_index, count) + data + b")"

    def _parse_data_message(self, frame, channel_sizes, group_sizes):
        """Parse a data message using compiled opcode dispatch."""
        opcode = frame[:2]
        struct_def = _DATA_MSG_STRUCTS.get(opcode)
        if struct_def is None:
            return None
        try:
            stream = io.BytesIO(frame)
            dmsg = struct_def.parse_stream(
                stream,
                channel_sizes=channel_sizes,
                group_sizes=group_sizes,
            )
            return dmsg.msg_type, _container_to_dict(dmsg), stream.tell()
        except (ConstructError, KeyError):
            return None

    def test_s_message_round_trip(self, sfj_parsed):
        """S message build -> parse should match."""
        s_msgs = [m for m in sfj_parsed.data_messages() if m.msg_type == "S"]
        if not s_msgs:
            pytest.skip("No S messages")

        msg = s_msgs[0]
        frame = self._build_s_message(
            msg.parsed["timecode"],
            msg.parsed["channel_index"],
            msg.parsed["data"],
        )
        result = self._parse_data_message(frame, sfj_parsed.channel_sizes, sfj_parsed.group_sizes)
        assert result is not None
        msg_type, parsed, next_offset = result
        assert msg_type == "S"
        assert parsed["timecode"] == msg.parsed["timecode"]
        assert parsed["channel_index"] == msg.parsed["channel_index"]
        assert parsed["data"] == msg.parsed["data"]
        assert next_offset == len(frame)

    def test_g_message_round_trip(self, file_86_parsed):
        """G message build -> parse should match."""
        g_msgs = [m for m in file_86_parsed.data_messages() if m.msg_type == "G"]
        if not g_msgs:
            pytest.skip("No G messages")

        msg = g_msgs[0]
        frame = self._build_g_message(
            msg.parsed["timecode"],
            msg.parsed["group_index"],
            msg.parsed["data"],
        )
        result = self._parse_data_message(
            frame, file_86_parsed.channel_sizes, file_86_parsed.group_sizes
        )
        assert result is not None
        msg_type, parsed, next_offset = result
        assert msg_type == "G"
        assert parsed["timecode"] == msg.parsed["timecode"]
        assert parsed["group_index"] == msg.parsed["group_index"]
        assert parsed["data"] == msg.parsed["data"]

    def test_m_message_round_trip(self, sfj_parsed):
        """M message build -> parse should match."""
        m_msgs = [m for m in sfj_parsed.data_messages() if m.msg_type == "M"]
        if not m_msgs:
            pytest.skip("No M messages")

        msg = m_msgs[0]
        frame = self._build_m_message(
            msg.parsed["timecode"],
            msg.parsed["channel_index"],
            msg.parsed["count"],
            msg.parsed["data"],
        )
        result = self._parse_data_message(frame, sfj_parsed.channel_sizes, sfj_parsed.group_sizes)
        assert result is not None
        msg_type, parsed, next_offset = result
        assert msg_type == "M"
        assert parsed["timecode"] == msg.parsed["timecode"]
        assert parsed["channel_index"] == msg.parsed["channel_index"]
        assert parsed["count"] == msg.parsed["count"]
        assert parsed["data"] == msg.parsed["data"]


# ---------------------------------------------------------------------------
# c-message round-trip — all three variants (V1, V2, V3) for issue #68
# ---------------------------------------------------------------------------


class TestCMessageRoundTrip:
    """(c expansion-channel messages must round-trip for each variant.

    V1 is exercised by every older corpus that has c-messages; V2 and V3
    appear only on the issue68 fixture per c_variant_scanner.py.
    """

    def _rebuild_c_variant(self, parsed, channel_sizes):
        """Build a c-message frame from a parsed dict for its variant.

        Const fields (unk1/unk3/unk4) are written from the struct's
        hardcoded values — no need to supply them in the Container.
        """
        from construct import Container

        variant = parsed["variant"]
        struct_def = _C_MSG_VARIANT_STRUCTS[variant]
        container = Container(
            channel_field=parsed["channel_field"],
            data=parsed["data"],
        )
        if variant in ("V1", "V2"):
            container["timecode"] = parsed["timecode"]
        return struct_def.build(container, channel_sizes=channel_sizes)

    def _find_raw_c_frames(self, data_bytes, expected_variants):
        """Scan raw bytes for (c message frames, classify by (unk1, unk4).

        Returns a dict {variant: (file_offset, raw_frame_bytes)} capturing
        the first frame of each requested variant.
        """
        found = {}
        i = 0
        needle = b"(c"
        while True:
            i = data_bytes.find(needle, i)
            if i < 0 or len(found) == len(expected_variants):
                break
            if i + 10 > len(data_bytes):
                break
            unk1 = data_bytes[i + 2]
            unk3 = data_bytes[i + 5]
            unk4 = data_bytes[i + 6]
            if unk3 != 0x84:
                i += 1
                continue
            # Classify — must match variant shape AND close byte.
            if unk1 == 0 and unk4 == 0x08:
                frame = data_bytes[i : i + 16]
                if len(frame) == 16 and frame[-1] == 0x29 and "V2" not in found:
                    found["V2"] = (i, frame)
            elif unk1 == 0x01 and unk4 == 0x02:
                frame = data_bytes[i : i + 10]
                if len(frame) == 10 and frame[-1] == 0x29 and "V3" not in found:
                    found["V3"] = (i, frame)
            i += 1
        return found

    def test_c_variants_raw_frame_round_trip(self, issue68_parsed):
        """Locate one frame of each variant in the raw byte stream, parse it,
        build it back, and assert byte-identity. This is the strictest
        round-trip check: it bypasses the spec's high-level parser and
        directly exercises each variant struct.
        """
        import zlib

        raw = ISSUE68_XRZ.read_bytes()
        if raw[:2] in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"):
            raw = zlib.decompress(raw)

        frames = self._find_raw_c_frames(raw, {"V2", "V3"})
        assert "V2" in frames, "Failed to locate a V2 frame in raw bytes"
        assert "V3" in frames, "Failed to locate a V3 frame in raw bytes"

        # V1 round-trip is covered by test_all_v1_c_samples_round_trip (below)
        # which rebuilds from parsed dicts. Raw-frame V1 requires knowing CHS
        # payload size per channel, which we'd have to duplicate from the spec.

        # V2 round-trip — fixed 4-byte payload, no CHS dependency.
        _, v2_frame = frames["V2"]
        v2_parsed = cMessageV2Compiled.parse(v2_frame)
        v2_rebuilt = cMessageV2Compiled.build(v2_parsed)
        assert v2_rebuilt == v2_frame, "V2 raw-frame round-trip must be byte-identical"

        # V3 round-trip — fixed 2-byte payload, no embedded timecode.
        _, v3_frame = frames["V3"]
        v3_parsed = cMessageV3Compiled.parse(v3_frame)
        v3_rebuilt = cMessageV3Compiled.build(v3_parsed)
        assert v3_rebuilt == v3_frame, "V3 raw-frame round-trip must be byte-identical"

    def test_all_v1_c_samples_round_trip(self, issue68_parsed):
        """Spot-check three V1 c-messages from the parsed stream; sanity check
        that the parsed dict survives round-trip under the compiled struct.
        """
        v1_msgs = [
            m
            for m in issue68_parsed.data_messages()
            if m.msg_type == "c" and m.parsed.get("variant") == "V1"
        ]
        if not v1_msgs:
            pytest.skip("No V1 c-messages in issue68 fixture")
        for msg in (v1_msgs[0], v1_msgs[len(v1_msgs) // 2], v1_msgs[-1]):
            rebuilt = self._rebuild_c_variant(msg.parsed, issue68_parsed.channel_sizes)
            reparsed = cMessageV1Compiled.parse(rebuilt, channel_sizes=issue68_parsed.channel_sizes)
            assert reparsed.channel_field == msg.parsed["channel_field"]
            assert reparsed.timecode == msg.parsed["timecode"]
            assert bytes(reparsed.data) == msg.parsed["data"]
