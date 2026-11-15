#!/usr/bin/env python3
"""Compare the telemetry embedded in a SmartyCam MP4 with the XRK/XRZ of the same session.

Prints the evidence behind spec/docs/mp4_format.md §5 and §8: token tallies, identity
strings, LAP equality, GPS shared-fix equality, channel usage, and the clock offset.

Usage:
    uv run python scripts/investigate_mp4/compare_with_xrk.py <file.MP4> <file.xrk|xrz>
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from spec.mp4_format import gps_fix_utc, link_report, parse_mp4_file  # noqa: E402
from spec.xrk_format import parse_xrk_file  # noqa: E402

IDENTITY_TOKENS = (
    "RCR",
    "VEH",
    "CMP",
    "VTY",
    "NDV",
    "TMD",
    "TMT",
    "SRC",
    "TRK",
    "idn",
    "iSLV",
    "GPSR",
    "NTE",
)


def tally(res, label):
    hm = res.header_messages()
    print(f"[{label}] header tokens:", dict(Counter(m.token_str() for m in hm).most_common()))
    print(f"[{label}] data messages:", dict(Counter(m.msg_type for m in res.data_messages())))
    print(f"[{label}] CHS (incl. nested):", len(res.find_nested_messages("CHS")))


def identity(res, label):
    for tok in IDENTITY_TOKENS:
        msgs = res.find_nested_messages(tok)
        for m in msgs[:3]:
            p = m.payload
            if hasattr(p, "items") and not isinstance(p, (str, bytes)):
                p = {k: v for k, v in dict(p).items() if not k.startswith("_")}
            print(f"[{label}] {tok!r:7} v{m.version} len={len(m.raw_payload)} -> {p!r}")


def channel_usage(res, label, limit=60):
    names = res.channel_display_names()
    cnt = Counter()
    tmin, tmax = None, None
    for m in res.data_messages():
        if m.msg_type != "S":
            continue
        cnt[m.parsed["channel_index"]] += 1
        tc = m.parsed["timecode"]
        tmin = tc if tmin is None else min(tmin, tc)
        tmax = tc if tmax is None else max(tmax, tc)
    span = (tmax - tmin) / 1000.0 if tmin is not None and tmax != tmin else 1.0
    print(f"[{label}] (S timecode range {tmin}..{tmax}; channels used: {len(cnt)}")
    for idx, c in sorted(cnt.items())[:limit]:
        print(f"[{label}]   idx {idx:3d} {str(names.get(idx)):24s} n={c:8d} ~{c / span:5.1f} Hz")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    mp4 = parse_mp4_file(argv[1])
    xrk = parse_xrk_file(argv[2])
    tel = mp4.telemetry

    print("=== MP4 container")
    print("creation (camera local):", mp4.creation_time, " movie timescale:", mp4.movie_timescale)
    print("meta tail generic_info:", mp4.meta_data.get("generic_info"))
    print("meta tail hw_info:", mp4.meta_data.get("hw_info"))

    print("\n=== Token tallies")
    tally(tel, "MP4")
    tally(xrk, "XRK")

    print("\n=== Identity messages")
    identity(tel, "MP4")
    identity(xrk, "XRK")
    print("camera identity (MP4 SRC):", mp4.camera_identity())

    print("\n=== LAP messages")
    for a, b in zip(tel.get_laps(), xrk.get_laps()):
        print(
            f"  lap {a['lap_num']:2d}: MP4 dur={a['duration']} end={a['end_time']}  "
            f"XRK dur={b['duration']} end={b['end_time']}  equal={a == b}"
        )

    print("\n=== GPS")
    mg = tel.get_gps_samples()
    xg = xrk.get_gps_samples()
    print(
        f"MP4 fixes={len(mg)} first tc={mg[0].timecode} iTOW={mg[0].iTOW} week={mg[0].week} "
        f"UTC={gps_fix_utc(mg[0].week, mg[0].iTOW)}"
    )
    print(
        f"XRK fixes={len(xg)} first tc={xg[0].timecode} iTOW={xg[0].iTOW} week={xg[0].week} "
        f"UTC={gps_fix_utc(xg[0].week, xg[0].iTOW)}"
    )
    mi = {s.iTOW: s for s in mg}
    xi = {s.iTOW: s for s in xg}
    shared = sorted(set(mi) & set(xi))
    same_tc = sum(1 for k in shared if mi[k].timecode == xi[k].timecode)
    same_pos = sum(
        1
        for k in shared
        if (mi[k].ecefX, mi[k].ecefY, mi[k].ecefZ) == (xi[k].ecefX, xi[k].ecefY, xi[k].ecefZ)
    )
    print(f"shared iTOW: {len(shared)}  same timecode: {same_tc}  same ECEF: {same_pos}")
    diff_fields = Counter()
    for k in shared:
        for fld in ("numSV", "pAcc", "sAcc", "pDOP", "flags", "gpsFix", "reserved2"):
            if mi[k][fld] != xi[k][fld]:
                diff_fields[fld] += 1
    print("fields that differ on shared fixes:", dict(diff_fields))

    print("\n=== (S channel usage")
    channel_usage(tel, "MP4")

    print("\n=== Clock mapping")
    tbl = mp4.clock_offset_table()
    print(
        f"logger-media offset: first={tbl[0][1] / 1000:.3f} ms  last={tbl[-1][1] / 1000:.3f} ms  "
        f"samples={len(tbl)}"
    )
    for lap in tel.get_laps()[:3]:
        end = lap["end_time"]
        print(
            f"  lap {lap['lap_num']} end logger={end} ms -> telemetry media={mp4.logger_ms_to_media_us(end) / 1000:.1f} ms "
            f"-> movie={mp4.logger_ms_to_movie_ms(end):.1f} ms"
        )

    print("\n=== Link report")
    for k, v in link_report(mp4, xrk).items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
