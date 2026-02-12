
# Copyright 2024, Scott Smith.  MIT License (see LICENSE).

from array import array
import concurrent.futures
import ctypes
from dataclasses import dataclass, field
import math
import mmap
import numpy as np
import os
from pprint import pprint # pylint: disable=unused-import
import struct
import sys
import time
import traceback # pylint: disable=unused-import
from typing import Dict, List, Optional
import zlib

import cython
from cython.operator cimport dereference
from libcpp.vector cimport vector

import pyarrow as pa

from . import gps
from .gps import fix_gps_timing_gaps
from . import base

# 1,2,5,10,20,25,50 Hz
# units
# dec ptr

dc_slots = {'slots': True} if sys.version_info.minor >= 10 else {}

@dataclass(**dc_slots)
class Group:
    index: int
    channels: List[int]
    samples: array = field(default_factory=lambda: array('I'), repr=False)
    # used during building:
    timecodes: Optional[array] = field(default=None, repr=False)

@dataclass(**dc_slots)
class GroupRef:
    group: Group
    offset: int

@dataclass(**dc_slots)
class Channel:
    index: int = -1
    short_name: str = ""
    long_name: str = ""
    size: int = 0
    units: str = ""
    dec_pts: int = 0
    interpolate: bool = False
    unknown: bytes = b""
    source_type: int = 0
    source_channel_id: int = 0
    device_tag: str = ""
    cal_value_1: float = 0.0
    cal_value_2: float = 1.0
    display_range_min: float = 0.0
    display_range_max: float = 0.0
    group: Optional[GroupRef] = None
    timecodes: object = field(default=None, repr=False)
    sampledata: object = field(default=None, repr=False)

@dataclass(**dc_slots)
class Message:
    token: bytes
    num: int
    content: bytes

@dataclass(**dc_slots)
class DataStream:
    channels: Dict[str, Channel]
    messages: Dict[str, List[Message]]
    laps: pa.Table
    time_offset: int
    gnfi_timecodes: Optional[object] = None

@dataclass(**dc_slots)
class Decoder:
    stype: str
    interpolate: bool = False
    fixup: object = None

def _nullterm_string(s):
    zero = s.find(0)
    if zero >= 0: s = s[:zero]
    return s.decode('ascii')

_manual_decoders = {
    'Calculated_Gear': Decoder('Q', fixup=lambda a: array('I', [0 if int(x) & 0x80000 else
                                                                (int(x) >> 16) & 7 for x in a])),
    'PreCalcGear':     Decoder('Q', fixup=lambda a: array('I', [0 if int(x) & 0x80000 else
                                                                (int(x) >> 16) & 7 for x in a])),
}

_gear_table = np.arange(65536, dtype=np.uint16)
_gear_table[ord('N')] = 0
_gear_table[ord('1')] = 1
_gear_table[ord('2')] = 2
_gear_table[ord('3')] = 3
_gear_table[ord('4')] = 4
_gear_table[ord('5')] = 5
_gear_table[ord('6')] = 6

_decoders = {
    0:  Decoder('i'), # Master Clock on M4GT4?
    1:  Decoder('H', interpolate=True),  # uint16 integer (e.g. StartRec)
    3:  Decoder('i'), # Master Clock on ScottE46?
    4:  Decoder('h'),
    6:  Decoder('f', interpolate=True),
    8:  Decoder('i'), # iGPS reference
    11: Decoder('h'),
    12: Decoder('i'), # Predictive Time?
    13: Decoder('B'), # status field?
    15: Decoder('H', fixup=lambda a: _gear_table[a]), # ?? NdscSwitch on M4GT4.  Also actual size is 8 bytes
    20: Decoder('H', interpolate=True,
                fixup=lambda a: np.ndarray(buffer=a, shape=(len(a),),
                                           dtype=np.float16).astype(np.float32).data),
    22: Decoder('i'), # Lap Time (variant)
    24: Decoder('i'), # Best Run Diff?
    26: Decoder('i'), # Total Odometer
    27: Decoder('i'), # Reset Odometer
    31: Decoder('i'), # Lap Time
    32: Decoder('i'), # Roll Time
    33: Decoder('i'), # Best Time
    37: Decoder('i'), # GPS_Hours
    38: Decoder('i'), # GPS_Date
    39: Decoder('i'), # GPS_Time
}

# Logger model ID to name mapping
# These values are from the idn message in XRK files
_logger_models = {
    649: "MXP 1.3",
    793: "MXm",
}

_unit_map = {
    1:  ('%', 2),
    3:  ('g', 2),
    4:  ('deg', 1),
    5:  ('deg/s', 1),
    6:  ('', 0), # number
    9:  ('Hz', 0),
    11: ('', 0), # number
    12: ('mm', 0),
    14: ('bar', 2),
    15: ('rpm', 0),
    16: ('km/h', 0),
    17: ('C', 1),
    18: ('ms', 0),
    19: ('Nm', 0),
    20: ('km/h', 0),
    21: ('mV', 1),
    22: ('l', 1),
    24: ('l/s', 0), # ? rs3 displayed 1l/h
    26: ('time?', 0),
    27: ('A', 0),
    30: ('lambda', 2),
    31: ('gear', 0),
    33: ('%', 2),
    43: ('kg', 3),
}

def _ndarray_from_mv(mv):
    mv = memoryview(mv) # force it
    return np.ndarray(buffer=mv, shape=(len(mv),), dtype=np.dtype(mv.format))

def _sliding_ndarray(buf, typ):
    return np.ndarray(buffer=buf, dtype=typ,
                      shape=(len(buf) - array(typ).itemsize + 1,), strides=(1,))

def _tokdec(s):
    if s: return ord(s[0]) + 256 * _tokdec(s[1:])
    return 0

def _tokenc(i):
    s = ''
    while i:
        s += chr(i & 255)
        i >>= 8
    return s

accum = cython.struct(
    last_timecode=cython.int,
    add_helper=cython.ushort,
    Mms=cython.uint,
    data=vector[cython.uchar],
    timecodes=vector[cython.int])

cdef packed struct smsg_hdr:  # covers G, S, and M messages
    cython.ushort op
    cython.int timecode
    cython.ushort index
    cython.ushort count # for M messages only
    # data field(s) follow(s), size depends on type/group

cdef packed struct cmsg_hdr:  # covers c messages
    cython.ushort op
    cython.uchar unk1 # always 0?
    cython.ushort channel # bottom 3 bits always 4?
    cython.uchar unk3 # always 0x84?
    cython.uchar unk4 # always 6?
    cython.int timecode
    # data field follows, size depends on type

cdef packed struct hmsg_hdr:
    cython.ushort op
    cython.uint tok
    cython.int hlen
    cython.uchar ver
    cython.uchar cl

cdef packed struct hmsg_ftr:
    cython.uchar op
    cython.uint tok
    cython.ushort bytesum
    cython.uchar cl

cdef union msg_hdr:
    smsg_hdr s
    cmsg_hdr c
    hmsg_hdr h

ctypedef const cython.uchar* byte_ptr
ctypedef vector[accum] vaccum

cdef extern from '<numeric>' namespace 'std' nogil:
    T accumulate[InputIt, T](InputIt first, InputIt last, T init)

cdef _resize_vaccum(vaccum & v, size_t idx):
    if idx >= v.size():
        old_len = v.size()
        v.resize(idx + 1)
        for i in range(old_len, v.size()):
            v[i].last_timecode = -1
            v[i].add_helper = 1
            v[i].Mms = 0


@cython.wraparound(False)
def _decode_sequence(s, progress=None):
    cdef const cython.uchar[::1] sv = s
    groups = []
    channels = []
    messages = {}
    tok_GPS: cython.uint = _tokdec('GPS')
    tok_GPS1: cython.uint = _tokdec('GPS1')
    tok_GNFI: cython.uint = _tokdec('GNFI')
    progress_interval: cython.Py_ssize_t = 8_000_000
    next_progress: cython.Py_ssize_t = progress_interval
    pos: cython.Py_ssize_t = 0
    oldpos: cython.Py_ssize_t = pos
    badbytes: cython.Py_ssize_t = 0
    badpos: cython.Py_ssize_t = 0
    ord_op: cython.int = ord('(')
    ord_cp: cython.int = ord(')')
    ord_op_G : cython.int = ord_op + 256 * ord('G')
    ord_op_S : cython.int = ord_op + 256 * ord('S')
    ord_op_M : cython.int = ord_op + 256 * ord('M')
    ord_op_c : cython.int = ord_op + 256 * ord('c')
    ord_lt: cython.int = ord('<')
    ord_lt_h : cython.int = ord_lt + 256 * ord('h')
    ord_gt: cython.int = ord('>')
    len_s: cython.Py_ssize_t = len(s)
    cdef vaccum[4] gc_data # [0]: G messages (groups) [1]: S messages (samples?) [2]: c messages (channels from expansion) [3]: M messages
    time_offset = None
    last_time = None
    t1 = time.perf_counter()
    cdef vaccum * data_cat
    cdef accum * data_p
    cdef cython.int * tc_ptr
    gpsmsg: vector[cython.uchar]
    gnfimsg: vector[cython.uchar]
    show_all: cython.int = 0
    show_bad: cython.int = 0
    while pos < len_s:
        try:
            while True:
                oldpos = pos
                if pos + 10 >= len_s: # smallest message is 3 (frame) + 4 (tc) + 2 (idx) + 1 (data)
                    raise IndexError
                msg = <msg_hdr *>&sv[pos]
                typ: cython.int = msg.s.op
                if abs(typ - (ord_op_G + ord_op_S) // 2) == (ord_op_S - ord_op_G) // 2:
                    data_cat = &gc_data[typ == ord_op_S]
                    data_p = &dereference(data_cat)[msg.s.index]
                    if data_p >= &dereference(data_cat.end()):
                        raise IndexError
                    pos += data_p.add_helper
                    last = &sv[pos-1]
                    if last[0] != ord_cp:
                        raise ValueError("%s at %x" % (chr(s[pos-1]), pos-1))
                    if show_all:
                        print('tc=%d %s idx=%d' % (msg.s.timecode, chr(msg.s.op >> 8), msg.s.index))
                    if msg.s.timecode > data_p.last_timecode:
                        data_p.last_timecode = msg.s.timecode
                        data_p.data.insert(data_p.data.end(),
                                           <const cython.uchar *>&msg.s.timecode, last)
                elif typ == ord_op_M:
                    data_p = &gc_data[3][msg.s.index]
                    if data_p >= &dereference(gc_data[3].end()):
                        raise IndexError
                    if data_p.Mms == 0:
                        raise ValueError('No ms understood for channel %s' %
                                         channels[msg.s.index].long_name)
                    pos += data_p.add_helper * msg.s.count + 10
                    if sv[pos] != ord_cp:
                        raise ValueError("%s at %x" % (chr(s[pos]), pos))
                    if show_all:
                        print('tc=%d M idx=%d cnt=%d ms=%d' %
                              (msg.s.timecode, msg.s.index, msg.s.count, data_p.Mms))
                    # Compute how many samples to skip (overlap with already-accepted data)
                    m_skip: cython.int = 0
                    if msg.s.timecode <= data_p.last_timecode and data_p.Mms > 0:
                        m_skip = (data_p.last_timecode - msg.s.timecode) // data_p.Mms + 1
                    if m_skip < msg.s.count:
                        data_p.last_timecode = msg.s.timecode + (msg.s.count-1) * data_p.Mms
                        m_tc : cython.int
                        for m_tc in range(m_skip, msg.s.count):
                            data_p.timecodes.push_back(msg.s.timecode + m_tc * data_p.Mms)
                        data_p.data.insert(data_p.data.end(),
                                           &sv[oldpos+10 + m_skip * data_p.add_helper],
                                           &sv[pos])
                    pos += 1
                elif typ == ord_op_c:
                    assert msg.c.unk1 == 0, '%x' % msg.c.unk1
                    assert (msg.c.channel & 7) == 4, '%x' % (msg.c.channel & 7)
                    assert msg.c.unk3 == 0x84, '%x' % msg.c.unk3
                    assert msg.c.unk4 == 6, '%x' % msg.c.unk4
                    data_cat = &gc_data[2]
                    data_p = &dereference(data_cat)[msg.c.channel >> 3]
                    if data_p >= &dereference(data_cat.end()):
                        raise IndexError
                    pos += data_p.add_helper
                    last = &sv[pos-1]
                    if last[0] != ord_cp:
                        raise ValueError("%s at %x" % (chr(s[pos-1]), pos-1))
                    if show_all:
                        print('tc=%d c idx=%d' % (msg.c.timecode, msg.c.channel >> 3))
                    if msg.c.timecode > data_p.last_timecode:
                        data_p.last_timecode = msg.c.timecode
                        data_p.data.insert(data_p.data.end(),
                                           <const cython.uchar *>&msg.c.timecode, last)
                elif typ == ord_lt_h:
                    if pos > next_progress:
                        next_progress += progress_interval
                        if progress:
                            progress(pos, len(s))
                    tok: cython.uint = msg.h.tok
                    hlen: cython.Py_ssize_t = msg.h.hlen
                    if hlen >= len_s:
                        raise IndexError
                    ver = msg.h.ver
                    assert msg.h.cl == ord_gt, "%s at %x" % (chr(msg.h.cl), pos+11)
                    pos += 12

                    # get some "free" range checking here before we go walking data[]
                    assert sv[pos+hlen] == ord_lt, "%s at %x" % (s[pos+hlen], pos+hlen)

                    bytesum: cython.ushort = accumulate[byte_ptr, cython.int](
                        &sv[pos], &sv[pos+hlen], 0)
                    pos += hlen

                    msgf = <hmsg_ftr *>&sv[pos]

                    assert msgf.tok == tok, "%x vs %x at %x" % (msgf.tok, tok, pos+1)
                    assert msgf.bytesum == bytesum, '%x vs %x at %x' % (msgf.bytesum, bytesum, pos+5)
                    assert msgf.cl == ord_gt, "%s at %x" % (chr(msgf.cl), pos+7)
                    pos += 8

                    if (tok >> 24) == 32:
                        tok -= 32 << 24 # rstrip(' ')

                    if tok == tok_GPS or tok == tok_GPS1:
                        # fast path common case
                        gpsmsg.insert(gpsmsg.end(), &sv[oldpos+12], &sv[pos-8])
                    elif tok == tok_GNFI:
                        # fast path for GNFI messages (logger internal clock)
                        gnfimsg.insert(gnfimsg.end(), &sv[oldpos+12], &sv[pos-8])
                    else:
                        data = s[oldpos + 12 : pos - 8]
                        if tok == _tokdec('CNF'):
                            # Reset last_timecode so data from new CNF sections
                            # is not dropped by the dedup check (timecodes may
                            # restart or overlap across CNF boundaries).
                            for cat_idx in range(4):
                                for acc_idx in range(gc_data[cat_idx].size()):
                                    gc_data[cat_idx][acc_idx].last_timecode = -1
                            data = _decode_sequence(data).messages
                            #channels = {} # Replays don't necessarily contain all the original channels
                            for m in data[_tokdec('CHS')]:
                                channels += [None] * (m.content.index - len(channels) + 1)
                                if not channels[m.content.index]:
                                    channels[m.content.index] = m.content
                                    _resize_vaccum(gc_data[1], m.content.index)
                                    gc_data[1][m.content.index].add_helper = m.content.size + 9
                                    _resize_vaccum(gc_data[2], m.content.index)
                                    gc_data[2][m.content.index].add_helper = m.content.size + 12
                                    _resize_vaccum(gc_data[3], m.content.index)
                                    gc_data[3][m.content.index].add_helper = m.content.size
                                    gc_data[3][m.content.index].Mms = struct.unpack_from(
                                        '<I', m.content.unknown, 64)[0] // 1000
                                else:
                                    assert channels[m.content.index].short_name == m.content.short_name, "%s vs %s" % (channels[m.content.index].short_name, m.content.short_name)
                                    assert channels[m.content.index].long_name == m.content.long_name
                            for m in data.get(_tokdec('GRP'), []):
                                groups += [None] * (m.content.index - len(groups) + 1)
                                groups[m.content.index] = m.content
                                idx = 6
                                for ch in m.content.channels:
                                    channels[ch].group = GroupRef(m.content, idx)
                                    idx += channels[ch].size
                                if show_all:
                                    print('GROUP', m.content.index,
                                          [(ch, channels[ch].long_name, channels[ch].size)
                                           for ch in m.content.channels])

                                _resize_vaccum(gc_data[0], m.content.index)
                                gc_data[0][m.content.index].add_helper = 9 + sum(
                                    channels[ch].size for ch in m.content.channels)
                        elif tok == _tokdec('GRP'):
                            data = memoryview(data).cast('H')
                            assert data[1] == len(data[2:])
                            data = Group(index = data[0], channels = data[2:])
                        elif tok == _tokdec('CDE'):
                            data = ['%02x' % x for x in data]
                        elif tok == _tokdec('CHS'):
                            dcopy = bytearray(data) # copy
                            data = Channel()
                            (data.index,
                             data.short_name,
                             data.long_name,
                             data.size) = struct.unpack('<H22x8s24s16xB39x', dcopy)
                            try:
                                data.units, data.dec_pts = _unit_map[dcopy[12] & 127]
                            except KeyError:
                                print('Unknown units[%d] for %s' %
                                      (dcopy[12] & 127, data.long_name))
                                data.units = ''
                                data.dec_pts = 0
                            if dcopy[12] & 0x80 and data.units == 'mV':
                                data.units = 'V'

                            # CHS layout (112 bytes):
                            # [0:2]    uint16 LE   channel index
                            # [2:4]    padding
                            # [4:6]    uint16 LE   hardware ID (non-zero for GPS only)
                            # [6:8]    uint16 LE   source channel ID within device
                            # [8:12]   uint32 LE   hardware reference (GPS-only)
                            # [12]     uint8       unit type (lower 7 bits); high bit = calibrated flag
                            # [13]     uint8       maybe_display_format (purpose unclear)
                            # [14:16]  uint16 LE   maybe_config_flags (encoding unknown)
                            # [16]     uint8       source type (1=internal, 5=GPS, 9=CAN, etc.)
                            # [17:20]  padding
                            # [20]     uint8       decoder type, used by _decoders
                            # [21:24]  padding
                            # [24:32]  char[8]     short name
                            # [32:56]  char[24]    long name
                            # [56:64]  padding
                            # [64:68]  uint32 LE   sample period in microseconds; Mms = value // 1000
                            # [68:70]  uint16 LE   data offset into packed channel data
                            # [70:72]  padding
                            # [72]     uint8       data size (bytes per sample)
                            # [73:76]  padding
                            # [76:80]  char[4]     device tag ("@AIM" or null)
                            # [80]     uint8       device node ID
                            # [81]     uint8       maybe_device_flags (3 values: 0x00, 0x02, 0x10)
                            # [82:84]  padding
                            # [84]     uint8       maybe_output_type (1/4/6/0xFF)
                            # [85:88]  padding
                            # [88:92]  uint32 LE   display index (0xFFFFFFFF for virtual)
                            # [92]     uint8       maybe_output_size (0/2/4/8)
                            # [93:96]  padding
                            # [96:100] float32 LE  cal_value_1 (= CAL offset 24; confirmed)
                            # [100:104] float32 LE cal_value_2 (= CAL offset 28; confirmed)
                            # [104:108] float32 LE display range min
                            # [108:112] float32 LE display range max

                            # Validate padding bytes - if any are non-zero, this CHS
                            # layout has unknown fields we haven't seen before.
                            _chs_padding = (
                                dcopy[2:4] + dcopy[17:20] + dcopy[21:24] +
                                dcopy[56:64] + dcopy[70:72] + dcopy[73:76] +
                                dcopy[82:84] + dcopy[85:88] + dcopy[93:96]
                            )
                            if any(_chs_padding):
                                _ch_name = dcopy[32:56].split(b'\x00')[0].decode(
                                    'ascii', errors='replace')
                                _nonzero = [
                                    (i, b) for i, b in enumerate(dcopy)
                                    if b != 0 and i in (
                                        2, 3, 17, 18, 19, 21, 22, 23,
                                        56, 57, 58, 59, 60, 61, 62, 63,
                                        70, 71, 73, 74, 75, 82, 83,
                                        85, 86, 87, 93, 94, 95)
                                ]
                                print(
                                    'CHS padding non-zero for channel %s: %s. '
                                    'Please report at '
                                    'https://github.com/m3rlin45/libxrk/issues '
                                    'with your XRK file.' %
                                    (_ch_name,
                                     ', '.join('[%d]=0x%02x' % (i, b)
                                               for i, b in _nonzero)))

                            data.source_type = dcopy[16]
                            data.source_channel_id = struct.unpack_from('<H', dcopy, 6)[0]
                            _dtag = dcopy[76:80]
                            data.device_tag = _dtag.rstrip(b'\x00').decode('ascii', errors='replace') if any(_dtag) else ''
                            (data.cal_value_1, data.cal_value_2,
                             data.display_range_min, data.display_range_max
                            ) = struct.unpack_from('<4f', dcopy, 96)

                            dcopy[0:2] = [0] * 2 # reset index
                            dcopy[24:32] = [0] * 8 # short name
                            dcopy[32:56] = [0] * 24 # long name
                            data.unknown = bytes(dcopy)
                            data.short_name = _nullterm_string(data.short_name)
                            data.long_name = _nullterm_string(data.long_name)
                            data.timecodes = array('i')
                            data.sampledata = bytearray()
                        elif tok == _tokdec('LAP'):
                            # cache first time offset for use later
                            duration, end_time = struct.unpack('4xI8xI', data)
                            if time_offset is None:
                                time_offset = end_time - duration
                            last_time = end_time
                        elif tok in (_tokdec('RCR'), _tokdec('VEH'), _tokdec('CMP'), _tokdec('VTY'), _tokdec('NDV'), _tokdec('TMD'), _tokdec('TMT'),
                                     _tokdec('DBUN'), _tokdec('DBUT'), _tokdec('DVER'), _tokdec('MANL'), _tokdec('MODL'), _tokdec('MANI'),
                                     _tokdec('MODI'), _tokdec('HWNF'), _tokdec('PDLT'), _tokdec('NTE')):
                            data = _nullterm_string(data)
                        elif tok == _tokdec('idn'):
                            # idn message: 56-byte payload with logger info
                            # Offset +0: model ID (16-bit LE)
                            # Offset +6: logger ID (32-bit LE)
                            if len(data) >= 10:
                                model_id = struct.unpack('<H', data[0:2])[0]
                                logger_id = struct.unpack('<I', data[6:10])[0]
                                data = {'model_id': model_id, 'logger_id': logger_id}
                        elif tok == _tokdec('SRC'):
                            # SRC message contains embedded idn data
                            # Format: 3-byte token + 1-byte version + 2-byte length + payload
                            if len(data) >= 62 and data[:3] == b'idn':
                                # Parse the embedded idn payload (skip 6-byte header)
                                idn_payload = data[6:62]
                                model_id = struct.unpack('<H', idn_payload[0:2])[0]
                                logger_id = struct.unpack('<I', idn_payload[6:10])[0]
                                # Store as idn message type for metadata extraction
                                idn_msg = Message(_tokdec('idn'), 1, {'model_id': model_id, 'logger_id': logger_id})
                                if _tokdec('idn') not in messages:
                                    messages[_tokdec('idn')] = []
                                messages[_tokdec('idn')].append(idn_msg)
                        elif tok == _tokdec('GPSR'):
                            if len(data) >= 36:
                                gps_type = _nullterm_string(data[4:8])
                                gps_channel_idx = struct.unpack_from('<H', data, 22)[0]
                                _gpsr_u32_32 = struct.unpack_from('<I', data, 32)[0]
                                if _gpsr_u32_32 != 410:
                                    print('libxrk: unexpected GPSR u32[32]=%d (expected 410).'
                                          ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                          % _gpsr_u32_32)
                                data = {'type': gps_type,
                                        'channel_index': gps_channel_idx}
                        elif tok == _tokdec('RACM'):
                            if len(data) > 1:
                                data = _nullterm_string(data)
                                if data not in ('speed', 'performance'):
                                    print('libxrk: unexpected RACM mode %r (expected "speed" or "performance").'
                                          ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                          % data)
                            else:
                                data = data[0]
                                if data != 0:
                                    print('libxrk: unexpected RACM flag %d (expected 0).'
                                          ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                          % data)
                        elif tok == _tokdec('VET'):
                            data = data[0]
                            if data != 0:
                                print('libxrk: unexpected VET value %d (expected 0).'
                                      ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                      % data)
                        elif tok == _tokdec('iSLV'):
                            if len(data) >= 16 and data[:3] == b'idn':
                                idn = data[6:]
                                if len(idn) >= 10:
                                    model_id = struct.unpack('<H', idn[0:2])[0]
                                    logger_id = struct.unpack('<I', idn[6:10])[0]
                                    data = {'model_id': model_id, 'logger_id': logger_id}
                        elif tok == _tokdec('CAL'):
                            if len(data) >= 40:
                                _cal_u32_8 = struct.unpack_from('<I', data, 8)[0]
                                if _cal_u32_8 != 1:
                                    print('libxrk: unexpected CAL u32[8]=%d (expected 1).'
                                          ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                          % _cal_u32_8)
                                cal_type = struct.unpack_from('<I', data, 20)[0]
                                if cal_type not in (1, 20):
                                    print('libxrk: unexpected CAL type %d (expected 1 or 20).'
                                          ' Please report at https://github.com/m3rlin45/libxrk/issues'
                                          % cal_type)
                                val_1 = struct.unpack_from('<f', data, 24)[0]
                                val_2 = struct.unpack_from('<f', data, 28)[0]
                                cal = {'type': cal_type, 'raw_1': val_1, 'raw_2': val_2}
                                if cal_type == 1 and len(data) >= 40:
                                    cal['output_1'] = struct.unpack_from('<f', data, 32)[0]
                                    cal['output_2'] = struct.unpack_from('<f', data, 36)[0]
                                data = cal
                        elif tok == _tokdec('ENF'):
                            data = _decode_sequence(data).messages
                        elif tok == _tokdec('TRK'):
                            data = {'name': _nullterm_string(data[:32]),
                                    'sf_lat': memoryview(data).cast('i')[9] / 1e7,
                                    'sf_long': memoryview(data).cast('i')[10] / 1e7}
                        elif tok == _tokdec('ODO'):
                            # not sure how to map fuel.
                            # Fuel Used channel claims 8.56l used (2046.0-2037.4)
                            # Fuel Used odo says 70689.
                            data = {_nullterm_string(data[i:i+16]):
                                    {'time': memoryview(data[i+16:i+24]).cast('I')[0], # seconds
                                     'dist': memoryview(data[i+16:i+24]).cast('I')[1]} # meters
                                    for i in range(0, len(data), 64)
                                    # not sure how to parse fuel, doesn't match any expected units
                                    if not _nullterm_string(data[i:i+16]).startswith('Fuel')}

                        try:
                            messages[tok].append(Message(tok, ver, data))
                        except KeyError:
                            messages[tok] = [Message(tok, ver, data)]
                else:
                    assert False, "%02x%02x at %x" % (s[pos], s[pos+1], pos)
        except Exception as _err: # pylint: disable=broad-exception-caught
            if oldpos != badpos + badbytes and badbytes:
                if show_bad:
                    print('Bad bytes(%d at %x):' % (badbytes, badpos),
                          ', '.join('%02x' % c for c in s[badpos:badpos + badbytes])
                          )
                badbytes = 0
            if not badbytes:
                if show_bad:
                    sys.stdout.flush()
                    traceback.print_exc()
                badpos = oldpos # pylint: disable=unused-variable
            if oldpos < len_s:
                badbytes += 1
                pos = oldpos + 1
    t2 = time.perf_counter()
    if badbytes:
        if show_bad:
            print('Bad bytes(%d at %x):' % (badbytes, badpos),
                  ', '.join('%02x' % c for c in s[badpos:badpos + badbytes])
                  )
        badbytes = 0
    assert pos == len(s)
    # Compute time_offset and last_time from raw gc_data buffers and GPS data.
    # Channel timecodes are not yet populated (process_channel runs later), so we
    # scan the accumulated raw data vectors directly.
    if channels:
        tc_min_candidates = [time_offset] if time_offset is not None else []
        tc_max_candidates = [last_time] if last_time is not None else []
        # gc_data[0..2]: first/last 4 bytes of .data are int32 timecodes
        for cat_idx in range(3):
            for acc_idx in range(gc_data[cat_idx].size()):
                if gc_data[cat_idx][acc_idx].data.size():
                    tc_ptr = <cython.int *>&gc_data[cat_idx][acc_idx].data[0]
                    tc_min_candidates.append(tc_ptr[0])
                    stride = gc_data[cat_idx][acc_idx].add_helper - (3 if cat_idx < 2 else 8)
                    n_rows = gc_data[cat_idx][acc_idx].data.size() // stride
                    tc_ptr = <cython.int *>&gc_data[cat_idx][acc_idx].data[(n_rows - 1) * stride]
                    tc_max_candidates.append(tc_ptr[0])
        # gc_data[3] (M messages): timecodes stored in separate vector
        for acc_idx in range(gc_data[3].size()):
            if gc_data[3][acc_idx].timecodes.size():
                tc_min_candidates.append(gc_data[3][acc_idx].timecodes[0])
                tc_max_candidates.append(gc_data[3][acc_idx].timecodes[gc_data[3][acc_idx].timecodes.size() - 1])
        # GPS messages: 56-byte records, first 4 bytes = int32 timecode
        if gpsmsg.size():
            tc_ptr = <cython.int *>&gpsmsg[0]
            tc_min_candidates.append(tc_ptr[0])
            tc_ptr = <cython.int *>&gpsmsg[gpsmsg.size() - 56]
            tc_max_candidates.append(tc_ptr[0])
        time_offset = int(min(tc_min_candidates, default=0))
        last_time = int(max(tc_max_candidates, default=0))
    def process_group(g):
        g.samples = np.array([], dtype=np.int32)
        g.timecodes = g.samples.data
        if g.index < gc_data[0].size():
            data_p = &gc_data[0][g.index]
            if data_p.data.size():
                g.samples = np.asarray(<cython.uchar[:data_p.data.size()]> &data_p.data[0])
                rows = len(g.samples) // (data_p.add_helper - 3)
                g.timecodes = np.ndarray(buffer=g.samples, dtype=np.int32,
                                         shape=(rows,),
                                         strides=(data_p.add_helper-3,)) - time_offset
        for ch in g.channels:
            process_channel(channels[ch])

    def process_channel(c):
        if c.long_name in _manual_decoders:
            d = _manual_decoders[c.long_name]
        elif c.unknown[20] in _decoders:
            d = _decoders[c.unknown[20]]
        else:
            return

        c.interpolate = d.interpolate
        if c.group:
            grp = c.group.group
            c.timecodes = grp.timecodes
            c.sampledata = np.ndarray(buffer=grp.samples[c.group.offset:], dtype=d.stype,
                                      shape=grp.timecodes.shape,
                                      strides=(gc_data[0][grp.index].add_helper-3,)).copy()
        else:
            # check for S messages
            view_offset = 6
            stride_offset = 3
            data_p = &gc_data[1][c.index]
            if not data_p.data.size():
                # No? maybe c messages
                view_offset = 4
                stride_offset = 8
                data_p = &gc_data[2][c.index]
            if data_p.data.size():
                assert len(c.timecodes) == 0, "Can't have both S/c and M records for channel %s (index=%d, %d vs %d)" % (c.long_name, c.index, len(c.timecodes), data_p.data.size())

                # TREAD LIGHTLY - raw pointers here
                view = np.asarray(<cython.uchar[:data_p.data.size()]> &data_p.data[0])
                rows = len(view) // (data_p.add_helper - stride_offset)

                tc = np.ndarray(buffer=view, dtype=np.int32,
                                shape=(rows,), strides=(data_p.add_helper-stride_offset,)).copy()
                samp = np.ndarray(buffer=view[view_offset:], dtype=d.stype,
                                  shape=(rows,), strides=(data_p.add_helper-stride_offset,)).copy()
            else:
                data_p = &gc_data[3][c.index] # M messages
                if data_p.timecodes.size():
                    tc = np.asarray(<cython.int[:data_p.timecodes.size()]>
                                    &data_p.timecodes[0]).copy()
                    samp = np.ndarray(buffer=np.asarray(<cython.uchar[:data_p.data.size()]>
                                                        &data_p.data[0]),
                                      dtype=d.stype, shape=tc.shape).copy()
                else:
                    tc = _ndarray_from_mv(c.timecodes)
                    samp = _ndarray_from_mv(memoryview(c.sampledata).cast(d.stype))
            c.timecodes = (tc - time_offset).data
            c.sampledata = samp.data

        if d.fixup:
            c.sampledata = memoryview(d.fixup(c.sampledata))
        if c.units == 'V': # unit_type 21 is mV; calibrated flag converts to V
            c.sampledata = np.divide(c.sampledata, 1000).data

    laps = None
    gnfi_timecodes = None
    if not channels:
        t4 = time.perf_counter()
        pass # nothing to do
    elif progress:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, os.cpu_count())) as worker:
            bg_work = worker.submit(_bg_gps_laps, <cython.uchar[:gpsmsg.size()]> &gpsmsg[0],
                                    <cython.uchar[:gnfimsg.size()]> &gnfimsg[0] if gnfimsg.size() else None,
                                    messages, time_offset, last_time)
            group_work = worker.map(process_group, [x for x in groups if x])
            channel_work = worker.map(process_channel,
                                      [x for x in channels if x and not x.group])
            gps_ch, laps, gnfi_timecodes = bg_work.result()
            t4 = time.perf_counter()
            for i in group_work:
                pass
            for i in channel_work:
                pass
            channels.extend(gps_ch)
    else:
        for g in groups:
            if g: process_group(g)
        for c in channels:
            if c and not c.group: process_channel(c)
        t4 = time.perf_counter()
        gps_ch, laps, gnfi_timecodes = _bg_gps_laps(
            <cython.uchar[:gpsmsg.size()]> &gpsmsg[0],
            <cython.uchar[:gnfimsg.size()]> &gnfimsg[0] if gnfimsg.size() else None,
            messages, time_offset, last_time)
        channels.extend(gps_ch)

    return DataStream(
        channels={ch.long_name: ch for ch in channels
                  if ch and len(ch.sampledata)
                  and ch.long_name not in ('StrtRec', 'Master Clk')},
        messages=messages,
        laps=laps,
        time_offset=time_offset,
        gnfi_timecodes=gnfi_timecodes)

def _get_metadata(msg_by_type, channels=None):
    ret = {}
    for msg, name in [(_tokdec('RCR'), 'Driver'),
                      (_tokdec('VEH'), 'Vehicle'),
                      (_tokdec('TMD'), 'Log Date'),
                      (_tokdec('TMT'), 'Log Time'),
                      (_tokdec('VTY'), 'Session'),
                      (_tokdec('CMP'), 'Series'),
                      (_tokdec('NTE'), 'Long Comment'),
                      ]:
        if msg in msg_by_type:
            ret[name] = msg_by_type[msg][-1].content
    if _tokdec('TRK') in msg_by_type:
        ret['Venue'] = msg_by_type[_tokdec('TRK')][-1].content['name']
        # ignore the start/finish line?
    if _tokdec('ODO') in msg_by_type:
        for name, stats in msg_by_type[_tokdec('ODO')][-1].content.items():
            ret['Odo/%s Distance (km)' % name] = stats['dist'] / 1000
            ret['Odo/%s Time' % name] = '%d:%02d:%02d' % (stats['time'] // 3600,
                                                          stats['time'] // 60 % 60,
                                                          stats['time'] % 60)
    # Logger info from idn message
    if _tokdec('idn') in msg_by_type:
        idn_data = msg_by_type[_tokdec('idn')][-1].content
        if isinstance(idn_data, dict):
            ret['Logger ID'] = idn_data['logger_id']
            ret['Logger Model ID'] = idn_data['model_id']
            ret['Logger Model'] = _logger_models.get(idn_data['model_id'])
    # Device name from NDV message
    if _tokdec('NDV') in msg_by_type:
        ret['Device Name'] = msg_by_type[_tokdec('NDV')][-1].content
    # GPS receiver info from GPSR message
    if _tokdec('GPSR') in msg_by_type:
        gpsr_data = msg_by_type[_tokdec('GPSR')][-1].content
        if isinstance(gpsr_data, dict):
            ret['GPS Receiver'] = gpsr_data['type']
    # Expansion device info from ENF messages
    if _tokdec('ENF') in msg_by_type:
        expansion_devices = []
        for enf_msg in msg_by_type[_tokdec('ENF')]:
            if isinstance(enf_msg.content, dict):
                device = {}
                # Extract device properties from nested messages
                for tok_str, key in [('DBUN', 'Bus Unit'),
                                     ('DBUT', 'Bus Type'),
                                     ('DVER', 'Version'),
                                     ('MANL', 'Manufacturer'),
                                     ('MODL', 'Model')]:
                    tok = _tokdec(tok_str)
                    if tok in enf_msg.content and enf_msg.content[tok]:
                        device[key] = enf_msg.content[tok][-1].content
                if device:
                    expansion_devices.append(device)
        # Enrich with hardware IDs from iSLV messages (positional match)
        if _tokdec('iSLV') in msg_by_type:
            slave_msgs = [m for m in msg_by_type[_tokdec('iSLV')]
                          if isinstance(m.content, dict)]
            for i, device in enumerate(expansion_devices):
                if i < len(slave_msgs):
                    device['Logger ID'] = slave_msgs[i].content['logger_id']
                    device['Model ID'] = slave_msgs[i].content['model_id']
        if expansion_devices:
            ret['Expansion Devices'] = expansion_devices
    # Race mode from RACM message
    if _tokdec('RACM') in msg_by_type:
        for racm_msg in msg_by_type[_tokdec('RACM')]:
            if isinstance(racm_msg.content, str):
                ret['Race Mode'] = racm_msg.content
    # Vehicle electronics type from VET message
    if _tokdec('VET') in msg_by_type:
        vet = msg_by_type[_tokdec('VET')][-1].content
        if isinstance(vet, int):
            ret['Vehicle Electronics Type'] = vet
    # Calibrations from CAL messages, cross-referenced with CHS channel data
    if _tokdec('CAL') in msg_by_type:
        # Build map from (cal_val_1, cal_val_2) -> channel name via CHS offsets 96-103
        cal_to_channel = {}
        if channels:
            for ch in channels.values():
                if hasattr(ch, 'unknown') and len(ch.unknown) >= 104:
                    f96, f100 = struct.unpack_from('<ff', ch.unknown, 96)
                    cal_to_channel[(f96, f100)] = ch.long_name
        calibrations = []
        for cal_msg in msg_by_type[_tokdec('CAL')]:
            if isinstance(cal_msg.content, dict):
                cal = dict(cal_msg.content)
                key = (cal['raw_1'], cal['raw_2'])
                if key in cal_to_channel:
                    cal['channel'] = cal_to_channel[key]
                calibrations.append(cal)
        if calibrations:
            ret['Calibrations'] = calibrations
    return ret

def _bg_gps_laps(gpsmsg, gnfimsg, msg_by_type, time_offset, last_time):
    channels = _decode_gps(gpsmsg, time_offset)
    gnfi_timecodes = _decode_gnfi(gnfimsg, time_offset)
    lat_ch = None
    lon_ch = None
    for ch in channels:
        if ch.long_name == 'GPS Latitude': lat_ch = ch
        if ch.long_name == 'GPS Longitude': lon_ch = ch
    laps = _get_laps(lat_ch, lon_ch, msg_by_type, time_offset, last_time)
    return channels, laps, gnfi_timecodes

def _decode_gps(gpsmsg, time_offset):
    """Decode GPS messages from XRK data stream.

    Each GPS message is 56 bytes: a 4-byte AIM logger timecode followed by a
    52-byte u-blox NAV-SOL (Navigation Solution) payload.

    56-byte layout:

        Offset  NAV-SOL  Field      Type    Notes
        0       -        timecode   int32   AIM logger time [ms]
        4       0        iTOW       uint32  GPS time of week [ms]
        8       4        fTOW       int32   Fractional TOW [ns], +/-500000
        12      8        week       uint16  GPS week number
        14      10       gpsFix     uint8   Fix type (0=none, 2=2D, 3=3D)
        15      11       flags      uint8   Validity bitmask
        16      12       ecefX      int32   ECEF X position [cm]
        20      16       ecefY      int32   ECEF Y position [cm]
        24      20       ecefZ      int32   ECEF Z position [cm]
        28      24       pAcc       uint32  Position accuracy [cm]
        32      28       ecefVX     int32   ECEF X velocity [cm/s]
        36      32       ecefVY     int32   ECEF Y velocity [cm/s]
        40      36       ecefVZ     int32   ECEF Z velocity [cm/s]
        44      40       sAcc       uint32  Speed accuracy [cm/s]
        48      44       pDOP       uint16  Position DOP [*0.01]
        50      46       reserved1  uint8   u-blox reserved
        51      47       numSV      uint8   Number of satellites used
        52      48       reserved2  uint32  u-blox reserved (non-zero in some firmware)
    """
    if not gpsmsg: return []
    alldata = memoryview(gpsmsg)
    assert len(alldata) % 56 == 0
    timecodes = np.asarray(alldata[0:].cast('i')[::56//4])
    # certain old MXP firmware (and maybe others) would periodically
    # butcher the upper 16-bits of the timecode field.  If necessary,
    # reconstruct it using only the bottom 16-bits and assuming time
    # never skips ahead too far.
    if np.any(timecodes[1:] < timecodes[:-1]):
        timecodes = (timecodes & 65535) + (timecodes[0] - (timecodes[0] & 65535))
        timecodes += 65536 * np.cumsum(np.concatenate(([0], timecodes[1:] < timecodes[:-1])))
    # NAV-SOL fields (known, used for position/velocity)
    #itow_ms = alldata[4:].cast('I')[::56//4]       # iTOW - GPS time of week
    #fTOW_ns = alldata[8:].cast('i')[::56//4]       # fTOW - fractional TOW [ns]
    #weekN = alldata[12:].cast('H')[::56//2]         # GPS week number
    ecefX_cm = alldata[16:].cast('i')[::56//4]       # ecefX [cm]
    ecefY_cm = alldata[20:].cast('i')[::56//4]       # ecefY [cm]
    ecefZ_cm = alldata[24:].cast('i')[::56//4]       # ecefZ [cm]
    posacc_cm = np.asarray(alldata[28:].cast('I')[::56//4])   # pAcc [cm]
    ecefdX_cms = alldata[32:].cast('i')[::56//4]     # ecefVX [cm/s]
    ecefdY_cms = alldata[36:].cast('i')[::56//4]     # ecefVY [cm/s]
    ecefdZ_cms = alldata[40:].cast('i')[::56//4]     # ecefVZ [cm/s]
    velacc_cms = np.asarray(alldata[44:].cast('I')[::56//4])  # sAcc [cm/s]

    # NAV-SOL fields (newly exposed as channels)
    gpsFix = np.asarray(alldata[14::56]).astype(np.uint8)     # gpsFix [0-5]
    pDOP_raw = np.asarray(alldata[48:].cast('H')[::56//2])    # pDOP [*0.01]
    nsat = np.asarray(alldata[51::56])                        # numSV

    timecodes_raw = timecodes - time_offset
    timecodes = memoryview(timecodes_raw)

    gpsconv = gps.ecef2lla(np.divide(ecefX_cm, 100),
                           np.divide(ecefY_cm, 100),
                           np.divide(ecefZ_cm, 100))

    # Compute GPS speed (m/s)
    speed_ms = np.sqrt(np.square(ecefdX_cms) + np.square(ecefdY_cms) + np.square(ecefdZ_cms)) / 100.0

    # Compute heading from ECEF velocity using ENU transformation
    lat_rad = gpsconv.lat * (np.pi / 180)
    lon_rad = gpsconv.long * (np.pi / 180)
    V_east, V_north = gps.ecef_velocity_to_enu(
        np.asarray(ecefdX_cms), np.asarray(ecefdY_cms), np.asarray(ecefdZ_cms),
        lat_rad, lon_rad
    )
    heading_deg = np.arctan2(V_east, V_north) * (180 / np.pi)

    # Compute time deltas (in seconds)
    dt_sec = np.diff(timecodes_raw) / 1000.0
    # Protect against division by zero
    dt_sec = np.where(dt_sec > 0, dt_sec, np.inf)

    # GPS_InlineAcc = d(speed)/dt / 9.81 (convert m/s² to g)
    dv = np.diff(speed_ms)
    inline_acc = np.concatenate([[0], dv / dt_sec]) / 9.81

    # GPS_Yaw_Rate = d(heading)/dt (deg/s)
    dheading = np.diff(heading_deg)
    # Handle wrap-around at ±180°
    dheading = np.where(dheading > 180, dheading - 360, dheading)
    dheading = np.where(dheading < -180, dheading + 360, dheading)
    yaw_rate = np.concatenate([[0], dheading / dt_sec])

    # GPS_LateralAcc = speed × yaw_rate × π/180 / 9.81 (g)
    lateral_acc = speed_ms * yaw_rate * (np.pi / 180) / 9.81

    return [Channel(
        long_name='GPS Speed',
        units='m/s',
        dec_pts=1,
        interpolate=True,
        timecodes=timecodes,
        sampledata=memoryview(speed_ms.astype(np.float64))),
            Channel(long_name='GPS Latitude',  units='deg', dec_pts=4, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(gpsconv.lat)),
            Channel(long_name='GPS Longitude', units='deg', dec_pts=4, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(gpsconv.long)),
            Channel(long_name='GPS Altitude', units='m', dec_pts=1, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(gpsconv.alt)),
            # GPS accuracy metrics (from NAV-SOL)
            Channel(long_name='GPS_Satellites', units='', dec_pts=0, interpolate=False,
                    timecodes=timecodes, sampledata=memoryview(nsat.astype(np.float32))),
            Channel(long_name='GPS_Fix', units='', dec_pts=0, interpolate=False,
                    timecodes=timecodes, sampledata=memoryview(gpsFix.astype(np.float32))),
            Channel(long_name='GPS_pDOP', units='', dec_pts=2, interpolate=False,
                    timecodes=timecodes,
                    sampledata=memoryview(np.divide(pDOP_raw, 100.0).astype(np.float32))),
            Channel(long_name='GPS_Position_Accuracy', units='m', dec_pts=2, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview((posacc_cm / 100.0).astype(np.float32))),
            Channel(long_name='GPS_Velocity_Accuracy', units='m/s', dec_pts=2, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview((velacc_cms / 100.0).astype(np.float32))),
            # GPS derived channels
            Channel(long_name='GPS_InlineAcc', units='g', dec_pts=2, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(inline_acc.astype(np.float32))),
            Channel(long_name='GPS_LateralAcc', units='g', dec_pts=2, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(lateral_acc.astype(np.float32))),
            Channel(long_name='GPS_Yaw_Rate', units='deg/s', dec_pts=1, interpolate=True,
                    timecodes=timecodes, sampledata=memoryview(yaw_rate.astype(np.float32)))]

def _decode_gnfi(gnfimsg, time_offset):
    """Parse GNFI messages and return timecodes array.

    GNFI messages run on the logger's internal clock, not the GPS timecode stream.
    This provides a ground truth reference for detecting GPS timing bugs.

    GNFI message structure (32 bytes each):
    - Bytes 0-3: Logger timecode (int32)
    - Bytes 4-31: Other data (not used for timing)

    Args:
        gnfimsg: Raw GNFI message bytes
        time_offset: Time offset to subtract from timecodes

    Returns:
        numpy array of GNFI timecodes, or None if no GNFI data
    """
    if not gnfimsg:
        return None
    alldata = memoryview(gnfimsg)
    if len(alldata) % 32 != 0:
        return None
    timecodes = np.asarray(alldata[0:].cast('i')[::32//4]) - time_offset
    return timecodes


def _get_laps(lat_ch, lon_ch, msg_by_type, time_offset, last_time):
    lap_nums = []
    start_times = []
    end_times = []

    # Prefer LAP messages when available (matches official DLL behavior)
    if _tokdec('LAP') in msg_by_type:
        for m in msg_by_type[_tokdec('LAP')]:
            # 2nd byte is segment #, see M4GT4
            segment, lap, duration, end_time = struct.unpack('xBHIxxxxxxxxI', m.content)
            end_time -= time_offset
            if segment:
                continue
            elif not lap_nums:
                pass
            elif lap_nums[-1] == lap:
                continue
            elif lap_nums[-1] + 1 == lap:
                pass
            elif lap_nums[-1] + 2 == lap:
                # emit inferred lap
                lap_nums.append(lap - 1)
                start_times.append(end_times[-1])
                end_times.append(end_time - duration)
            else:
                assert False, 'Lap gap from %d to %d' % (lap_nums[-1], lap)
            lap_nums.append(lap)
            start_times.append(end_time - duration)
            end_times.append(end_time)
    elif lat_ch and lon_ch:
        # Fall back to GPS-based lap detection only when no LAP messages exist
        track = msg_by_type[_tokdec('TRK')][-1].content
        XYZ = np.column_stack(gps.lla2ecef(np.array(lat_ch.sampledata),
                                           np.array(lon_ch.sampledata), 0))
        lap_markers = gps.find_laps(XYZ,
                                    np.array(lat_ch.timecodes),
                                    (track['sf_lat'], track['sf_long']))

        # Use GPS channel's last timecode as session end (already adjusted)
        # This avoids relying on last_time which may be 0 when no LAP messages exist
        session_end = int(lat_ch.timecodes[-1]) if len(lat_ch.timecodes) else (last_time - time_offset if last_time else 0)

        # Only add session boundaries if we have detected lap crossings
        # This creates laps from each crossing to the next
        if lap_markers:
            lap_markers = lap_markers + [session_end]
            for lap, (start_time, end_time) in enumerate(zip(lap_markers[:-1], lap_markers[1:])):
                lap_nums.append(lap)
                start_times.append(start_time)
                end_times.append(end_time)
    
    # Normalize lap numbers to 0-based indexing (matches DLL behavior)
    if lap_nums:
        min_lap = min(lap_nums)
        lap_nums = [n - min_lap for n in lap_nums]

    # Create PyArrow table
    return pa.table({
        'num': pa.array(lap_nums, type=pa.int32()),
        'start_time': pa.array(start_times, type=pa.int64()),
        'end_time': pa.array(end_times, type=pa.int64())
    })


def _channel_to_table(ch):
    """Convert a Channel object to a PyArrow table with metadata."""
    # Create metadata dict for the channel data field (without name, as it's the column name)
    metadata = {
        b'units': (ch.units if ch.size != 1 else '').encode('utf-8'),
        b'dec_pts': str(ch.dec_pts).encode('utf-8'),
        b'interpolate': str(ch.interpolate).encode('utf-8'),
        b'source_type': str(ch.source_type).encode('utf-8'),
        b'source_channel_id': str(ch.source_channel_id).encode('utf-8'),
        b'device_tag': ch.device_tag.encode('utf-8'),
        b'cal_value_1': str(ch.cal_value_1).encode('utf-8'),
        b'cal_value_2': str(ch.cal_value_2).encode('utf-8'),
        b'display_range_min': str(ch.display_range_min).encode('utf-8'),
        b'display_range_max': str(ch.display_range_max).encode('utf-8'),
    }
    
    # Determine the appropriate type for values based on the data
    if isinstance(ch.sampledata, memoryview):
        values_array = np.array(ch.sampledata)
    else:
        values_array = ch.sampledata
    
    # Create the schema with metadata on the channel data field
    # Use the actual channel name as the column name
    channel_field = pa.field(ch.long_name, pa.from_numpy_dtype(values_array.dtype), metadata=metadata)
    schema = pa.schema([
        pa.field('timecodes', pa.int64()),
        channel_field
    ])
    
    # Create the table with the channel name as the column name
    return pa.table({
        'timecodes': pa.array(ch.timecodes, type=pa.int64()),
        ch.long_name: pa.array(values_array)
    }, schema=schema)


def _decompress_if_zlib(data):
    """Decompress zlib-compressed data if detected, otherwise return as-is.
    
    XRZ files are XRK files compressed with zlib. They start with zlib magic
    bytes (0x78 followed by 0x01, 0x9C, or 0xDA).
    """
    if len(data) < 2:
        return data
    
    # Check for zlib magic bytes
    first_byte = data[0] if isinstance(data[0], int) else ord(data[0])
    second_byte = data[1] if isinstance(data[1], int) else ord(data[1])
    
    if first_byte == 0x78 and second_byte in (0x01, 0x9C, 0xDA):
        deco = zlib.decompressobj()
        try:
            return deco.decompress(bytes(data))
        except zlib.error:
            # Truncated stream - recover partial data
            return deco.flush()

    return data


class _open_xrk:
    """Context manager that opens an XRK/XRZ file, using mmap if available, falling back to read().
    
    This handles environments like JupyterLite where mmap may not be supported.
    Also accepts bytes or file-like objects directly.
    XRZ files (zlib-compressed XRK) are automatically decompressed.
    """
    def __init__(self, source):
        self._source = source
        self._file = None
        self._mmap = None
        self._data = None
    
    def __enter__(self):
        # Handle bytes input directly
        if isinstance(self._source, (bytes, bytearray)):
            self._data = _decompress_if_zlib(self._source)
            return self._data
        
        # Handle memoryview - convert to bytes for consistent handling
        if isinstance(self._source, memoryview):
            self._data = _decompress_if_zlib(bytes(self._source))
            return self._data
        
        # Handle file-like objects (BytesIO, etc.)
        if hasattr(self._source, 'read'):
            self._source.seek(0)
            self._data = _decompress_if_zlib(self._source.read())
            return self._data
        
        # Handle file path - try mmap first, fall back to read()
        self._file = open(self._source, 'rb')
        try:
            self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            # Check if zlib compressed - if so, decompress and use bytes instead of mmap
            if len(self._mmap) >= 2 and self._mmap[0] == 0x78 and self._mmap[1] in (0x01, 0x9C, 0xDA):
                deco = zlib.decompressobj()
                try:
                    self._data = deco.decompress(self._mmap[:])
                except zlib.error:
                    # Truncated stream - recover partial data
                    self._data = deco.flush()
                self._mmap.close()
                self._mmap = None
                return self._data
            return self._mmap
        except (OSError, ValueError):
            # mmap failed (e.g., JupyterLite/IDBFS) - fall back to read()
            self._file.seek(0)
            self._data = _decompress_if_zlib(self._file.read())
            return self._data
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._mmap is not None:
            self._mmap.close()
        if self._file is not None:
            self._file.close()
        return False


def aim_xrk(fname, progress=None):
    """Load an AIM XRK or XRZ file.
    
    Args:
        fname: Path to the XRK/XRZ file, or bytes/BytesIO containing file data
        progress: Optional progress callback
        
    Returns:
        LogFile object with channels, laps, and metadata
    """
    with _open_xrk(fname) as m:
        data = _decode_sequence(m, progress)

    log = base.LogFile(
        {ch.long_name: _channel_to_table(ch) for ch in data.channels.values()},
        data.laps,
        _get_metadata(data.messages, data.channels),
        fname if not isinstance(fname, (bytes, bytearray, memoryview)) and not hasattr(fname, 'read') else "<bytes>")

    # Fix GPS timing gaps (spurious timestamp jumps in some AIM loggers)
    # Pass GNFI timecodes for more robust detection (if available)
    fix_gps_timing_gaps(log, gnfi_timecodes=data.gnfi_timecodes)

    return log


def aim_track_dbg(fname):
    """Debug function to extract track data from an AIM XRK file."""
    with _open_xrk(fname) as m:
        data = _decode_sequence(m, None)
    return {_tokenc(k): v for k, v in data.messages.items()}


