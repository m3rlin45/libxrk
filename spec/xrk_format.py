"""AIM XRK File Format Specification — Construct-based executable spec.

This module provides a complete, machine-readable specification of the AIM XRK/XRZ
motorsports telemetry binary format. It is built using the Construct library and
serves as:
  - A single source of truth for the binary format
  - A parser that can read real XRK files
  - A builder that can produce byte-identical output (round-trip)
  - A reference for other implementations (JS/TS)

The format is reverse-engineered from AIM MXP/MXm data loggers. Field names and
semantics are inferred from observed data and cross-referenced with the Cython
parser in src/libxrk/aim_xrk.pyx.

Usage:
    from spec.xrk_format import parse_xrk_file, parse_xrk_bytes
    messages = parse_xrk_file("path/to/file.xrk")
    messages = parse_xrk_bytes(raw_bytes)
"""

import struct
import zlib
from pathlib import Path

from construct import (
    Adapter,
    Array,
    Byte,
    Bytes,
    BytesInteger,
    Computed,
    Const,
    Construct,
    Container,
    ExprValidator,
    Flag,
    GreedyRange,
    If,
    IfThenElse,
    Int8ub,
    Int8sl,
    Int8ul,
    Int16sl,
    Int16ul,
    Int32sl,
    Int32ul,
    Float32l,
    Padding,
    Pass,
    Peek,
    Struct,
    Switch,
    this,
)

# ---------------------------------------------------------------------------
# Primitives and Helpers
# ---------------------------------------------------------------------------


def _tokdec(s):
    """Encode a token string to a uint32 LE integer.

    Matches aim_xrk.pyx:181-183.  Token strings are 3 or 4 ASCII characters
    stored as little-endian uint32.  3-char tokens are padded with a trailing
    space (0x20) by the logger; we strip that on decode.
    """
    if s:
        return ord(s[0]) + 256 * _tokdec(s[1:])
    return 0


def _tokenc(i):
    """Decode a uint32 LE integer to a token string.

    Matches aim_xrk.pyx:185-190.
    """
    s = ""
    while i:
        s += chr(i & 255)
        i >>= 8
    return s


# Pre-compute common tokens
TOK_CHS = _tokdec("CHS")
TOK_GRP = _tokdec("GRP")
TOK_GPS = _tokdec("GPS")
TOK_GPS1 = _tokdec("GPS1")
TOK_GNFI = _tokdec("GNFI")
TOK_LAP = _tokdec("LAP")
TOK_CDE = _tokdec("CDE")
TOK_CAL = _tokdec("CAL")
TOK_IDN = _tokdec("idn")
TOK_SRC = _tokdec("SRC")
TOK_TRK = _tokdec("TRK")
TOK_ODO = _tokdec("ODO")
TOK_GPSR = _tokdec("GPSR")
TOK_ISLV = _tokdec("iSLV")
TOK_RACM = _tokdec("RACM")
TOK_VET = _tokdec("VET")
TOK_CNF = _tokdec("CNF")
TOK_ENF = _tokdec("ENF")

# String message tokens
_STRING_TOKENS = frozenset(
    [
        _tokdec(t)
        for t in (
            "RCR",
            "VEH",
            "CMP",
            "VTY",
            "NDV",
            "TMD",
            "TMT",
            "DBUN",
            "DBUT",
            "DVER",
            "MANL",
            "MODL",
            "MANI",
            "MODI",
            "HWNF",
            "PDLT",
            "NTE",
        )
    ]
)

# Unit type map: unit_type_byte -> (unit_string, decimal_points)
# From aim_xrk.pyx:146-171
UNIT_MAP = {
    1: ("%", 2),
    3: ("g", 2),
    4: ("deg", 1),
    5: ("deg/s", 1),
    6: ("", 0),
    9: ("Hz", 0),
    11: ("", 0),
    12: ("mm", 0),
    14: ("bar", 2),
    15: ("rpm", 0),
    16: ("km/h", 0),
    17: ("C", 1),
    18: ("ms", 0),
    19: ("Nm", 0),
    20: ("km/h", 0),
    21: ("V", 1),
    22: ("l", 1),
    24: ("l/s", 0),
    26: ("time?", 0),
    27: ("A", 0),
    30: ("lambda", 2),
    31: ("gear", 0),
    33: ("%", 2),
    43: ("kg", 3),
}

# Decoder type table: decoder_type -> (struct_format, interpolate)
# From aim_xrk.pyx:111-137
DECODER_TABLE = {
    0: ("i", False),
    1: ("H", True),  # float16 encoded as uint16
    3: ("i", False),
    4: ("h", False),
    6: ("f", True),
    8: ("i", False),
    11: ("h", False),
    12: ("i", False),
    13: ("B", False),
    15: ("H", False),  # gear lookup
    20: ("H", True),  # float16 encoded as uint16
    22: ("i", False),
    24: ("i", False),
    26: ("i", False),
    27: ("i", False),
    31: ("i", False),
    32: ("i", False),
    33: ("i", False),
    37: ("i", False),
    38: ("i", False),
    39: ("i", False),
}

# Logger model ID to name mapping (from aim_xrk.pyx:141-144)
LOGGER_MODELS = {
    649: "MXP 1.3",
    793: "MXm",
}


def _nullterm(data, encoding="ascii"):
    """Decode a null-terminated string from bytes."""
    zero = data.find(0)
    if zero >= 0:
        data = data[:zero]
    return data.decode(encoding, errors="replace")


def _compute_checksum(payload_bytes):
    """Compute the header message checksum (sum of bytes as uint16).

    Matches aim_xrk.pyx:363-364 (std::accumulate).
    """
    return sum(payload_bytes) & 0xFFFF


# ---------------------------------------------------------------------------
# Header Message Payloads
# ---------------------------------------------------------------------------


# CHS — Channel Definition (112 bytes)
# Reference: aim_xrk.pyx:424-516, CHS layout comment at :439-472
CHSPayload = Struct(
    "index" / Int16ul,  # [0:2]    channel index
    "_pad_2_4" / Bytes(2),  # [2:4]    padding (expected zero)
    "hardware_id" / Int16ul,  # [4:6]    hardware ID (non-zero for GPS only)
    "source_channel_id" / Int16ul,  # [6:8]    source channel ID within device
    "hardware_ref" / Int32ul,  # [8:12]   hardware reference (GPS-only)
    "unit_type_byte" / Int8ul,  # [12]     unit type (lower 7 bits); high bit = calibrated flag
    "maybe_display_format" / Int8ul,  # [13]     purpose unclear
    "maybe_config_flags" / Int16ul,  # [14:16]  encoding unknown
    "source_type" / Int8ul,  # [16]     source type (1=internal, 5=GPS, 9=CAN, etc.)
    "_pad_17_20" / Bytes(3),  # [17:20]  padding (expected zero)
    "decoder_type" / Int8ul,  # [20]     decoder type, key into DECODER_TABLE
    "_pad_21_24" / Bytes(3),  # [21:24]  padding (expected zero)
    "short_name" / Bytes(8),  # [24:32]  short name, null-terminated ASCII
    "long_name" / Bytes(24),  # [32:56]  long name, null-terminated ASCII
    "_pad_56_64" / Bytes(8),  # [56:64]  padding (expected zero)
    "sample_period_us" / Int32ul,  # [64:68]  sample period in microseconds
    "data_offset" / Int16ul,  # [68:70]  data offset into packed channel data
    "_pad_70_72" / Bytes(2),  # [70:72]  padding (expected zero)
    "data_size" / Int8ul,  # [72]     data size (bytes per sample)
    "_pad_73_76" / Bytes(3),  # [73:76]  padding (expected zero)
    "device_tag" / Bytes(4),  # [76:80]  device tag ("@AIM" or null)
    "device_node_id" / Int8ul,  # [80]     device node ID
    "maybe_device_flags" / Int8ul,  # [81]     (3 values: 0x00, 0x02, 0x10)
    "_pad_82_84" / Bytes(2),  # [82:84]  padding (expected zero)
    "maybe_output_type" / Int8ul,  # [84]     (1/4/6/0xFF)
    "_pad_85_88" / Bytes(3),  # [85:88]  padding (expected zero)
    "display_index" / Int32ul,  # [88:92]  display index (0xFFFFFFFF for virtual)
    "maybe_output_size" / Int8ul,  # [92]     (0/2/4/8)
    "_pad_93_96" / Bytes(3),  # [93:96]  padding (expected zero)
    "cal_value_1" / Float32l,  # [96:100] calibration value 1
    "cal_value_2" / Float32l,  # [100:104] calibration value 2
    "display_range_min" / Float32l,  # [104:108] display range minimum
    "display_range_max" / Float32l,  # [108:112] display range maximum
)


# GRP — Group Definition (variable size)
# Reference: aim_xrk.pyx:418-421
GRPPayload = Struct(
    "index" / Int16ul,
    "count" / Int16ul,
    "channel_indices" / Array(this.count, Int16ul),
)


# GPS — GPS Message (56 bytes)
# Reference: aim_xrk.pyx:884-911
# 4-byte AIM timecode + 52-byte u-blox NAV-SOL payload
GPSPayload = Struct(
    "timecode" / Int32sl,  # [0:4]   AIM logger time [ms]
    "iTOW" / Int32ul,  # [4:8]   GPS time of week [ms]
    "fTOW" / Int32sl,  # [8:12]  Fractional TOW [ns], +/-500000
    "week" / Int16ul,  # [12:14] GPS week number
    "gpsFix" / Int8ul,  # [14]    Fix type (0=none, 2=2D, 3=3D)
    "flags" / Int8ul,  # [15]    Validity bitmask
    "ecefX" / Int32sl,  # [16:20] ECEF X position [cm]
    "ecefY" / Int32sl,  # [20:24] ECEF Y position [cm]
    "ecefZ" / Int32sl,  # [24:28] ECEF Z position [cm]
    "pAcc" / Int32ul,  # [28:32] Position accuracy [cm]
    "ecefVX" / Int32sl,  # [32:36] ECEF X velocity [cm/s]
    "ecefVY" / Int32sl,  # [36:40] ECEF Y velocity [cm/s]
    "ecefVZ" / Int32sl,  # [40:44] ECEF Z velocity [cm/s]
    "sAcc" / Int32ul,  # [44:48] Speed accuracy [cm/s]
    "pDOP" / Int16ul,  # [48:50] Position DOP [*0.01]
    "reserved1" / Int8ul,  # [50]    u-blox reserved
    "numSV" / Int8ul,  # [51]    Number of satellites used
    "reserved2" / Int32ul,  # [52:56] u-blox reserved
)


# GNFI — Logger Internal Clock (32 bytes)
# Reference: aim_xrk.pyx:1012-1035
GNFIPayload = Struct(
    "timecode" / Int32sl,  # [0:4]   Logger timecode
    "_unknown" / Bytes(28),  # [4:32]  Other data (not used for timing)
)


# LAP — Lap Marker (20 bytes)
# Reference: aim_xrk.pyx:517-521, 1038-1047
LAPPayload = Struct(
    "_pad" / Bytes(1),  # [0]     padding
    "segment" / Int8ul,  # [1]     segment number
    "lap_num" / Int16ul,  # [2:4]   lap number
    "duration" / Int32ul,  # [4:8]   lap duration [ms]
    "_reserved" / Bytes(8),  # [8:16]  reserved
    "end_time" / Int32ul,  # [16:20] lap end time [ms]
)


# CDE — Channel-Device Entry (6 bytes)
# Reference: aim_xrk.pyx:422-423
CDEPayload = Struct(
    "channel_index" / Int16ul,  # [0:2]
    "session_uid" / Int32ul,  # [2:6]
)


# CAL — Calibration (fixed 40-byte prefix; parsed via parse_cal())
# Reference: aim_xrk.pyx:585-603
CALPayload = Struct(
    "_prefix" / Bytes(8),  # [0:8]   unknown prefix
    "u32_8" / Int32ul,  # [8:12]  expected to be 1
    "_pad_12_20" / Bytes(8),  # [12:20] unknown
    "cal_type" / Int32ul,  # [20:24] calibration type (1=2-point, 20=IMU bias)
    "val_1" / Float32l,  # [24:28] cal value 1
    "val_2" / Float32l,  # [28:32] cal value 2
    "output_1" / Float32l,  # [32:36] output 1 (type 1 only; type 20 = 0 or junk)
    "output_2" / Float32l,  # [36:40] output 2 (type 1 only; type 20 = 0 or junk)
)


# idn — Logger Identity (min 10 bytes; parsed via parse_idn())
# Reference: aim_xrk.pyx:527-534
IDNPayload = Struct(
    "model_id" / Int16ul,  # [0:2]   model ID
    "_pad_2_6" / Bytes(4),  # [2:6]   unknown
    "logger_id" / Int32ul,  # [6:10]  logger serial number
)


# TRK — Track Info (min 44 bytes; parsed via parse_trk())
# Reference: aim_xrk.pyx:606-609
TRKPayload = Struct(
    "name" / Bytes(32),  # [0:32]  track name, null-terminated
    "_pad" / Bytes(4),  # [32:36] unknown
    "sf_lat" / Int32sl,  # [36:40] start/finish lat * 1e7
    "sf_long" / Int32sl,  # [40:44] start/finish long * 1e7
)


# GPSR — GPS Receiver (36 bytes)
# Reference: aim_xrk.pyx:548-558
GPSRPayload = Struct(
    "_prefix" / Bytes(4),  # [0:4]   unknown
    "gps_type" / Bytes(4),  # [4:8]   GPS type string (null-terminated)
    "_mid" / Bytes(14),  # [8:22]  unknown
    "channel_index" / Int16ul,  # [22:24] GPS channel index
    "_post" / Bytes(8),  # [24:32] unknown
    "u32_32" / Int32ul,  # [32:36] expected 410
)


# ---------------------------------------------------------------------------
# Parsing Functions (non-Construct, for flexibility)
# ---------------------------------------------------------------------------


def parse_header_message(data, offset):
    """Parse a single header message starting at offset.

    Returns (token_int, version, payload_bytes, next_offset) or None if invalid.

    Header message framing (from aim_xrk.pyx:215-226):
        '<h'          opcode (2 bytes: 0x3C 0x68)
        token         uint32 LE
        payload_len   int32 LE
        version       uint8
        '>'           close bracket (0x3E)
        [payload]     payload_len bytes
        '<'           footer open (0x3C)
        token         uint32 LE (must match header)
        checksum      uint16 LE (sum of payload bytes)
        '>'           footer close (0x3E)
    """
    if offset + 12 > len(data):
        return None

    op = struct.unpack_from("<H", data, offset)[0]
    if op != 0x683C:  # '<h'
        return None

    tok = struct.unpack_from("<I", data, offset + 2)[0]
    hlen = struct.unpack_from("<i", data, offset + 6)[0]
    ver = data[offset + 10]
    close = data[offset + 11]

    if close != 0x3E:  # '>'
        return None

    payload_start = offset + 12
    payload_end = payload_start + hlen

    if payload_end + 8 > len(data):
        return None

    # Footer
    ftr_open = data[payload_end]
    if ftr_open != 0x3C:  # '<'
        return None

    ftr_tok = struct.unpack_from("<I", data, payload_end + 1)[0]
    ftr_checksum = struct.unpack_from("<H", data, payload_end + 5)[0]
    ftr_close = data[payload_end + 7]

    if ftr_tok != tok:
        return None
    if ftr_close != 0x3E:  # '>'
        return None

    payload = data[payload_start:payload_end]
    actual_checksum = sum(payload) & 0xFFFF
    if actual_checksum != ftr_checksum:
        return None

    # Strip trailing space from 3-char tokens
    if (tok >> 24) == 0x20:
        tok -= 0x20 << 24

    return tok, ver, payload, payload_end + 8


def parse_chs(payload):
    """Parse a CHS payload into a dict of fields."""
    if len(payload) != 112:
        return None
    return CHSPayload.parse(payload)


def parse_grp(payload):
    """Parse a GRP payload into a dict of fields."""
    return GRPPayload.parse(payload)


def parse_gps(payload):
    """Parse a GPS payload (56 bytes) into a dict of fields."""
    if len(payload) != 56:
        return None
    return GPSPayload.parse(payload)


def parse_gnfi(payload):
    """Parse a GNFI payload (32 bytes) into a dict of fields."""
    if len(payload) != 32:
        return None
    return GNFIPayload.parse(payload)


def parse_lap(payload):
    """Parse a LAP payload (20 bytes) into a dict of fields."""
    if len(payload) != 20:
        return None
    return LAPPayload.parse(payload)


def parse_cde(payload):
    """Parse a CDE payload (6 bytes) into a dict of fields."""
    if len(payload) != 6:
        return None
    return CDEPayload.parse(payload)


def parse_cal(payload):
    """Parse a CAL payload into a dict.

    Returns a plain dict (not Container) for simpler downstream use.
    """
    if len(payload) < 40:
        return None
    cal_type = struct.unpack_from("<I", payload, 20)[0]
    val_1 = struct.unpack_from("<f", payload, 24)[0]
    val_2 = struct.unpack_from("<f", payload, 28)[0]
    result = {"type": cal_type, "raw_1": val_1, "raw_2": val_2}
    if cal_type == 1 and len(payload) >= 40:
        result["output_1"] = struct.unpack_from("<f", payload, 32)[0]
        result["output_2"] = struct.unpack_from("<f", payload, 36)[0]
    return result


def parse_idn(payload):
    """Parse an idn payload into a dict."""
    if len(payload) < 10:
        return None
    model_id = struct.unpack_from("<H", payload, 0)[0]
    logger_id = struct.unpack_from("<I", payload, 6)[0]
    return {"model_id": model_id, "logger_id": logger_id}


def parse_trk(payload):
    """Parse a TRK payload into a dict."""
    if len(payload) < 44:
        return None
    name = _nullterm(payload[:32])
    sf_lat = struct.unpack_from("<i", payload, 36)[0] / 1e7
    sf_long = struct.unpack_from("<i", payload, 40)[0] / 1e7
    return {"name": name, "sf_lat": sf_lat, "sf_long": sf_long}


def parse_odo(payload):
    """Parse an ODO payload into a dict of records."""
    result = {}
    for i in range(0, len(payload), 64):
        name = _nullterm(payload[i : i + 16])
        time_val = struct.unpack_from("<I", payload, i + 16)[0]
        dist_val = struct.unpack_from("<I", payload, i + 20)[0]
        result[name] = {"time": time_val, "dist": dist_val}
    return result


def parse_gpsr(payload):
    """Parse a GPSR payload into a dict."""
    if len(payload) < 36:
        return None
    gps_type = _nullterm(payload[4:8])
    channel_index = struct.unpack_from("<H", payload, 22)[0]
    u32_32 = struct.unpack_from("<I", payload, 32)[0]
    return {"type": gps_type, "channel_index": channel_index, "u32_32": u32_32}


def parse_islv(payload):
    """Parse an iSLV payload (embedded idn)."""
    if len(payload) >= 16 and payload[:3] == b"idn":
        idn = payload[6:]
        if len(idn) >= 10:
            model_id = struct.unpack_from("<H", idn, 0)[0]
            logger_id = struct.unpack_from("<I", idn, 6)[0]
            return {"model_id": model_id, "logger_id": logger_id}
    return None


def parse_racm(payload):
    """Parse a RACM payload (flag byte or string)."""
    if len(payload) > 1:
        return {"mode": "string", "value": _nullterm(payload)}
    elif len(payload) == 1:
        return {"mode": "flag", "value": payload[0]}
    return None


def parse_vet(payload):
    """Parse a VET payload (single byte)."""
    if len(payload) >= 1:
        return payload[0]
    return None


def parse_src(payload):
    """Parse an SRC payload (embedded idn).

    Returns (model_id, logger_id) dict or None.
    """
    if len(payload) >= 62 and payload[:3] == b"idn":
        idn_payload = payload[6:62]
        model_id = struct.unpack_from("<H", idn_payload, 0)[0]
        logger_id = struct.unpack_from("<I", idn_payload, 6)[0]
        return {"model_id": model_id, "logger_id": logger_id}
    return None


# ---------------------------------------------------------------------------
# Data Message Parsing
# ---------------------------------------------------------------------------


def parse_data_message(data, offset, channel_sizes, group_sizes):
    """Parse a single data message at offset.

    Returns (msg_type, parsed_dict, next_offset) or None.

    Data message types:
      (G - group data
      (S - single channel sample
      (M - multi-sample (burst) for a channel
      (c - expansion device channel data
    """
    if offset + 3 > len(data):
        return None

    op = struct.unpack_from("<H", data, offset)[0]
    ord_op = ord("(")
    ord_G = ord_op + 256 * ord("G")
    ord_S = ord_op + 256 * ord("S")
    ord_M = ord_op + 256 * ord("M")
    ord_c = ord_op + 256 * ord("c")

    if op == ord_G:
        # (G message: op(2) + timecode(4) + group_index(2) + packed_data + )
        if offset + 8 > len(data):
            return None
        timecode = struct.unpack_from("<i", data, offset + 2)[0]
        group_index = struct.unpack_from("<H", data, offset + 6)[0]
        if group_index not in group_sizes:
            return None
        payload_size = group_sizes[group_index]
        msg_end = offset + 8 + payload_size + 1  # +1 for ')'
        if msg_end > len(data):
            return None
        if data[msg_end - 1] != ord(")"):
            return None
        payload = data[offset + 8 : msg_end - 1]
        return (
            "G",
            {
                "timecode": timecode,
                "group_index": group_index,
                "data": bytes(payload),
            },
            msg_end,
        )

    elif op == ord_S:
        # (S message: op(2) + timecode(4) + channel_index(2) + data + )
        if offset + 8 > len(data):
            return None
        timecode = struct.unpack_from("<i", data, offset + 2)[0]
        channel_index = struct.unpack_from("<H", data, offset + 6)[0]
        if channel_index not in channel_sizes:
            return None
        payload_size = channel_sizes[channel_index]
        msg_end = offset + 8 + payload_size + 1
        if msg_end > len(data):
            return None
        if data[msg_end - 1] != ord(")"):
            return None
        payload = data[offset + 8 : msg_end - 1]
        return (
            "S",
            {
                "timecode": timecode,
                "channel_index": channel_index,
                "data": bytes(payload),
            },
            msg_end,
        )

    elif op == ord_M:
        # (M message: op(2) + timecode(4) + channel_index(2) + count(2) + data(N*count) + )
        if offset + 10 > len(data):
            return None
        timecode = struct.unpack_from("<i", data, offset + 2)[0]
        channel_index = struct.unpack_from("<H", data, offset + 6)[0]
        count = struct.unpack_from("<H", data, offset + 8)[0]
        if channel_index not in channel_sizes:
            return None
        payload_size = channel_sizes[channel_index] * count
        msg_end = offset + 10 + payload_size + 1
        if msg_end > len(data):
            return None
        if data[msg_end - 1] != ord(")"):
            return None
        payload = data[offset + 10 : msg_end - 1]
        return (
            "M",
            {
                "timecode": timecode,
                "channel_index": channel_index,
                "count": count,
                "data": bytes(payload),
            },
            msg_end,
        )

    elif op == ord_c:
        # (c message: op(2) + 0x00 + channel_field(2) + 0x84 + 0x06 + timecode(4) + data + )
        if offset + 11 > len(data):
            return None
        unk1 = data[offset + 2]
        channel_field = struct.unpack_from("<H", data, offset + 3)[0]
        unk3 = data[offset + 5]
        unk4 = data[offset + 6]
        timecode = struct.unpack_from("<i", data, offset + 7)[0]
        channel_index = channel_field >> 3
        if channel_index not in channel_sizes:
            return None
        payload_size = channel_sizes[channel_index]
        msg_end = offset + 11 + payload_size + 1
        if msg_end > len(data):
            return None
        if data[msg_end - 1] != ord(")"):
            return None
        payload = data[offset + 11 : msg_end - 1]
        return (
            "c",
            {
                "timecode": timecode,
                "channel_index": channel_index,
                "channel_field": channel_field,
                "unk1": unk1,
                "unk3": unk3,
                "unk4": unk4,
                "data": bytes(payload),
            },
            msg_end,
        )

    return None


# ---------------------------------------------------------------------------
# Top-Level File Parser
# ---------------------------------------------------------------------------


class ParsedMessage:
    """A parsed message from an XRK file."""

    __slots__ = ("msg_type", "token", "version", "payload", "parsed", "raw_payload")

    def __init__(
        self, msg_type, token=None, version=None, payload=None, parsed=None, raw_payload=None
    ):
        self.msg_type = msg_type  # "header", "G", "S", "M", "c"
        self.token = token  # token int (header messages only)
        self.version = version  # version byte (header messages only)
        self.payload = payload  # parsed payload (varies by type)
        self.parsed = parsed  # data message parsed dict
        self.raw_payload = raw_payload  # raw payload bytes

    def token_str(self):
        """Return the token as a string."""
        if self.token is not None:
            return _tokenc(self.token)
        return None

    def __repr__(self):
        if self.msg_type == "header":
            return f"ParsedMessage(header, {self.token_str()!r}, ver={self.version})"
        return f"ParsedMessage({self.msg_type})"


def _decompress_if_zlib(data):
    """Decompress zlib-compressed data if detected (XRZ files)."""
    if len(data) < 2:
        return data
    if data[0] == 0x78 and data[1] in (0x01, 0x9C, 0xDA):
        deco = zlib.decompressobj()
        try:
            return deco.decompress(bytes(data))
        except zlib.error:
            return deco.flush()
    return data


def parse_xrk_bytes(data, include_data_messages=True):
    """Parse an XRK byte stream into a list of ParsedMessage objects.

    This is the main entry point for parsing XRK data. It handles:
      - Header messages (CHS, GRP, GPS, LAP, etc.)
      - Data messages (G, S, M, c) if include_data_messages=True
      - Recursive parsing of CNF/ENF embedded header sections

    Args:
        data: Raw XRK file bytes (or XRZ, auto-decompressed)
        include_data_messages: If True, also parse G/S/M/c data messages

    Returns:
        ParseResult with messages, channel_sizes, group_sizes, etc.
    """
    data = _decompress_if_zlib(data)
    if isinstance(data, memoryview):
        data = bytes(data)
    return _parse_sequence(data, include_data_messages=include_data_messages)


def parse_xrk_file(filepath, include_data_messages=True):
    """Parse an XRK/XRZ file.

    Args:
        filepath: Path to the XRK/XRZ file

    Returns:
        ParseResult object
    """
    filepath = Path(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    return parse_xrk_bytes(data, include_data_messages=include_data_messages)


class ParseResult:
    """Result of parsing an XRK file."""

    def __init__(self):
        self.messages = []  # List[ParsedMessage]
        self.channel_sizes = {}  # {channel_index: data_size}
        self.group_sizes = {}  # {group_index: payload_size}
        self.channels = {}  # {channel_index: CHS Container}
        self.groups = {}  # {group_index: GRP Container}
        self.channel_mms = {}  # {channel_index: Mms value}
        self.gps_payloads = []  # List of raw GPS payload bytes (56B each)
        self.gnfi_payloads = []  # List of raw GNFI payload bytes (32B each)
        self.leftover_bytes = 0  # Non-zero means unparsed trailing data

    def header_messages(self):
        """Return only header messages."""
        return [m for m in self.messages if m.msg_type == "header"]

    def data_messages(self):
        """Return only data messages."""
        return [m for m in self.messages if m.msg_type in ("G", "S", "M", "c")]

    def messages_by_token(self, token_str=None, token_int=None):
        """Return header messages matching the given token."""
        if token_str is not None:
            token_int = _tokdec(token_str)
        return [m for m in self.messages if m.msg_type == "header" and m.token == token_int]

    def get_laps(self):
        """Return parsed LAP messages as list of dicts."""
        result = []
        for m in self.messages_by_token("LAP"):
            if m.payload is not None:
                result.append(
                    {
                        "segment": m.payload.segment,
                        "lap_num": m.payload.lap_num,
                        "duration": m.payload.duration,
                        "end_time": m.payload.end_time,
                    }
                )
        return result

    def get_gps_samples(self):
        """Return parsed GPS samples as list of dicts."""
        result = []
        for raw in self.gps_payloads:
            parsed = parse_gps(raw)
            if parsed:
                result.append(parsed)
        return result

    def get_gnfi_samples(self):
        """Return parsed GNFI samples as list of dicts."""
        result = []
        for raw in self.gnfi_payloads:
            parsed = parse_gnfi(raw)
            if parsed:
                result.append(parsed)
        return result


def _parse_sequence(data, include_data_messages=True, _depth=0):
    """Internal recursive parser for XRK byte sequences.

    Handles both top-level files and embedded CNF/ENF sections.
    """
    result = ParseResult()
    pos = 0
    data_len = len(data)

    while pos < data_len:
        # Try header message first
        hdr = parse_header_message(data, pos)
        if hdr is not None:
            tok, ver, payload, next_pos = hdr
            msg = ParsedMessage("header", token=tok, version=ver, raw_payload=payload)

            # Parse payload based on token type
            if tok in (TOK_GPS, TOK_GPS1):
                # GPS messages: store raw payload for batch processing
                result.gps_payloads.append(payload)
            elif tok == TOK_GNFI:
                result.gnfi_payloads.append(payload)
            elif tok == TOK_CHS:
                parsed = parse_chs(payload)
                msg.payload = parsed
                if parsed:
                    idx = parsed.index
                    result.channels[idx] = parsed
                    result.channel_sizes[idx] = parsed.data_size
                    # Mms = sample_period_us // 1000
                    result.channel_mms[idx] = parsed.sample_period_us // 1000
            elif tok == TOK_GRP:
                parsed = parse_grp(payload)
                msg.payload = parsed
                if parsed:
                    idx = parsed.index
                    result.groups[idx] = parsed
                    # Group payload size = sum of member channel data_sizes
                    grp_size = sum(result.channel_sizes.get(ch, 0) for ch in parsed.channel_indices)
                    result.group_sizes[idx] = grp_size
            elif tok == TOK_LAP:
                msg.payload = parse_lap(payload)
            elif tok == TOK_CDE:
                msg.payload = parse_cde(payload)
            elif tok == TOK_CAL:
                msg.payload = parse_cal(payload)
            elif tok == TOK_IDN:
                msg.payload = parse_idn(payload)
            elif tok == TOK_SRC:
                msg.payload = parse_src(payload)
            elif tok == TOK_TRK:
                msg.payload = parse_trk(payload)
            elif tok == TOK_ODO:
                msg.payload = parse_odo(payload)
            elif tok == TOK_GPSR:
                msg.payload = parse_gpsr(payload)
            elif tok == TOK_ISLV:
                msg.payload = parse_islv(payload)
            elif tok == TOK_RACM:
                msg.payload = parse_racm(payload)
            elif tok == TOK_VET:
                msg.payload = parse_vet(payload)
            elif tok in (TOK_CNF, TOK_ENF):
                # Recursively parse embedded header section
                sub = _parse_sequence(payload, include_data_messages=False, _depth=_depth + 1)
                msg.payload = sub
                # Pull channel/group definitions up from CNF
                if tok == TOK_CNF:
                    for idx, ch in sub.channels.items():
                        if idx not in result.channels:
                            result.channels[idx] = ch
                            result.channel_sizes[idx] = ch.data_size
                            result.channel_mms[idx] = ch.sample_period_us // 1000
                    for idx, grp in sub.groups.items():
                        if idx not in result.groups:
                            result.groups[idx] = grp
                            grp_size = sum(
                                result.channel_sizes.get(ch, 0) for ch in grp.channel_indices
                            )
                            result.group_sizes[idx] = grp_size
            elif tok in _STRING_TOKENS:
                msg.payload = _nullterm(payload)
            else:
                # Unknown token - store raw payload
                msg.payload = payload

            result.messages.append(msg)
            pos = next_pos
            continue

        # Try data message
        if include_data_messages:
            dmsg = parse_data_message(data, pos, result.channel_sizes, result.group_sizes)
            if dmsg is not None:
                msg_type, parsed_dict, next_pos = dmsg
                msg = ParsedMessage(msg_type, parsed=parsed_dict)
                result.messages.append(msg)
                pos = next_pos
                continue

        # Unknown byte — skip (matches Cython parser's error recovery)
        pos += 1
        result.leftover_bytes += 1

    return result


# ---------------------------------------------------------------------------
# Utility: Build Header Message Frame
# ---------------------------------------------------------------------------


def build_header_frame(token_str, payload, version=0):
    """Build a complete header message frame from token, payload, and version.

    Returns the complete framed message bytes including header, payload,
    and footer with checksum.
    """
    tok_int = _tokdec(token_str)
    # 3-char tokens get space-padded to 4 chars in the wire format
    if len(token_str) == 3:
        wire_tok = tok_int + (0x20 << 24)
    else:
        wire_tok = tok_int

    hdr = struct.pack("<HiH", 0x683C, wire_tok & 0xFFFFFFFF, 0)
    # Repack with correct token and length
    hdr = struct.pack("<HIiB", 0x683C, wire_tok, len(payload), version)
    hdr += b"\x3e"  # '>'

    checksum = sum(payload) & 0xFFFF
    ftr = struct.pack("<BIH", 0x3C, wire_tok, checksum)
    ftr += b"\x3e"  # '>'

    return hdr + payload + ftr


def build_chs(container):
    """Build a CHS payload from a Container/dict."""
    return CHSPayload.build(container)


def build_grp(container):
    """Build a GRP payload from a Container/dict."""
    return GRPPayload.build(container)


def build_gps(container):
    """Build a GPS payload from a Container/dict."""
    return GPSPayload.build(container)


def build_lap(container):
    """Build a LAP payload from a Container/dict."""
    return LAPPayload.build(container)


def build_cde(container):
    """Build a CDE payload from a Container/dict."""
    return CDEPayload.build(container)


def build_gnfi(container):
    """Build a GNFI payload from a Container/dict."""
    return GNFIPayload.build(container)


def build_gpsr(container):
    """Build a GPSR payload from a Container/dict."""
    return GPSRPayload.build(container)


# ---------------------------------------------------------------------------
# CHS Helpers
# ---------------------------------------------------------------------------


def chs_short_name(chs):
    """Extract the short_name string from a CHS Container."""
    return _nullterm(chs.short_name)


def chs_long_name(chs):
    """Extract the long_name string from a CHS Container."""
    return _nullterm(chs.long_name)


def chs_units(chs):
    """Derive the unit string from a CHS Container."""
    unit_key = chs.unit_type_byte & 127
    if unit_key in UNIT_MAP:
        return UNIT_MAP[unit_key][0]
    return ""


def chs_dec_pts(chs):
    """Derive the decimal points from a CHS Container."""
    unit_key = chs.unit_type_byte & 127
    if unit_key in UNIT_MAP:
        return UNIT_MAP[unit_key][1]
    return 0


def chs_interpolate(chs):
    """Derive the interpolate flag from a CHS Container."""
    dt = chs.decoder_type
    if dt in DECODER_TABLE:
        return DECODER_TABLE[dt][1]
    return False


def chs_device_tag(chs):
    """Extract the device tag string from a CHS Container."""
    tag = chs.device_tag
    if any(tag):
        return tag.rstrip(b"\x00").decode("ascii", errors="replace")
    return ""


def chs_padding_bytes(chs):
    """Return the concatenation of all padding fields (expected to be all-zero)."""
    return (
        chs._pad_2_4
        + chs._pad_17_20
        + chs._pad_21_24
        + chs._pad_56_64
        + chs._pad_70_72
        + chs._pad_73_76
        + chs._pad_82_84
        + chs._pad_85_88
        + chs._pad_93_96
    )
