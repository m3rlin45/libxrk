"""Round-trip tests: parse -> build -> compare for all message types.

For each message type, extract raw bytes from a real file, parse with Construct,
build back, and verify byte-identical output.
"""

import io
import struct

import pytest

pytestmark = pytest.mark.slow

from construct import ConstructError, Select

from spec.xrk_format import (
    CHSPayload,
    GRPPayload,
    GPSPayload,
    GNFIPayload,
    LAPPayload,
    CDEPayload,
    GPSRPayload,
    HeaderMessage,
    SMessage,
    GMessage,
    MMessage,
    cMessage,
    _container_to_dict,
    build_chs,
    build_grp,
    build_gps,
    build_lap,
    build_cde,
    build_gnfi,
    build_gpsr,
    build_header_frame,
    parse_xrk_file,
    chs_long_name,
    _tokdec,
    _tokenc,
)
from spec.tests.conftest import SFJ_XRK, FILE_86_XRK


def _find_nested_messages(result, token_str):
    """Find messages by token, including inside CNF/ENF sub-results."""
    from spec.xrk_format import ParseResult, _tokdec

    tok = _tokdec(token_str)
    found = []
    for m in result.messages:
        if m.msg_type == "header" and m.token == tok:
            found.append(m)
        # Recurse into CNF/ENF sub-results
        if m.msg_type == "header" and isinstance(m.payload, ParseResult):
            found.extend(_find_nested_messages(m.payload, token_str))
    return found


# ---------------------------------------------------------------------------
# CHS round-trip (112 bytes)
# ---------------------------------------------------------------------------


class TestCHSRoundTrip:
    """CHS payloads should survive parse -> build round-trip."""

    def test_sfj_chs_round_trip(self, sfj_parsed):
        """Every CHS payload from SFJ should round-trip exactly."""
        chs_msgs = _find_nested_messages(sfj_parsed, "CHS")
        assert len(chs_msgs) > 0
        for msg in chs_msgs:
            raw = msg.raw_payload
            assert len(raw) == 112
            parsed = CHSPayload.parse(raw)
            rebuilt = build_chs(parsed)
            assert rebuilt == raw, f"CHS round-trip failed for {chs_long_name(parsed)!r}"

    def test_86_chs_round_trip(self, file_86_parsed):
        """Every CHS payload from 86 should round-trip exactly."""
        chs_msgs = _find_nested_messages(file_86_parsed, "CHS")
        assert len(chs_msgs) > 0
        for msg in chs_msgs:
            raw = msg.raw_payload
            assert len(raw) == 112
            parsed = CHSPayload.parse(raw)
            rebuilt = build_chs(parsed)
            assert rebuilt == raw, f"CHS round-trip failed for {chs_long_name(parsed)!r}"


# ---------------------------------------------------------------------------
# GRP round-trip
# ---------------------------------------------------------------------------


class TestGRPRoundTrip:
    """GRP payloads should survive parse -> build round-trip."""

    def test_86_grp_round_trip(self, file_86_parsed):
        """Every GRP payload from 86 should round-trip exactly."""
        grp_msgs = _find_nested_messages(file_86_parsed, "GRP")
        assert len(grp_msgs) > 0
        for msg in grp_msgs:
            raw = msg.raw_payload
            parsed = GRPPayload.parse(raw)
            rebuilt = build_grp(parsed)
            assert rebuilt == raw, f"GRP round-trip failed for index {parsed.index}"


# ---------------------------------------------------------------------------
# GPS round-trip (56 bytes)
# ---------------------------------------------------------------------------


class TestGPSRoundTrip:
    """GPS payloads should survive parse -> build round-trip."""

    def test_sfj_gps_round_trip(self, sfj_parsed):
        """First 10 GPS payloads from SFJ should round-trip."""
        for raw in sfj_parsed.gps_payloads[:10]:
            assert len(raw) == 56
            parsed = GPSPayload.parse(raw)
            rebuilt = build_gps(parsed)
            assert rebuilt == raw

    def test_86_gps_round_trip(self, file_86_parsed):
        """First 10 GPS payloads from 86 should round-trip."""
        for raw in file_86_parsed.gps_payloads[:10]:
            assert len(raw) == 56
            parsed = GPSPayload.parse(raw)
            rebuilt = build_gps(parsed)
            assert rebuilt == raw


# ---------------------------------------------------------------------------
# LAP round-trip (20 bytes)
# ---------------------------------------------------------------------------


class TestLAPRoundTrip:
    """LAP payloads should survive parse -> build round-trip."""

    def test_sfj_lap_round_trip(self, sfj_parsed):
        """Every LAP payload from SFJ should round-trip."""
        lap_msgs = sfj_parsed.messages_by_token("LAP")
        assert len(lap_msgs) > 0
        for msg in lap_msgs:
            raw = msg.raw_payload
            assert len(raw) == 20
            parsed = LAPPayload.parse(raw)
            rebuilt = build_lap(parsed)
            assert rebuilt == raw

    def test_86_lap_round_trip(self, file_86_parsed):
        """Every LAP payload from 86 should round-trip."""
        lap_msgs = file_86_parsed.messages_by_token("LAP")
        assert len(lap_msgs) > 0
        for msg in lap_msgs:
            raw = msg.raw_payload
            assert len(raw) == 20
            parsed = LAPPayload.parse(raw)
            rebuilt = build_lap(parsed)
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
            rebuilt = build_cde(parsed)
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
            rebuilt = build_gnfi(parsed)
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
            rebuilt = build_gpsr(parsed)
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
        chs_msgs = _find_nested_messages(sfj_parsed, "CHS")
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
        """Parse a data message using Select(SMessage, GMessage, MMessage, cMessage)."""
        try:
            stream = io.BytesIO(frame)
            dmsg = Select(SMessage, GMessage, MMessage, cMessage).parse_stream(
                stream,
                channel_sizes=channel_sizes,
                group_sizes=group_sizes,
            )
            return dmsg.msg_type, _container_to_dict(dmsg), stream.tell()
        except ConstructError:
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
