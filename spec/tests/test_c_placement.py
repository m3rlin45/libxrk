"""V2/V3 (c sample placement: the spec's rule against both backends (issue #151).

The rule is ``C_SAMPLE_PLACEMENT`` and ``_place_c_variants`` in
``spec/xrk_format.py`` (companion.md section 3, "Expansion samples"). The
fixtures only show it on well-behaved streams, so these tests build small
streams by hand: a CNF with one or two expansion CHS entries followed by V1,
V2 and V3 (c messages. Each case states the samples the rule must produce,
checks the spec against that, and checks both backends against the spec, on
absolute session timecodes.

Every stream ends on a V2. The Cython backend skips a 10-byte message that
ends exactly at the end of the input (a V3 can), which is a framing quirk and
not part of the placement rule.
"""

import struct

import numpy as np
import pytest

from spec.tests.conftest import _BACKENDS
from spec.xrk_format import (
    C_SAMPLE_PLACEMENT,
    C_SAMPLE_STEP_MS,
    CHSPayload,
    build_header_frame,
    parse_xrk_bytes,
)

# channel_fields as on the KK-SII fixtures: a (base, +4) pair and two orphans.
BASE, PLUS4 = 0x1290, 0x1294
ORPHAN_PLUS4_SIDE = 0x12B4  # low nibble 4: the "+4" row
ORPHAN_BASE_SIDE = 0x12B0  # low nibble 0: the "base" row

SHOCK, ACCEL = 1, 2  # CHS indices


def _chs(index, name, period_us):
    """A CHS frame for an fp16 expansion channel (decoder 20, source 1, hardware_id != 0)."""
    chs = CHSPayload.parse(bytes(112))
    chs.update(
        dict(
            index=index,
            hardware_id=1,
            source_channel_id=index,
            unit_type_byte=12,  # mm
            source_type=1,
            decoder_type=20,
            short_name=name[:7].encode().ljust(8, b"\0"),
            long_name=name.encode().ljust(24, b"\0"),
            sample_period_us=period_us,
            data_size=2,
        )
    )
    return build_header_frame("CHS", CHSPayload.build(chs))


def _f16(value):
    return np.float16(value).tobytes()


def _v1(channel, tc, value):
    return b"(c\x00" + struct.pack("<HBBi", channel << 3 | 4, 0x84, 6, tc) + _f16(value) + b")"


def _v2(channel_field, tc, first, second):
    header = struct.pack("<HBBi", channel_field, 0x84, 8, tc)
    return b"(c\x00" + header + _f16(first) + _f16(second) + b")"


def _v3(channel_field, value):
    return b"(c\x01" + struct.pack("<HBB", channel_field, 0x84, 2) + _f16(value) + b")"


def _stream(messages, shock_period_us=2000):
    """A paired-class channel "Shock" and an orphan-class channel "Accel", then the messages."""
    cnf = _chs(SHOCK, "Shock", shock_period_us) + _chs(ACCEL, "Accel", 10000)
    return build_header_frame("CNF", cnf) + b"".join(messages)


# name -> (messages, stream options, {channel: [(logger time, value)] in stream order})
CASES = {
    # One of each table row. V2(base) 1010: [0]@1010, [1]@1006. V2(+4) 1016:
    # [0]@1014, [1]@1012. The V3 follows a V2(+4), so it sits at 1016 + 2.
    # V2(base) 1020: [0]@1020, [1]@1016.
    "each_table_row": (
        [_v2(BASE, 1010, 1, 2), _v2(PLUS4, 1016, 3, 4), _v3(PLUS4, 5), _v2(BASE, 1020, 6, 7)],
        {},
        {SHOCK: [(1006, 2), (1010, 1), (1012, 4), (1014, 3), (1016, 7), (1018, 5), (1020, 6)]},
    ),
    # V3 (priority 2) beats V2(+4) (priority 1): the V3 at 1012 + 2 = 1014 keeps
    # its slot when the next V2(+4) puts its [0] there.
    "v3_beats_v2_plus4": (
        [_v2(PLUS4, 1012, 3, 4), _v3(PLUS4, 5), _v2(PLUS4, 1016, 6, 7), _v2(BASE, 1030, 8, 9)],
        {},
        {SHOCK: [(1008, 4), (1010, 3), (1012, 7), (1014, 5), (1026, 9), (1030, 8)]},
    ),
    # V2(base) (priority 3) beats both: at 1010 over V2(+4)[0], at 1014 over
    # the V3. Equal priorities keep the earlier message: the second and third
    # V2(base) lose 1010 and 1014, the last V2(+4) loses 1008.
    "v2_base_beats_all_and_first_wins_ties": (
        [
            _v2(BASE, 1010, 1, 2),
            _v2(PLUS4, 1012, 3, 4),
            _v3(PLUS4, 5),
            _v2(BASE, 1014, 6, 7),
            _v2(BASE, 1014, 8, 9),
            _v2(PLUS4, 1012, 10, 11),
        ],
        {},
        {SHOCK: [(1006, 2), (1008, 4), (1010, 1), (1014, 6)]},
    ),
    # V1 messages of the channel are accumulated during the parse, so they
    # come first whatever their file position, and V2/V3 samples at or before
    # the last V1 timecode (1011) are dropped.
    "v1_samples_come_first": (
        [
            _v1(SHOCK, 1008, 50),
            _v2(BASE, 1010, 1, 2),
            _v2(PLUS4, 1016, 3, 4),
            _v1(SHOCK, 1011, 51),
            _v2(BASE, 1020, 6, 7),
        ],
        {},
        {SHOCK: [(1008, 50), (1011, 51), (1012, 4), (1014, 3), (1016, 7), (1020, 6)]},
    ),
    # An orphan field follows the row of its side, with the same fixed 2 ms
    # step although the channel's period is 10 ms.
    "orphan_on_the_plus4_side": (
        [_v2(ORPHAN_PLUS4_SIDE, 1010, 1, 2), _v2(ORPHAN_PLUS4_SIDE, 1030, 3, 4)],
        {},
        {ACCEL: [(1006, 2), (1008, 1), (1026, 4), (1028, 3)]},
    ),
    "orphan_on_the_base_side": (
        [_v2(ORPHAN_BASE_SIDE, 1010, 1, 2), _v2(ORPHAN_BASE_SIDE, 1030, 3, 4)],
        {},
        {ACCEL: [(1006, 2), (1010, 1), (1026, 4), (1030, 3)]},
    ),
    # A V3 on the base side of a pair carries no sample.
    "v3_on_the_base_side": (
        [_v2(BASE, 1010, 1, 2), _v2(PLUS4, 1016, 3, 4), _v3(BASE, 5), _v2(BASE, 1030, 6, 7)],
        {},
        {SHOCK: [(1006, 2), (1010, 1), (1012, 4), (1014, 3), (1026, 7), (1030, 6)]},
    ),
    # The step is a constant: with a 1 ms channel period the V3s still sit
    # 2 ms from their anchor (1010 - 2, then 1016 + 2).
    "step_is_not_the_channel_period": (
        [
            _v2(BASE, 1010, 1, 2),
            _v3(PLUS4, 5),
            _v2(PLUS4, 1016, 3, 4),
            _v3(PLUS4, 8),
            _v2(BASE, 1030, 6, 7),
        ],
        {"shock_period_us": 1000},
        {
            SHOCK: [
                (1006, 2),
                (1008, 5),
                (1010, 1),
                (1012, 4),
                (1014, 3),
                (1018, 8),
                (1026, 7),
                (1030, 6),
            ]
        },
    ),
    # Samples placed before time 0 are dropped (-1 twice here).
    "samples_before_time_zero": (
        [_v2(BASE, 3, 1, 2), _v2(PLUS4, 3, 3, 4), _v3(PLUS4, 5), _v2(BASE, 13, 6, 7)],
        {},
        {SHOCK: [(1, 3), (3, 1), (5, 5), (9, 7), (13, 6)]},
    ),
}


@pytest.fixture(params=CASES, ids=list(CASES))
def case(request):
    messages, options, expected = CASES[request.param]
    return _stream(messages, **options), expected


def test_placement_table_shape():
    """The table is the rule: fixed 2 ms step, and no row for a V3 on the base side."""
    assert C_SAMPLE_STEP_MS == 2
    assert set(C_SAMPLE_PLACEMENT) == {("V2", "base"), ("V2", "+4"), ("V3", "+4")}
    assert [tuple(p) for p in C_SAMPLE_PLACEMENT["V2", "base"]] == [(0, 0, 3), (1, -4, 3)]
    assert [tuple(p) for p in C_SAMPLE_PLACEMENT["V2", "+4"]] == [(0, -2, 1), (1, -4, 1)]
    assert [tuple(p) for p in C_SAMPLE_PLACEMENT["V3", "+4"]] == [(0, 0, 2)]


def test_spec_places_samples(case):
    """channel_values() and stream_times hold exactly the stated samples."""
    raw, expected = case
    parsed = parse_xrk_bytes(raw)
    values = parsed.channel_values()
    assert set(values) == set(expected)
    for ch, samples in expected.items():
        assert values[ch].tolist() == [float(v) for _, v in samples]
        assert parsed.stream_times[ch].tolist() == [t for t, _ in samples]
    assert parsed.time_offset() == min(samples[0][0] for samples in expected.values())


def test_backends_match_spec(case, backend):
    """Both backends expose the spec's samples at the spec's session times."""
    raw, expected = case
    parsed = parse_xrk_bytes(raw)
    log = _BACKENDS[backend](raw)
    names = parsed.channel_display_names()
    values = parsed.channel_values()
    assert set(log.channels) == {names[ch] for ch in values}
    for ch, spec_values in values.items():
        table = log.channels[names[ch]]
        backend_values = table.column(names[ch]).to_numpy()
        np.testing.assert_array_equal(spec_values.astype(backend_values.dtype), backend_values)
        backend_times = table.column("timecodes").to_numpy()
        np.testing.assert_array_equal(parsed.channel_timecodes()[ch], backend_times)
        # And against the stated times, V1 samples included.
        stated = np.array([t for t, _ in expected[ch]]) - parsed.time_offset()
        np.testing.assert_array_equal(stated, backend_times)
