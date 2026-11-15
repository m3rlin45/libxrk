"""Generic ISO BMFF (MP4) container layer — Construct-based, lazy, offset-only.

This module knows nothing about AIM. It provides exactly what a timed-metadata
consumer needs from ISO/IEC 14496-12: a lazy box walker that never loads
``mdat``, Construct structs for the movie/track/sample-table boxes, and the
standard procedure that turns ``stsc``/``stsz``/``stco``/``stts`` into
per-sample file offsets and decode timestamps.

It is intentionally small. The AIM-specific layer (``spec/mp4_format.py``)
consumes a ``Movie`` from here through a narrow adapter, so it can be mated to
any other MP4 parser that can supply the same facts about a track (see
``spec.mp4_format.TrackSamples``).

Deviation from the standard handled here: ``iter_boxes`` stops, without
raising, at the first box whose declared size runs past the end of the range.
``Movie.trailing_offset`` then points at the unparsed remainder so a caller can
inspect it (SmartyCam files end with a malformed box).
"""

import datetime
from dataclasses import dataclass, field
from pathlib import Path

from construct import (
    Array,
    Bytes,
    Computed,
    ConstructError,
    CString,
    GreedyBytes,
    If,
    IfThenElse,
    Int8ul,
    Int16sb,
    Int16ub,
    Int32sb,
    Int32ub,
    Int64sb,
    Int64ub,
    Struct,
    this,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ISO BMFF epoch for mvhd/tkhd/mdhd timestamps (seconds since 1904-01-01 UTC,
# though many writers use local time).
MP4_EPOCH = datetime.datetime(1904, 1, 1)

# Boxes whose payload is a sequence of child boxes.
CONTAINER_BOXES = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"dinf", b"mvex", b"tref"}
)
# FullBoxes (4 bytes version/flags) that then hold child boxes.
FULLBOX_CONTAINERS = frozenset({b"meta"})

# ---------------------------------------------------------------------------
# Box header — ISO/IEC 14496-12 §4.2
# ---------------------------------------------------------------------------

# size == 1 -> 64-bit largesize follows; size == 0 -> box extends to end of file.
BoxHeader = Struct(
    "size32" / Int32ub,  # [0:4]   box size incl. header (1 = largesize, 0 = to EOF)
    "type" / Bytes(4),  # [4:8]   four-character code
    "largesize" / If(this.size32 == 1, Int64ub),  # [8:16]  only when size32 == 1
    "header_size" / Computed(lambda ctx: 16 if ctx.size32 == 1 else 8),
)


def _fullbox(*fields):
    """FullBox = version (uint8) + flags (24 bits) + fields."""
    return Struct("version" / Int8ul, "flags" / Bytes(3), *fields)


def _time_field(name):
    """mvhd/tkhd/mdhd timestamps are 32-bit in version 0 and 64-bit in version 1."""
    return name / IfThenElse(this.version == 1, Int64ub, Int32ub)


# ---------------------------------------------------------------------------
# Box payloads
# ---------------------------------------------------------------------------

# mvhd — §8.2.2
MovieHeader = _fullbox(
    _time_field("creation_time"),
    _time_field("modification_time"),
    "timescale" / Int32ub,  # movie time units per second
    _time_field("duration"),  # in movie timescale
    "_rest" / GreedyBytes,  # rate, volume, matrix, next_track_ID
)

# tkhd — §8.3.2
TrackHeader = _fullbox(
    _time_field("creation_time"),
    _time_field("modification_time"),
    "track_id" / Int32ub,
    "_reserved" / Int32ub,
    _time_field("duration"),  # in MOVIE timescale
    "_rest" / GreedyBytes,  # layer, alternate_group, volume, matrix, width, height
)

# mdhd — §8.4.2
MediaHeader = _fullbox(
    _time_field("creation_time"),
    _time_field("modification_time"),
    "timescale" / Int32ub,  # media time units per second
    _time_field("duration"),  # in media timescale
    "language" / Int16ub,
    "quality" / Int16ub,
)

# hdlr — §8.4.3
HandlerReference = _fullbox(
    "pre_defined" / Int32ub,
    "handler_type" / Bytes(4),  # 'vide', 'soun', 'meta', ...
    "_reserved" / Bytes(12),
    "name" / CString("utf8"),
)

# elst — §8.6.6
EditListEntry = Struct(
    "segment_duration" / IfThenElse(this._.version == 1, Int64ub, Int32ub),  # movie timescale
    "media_time" / IfThenElse(this._.version == 1, Int64sb, Int32sb),  # media timescale; -1 = empty
    "media_rate_integer" / Int16sb,
    "media_rate_fraction" / Int16sb,
)
EditList = _fullbox(
    "entry_count" / Int32ub,
    "entries" / Array(this.entry_count, EditListEntry),
)

# tref child — §8.3.3 (a whole box: size, type, track IDs)
TrackReferenceType = Struct(
    "size" / Int32ub,
    "type" / Bytes(4),
    "track_ids" / Array((this.size - 8) // 4, Int32ub),
)

# stsd — §8.5.2. Generic SampleEntry prefix only; codec-specific data stays raw.
SampleEntry = Struct(
    "size" / Int32ub,
    "format" / Bytes(4),
    "_reserved" / Bytes(6),
    "data_reference_index" / Int16ub,
    "_rest" / Bytes(this.size - 16),
)
SampleDescription = _fullbox(
    "entry_count" / Int32ub,
    "entries" / Array(this.entry_count, SampleEntry),
)

# stts — §8.6.1.2
TimeToSample = _fullbox(
    "entry_count" / Int32ub,
    "entries"
    / Array(
        this.entry_count,
        Struct("sample_count" / Int32ub, "sample_delta" / Int32ub),
    ),
)

# stsc — §8.7.4
SampleToChunk = _fullbox(
    "entry_count" / Int32ub,
    "entries"
    / Array(
        this.entry_count,
        Struct(
            "first_chunk" / Int32ub,  # 1-based
            "samples_per_chunk" / Int32ub,
            "sample_description_index" / Int32ub,
        ),
    ),
)

# stsz — §8.7.3.2
SampleSize = _fullbox(
    "sample_size" / Int32ub,  # 0 -> per-sample sizes follow
    "sample_count" / Int32ub,
    "entry_sizes" / If(this.sample_size == 0, Array(this.sample_count, Int32ub)),
)

# stco / co64 — §8.7.5
ChunkOffset = _fullbox(
    "entry_count" / Int32ub,
    "offsets" / Array(this.entry_count, Int32ub),
)
ChunkOffset64 = _fullbox(
    "entry_count" / Int32ub,
    "offsets" / Array(this.entry_count, Int64ub),
)

# ---------------------------------------------------------------------------
# Located boxes and lazy walking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Box:
    """A box located in the file (payload not loaded)."""

    offset: int
    size: int
    type: bytes
    header_size: int

    @property
    def payload_offset(self):
        return self.offset + self.header_size

    @property
    def end(self):
        return self.offset + self.size


def read_box_header(f, offset, end):
    """Return (Box, malformed) for the box starting at offset, or None at end of range."""
    if offset + 8 > end:
        return None
    f.seek(offset)
    hdr = f.read(16)
    if len(hdr) < 8:
        return None
    try:
        h = BoxHeader.parse(hdr)
    except ConstructError:
        return None
    if h.size32 == 1:
        size = h.largesize
    elif h.size32 == 0:
        size = end - offset
    else:
        size = h.size32
    box = Box(offset=offset, size=size, type=bytes(h.type), header_size=h.header_size)
    malformed = size < h.header_size or offset + size > end
    return box, malformed


def iter_boxes(f, start, end):
    """Yield Box objects for the sequence of boxes in [start, end).

    Stops (without raising) at a box whose declared size runs past ``end``.
    """
    offset = start
    while offset < end:
        r = read_box_header(f, offset, end)
        if r is None:
            return
        box, malformed = r
        if malformed:
            return
        yield box
        offset = box.end


def walk_boxes(f, start, end, depth=0):
    """Yield (depth, Box) for the full tree under [start, end)."""
    for box in iter_boxes(f, start, end):
        yield depth, box
        if box.type in CONTAINER_BOXES or box.type in FULLBOX_CONTAINERS:
            yield from walk_boxes(f, children_offset(box), box.end, depth + 1)


def children_offset(box):
    """Offset of the first child box inside a container box."""
    return box.payload_offset + (4 if box.type in FULLBOX_CONTAINERS else 0)


def children(f, box):
    """Child boxes of a container box."""
    return list(iter_boxes(f, children_offset(box), box.end))


def find_child(f, box, type_):
    """First child box of the given type, or None."""
    for c in children(f, box):
        if c.type == type_:
            return c
    return None


def read_payload(f, box):
    """Read a box's payload (everything after its header)."""
    f.seek(box.payload_offset)
    return f.read(box.size - box.header_size)


def read_raw(f, box):
    """Read a whole box including its header."""
    f.seek(box.offset)
    return f.read(box.size)


# ---------------------------------------------------------------------------
# Tracks and movies
# ---------------------------------------------------------------------------


@dataclass
class Track:
    """The facts about one track that a sample consumer needs."""

    track_id: int
    handler_type: bytes
    handler_name: str
    media_timescale: int
    media_duration: int
    track_duration: int  # movie timescale
    sample_format: bytes | None  # format of the first stsd entry
    sample_entry_raw: bytes | None  # raw bytes of the first stsd entry
    edit_list: list  # EditListEntry containers
    track_references: list  # [(type, [track_ids])]
    stts: list  # [(sample_count, sample_delta)]
    stsc: list  # [(first_chunk, samples_per_chunk, sample_description_index)]
    sample_sizes: list  # per-sample sizes
    chunk_offsets: list  # absolute file offsets of chunks
    sample_count: int

    def sample_offsets(self):
        """Absolute file offset of every sample, resolved from stsc/stsz/stco (§8.7.4)."""
        offsets = []
        n_chunks = len(self.chunk_offsets)
        sample = 0
        for i, (first_chunk, per_chunk, _sdi) in enumerate(self.stsc):
            last_chunk = self.stsc[i + 1][0] - 1 if i + 1 < len(self.stsc) else n_chunks
            for chunk in range(first_chunk, last_chunk + 1):
                off = self.chunk_offsets[chunk - 1]
                for _ in range(per_chunk):
                    if sample >= self.sample_count:
                        return offsets
                    offsets.append(off)
                    off += self.sample_sizes[sample]
                    sample += 1
        return offsets

    def sample_dts(self):
        """Decoding timestamp of every sample in media timescale units (§8.6.1.2)."""
        dts = []
        t = 0
        for count, delta in self.stts:
            for _ in range(count):
                dts.append(t)
                t += delta
        return dts

    def empty_edit_duration(self):
        """Movie-timescale duration of a leading empty edit (media_time == -1), else 0."""
        if self.edit_list and self.edit_list[0].media_time == -1:
            return self.edit_list[0].segment_duration
        return 0


@dataclass
class Movie:
    """A parsed moov plus the location of the top-level boxes."""

    path: Path | None
    file_size: int
    top_level_boxes: list = field(default_factory=list)  # [Box]
    timescale: int = 0
    duration: int = 0
    creation_time: datetime.datetime | None = None  # naive; writer-defined zone
    tracks: list = field(default_factory=list)  # [Track]
    # Offset of the first byte after the last well-formed top-level box
    # (== file_size for a clean file). Anything after it is not ISO BMFF.
    trailing_offset: int = 0

    def track_by_id(self, track_id):
        return next((t for t in self.tracks if t.track_id == track_id), None)


def parse_track(f, trak):
    """Parse one trak box into a Track."""
    tkhd = TrackHeader.parse(read_payload(f, find_child(f, trak, b"tkhd")))
    mdia = find_child(f, trak, b"mdia")
    mdhd = MediaHeader.parse(read_payload(f, find_child(f, mdia, b"mdhd")))
    hdlr = HandlerReference.parse(read_payload(f, find_child(f, mdia, b"hdlr")))
    minf = find_child(f, mdia, b"minf")
    stbl = find_child(f, minf, b"stbl")

    edit_list = []
    edts = find_child(f, trak, b"edts")
    if edts is not None:
        elst = find_child(f, edts, b"elst")
        if elst is not None:
            edit_list = list(EditList.parse(read_payload(f, elst)).entries)

    track_references = []
    tref = find_child(f, trak, b"tref")
    if tref is not None:
        for ref in children(f, tref):
            r = TrackReferenceType.parse(read_raw(f, ref))
            track_references.append((bytes(r.type), list(r.track_ids)))

    stsd_payload = read_payload(f, find_child(f, stbl, b"stsd"))
    stsd = SampleDescription.parse(stsd_payload)
    entry = stsd.entries[0] if stsd.entry_count else None
    stts = TimeToSample.parse(read_payload(f, find_child(f, stbl, b"stts")))
    stsc = SampleToChunk.parse(read_payload(f, find_child(f, stbl, b"stsc")))
    stsz = SampleSize.parse(read_payload(f, find_child(f, stbl, b"stsz")))
    stco = find_child(f, stbl, b"stco")
    if stco is not None:
        offsets = list(ChunkOffset.parse(read_payload(f, stco)).offsets)
    else:
        offsets = list(ChunkOffset64.parse(read_payload(f, find_child(f, stbl, b"co64"))).offsets)
    sizes = (
        list(stsz.entry_sizes) if stsz.sample_size == 0 else [stsz.sample_size] * stsz.sample_count
    )

    return Track(
        track_id=tkhd.track_id,
        handler_type=bytes(hdlr.handler_type),
        handler_name=hdlr.name,
        media_timescale=mdhd.timescale,
        media_duration=mdhd.duration,
        track_duration=tkhd.duration,
        sample_format=bytes(entry.format) if entry is not None else None,
        sample_entry_raw=stsd_payload[8 : 8 + entry.size] if entry is not None else None,
        edit_list=edit_list,
        track_references=track_references,
        stts=[(e.sample_count, e.sample_delta) for e in stts.entries],
        stsc=[
            (e.first_chunk, e.samples_per_chunk, e.sample_description_index) for e in stsc.entries
        ],
        sample_sizes=sizes,
        chunk_offsets=offsets,
        sample_count=stsz.sample_count,
    )


def parse_movie(f, file_size, path=None):
    """Parse the top-level boxes and the moov of an open file object."""
    boxes = list(iter_boxes(f, 0, file_size))
    moov = next((b for b in boxes if b.type == b"moov"), None)
    if moov is None:
        raise ValueError("no moov box")
    mvhd = MovieHeader.parse(read_payload(f, find_child(f, moov, b"mvhd")))
    movie = Movie(
        path=Path(path) if path is not None else None,
        file_size=file_size,
        top_level_boxes=boxes,
        timescale=mvhd.timescale,
        duration=mvhd.duration,
        creation_time=MP4_EPOCH + datetime.timedelta(seconds=mvhd.creation_time),
        trailing_offset=boxes[-1].end if boxes else 0,
    )
    for trak in children(f, moov):
        if trak.type == b"trak":
            movie.tracks.append(parse_track(f, trak))
    return movie


def read_movie(filepath):
    """Parse the container structure of an MP4 file (payloads are not loaded)."""
    path = Path(filepath)
    with open(path, "rb") as f:
        return parse_movie(f, path.stat().st_size, path)
