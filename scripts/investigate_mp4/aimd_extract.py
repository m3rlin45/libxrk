#!/usr/bin/env python3
"""Extract the 'aimd' telemetry track of a SmartyCam MP4 and validate its sample headers.

Writes (into --out-dir, default: alongside nothing — only prints) the concatenated
XRK-grammar payload and a CSV describing every sample so the clock drift between the
video timeline and the logger clock can be studied.

Usage:
    uv run python scripts/investigate_mp4/aimd_extract.py <file.MP4> [--out-dir DIR]

Outputs when --out-dir is given:
    DIR/aimd_payload.bin   all sample payloads back to back (an XRK message stream)
    DIR/aimd_samples.csv   index,offset,size,dts_ms,logger_time_us,media_time_us,payload_length,offset_ms
"""

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from spec.mp4_format import AIMD_HEADER_SIZE, AimdSample, parse_mp4_file  # noqa: E402


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    out_dir = None
    if "--out-dir" in argv:
        out_dir = Path(argv[argv.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)

    res = parse_mp4_file(path, parse_telemetry_payload=False)
    trk = res.telemetry_track
    if trk is None:
        print(
            "no aimd track found; tracks:",
            [(t.track_id, t.handler_type, t.sample_format) for t in res.tracks],
        )
        return 1
    box_track = res.movie.track_by_id(trk.track_id)
    print(
        f"telemetry track id={trk.track_id} timescale={trk.media_timescale} "
        f"duration={box_track.media_duration}"
    )
    print(f"samples={trk.sample_count} chunks={len(box_track.chunk_offsets)} stsc={box_track.stsc}")
    print(f"empty edit = {trk.empty_edit_ticks} movie ticks = {trk.empty_edit_ms():.1f} ms")

    sizes = Counter(s.size for s in res.samples)
    print("sample size histogram (top 8):", sizes.most_common(8))
    big = [s.index for s in res.samples if s.size > 5000]
    print("samples > 5000 bytes:", big)

    # Header invariants, independently of the spec's Checks: round-trip every sample.
    payload = bytearray()
    rows = []
    with open(path, "rb") as f:
        for s in res.samples:
            f.seek(s.offset)
            raw = f.read(s.size)
            parsed = AimdSample.parse(raw)
            assert AimdSample.build(parsed) == raw, f"sample {s.index} does not round-trip"
            assert struct.unpack(">H", raw[:2])[0] == (s.size - 2) & 0xFFFF
            assert raw[2:6] == b"\x40\x00\x00\x00" and raw[6:10] == b"amv0"
            assert raw[26:62] == bytes(36)
            assert struct.unpack("<I", raw[62:66])[0] == s.size - AIMD_HEADER_SIZE
            payload += raw[AIMD_HEADER_SIZE:]
            rows.append(
                (
                    s.index,
                    s.offset,
                    s.size,
                    s.dts,
                    s.logger_time_us,
                    s.media_time_us,
                    s.payload_length,
                    (s.logger_time_us - s.media_time_us) / 1000.0,
                )
            )
    print(f"all {len(res.samples)} sample headers valid and round-trip byte-identically")
    print(f"total payload bytes: {len(payload)}")

    reg = [r for r in rows if r[0] > 0]
    offs = [r[7] for r in reg]
    print(
        f"sample 0 header: logger_us={rows[0][4]} media_us={rows[0][5]} (media value is an outlier)"
    )
    print(
        f"logger-media offset (ms): first={offs[0]:.3f} last={offs[-1]:.3f} "
        f"min={min(offs):.3f} max={max(offs):.3f} drift={offs[-1] - offs[0]:.3f} over "
        f"{(reg[-1][5] - reg[0][5]) / 1e6:.1f} s = {(offs[-1] - offs[0]) / ((reg[-1][5] - reg[0][5]) / 1e3) * 1e6:.1f} ppm"
    )
    err = [r[5] / 1000.0 - r[3] for r in reg]
    print(f"header media_time_us/1000 - stts dts (ms): min={min(err):.3f} max={max(err):.3f}")
    mono = all(a[4] < b[4] and a[5] < b[5] for a, b in zip(reg, reg[1:]))
    print(f"both header clocks strictly increasing over regular samples: {mono}")

    if out_dir is not None:
        (out_dir / "aimd_payload.bin").write_bytes(payload)
        with open(out_dir / "aimd_samples.csv", "w") as fh:
            fh.write(
                "index,offset,size,dts_ms,logger_time_us,media_time_us,payload_length,offset_ms\n"
            )
            for r in rows:
                fh.write(",".join(str(x) for x in r) + "\n")
        print(f"wrote {out_dir / 'aimd_payload.bin'} and {out_dir / 'aimd_samples.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
