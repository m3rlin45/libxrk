"""AIM SmartyCam MP4 Telemetry Format Specification — Construct-based executable spec.

AIM SmartyCam cameras (observed: SmartyCam 3 GP, firmware 01.17.08) record a
third track alongside video and audio: an ISO BMFF *timed metadata* track whose
handler is named ``MetaAimHandler`` and whose sample entry type is ``aimd``.
Every ``aimd`` sample is a 66-byte header followed by a byte stream in the
**XRK message grammar** (the same ``<h...>`` header messages and ``(S...)``
data messages that ``spec/xrk_format.py`` defines). After the ``moov`` box the
camera appends an ``<aim_meta_data>`` XML document inside a malformed ``free``
box.

Layering — this module holds only the AIM-specific knowledge:

  - which track is the telemetry track (``is_telemetry_track``)
  - the 66-byte ``aimd`` sample header (``AimdSampleHeader``/``AimdSample``)
  - the trailing ``aim_meta_data`` pseudo-box (``AimMetaDataTail``)
  - the time mapping between the video timeline and the logger clock
  - the keys used to link an MP4 to the XRK/XRZ of the same session

The ISO BMFF container itself is handled by ``spec/isobmff.py`` (a small, lazy
Construct walker). The two meet at ``TrackSamples``: a plain record of the facts
the AIM layer needs about a track (sample offsets/sizes/timestamps, timescales,
handler identity, leading empty edit). ``TrackSamples.from_isobmff`` fills it
from ``spec.isobmff``; a different MP4 library can fill it directly and call
``parse_telemetry(...)`` without touching anything else here.

The payload bytes of all samples, concatenated, are handed to
``spec.xrk_format.parse_xrk_bytes`` unchanged.

Usage:
    from spec.mp4_format import parse_mp4_file
    mp4 = parse_mp4_file("path/to/SCHD0487.MP4")
    mp4.telemetry.get_laps()             # LAP messages, identical to the XRK's
    mp4.media_us_to_logger_ms(60_000_000)  # logger ms at 60 s of telemetry media time

Prose specification: ``spec/docs/mp4_format.md``.
"""

import bisect
import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from construct import (
    Bytes,
    Check,
    Const,
    ConstructError,
    GreedyBytes,
    Int16ub,
    Int32ul,
    Int64ul,
    Rebuild,
    Struct,
    this,
)

from spec import isobmff
from spec.xrk_format import EmbeddedIDNPayload, ParseResult, TRKPayload, parse_xrk_bytes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AIMD_SAMPLE_ENTRY = b"aimd"  # stsd sample entry format of the telemetry track
AIMD_HANDLER_TYPE = b"meta"  # hdlr handler_type of the telemetry track
AIMD_HANDLER_NAME = "MetaAimHandler"  # hdlr name of the telemetry track
AIMD_MAGIC = b"amv0"  # [6:10] of every aimd sample
AIMD_HEADER_SIZE = 66  # bytes before the XRK message stream in every sample

# The trailing pseudo-box: where a uint32 BE box size belongs, the camera writes
# the ASCII text "4096" (0x34 0x30 0x39 0x36 = 875_575_606 as an integer).
META_TAIL_SIZE_TEXT = b"4096"
META_TAIL_TYPE = b"free"
META_TAIL_XML_ROOT = "aim_meta_data"

# GPS epoch for (week, iTOW) -> UTC. GPS time is ahead of UTC by the leap
# second count (18 s since 2017-01-01).
GPS_EPOCH = datetime.datetime(1980, 1, 6)
GPS_UTC_LEAP_SECONDS = 18

# ---------------------------------------------------------------------------
# aimd sample: 66-byte header + XRK message stream
# ---------------------------------------------------------------------------

# Verified on all 14 850 samples of the reference file (SCHD0487.MP4):
#   [0:2]   uint16 BE  (sample length - 2) mod 65536. Truncated: the one 71 818-byte
#                      sample (syslog burst) stores 0x1888 = 71 816 - 65 536.
#   [2:6]   bytes      always 40 00 00 00 (purpose unknown; flags?)
#   [6:10]  ASCII      "amv0"
#   [10:18] uint64 LE  logger clock in microseconds when the sample was flushed.
#                      Always slightly after the newest timecode inside the
#                      payload (payload timecodes are logger ms).
#   [18:26] uint64 LE  media presentation time of the sample in microseconds.
#                      Tracks the stts DTS within ~11-12 ms. Sample 0 is an
#                      outlier (11 393 646 us; see unknown_regions.md).
#   [26:62] bytes      always zero
#   [62:66] uint32 LE  payload length = sample length - 66
#   [66:]   bytes      XRK message stream (parse with spec.xrk_format)
#
# The difference [10:18] - [18:26] is the logger time at media time 0. It
# drifts by ~100 ms over a 25-minute recording (~69 ppm), so it is a per-sample
# table, not a constant (see Mp4ParseResult.media_us_to_logger_ms).
AimdSampleHeader = Struct(
    "length_minus_2" / Int16ub,  # [0:2]
    "_flags" / Const(b"\x40\x00\x00\x00"),  # [2:6]   unknown, constant
    "_magic" / Const(AIMD_MAGIC),  # [6:10]
    "logger_time_us" / Int64ul,  # [10:18]
    "media_time_us" / Int64ul,  # [18:26]
    "_zero_26_62" / Const(bytes(36)),  # [26:62]
    "payload_length" / Int32ul,  # [62:66]
    Check(lambda ctx: ctx.length_minus_2 == (ctx.payload_length + AIMD_HEADER_SIZE - 2) & 0xFFFF),
)

# Whole sample, round-trippable: both length fields are rebuilt from the payload.
AimdSample = Struct(
    "length_minus_2"
    / Rebuild(Int16ub, lambda ctx: (len(ctx.payload) + AIMD_HEADER_SIZE - 2) & 0xFFFF),  # [0:2]
    "_flags" / Const(b"\x40\x00\x00\x00"),  # [2:6]
    "_magic" / Const(AIMD_MAGIC),  # [6:10]
    "logger_time_us" / Int64ul,  # [10:18]
    "media_time_us" / Int64ul,  # [18:26]
    "_zero_26_62" / Const(bytes(36)),  # [26:62]
    "payload_length" / Rebuild(Int32ul, lambda ctx: len(ctx.payload)),  # [62:66]
    "payload" / Bytes(this.payload_length),  # [66:]
    Check(lambda ctx: ctx.length_minus_2 == (ctx.payload_length + AIMD_HEADER_SIZE - 2) & 0xFFFF),
)

# ---------------------------------------------------------------------------
# Trailing aim_meta_data pseudo-box
# ---------------------------------------------------------------------------

# After 'moov' the file ends with 8 + len(xml) bytes:
#   [0:4]  ASCII "4096" where a uint32 BE box size belongs. Read as an integer
#          this is 875 575 606, far past EOF; a naive box walker fails here.
#   [4:8]  "free"
#   [8:]   UTF-8 XML, <?xml version="1.0"?> + <aim_meta_data version="0.0.0">,
#          1017 bytes in the reference file (NOT padded to 4096).
AimMetaDataTail = Struct(
    "_size_text" / Const(META_TAIL_SIZE_TEXT),  # [0:4]
    "_type" / Const(META_TAIL_TYPE),  # [4:8]
    "xml" / GreedyBytes,  # [8:]
)


def parse_aim_meta_data_xml(xml_bytes):
    """Parse the <aim_meta_data> XML into {section: {name: text}}.

    The document is ``<aim_meta_data version=...>`` with child sections
    (``generic_info``, ``hw_info``, ``audio_conf``) each holding
    ``<p n="name">text</p>`` elements. Empty elements (``<p n="lens"/>``)
    yield ``""``. The root's ``version`` attribute is returned under
    ``{"_root": {"version": ...}}``.
    """
    root = ET.fromstring(xml_bytes)
    if root.tag != META_TAIL_XML_ROOT:
        raise ValueError(f"unexpected XML root {root.tag!r}")
    out = {"_root": dict(root.attrib)}
    for section in root:
        out[section.tag] = {p.get("n"): (p.text or "") for p in section if p.tag == "p"}
    return out


def read_meta_tail(f, offset, end):
    """Parse the aim_meta_data pseudo-box occupying [offset, end); return XML bytes or None."""
    if offset >= end:
        return None
    f.seek(offset)
    raw = f.read(end - offset)
    try:
        tail = AimMetaDataTail.parse(raw)
    except ConstructError:
        return None
    return bytes(tail.xml)


# ---------------------------------------------------------------------------
# The seam to the container layer
# ---------------------------------------------------------------------------


def is_telemetry_track(track):
    """True for the AIM telemetry track: 'meta' handler named MetaAimHandler, 'aimd' samples.

    ``track`` is anything with ``handler_type``, ``handler_name`` and
    ``sample_format`` attributes (``spec.isobmff.Track`` or ``TrackSamples``).
    """
    return (
        track.handler_type == AIMD_HANDLER_TYPE
        and track.handler_name == AIMD_HANDLER_NAME
        and track.sample_format == AIMD_SAMPLE_ENTRY
    )


def find_telemetry_track(tracks):
    """First telemetry track in an iterable of track-like objects, or None."""
    return next((t for t in tracks if is_telemetry_track(t)), None)


@dataclass
class TrackSamples:
    """Everything the AIM layer needs to know about the telemetry track.

    This is the adapter between the container parser and the AIM-specific code.
    Fill it from ``spec.isobmff`` with ``from_isobmff``, or directly from any
    other MP4 library: resolve the track's samples to absolute file offsets and
    sizes, provide their decode timestamps in media timescale units, and copy the
    handler identity and the leading empty edit of the track's edit list.
    """

    track_id: int
    handler_type: bytes  # hdlr.handler_type ('meta')
    handler_name: str  # hdlr.name ('MetaAimHandler')
    sample_format: bytes | None  # first stsd entry format ('aimd')
    sample_entry_raw: bytes | None  # first stsd entry, raw (16 bytes for aimd)
    media_timescale: int  # mdhd.timescale (1000 = ms)
    movie_timescale: int  # mvhd.timescale (6000 observed)
    empty_edit_ticks: int  # leading empty edit in movie timescale, 0 if none
    offsets: list  # absolute file offset of every sample
    sizes: list  # size of every sample (header + payload)
    dts: list  # decode timestamp of every sample, media timescale
    track_references: list = field(default_factory=list)  # [(type, [track_ids])]

    @classmethod
    def from_isobmff(cls, track, movie_timescale):
        """Build from a ``spec.isobmff.Track`` and its movie's timescale."""
        return cls(
            track_id=track.track_id,
            handler_type=track.handler_type,
            handler_name=track.handler_name,
            sample_format=track.sample_format,
            sample_entry_raw=track.sample_entry_raw,
            media_timescale=track.media_timescale,
            movie_timescale=movie_timescale,
            empty_edit_ticks=track.empty_edit_duration(),
            offsets=track.sample_offsets(),
            sizes=list(track.sample_sizes),
            dts=track.sample_dts(),
            track_references=list(track.track_references),
        )

    @property
    def sample_count(self):
        return len(self.sizes)

    def empty_edit_ms(self):
        """Leading empty edit in milliseconds (media time 0 is presented at this movie time)."""
        return self.empty_edit_ticks * 1000.0 / self.movie_timescale


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AimdSampleInfo:
    """One telemetry sample: location, timing, and header fields."""

    index: int
    offset: int  # absolute file offset of the 66-byte header
    size: int  # header + payload
    dts: int  # from stts, media timescale (ms)
    logger_time_us: int  # header [10:18]
    media_time_us: int  # header [18:26]
    payload_length: int  # header [62:66]

    @property
    def payload_offset(self):
        return self.offset + AIMD_HEADER_SIZE


@dataclass
class Mp4ParseResult:
    """Result of parsing a SmartyCam MP4."""

    path: Path | None
    file_size: int
    telemetry_track: TrackSamples | None = None
    samples: list = field(default_factory=list)  # [AimdSampleInfo]
    meta_data_raw: bytes | None = None  # XML bytes of the trailing pseudo-box
    meta_data: dict = field(default_factory=dict)  # parse_aim_meta_data_xml()
    telemetry: ParseResult | None = None  # XRK-grammar parse of concatenated payloads
    movie: isobmff.Movie | None = None  # container details when parsed with spec.isobmff
    # Sorted (media_time_us, logger_time_us) pairs used for the time mapping.
    _clock_table: list = field(default_factory=list, repr=False)

    # --- container conveniences (only when parsed through spec.isobmff) ----

    @property
    def top_level_boxes(self):
        return self.movie.top_level_boxes if self.movie else []

    @property
    def tracks(self):
        return self.movie.tracks if self.movie else []

    @property
    def movie_timescale(self):
        if self.movie:
            return self.movie.timescale
        return self.telemetry_track.movie_timescale if self.telemetry_track else 0

    @property
    def movie_duration(self):
        return self.movie.duration if self.movie else 0

    @property
    def creation_time(self):
        return self.movie.creation_time if self.movie else None

    # --- time mapping -----------------------------------------------------

    def clock_offset_table(self):
        """[(media_time_us, logger_time_us - media_time_us)] for every regular sample.

        Sample 0 is excluded: its media_time_us does not fit the series (see
        unknown_regions.md, "aimd sample header").
        """
        return [(m, lg - m) for m, lg in self._clock_table]

    def _offset_at(self, media_us):
        table = self._clock_table
        if not table:
            raise ValueError("no telemetry samples")
        medias = [m for m, _ in table]
        i = bisect.bisect_left(medias, media_us)
        if i <= 0:
            m, lg = table[0]
            return lg - m
        if i >= len(table):
            m, lg = table[-1]
            return lg - m
        m0, l0 = table[i - 1]
        m1, l1 = table[i]
        f = (media_us - m0) / (m1 - m0) if m1 != m0 else 0.0
        return (l0 - m0) + f * ((l1 - m1) - (l0 - m0))

    def media_us_to_logger_ms(self, media_us):
        """Map a telemetry-track media time (µs) to logger time (ms, XRK timecode units)."""
        return (media_us + self._offset_at(media_us)) / 1000.0

    def logger_ms_to_media_us(self, logger_ms):
        """Inverse of media_us_to_logger_ms (two fixed-point iterations suffice: the
        offset varies by ~100 ms over 25 min)."""
        logger_us = logger_ms * 1000.0
        media = logger_us - self._offset_at(logger_us)
        media = logger_us - self._offset_at(media)
        return media

    def _empty_edit_ms(self):
        if self.telemetry_track is None:
            raise ValueError("no telemetry track")
        return self.telemetry_track.empty_edit_ms()

    def telemetry_media_us_to_movie_ms(self, media_us):
        """Telemetry media time -> movie (presentation) time, applying the elst empty edit."""
        return media_us / 1000.0 + self._empty_edit_ms()

    def movie_ms_to_logger_ms(self, movie_ms):
        """Movie (presentation) time in ms -> logger time in ms."""
        return self.media_us_to_logger_ms((movie_ms - self._empty_edit_ms()) * 1000.0)

    def logger_ms_to_movie_ms(self, logger_ms):
        """Logger time in ms -> movie (presentation) time in ms."""
        return self.logger_ms_to_media_us(logger_ms) / 1000.0 + self._empty_edit_ms()

    # --- identity ---------------------------------------------------------

    def _first_string(self, token):
        msgs = self.telemetry.find_nested_messages(token) if self.telemetry else []
        for m in msgs:
            if isinstance(m.payload, str):
                return m.payload
        return None

    def camera_identity(self):
        """Identity of the CAMERA (not the logger) from the embedded SRC message.

        The SRC payload is an EmbeddedIDNPayload whose logger_id is the camera's
        own hardware serial (matches <hwserial> in the XML tail).
        """
        if self.telemetry is None:
            return None
        for m in self.telemetry.find_nested_messages("SRC"):
            p = m.payload
            if p is not None and hasattr(p, "logger_id"):
                return {"model_id": p.model_id, "serial": p.logger_id}
            try:
                p = EmbeddedIDNPayload.parse(m.raw_payload)
                return {"model_id": p.model_id, "serial": p.logger_id}
            except ConstructError:
                return None
        return None

    def track_info(self):
        """TRK payload (venue name + start/finish coordinates) from the stream."""
        if self.telemetry is None:
            return None
        for m in self.telemetry.find_nested_messages("TRK"):
            p = m.payload
            if p is None or not hasattr(p, "name"):
                try:
                    p = TRKPayload.parse(m.raw_payload)
                except ConstructError:
                    return None
            return {"name": p.name, "sf_lat": p.sf_lat, "sf_long": p.sf_long}
        return None

    def link_keys(self):
        """Fields that identify the logger session this MP4 belongs to.

        Ranked by strength (see spec/docs/mp4_format.md §8):
          laps      exact LAP list (lap_num, duration, end_time) — byte-identical in the XRK
          gps       first/last (week, iTOW, timecode) — identical fixes exist in the XRK
          track     TRK name and start/finish coordinates
          date/time TMD (MM/DD/YYYY) and TMT (HH:MM:SS, **UTC** in the MP4)
          camera    camera serial/model (NOT present in the XRK)
        """
        tel = self.telemetry
        laps = [(lap["lap_num"], lap["duration"], lap["end_time"]) for lap in tel.get_laps()]
        gps = tel.get_gps_samples()

        def _fix(s):
            return {"week": s.week, "iTOW": s.iTOW, "timecode": s.timecode}

        gi = self.meta_data.get("generic_info", {})
        return {
            "laps": laps,
            "gps_count": len(gps),
            "gps_first": _fix(gps[0]) if gps else None,
            "gps_last": _fix(gps[-1]) if gps else None,
            "track": self.track_info(),
            "date": self._first_string("TMD"),
            "time_utc": self._first_string("TMT"),
            "camera": self.camera_identity(),
            "meta": {
                "track_name": gi.get("track_name"),
                "date": gi.get("date"),
                "hour": gi.get("hour"),
                "nlap": gi.get("nlap"),
                "best": gi.get("best"),
                "nbest": gi.get("nbest"),
                "hwserial": gi.get("hwserial"),
            },
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def read_samples(f, track):
    """Read every aimd sample of ``track``; return ([AimdSampleInfo], [payload bytes])."""
    infos = []
    payloads = []
    for i, (off, sz) in enumerate(zip(track.offsets, track.sizes)):
        f.seek(off)
        raw = f.read(sz)
        hdr = AimdSampleHeader.parse(raw[:AIMD_HEADER_SIZE])
        if hdr.payload_length != sz - AIMD_HEADER_SIZE:
            raise ValueError(f"sample {i}: payload_length {hdr.payload_length} != {sz - 66}")
        infos.append(
            AimdSampleInfo(
                index=i,
                offset=off,
                size=sz,
                dts=track.dts[i] if i < len(track.dts) else -1,
                logger_time_us=hdr.logger_time_us,
                media_time_us=hdr.media_time_us,
                payload_length=hdr.payload_length,
            )
        )
        payloads.append(raw[AIMD_HEADER_SIZE:])
    return infos, payloads


def parse_telemetry(f, track, meta_tail_offset=None, file_size=None, parse_payload=True, path=None):
    """Parse the AIM telemetry of an open MP4 given its telemetry ``TrackSamples``.

    This is the container-independent entry point. ``meta_tail_offset`` is the
    offset of the first byte after the last well-formed top-level box (the
    aim_meta_data pseudo-box starts there); pass None to skip the tail.

    Args:
        f: open binary file object
        track: TrackSamples for the 'aimd' track (see TrackSamples.from_isobmff)
        meta_tail_offset: where the trailing pseudo-box starts, or None
        file_size: total file size (needed with meta_tail_offset)
        parse_payload: if False, sample headers are read but the XRK stream is
            not parsed (fast; for inspection tools)
        path: recorded on the result for reference

    Returns:
        Mp4ParseResult
    """
    if file_size is None:
        f.seek(0, 2)
        file_size = f.tell()
    res = Mp4ParseResult(path=Path(path) if path else None, file_size=file_size)
    res.telemetry_track = track
    if meta_tail_offset is not None:
        res.meta_data_raw = read_meta_tail(f, meta_tail_offset, file_size)
        if res.meta_data_raw is not None:
            res.meta_data = parse_aim_meta_data_xml(res.meta_data_raw)
    res.samples, payloads = read_samples(f, track)
    res._clock_table = sorted(
        (s.media_time_us, s.logger_time_us) for s in res.samples if s.index > 0
    )
    if parse_payload:
        res.telemetry = parse_xrk_bytes(b"".join(payloads))
    return res


def parse_mp4_file(filepath, parse_telemetry_payload=True):
    """Parse a SmartyCam MP4 with the built-in container layer (``spec.isobmff``).

    Args:
        filepath: path to the .MP4
        parse_telemetry_payload: if False, sample headers are read but the XRK
            stream is not parsed.

    Returns:
        Mp4ParseResult (with ``movie`` set to the parsed container)
    """
    path = Path(filepath)
    size = path.stat().st_size
    with open(path, "rb") as f:
        movie = isobmff.parse_movie(f, size, path)
        track = find_telemetry_track(movie.tracks)
        if track is None:
            res = Mp4ParseResult(path=path, file_size=size, movie=movie)
            res.meta_data_raw = read_meta_tail(f, movie.trailing_offset, size)
            if res.meta_data_raw is not None:
                res.meta_data = parse_aim_meta_data_xml(res.meta_data_raw)
            return res
        res = parse_telemetry(
            f,
            TrackSamples.from_isobmff(track, movie.timescale),
            meta_tail_offset=movie.trailing_offset,
            file_size=size,
            parse_payload=parse_telemetry_payload,
            path=path,
        )
        res.movie = movie
        return res


# ---------------------------------------------------------------------------
# Linking an MP4 to an XRK/XRZ ParseResult
# ---------------------------------------------------------------------------


def gps_fix_utc(week, itow_ms):
    """UTC datetime of a GPS fix from (week, iTOW ms), applying the leap-second offset."""
    return GPS_EPOCH + datetime.timedelta(
        weeks=week, milliseconds=itow_ms, seconds=-GPS_UTC_LEAP_SECONDS
    )


def link_report(mp4, xrk):
    """Compare the identity keys of an Mp4ParseResult with an XRK ParseResult.

    Returns a dict of evidence; ``matched`` is True when the LAP lists are
    identical or, failing that, when shared GPS fixes agree on timecode and
    position and the track name matches.
    """
    mp4_laps = [
        (lap["lap_num"], lap["duration"], lap["end_time"]) for lap in mp4.telemetry.get_laps()
    ]
    xrk_laps = [(lap["lap_num"], lap["duration"], lap["end_time"]) for lap in xrk.get_laps()]
    laps_identical = bool(mp4_laps) and mp4_laps == xrk_laps
    common_laps = len(set(mp4_laps) & set(xrk_laps))

    mg = {(s.week, s.iTOW): s for s in mp4.telemetry.get_gps_samples()}
    xg = {(s.week, s.iTOW): s for s in xrk.get_gps_samples()}
    shared = set(mg) & set(xg)
    same = sum(
        1
        for k in shared
        if mg[k].timecode == xg[k].timecode
        and (mg[k].ecefX, mg[k].ecefY, mg[k].ecefZ) == (xg[k].ecefX, xg[k].ecefY, xg[k].ecefZ)
    )

    mt = mp4.track_info()
    xt = None
    for m in xrk.find_nested_messages("TRK"):
        p = m.payload
        if p is not None and hasattr(p, "name"):
            xt = {"name": p.name, "sf_lat": p.sf_lat, "sf_long": p.sf_long}
            break
    track_match = mt is not None and xt is not None and mt == xt

    def _str(res, tok):
        for m in res.find_nested_messages(tok):
            if isinstance(m.payload, str) and m.payload:
                return m.payload
        return None

    gps_match = len(shared) > 0 and same == len(shared)
    return {
        "laps_identical": laps_identical,
        "common_laps": common_laps,
        "mp4_laps": len(mp4_laps),
        "xrk_laps": len(xrk_laps),
        "gps_shared": len(shared),
        "gps_shared_identical": same,
        "track_match": track_match,
        "mp4_track": mt,
        "xrk_track": xt,
        "mp4_date": _str(mp4.telemetry, "TMD"),
        "xrk_date": _str(xrk, "TMD"),
        "mp4_time_utc": _str(mp4.telemetry, "TMT"),
        "xrk_time_local": _str(xrk, "TMT"),
        "matched": laps_identical or (gps_match and track_match),
    }
