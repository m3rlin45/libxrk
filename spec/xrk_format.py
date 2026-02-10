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

import io
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from construct import (
    Array,
    Bytes,
    Check,
    Computed,
    Const,
    ConstructError,
    Container,
    ExprAdapter,
    Float32l,
    GreedyBytes,
    GreedyRange,
    Int8ul,
    Int16sl,
    Int16ul,
    Int32sl,
    Int32ul,
    PaddedString,
    Peek,
    Rebuild,
    Select,
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


# --- Dispatch action types ---


@dataclass(frozen=True, slots=True)
class _RawAction:
    """Append raw payload bytes to a result attribute."""

    attr: str

    def execute(self, raw_payload, result, _depth):
        getattr(result, self.attr).append(raw_payload)
        return None


@dataclass(frozen=True, slots=True)
class _ParseAction:
    """Parse with a Construct struct, optional side-effect and attribute extraction."""

    struct: object
    post_fn: object = None
    result_attr: str | None = None

    def execute(self, raw_payload, result, _depth):
        parsed = _parse_payload(self.struct, raw_payload)
        if parsed is None:
            return None
        if self.post_fn is not None:
            self.post_fn(parsed, result)
        if self.result_attr is not None:
            return getattr(parsed, self.result_attr)
        return parsed


@dataclass(frozen=True, slots=True)
class _RecurseAction:
    """Recursive _parse_sequence for CNF/ENF containers."""

    merge: bool

    def execute(self, raw_payload, result, _depth):
        sub = _parse_sequence(raw_payload, _depth=_depth + 1)
        if self.merge:
            _merge_cnf_result(sub, result)
        return sub


@dataclass(frozen=True, slots=True)
class _StringAction:
    """Null-terminated string decode."""

    def execute(self, raw_payload, result, _depth):
        return _nullterm(raw_payload)


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
    21: ("mV", 1),
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
    # Derived fields (computed at parse time, not stored in binary)
    "name" / Computed(lambda ctx: _nullterm(ctx.long_name)),
    "units"
    / Computed(
        lambda ctx: (
            "V"
            if (
                ctx.unit_type_byte & 0x80
                and UNIT_MAP.get(ctx.unit_type_byte & 127, ("", 0))[0] == "mV"
            )
            else UNIT_MAP.get(ctx.unit_type_byte & 127, ("", 0))[0]
        )
    ),
    "dec_pts" / Computed(lambda ctx: UNIT_MAP.get(ctx.unit_type_byte & 127, ("", 0))[1]),
    "interpolate" / Computed(lambda ctx: DECODER_TABLE.get(ctx.decoder_type, ("", False))[1]),
    "short_name_str" / Computed(lambda ctx: _nullterm(ctx.short_name)),
    "device_tag_str"
    / Computed(
        lambda ctx: (
            ctx.device_tag.rstrip(b"\x00").decode("ascii", errors="replace")
            if any(ctx.device_tag)
            else ""
        )
    ),
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


# CAL — Calibration (40-byte fixed prefix + variable rest)
# Reference: aim_xrk.pyx:585-603
CALPayload = Struct(
    "_prefix" / Bytes(8),  # [0:8]   unknown prefix
    "_u32_8" / Int32ul,  # [8:12]  expected to be 1
    "_pad_12_20" / Bytes(8),  # [12:20] unknown
    "type" / Int32ul,  # [20:24] calibration type (1=2-point, 20=IMU bias)
    "raw_1" / Float32l,  # [24:28] cal value 1
    "raw_2" / Float32l,  # [28:32] cal value 2
    "output_1" / Float32l,  # [32:36] output 1 (type 1 only; type 20 = 0 or junk)
    "output_2" / Float32l,  # [36:40] output 2 (type 1 only; type 20 = 0 or junk)
    "_rest" / GreedyBytes,  # remaining bytes (variable)
)


# idn — Logger Identity (min 10 bytes)
# Reference: aim_xrk.pyx:527-534
IDNPayload = Struct(
    "model_id" / Int16ul,  # [0:2]   model ID
    "_pad_2_6" / Bytes(4),  # [2:6]   unknown
    "logger_id" / Int32ul,  # [6:10]  logger serial number
    "_rest" / GreedyBytes,  # remaining bytes (variable)
)


# TRK — Track Info (min 44 bytes)
# Reference: aim_xrk.pyx:606-609
TRKPayload = Struct(
    "name" / PaddedString(32, "ascii"),  # [0:32]  track name, null-terminated ASCII
    "_pad" / Bytes(4),  # [32:36] unknown
    "sf_lat"
    / ExprAdapter(
        Int32sl, lambda obj, ctx: obj / 1e7, lambda obj, ctx: int(round(obj * 1e7))
    ),  # [36:40] start/finish latitude (degrees)
    "sf_long"
    / ExprAdapter(
        Int32sl, lambda obj, ctx: obj / 1e7, lambda obj, ctx: int(round(obj * 1e7))
    ),  # [40:44] start/finish longitude (degrees)
    "_rest" / GreedyBytes,  # remaining bytes (variable)
)


# GPSR — GPS Receiver (36 bytes)
# Reference: aim_xrk.pyx:548-558
GPSRPayload = Struct(
    "_prefix" / Bytes(4),  # [0:4]   unknown
    "type" / PaddedString(4, "ascii"),  # [4:8]   GPS type string
    "_mid" / Bytes(14),  # [8:22]  unknown
    "channel_index" / Int16ul,  # [22:24] GPS channel index
    "_post" / Bytes(8),  # [24:32] unknown
    "u32_32" / Int32ul,  # [32:36] expected 410
)


# ODO — Odometer Record (64 bytes per record)
# Reference: aim_xrk.pyx:610-619
ODORecord = Struct(
    "name" / PaddedString(16, "ascii"),  # [0:16]  record name, null-terminated ASCII
    "time" / Int32ul,  # [16:20] time value
    "dist" / Int32ul,  # [20:24] distance value
    "_pad" / Bytes(40),  # [24:64] remaining bytes
)

# ODO — Odometer array (N * 64-byte records)
ODOPayload = Struct(
    "_records" / GreedyRange(ODORecord),
    "records"
    / Computed(lambda ctx: {r.name: {"time": r.time, "dist": r.dist} for r in ctx._records}),
)


# iSLV — Expansion Device Identity (embedded idn at offset 6)
# Reference: aim_xrk.pyx:535-545
ISLVPayload = Struct(
    "_idn_tag" / Const(b"idn"),  # [0:3]  "idn" ASCII marker
    "_gap" / Bytes(3),  # [3:6]  gap
    "model_id" / Int16ul,  # [6:8]  model ID
    "_pad" / Bytes(4),  # [8:12] unknown
    "logger_id" / Int32ul,  # [12:16] logger serial number
    "_rest" / GreedyBytes,  # remaining bytes
)

# SRC — Source Device Identity (embedded idn at offset 6, 56 bytes of idn data)
# Reference: aim_xrk.pyx:560-567
SRCPayload = Struct(
    "_idn_tag" / Const(b"idn"),  # [0:3]  "idn" ASCII marker
    "_gap" / Bytes(3),  # [3:6]  gap
    "model_id" / Int16ul,  # [6:8]  model ID
    "_pad" / Bytes(4),  # [8:12] unknown
    "logger_id" / Int32ul,  # [12:16] logger serial number
    "_rest" / GreedyBytes,  # remaining bytes
)


# RACM — Race Mode (string or flag byte)
# Reference: aim_xrk.pyx race mode handling
RACMPayload = Struct(
    "_raw" / GreedyBytes,
    "mode"
    / Computed(
        lambda ctx: "string" if len(ctx._raw) > 1 else ("flag" if len(ctx._raw) == 1 else None)
    ),
    "value"
    / Computed(
        lambda ctx: (
            _nullterm(ctx._raw)
            if len(ctx._raw) > 1
            else (ctx._raw[0] if len(ctx._raw) == 1 else None)
        )
    ),
)

# VET — Vehicle Electronics Type (single byte)
VETPayload = Struct(
    "value" / Int8ul,
)


# ---------------------------------------------------------------------------
# Header Message (self-validating Struct with inline checks)
# ---------------------------------------------------------------------------

# Reference: aim_xrk.pyx:215-226
#   '<h'          opcode (2 bytes: 0x3C 0x68)
#   token         uint32 LE (3-char tokens have trailing space 0x20)
#   payload_len   int32 LE
#   version       uint8
#   '>'           close bracket (0x3E)
#   [payload]     payload_len bytes
#   '<'           footer open (0x3C)
#   token         uint32 LE (must match header)
#   checksum      uint16 LE (sum of payload bytes)
#   '>'           footer close (0x3E)
HeaderMessage = Struct(
    Const(b"\x3c\x68"),  # opcode '<h'
    "wire_token"
    / Rebuild(
        Int32ul,
        lambda ctx: (ctx.token + (0x20 << 24) if len(_tokenc(ctx.token)) == 3 else ctx.token),
    ),
    "payload_length" / Rebuild(Int32sl, lambda ctx: len(ctx.raw_payload)),
    "version" / Int8ul,  # message version
    Const(b"\x3e"),  # '>'
    "raw_payload" / Bytes(this.payload_length),  # payload data
    Const(b"\x3c"),  # footer '<'
    "_footer_token" / Rebuild(Int32ul, lambda ctx: ctx.wire_token),
    "checksum" / Rebuild(Int16ul, lambda ctx: sum(ctx.raw_payload) & 0xFFFF),
    Const(b"\x3e"),  # footer '>'
    # Inline validation (raises ConstructError on mismatch)
    Check(lambda ctx: ctx._footer_token == ctx.wire_token),
    Check(lambda ctx: ctx.checksum == (sum(ctx.raw_payload) & 0xFFFF)),
    # Computed: strip trailing space from 3-char tokens
    "token"
    / Computed(
        lambda ctx: (
            ctx.wire_token - (0x20 << 24) if (ctx.wire_token >> 24) == 0x20 else ctx.wire_token
        )
    ),
)

# ---------------------------------------------------------------------------
# Data Message Structs (complete, self-contained with opcode + terminator)
# ---------------------------------------------------------------------------

# (S — Single channel sample
# Reference: aim_xrk.pyx data handling
# Note: Check removed and Bytes uses `this` expressions to enable Construct .compile().
# Invalid channel_index raises KeyError (caught alongside ConstructError in _parse_sequence).
SMessage = Struct(
    Const(b"\x28\x53"),  # '(S'
    "msg_type" / Computed("S"),
    "timecode" / Int32sl,
    "channel_index" / Int16ul,
    "data" / Bytes(this._params.channel_sizes[this.channel_index]),
    Const(b"\x29"),  # ')'
)
SMessageCompiled = SMessage.compile()

# (G — Group data
GMessage = Struct(
    Const(b"\x28\x47"),  # '(G'
    "msg_type" / Computed("G"),
    "timecode" / Int32sl,
    "group_index" / Int16ul,
    "data" / Bytes(this._params.group_sizes[this.group_index]),
    Const(b"\x29"),  # ')'
)
GMessageCompiled = GMessage.compile()

# (M — Multi-sample burst
MMessage = Struct(
    Const(b"\x28\x4d"),  # '(M'
    "msg_type" / Computed("M"),
    "timecode" / Int32sl,
    "channel_index" / Int16ul,
    "count" / Int16ul,
    "data" / Bytes(this._params.channel_sizes[this.channel_index] * this.count),
    Const(b"\x29"),  # ')'
)
MMessageCompiled = MMessage.compile()

# (c — Expansion device channel data
cMessage = Struct(
    Const(b"\x28\x63"),  # '(c'
    "msg_type" / Computed("c"),
    "unk1" / Int8ul,
    "channel_field" / Int16ul,
    "unk3" / Int8ul,
    "unk4" / Int8ul,
    "timecode" / Int32sl,
    "channel_index" / Computed(this.channel_field >> 3),
    "data" / Bytes(this._params.channel_sizes[this.channel_field >> 3]),
    Const(b"\x29"),  # ')'
)
cMessageCompiled = cMessage.compile()

# Data message opcode -> compiled struct (used by round-trip tests)
_DATA_MSG_STRUCTS = {
    b"\x28\x53": SMessageCompiled,  # '(S'
    b"\x28\x47": GMessageCompiled,  # '(G'
    b"\x28\x4d": MMessageCompiled,  # '(M'
    b"\x28\x63": cMessageCompiled,  # '(c'
}


# ---------------------------------------------------------------------------
# Hooks: post-parse actions for GreedyRange-based stream parser
# ---------------------------------------------------------------------------


def _on_header(obj, ctx):
    """Post-parse hook: dispatch header payload, append to result."""
    result = ctx._params.result
    raw = bytes(obj.raw_payload)
    payload = _dispatch_payload(obj.token, raw, result, ctx._params.depth)
    result.messages.append(
        ParsedMessage(
            "header", token=obj.token, version=obj.version, payload=payload, raw_payload=raw
        )
    )


def _on_data(obj, ctx):
    """Post-parse hook: collect data message into result."""
    result = ctx._params.result
    result.messages.append(ParsedMessage(obj.msg_type, parsed=_container_to_dict(obj)))


# ---------------------------------------------------------------------------
# Declarative protocol: opcode-dispatched Switch with side-effect hooks
# ---------------------------------------------------------------------------

XRKMessage = Struct(
    "_opcode" / Peek(Bytes(2)),
    "body"
    / Switch(
        this._opcode,
        {
            b"\x3c\x68": Select(HeaderMessage * _on_header, Bytes(1)),
            b"\x28\x53": Select(SMessageCompiled * _on_data, Bytes(1)),
            b"\x28\x47": Select(GMessageCompiled * _on_data, Bytes(1)),
            b"\x28\x4d": Select(MMessageCompiled * _on_data, Bytes(1)),
            b"\x28\x63": Select(cMessageCompiled * _on_data, Bytes(1)),
        },
        default=Bytes(1),
    ),
)

# ---------------------------------------------------------------------------
# Payload Dispatch Table
# ---------------------------------------------------------------------------

# --- Side-effect helpers for dispatch table ---


def _register_chs(parsed, result):
    idx = parsed.index
    result.channels[idx] = parsed
    result.channel_sizes[idx] = parsed.data_size
    result.channel_mms[idx] = parsed.sample_period_us // 1000


def _register_grp(parsed, result):
    idx = parsed.index
    result.groups[idx] = parsed
    result.group_sizes[idx] = sum(result.channel_sizes.get(ch, 0) for ch in parsed.channel_indices)


def _merge_cnf_result(sub, result):
    for idx, ch in sub.channels.items():
        if idx not in result.channels:
            _register_chs(ch, result)
    for idx, grp in sub.groups.items():
        if idx not in result.groups:
            _register_grp(grp, result)


# Dispatch table: token -> action descriptor (NamedTuple)
# Single source of truth for all token handling.
_DISPATCH_TABLE = {
    # Raw payload storage
    TOK_GPS: _RawAction("gps_payloads"),
    TOK_GPS1: _RawAction("gps_payloads"),
    TOK_GNFI: _RawAction("gnfi_payloads"),
    # Struct parsing (with optional side-effects and attribute extraction)
    TOK_CHS: _ParseAction(CHSPayload, _register_chs),
    TOK_GRP: _ParseAction(GRPPayload, _register_grp),
    TOK_ODO: _ParseAction(ODOPayload, result_attr="records"),
    TOK_LAP: _ParseAction(LAPPayload),
    TOK_CDE: _ParseAction(CDEPayload),
    TOK_CAL: _ParseAction(CALPayload),
    TOK_IDN: _ParseAction(IDNPayload),
    TOK_TRK: _ParseAction(TRKPayload),
    TOK_GPSR: _ParseAction(GPSRPayload),
    TOK_ISLV: _ParseAction(ISLVPayload),
    TOK_SRC: _ParseAction(SRCPayload),
    TOK_VET: _ParseAction(VETPayload),
    TOK_RACM: _ParseAction(RACMPayload),
    # Recursive containers
    TOK_CNF: _RecurseAction(merge=True),
    TOK_ENF: _RecurseAction(merge=False),
    # String messages (null-terminated ASCII)
    **{
        _tokdec(t): _StringAction()
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
    },
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_payload(struct_def, payload):
    """Parse a payload using a Construct Struct, returning None on failure."""
    try:
        return struct_def.parse(payload)
    except ConstructError:
        return None


_DATA_MSG_KEYS = (
    "msg_type",
    "timecode",
    "channel_index",
    "group_index",
    "count",
    "data",
    "channel_field",
    "unk1",
    "unk3",
    "unk4",
)


def _container_to_dict(container):
    """Convert a Construct Container to a plain dict for data messages."""
    # Use direct dict access (container is dict-like) to avoid slow
    # hasattr/getattr on Container objects (Container.__getattr__ is expensive).
    d = {}
    for key in _DATA_MSG_KEYS:
        val = container.get(key)
        if val is not None:
            d[key] = bytes(val) if isinstance(val, memoryview) else val
    return d


def _dispatch_payload(token, raw_payload, result, _depth):
    """Dispatch a header message payload to the appropriate parser.

    Uses _DISPATCH_TABLE for declarative routing. Each action type implements
    execute() for polymorphic dispatch.
    """
    entry = _DISPATCH_TABLE.get(token)
    if entry is None:
        return raw_payload
    return entry.execute(raw_payload, result, _depth)


# ---------------------------------------------------------------------------
# Top-Level File Parser
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ParsedMessage:
    """A parsed message from an XRK file."""

    msg_type: str  # "header", "G", "S", "M", "c"
    token: int | None = None  # token int (header messages only)
    version: int | None = None  # version byte (header messages only)
    payload: object = None  # parsed payload (varies by type)
    parsed: dict | None = None  # data message parsed dict
    raw_payload: bytes | None = None  # raw payload bytes

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


def parse_xrk_bytes(data):
    """Parse an XRK byte stream into a list of ParsedMessage objects.

    This is the main entry point for parsing XRK data. It handles:
      - Header messages (CHS, GRP, GPS, LAP, etc.)
      - Data messages (G, S, M, c)
      - Recursive parsing of CNF/ENF embedded header sections

    Args:
        data: Raw XRK file bytes (or XRZ, auto-decompressed)

    Returns:
        ParseResult with messages, channel_sizes, group_sizes, etc.
    """
    data = _decompress_if_zlib(data)
    if isinstance(data, memoryview):
        data = bytes(data)
    return _parse_sequence(data)


def parse_xrk_file(filepath):
    """Parse an XRK/XRZ file.

    Args:
        filepath: Path to the XRK/XRZ file

    Returns:
        ParseResult object
    """
    filepath = Path(filepath)
    with open(filepath, "rb") as f:
        data = f.read()
    return parse_xrk_bytes(data)


@dataclass
class ParseResult:
    """Result of parsing an XRK file."""

    messages: list = field(default_factory=list)  # List[ParsedMessage]
    channel_sizes: dict = field(default_factory=dict)  # {channel_index: data_size}
    group_sizes: dict = field(default_factory=dict)  # {group_index: payload_size}
    channels: dict = field(default_factory=dict)  # {channel_index: CHS Container}
    groups: dict = field(default_factory=dict)  # {group_index: GRP Container}
    channel_mms: dict = field(default_factory=dict)  # {channel_index: Mms value}
    gps_payloads: list = field(default_factory=list)  # List of raw GPS payload bytes (56B each)
    gnfi_payloads: list = field(default_factory=list)  # List of raw GNFI payload bytes (32B each)
    leftover_bytes: int = 0  # Non-zero means unparsed trailing data

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
        return [
            {
                "segment": m.payload.segment,
                "lap_num": m.payload.lap_num,
                "duration": m.payload.duration,
                "end_time": m.payload.end_time,
            }
            for m in self.messages_by_token("LAP")
            if m.payload is not None
        ]

    def get_gps_samples(self):
        """Return parsed GPS samples as list of Containers."""
        return [p for raw in self.gps_payloads if (p := _parse_payload(GPSPayload, raw))]

    def get_gnfi_samples(self):
        """Return parsed GNFI samples as list of Containers."""
        return [p for raw in self.gnfi_payloads if (p := _parse_payload(GNFIPayload, raw))]


def _parse_sequence(data, _depth=0):
    """Internal recursive parser for XRK byte sequences.

    Iterates XRKMessage (declarative Switch + Peek) over the stream. Each
    successful parse triggers side-effect hooks (_on_header, _on_data) that
    populate the result. Results are not accumulated to avoid O(n) memory.
    """
    result = ParseResult()
    stream = io.BytesIO(data)
    data_len = len(data)
    while stream.tell() < data_len:
        try:
            XRKMessage.parse_stream(
                stream,
                result=result,
                channel_sizes=result.channel_sizes,
                group_sizes=result.group_sizes,
                depth=_depth,
            )
        except ConstructError:
            break
    result.leftover_bytes = len(data) - stream.tell()
    return result


# ---------------------------------------------------------------------------
# Utility: Build Header Message Frame
# ---------------------------------------------------------------------------


def build_header_frame(token_str, payload, version=0):
    """Build a complete header message frame using HeaderMessage Struct.

    Returns the complete framed message bytes including header, payload,
    and footer with checksum. Token padding, payload length, footer token,
    and checksum are computed declaratively by Rebuild fields.
    """
    return HeaderMessage.build(
        Container(
            token=_tokdec(token_str),
            version=version,
            raw_payload=payload,
        )
    )


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
    return chs.short_name_str


def chs_long_name(chs):
    """Extract the long_name string from a CHS Container. Alias for chs.name."""
    return chs.name


def chs_units(chs):
    """Derive the unit string from a CHS Container. Alias for chs.units."""
    return chs.units


def chs_dec_pts(chs):
    """Derive the decimal points from a CHS Container. Alias for chs.dec_pts."""
    return chs.dec_pts


def chs_interpolate(chs):
    """Derive the interpolate flag from a CHS Container. Alias for chs.interpolate."""
    return chs.interpolate


def chs_device_tag(chs):
    """Extract the device tag string from a CHS Container."""
    return chs.device_tag_str


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
