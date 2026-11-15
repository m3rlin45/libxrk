# AIM SmartyCam MP4 Telemetry Format

This document specifies where and how AIM SmartyCam cameras store telemetry inside
their `.MP4` recordings, and which metadata links a recording to the XRK/XRZ file
of the logger session recorded at the same time. The executable counterpart is
split in two: `spec/isobmff.py` is a generic, AIM-agnostic ISO BMFF layer (lazy
box walker, box structs, sample-table resolution) and `spec/mp4_format.py` holds
everything AIM-specific (§4–§8). They meet at `spec.mp4_format.TrackSamples`, a
plain record of a track's sample offsets, sizes, timestamps, timescales, handler
identity and leading empty edit; any other MP4 parser that can fill it can drive
`spec.mp4_format.parse_telemetry` unchanged. The message grammar inside the
telemetry samples is the XRK format itself (`spec/xrk_format.py`), so nothing in
it is re-specified here.

Evidence for every statement comes from one reference triplet (same session,
Tsukuba Circuit, 2026-10-25), reproduced by the scripts in
`scripts/investigate_mp4/` and summarised in
`scripts/investigate_mp4/INVESTIGATION_REPORT.md`:

| File | Size | Producer |
|---|---|---|
| `CMD_KK-F_Tsukuba_Car_レース_SCHD0487.MP4` | 1 910 170 040 B | SmartyCam 3 GP, serial 4118157, firmware 01.17.08 |
| `CMD_KK-F_Tsukuba_Car_レース_a_0385.xrz` | 16 969 728 B | logger (AIM MX-series, serial 8401203) |
| `CMD_KK-F_Tsukuba_Car_レース_a_0385.xrk` | 51 892 058 B | RaceStudio 3 (decompressed XRZ + 176 B of session strings) |

Other camera models and firmware versions have not been examined (§10).

## 1. Summary

- The MP4 is a normal ISO BMFF file (`ftyp` = `mp42`) with three tracks: H.264
  video, AAC audio, and a **timed-metadata track** whose handler is named
  `MetaAimHandler` and whose sample entry type is `aimd`.
- Every `aimd` sample is a **66-byte header followed by an XRK message stream**:
  `<hCNF…>`, `<hGPS…>`, `<hLAP…>`, `(S…)` and friends, exactly as in an XRK file.
  Concatenating the payloads of all samples yields one stream that
  `spec.xrk_format.parse_xrk_bytes` parses with zero leftover bytes.
- The stream is **the logger's data relayed by the camera**, not the camera's own
  measurements: GPS fixes and LAP messages carry the logger's timecodes
  (milliseconds since logger power-on) and are identical to the XRK's. The `(S`
  samples are the camera's fixed overlay channel set (Engine_RPM, Speed, Gear,
  …, Heart_Rate) as float32, plus camera internals, all stamped with the same
  logger clock.
- The 66-byte header gives, per sample, the logger clock and the media
  presentation clock in microseconds. Their difference maps video time to logger
  time (§7); it drifts by ~69 ppm, so it is a table, not a constant.
- After `moov` the file ends with an `<aim_meta_data>` XML document wrapped in a
  **malformed** `free` box whose size field is the ASCII text `4096` (§6).
- Linking an MP4 to its XRK/XRZ is exact: the LAP list is byte-identical and the
  GPS fixes share `(week, iTOW) → timecode` (§8). The MP4 does **not** contain
  the logger's serial, and the XRK does not contain the camera's.

## 2. Container layout

Top-level boxes, in file order (ISO/IEC 14496-12):

| Box | Offset | Size | Notes |
|---|---|---|---|
| `ftyp` | 0 | 32 | major `mp42`, minor 0, compatible `mp42 mp41 isom iso2` |
| `free` | 32 | 8 | empty |
| `mdat` | 40 | 1 908 829 620 | interleaved video/audio/aimd chunks |
| `moov` | 1 908 829 660 | 1 339 355 | `mvhd`, three `trak`, `udta` |
| *(tail)* | 1 910 169 015 | 1 025 | `aim_meta_data` pseudo-box, §6 |

`mvhd`: version 0, timescale **6000**, duration 8 932 969 (1488.83 s),
creation 2026-10-25 11:00:59, modification 11:25:48. The creation/modification
times are the camera's **local** clock (UTC+9 here); compare the XML tail's
`hour` = 02:00:56 UTC and the first GPS fix at 02:00:57 UTC.

Every `trak` and the `moov` itself carry a `udta/meta(hdlr 'mdir')/ilst` triple
with an empty `ilst` (no iTunes-style tags).

## 3. Tracks

| # | `hdlr` type / name | `stsd` format | media timescale | duration | samples | `elst` |
|---|---|---|---|---|---|---|
| 1 | `vide` / VideoHandler | `avc1` 1920×1080 | 6000 | 8 932 093 | 89 321 (~60 fps; deltas 98–103) | empty 323 ticks (53.8 ms), then full |
| 2 | `soun` / SoundHandler | `mp4a` 48 kHz | 48 000 | 71 456 768 | 69 782 (1024/frame) | single full edit |
| 3 | `meta` / **MetaAimHandler** | **`aimd`** | **1000** | 1 488 723 | 14 850 | empty **631** ticks (105.2 ms), then full |

Track 3 is the telemetry track. Its identifying features, all of which
`spec.mp4_format.is_telemetry_track` requires (it needs only `handler_type`,
`handler_name` and `sample_format`, so any container parser can answer it):

- `hdlr.handler_type == b"meta"` and `hdlr.name == "MetaAimHandler"`;
- `stsd` has exactly one entry, the bare 16-byte SampleEntry
  `00 00 00 10 'aimd' 00×6 00 01` (no format-specific fields);
- `mdhd.timescale == 1000`, i.e. media time is in milliseconds.

Also observed: `minf` has **no** media header box (`nmhd`/`gmhd`), only `dinf`
and `stbl`; a `tref` box of type `tmcd` referencing **track 3 itself**; `stsc`
= one entry `(1, 3, 1)` (three samples per chunk, 4 950 chunks); `stsz` with
per-sample sizes; 32-bit `stco`. Sample deltas are 100 ms (11 161×), 101 ms
(3 656×), 102 ms (32×), 103 ms (1×) — a nominal **10 Hz** sample rate.

The video track has `stss` (sync samples) and `stsc` runs of 15/16 frames per
chunk; the audio track is 12 frames per chunk. Neither is needed for telemetry.

## 4. `aimd` sample layout

Each sample is `66 + payload_length` bytes (`AimdSample` in `spec/mp4_format.py`).
Verified on all 14 850 samples of the reference file; every sample round-trips
byte-identically.

| Offset | Type | Field | Value / meaning |
|---|---|---|---|
| [0:2] | uint16 BE | `length_minus_2` | `(sample length − 2) mod 65536`. The 71 818-byte sample stores 0x1888. |
| [2:6] | bytes | `_flags` | always `40 00 00 00`; purpose unknown |
| [6:10] | ASCII | `_magic` | `amv0` |
| [10:18] | uint64 LE | `logger_time_us` | **logger clock** in µs when the sample was flushed; always 1–110 ms after the newest timecode inside the payload |
| [18:26] | uint64 LE | `media_time_us` | **media presentation time** of the sample in µs; equals the `stts` DTS + 11–12 ms on every regular sample |
| [26:62] | bytes | `_zero_26_62` | always zero |
| [62:66] | uint32 LE | `payload_length` | `sample length − 66` |
| [66:] | bytes | `payload` | XRK message stream (§5) |

Notes:

- Mixed endianness is real: the two length fields differ in byte order.
- `media_time_us` of **sample 0** is 11 393 646 (11.4 s) although its DTS is 0
  and sample 1 reads 112 022. It is excluded from the clock table (§7); see
  `unknown_regions.md`.
- Sample sizes: typically 1.3–1.9 kB (10 Hz × ~25 Hz GPS + ~50 Hz overlay
  channels). Sample 0 is 6 830 B (the camera's `CNF`), sample 7 is 71 818 B (the
  boot syslog burst, §5.4).

## 5. The payload: an XRK message stream

The concatenation of all payloads is a valid XRK byte stream. Messages never
straddle a sample boundary in the reference file (each sample parses on its own,
which is how the trimmed fixture was produced). Token tally of the reference file:

| Token | Count | Where |
|---|---|---|
| `GPS` | 36 592 | every regular sample, 2–3 per 100 ms (25 Hz) |
| `XLOG` | 442 | sample 7 (69 chunks) and ~50 later samples |
| `LAP` | 20 | the sample at each lap end |
| `CNF` | 1 | sample 0 |
| `RCR` `VEH` `CMP` `VTY` `NDV` `SRC` `TRK` `TMD` `TMT` | 1 each | sample 0, after the `CNF` |
| `(S` data messages | 1 544 931 | every regular sample |

No `(G`, `(M` or `(c` messages, no `GRP`, `CAL`, `ODO`, `ENF`, `idn`, `GPSR`,
`iSLV`, `GSAT` — the camera does not relay the logger's configuration or raw
channel data.

### 5.1 Sample 0: the camera's own `CNF`

Sample 0 opens with `<hCNF` (payload 6 340 B, version 1) containing **40
`CHS`/`CDE` pairs** describing the *camera's* channel set, then nine top-level
string/struct messages. The channels (index → name):

| Range | Channels | Rate seen | Role |
|---|---|---|---|
| 0–7, 9–11 | LoggerTemp, Dirty L, Writeback L, VCC_PSPLL0 L, Battery, Input_Voltage, VBOUT_INTERNAL L, BULLET1 VSENSE L, Charger Status L, Charger Fault L, PMIC Status L | 1 Hz | camera internals |
| 12 | Master_Clock | 10 Hz | camera |
| 20 | LapPk | — | not sampled |
| 26 | Line Lap Distance L | ~26 Hz | camera lap logic |
| 27, 28 | Predictive_Time, +-_Test_Best_Time | ~2.4 Hz | camera lap logic |
| 31–33 | SW Status L, HW Status L, Master Mem On L | 1 Hz | camera |
| 35–54 | Engine_RPM, Speed, Gear, Water_Temp, Head_Temp, Exhaust_Temp, Oil_Temp, Oil_Press, Brake_Press, Throttle_Pos, Brake_Pos, Clutch_Pos, Steering_Pos, Lambda, Lateral_Acc, Inline_Acc, Fuel_Level, Battery_Voltage, Vertical_Acc, Heart_Rate | ~49 Hz | **overlay channels** fed by the logger |
| 55 | GPS0 | — | not sampled as `(S` |

`CHS[4:6]` (`hardware_id` in `CHSPayload`) is **613** on 39 channels — the
camera's `logger_code` from the XML tail — and 2120 on one. The overlay channels
are the SmartyCam's standard mapped set; their values are **not** the logger's
raw channels (different indices, float32 encoding, ~50 Hz), so they do not
compare bytewise with the XRK's `(S` data.

The string messages after the `CNF` are version 1 and, except for `TMD`/`TMT`,
**empty**: `RCR` (driver), `VEH`, `CMP`, `VTY`, `NDV` all have length 0.

| Token | Payload | Meaning |
|---|---|---|
| `SRC` | `EmbeddedIDNPayload`, `model_id` 635, `logger_id` **4118157** | the **camera's** identity (equals `<hwserial>`), not the logger's |
| `TRK ` | `TRKPayload`: `Tsukuba_Car`, sf_lat 36.150084, sf_long 139.9193874 | same bytes as the XRK's `TRK` |
| `TMD` | `10/25/2026` | recording date (MM/DD/YYYY) |
| `TMT` | `02:00:56` | recording start, **UTC** (the XRK's `TMT` is local time) |

### 5.2 Regular samples: GPS

`<hGPS` payloads are 56-byte u-blox NAV-SOL records (`GPSPayload`) at 25 Hz.
On the 36 549 fixes shared with the XRK (matched by `iTOW`): `timecode`, `week`,
`iTOW`, ECEF position and velocity, `pAcc`, `sAcc`, `pDOP`, `flags`, `gpsFix`
are **identical**; only `numSV` differs (on 29 142 fixes, by 1–2 satellites).
The MP4 stream therefore carries the logger's GPS, re-serialised by the camera.
The XRK has 213 more fixes: 170 before the camera started recording and 43 after.

### 5.3 LAP

The 20 `LAP` messages (version 1, 16 B) are **byte-identical** to the XRK's:
same `lap_num`, `duration`, and `end_time` in the logger clock. The XML tail's
`nlap`=20, `best`=59218, `nbest`=16 are derived from them.

### 5.4 XLOG: the camera's syslog

`<hXLOG` (token `XLOG`, version 0) carries slices of the camera's Linux syslog
as plain text. Sample 7 holds the boot burst sliced into **1000-byte** chunks
(`Oct 25 11:00:54 sc3gp syslog-ng[610]: syslog-ng starting up…`, kernel 5.4.0 on
a Xilinx ZynqMP); later samples carry whatever lines arrived since the previous
sample (one line to a few hundred bytes). Lines from `aim-main-app` include lap
events (`LAP: n.2  2:51.765`, `BestLap`), power faults, and a periodic status
line with recording statistics. `spec/xrk_format.py` stores these in
`ParseResult.xlog_payloads`.

## 6. Trailing `aim_meta_data` pseudo-box

The last 1 025 bytes of the file are:

```
34 30 39 36  66 72 65 65  3c 3f 78 6d 6c ...
"4096"       "free"       <?xml version="1.0"?>\n<aim_meta_data version="0.0.0"> ... </aim_meta_data>\n
```

Where a uint32 BE box size belongs, the camera writes the **ASCII text `4096`**
(0x34303936 = 875 575 606). A standards-conforming box walker reads a size far
past EOF; `spec.isobmff.iter_boxes` stops at the first box whose declared end
exceeds the file size and records that offset as `Movie.trailing_offset`, and
`spec.mp4_format.read_meta_tail` parses the remainder with `AimMetaDataTail`. The XML is
1 017 bytes and is **not** padded to 4096.

Content (`<p n="name">text</p>` elements per section; `parse_aim_meta_data_xml`):

| Section | Field | Reference value | Meaning |
|---|---|---|---|
| generic_info | track_name | Tsukuba_Car | same as `TRK` name |
| | track_lat / track_lon | 361500840 / 1399193874 | start/finish in 1e-7 degrees (= `TRK` sf_lat/sf_long) |
| | date | 25/10/2026 | **DD/MM/YYYY** (the stream's `TMD` is MM/DD/YYYY) |
| | hour | 02:00:56 | recording start, UTC (= `TMT`) |
| | file_duration | 1487 | seconds |
| | nlap_rec | 0 | unknown (recording lap?) |
| | nlap / best / nbest | 20 / 59218 / 16 | lap count, best lap ms, best lap number |
| | hwserial | 4118157 | camera serial (= `SRC.logger_id`) |
| | release | 01.17.08 | camera firmware |
| hw_info | board_id, board_rev, hwrelease, mainboardinfo, secboardinfo, pmicinfo | SMC11, C, SMC11C, SMC11C-103, CPC04A-101, PMIC_FW[0.4] | hardware revision |
| | data_collaudo | 2025-10-07 | factory test date |
| | first_fw_ver | 01.05.00 | firmware at manufacture |
| | hwserial | 4118157 | camera serial |
| | lens | *(empty)* | |
| | logger_code | 613 | device code; also `CHS[4:6]` of the camera channels |
| | machine | sc3gp | SmartyCam 3 GP |
| | meccaniche | Smarty3_GP | mechanical variant |
| audio_conf | mic_int_gain / mic_ext_gain | 5 / 0 | |

## 7. Timebases and the video ↔ logger mapping

Four clocks appear:

| Clock | Unit | Where | Zero |
|---|---|---|---|
| movie time | 1/6000 s | `mvhd`, `tkhd`, `elst.segment_duration` | start of presentation |
| media time (track 3) | ms (`mdhd`), µs in header [18:26] | `stts`, `aimd` header | first telemetry sample |
| logger time | ms (`(S`/`GPS`/`LAP` timecodes), µs in header [10:18] | payload, `aimd` header | logger power-on |
| wall clock | s | `mvhd` (local), `TMT`/`hour` (UTC), GPS `(week, iTOW)` | — |

Relations established on the reference file:

1. **Media → movie.** Track 3's `elst` has an empty edit of 631 ticks, so
   telemetry media time *t* is presented at movie time *t* + 105.17 ms. The
   video track's empty edit is 323 ticks (53.83 ms); video media time *v* is at
   movie time *v* + 53.83 ms. Hence video frame time ↔ telemetry media time
   differ by 51.3 ms (`Mp4ParseResult.telemetry_media_us_to_movie_ms`; the
   empty edit reaches the AIM layer as `TrackSamples.empty_edit_ticks`).
2. **Media → logger.** For every regular sample *k*,
   `offset_k = logger_time_us_k − media_time_us_k`. `offset` is 14 632 835 µs at
   the first regular sample and 14 735 908 µs at the last, i.e. it grows by
   103 ms over 1 488.5 s (**69 ppm**). Linear interpolation between the samples
   bracketing a media time gives `logger_ms = (media_us + offset(media_us)) / 1000`
   (`media_us_to_logger_ms`); the inverse converges in two iterations
   (`logger_ms_to_media_us`). Sample 0 is excluded from the table.
3. **Header media time vs `stts`.** `media_time_us/1000 − DTS` is 10.97–12.08 ms
   on every regular sample. The spec uses the header value; whether the 11 ms is
   capture latency or belongs to the DTS side is open (§10).
4. **Absolute time.** GPS `(week, iTOW)` → UTC = GPS epoch + week·7d + iTOW −
   18 s leap seconds (`gps_fix_utc`). First MP4 fix: week 2442, iTOW 7 275 480 →
   2026-10-25 02:00:57.48 UTC at logger timecode 14 601 ms, 1.5 s after `TMT`
   02:00:56. `mvhd.creation_time` (11:00:59 local) is 3 s after `TMT` + 9 h.

Worked example: lap 1 ends at logger 49 202 ms → telemetry media 34 569.2 ms →
movie 34 674.3 ms → video media 34 620.5 ms (frame ≈ 2 077 at 60 fps).

## 8. Linking an MP4 to an XRK/XRZ

The camera does not record the logger's serial, and the logger does not record
the camera's. RaceStudio nevertheless pairs them, and the data support these
keys, strongest first (`spec.mp4_format.link_report` implements 1–4):

1. **LAP list equality.** `[(lap_num, duration, end_time)]` from the MP4 equals
   the XRK's exactly (20/20). For a partial/trimmed MP4, the MP4's list is a
   prefix of the XRK's.
2. **GPS fix identity.** For every `(week, iTOW)` present in both, `timecode`
   and ECEF position are equal (36 549/36 549). Even a few shared fixes decide a
   match; `numSV` must be ignored.
3. **Venue.** `TRK` name and start/finish coordinates are equal; the XML tail's
   `track_name`/`track_lat`/`track_lon` repeat them.
4. **Date.** `TMD` is equal (`10/25/2026`). **Time is not directly comparable:**
   the MP4's `TMT` 02:00:56 is UTC, the XRK's 10:59:46 is local (UTC+9) and is
   57 s *earlier* than the logger's own first GPS fix implies — do not rely on
   `TMT` for sub-minute alignment; use GPS or LAP.
5. **File names.** RaceStudio names both files `<RCR>_<VEH>_<TRK>_<VTY>_…`:
   `CMD_KK-F_Tsukuba_Car_レース_a_0385.xrk` and
   `CMD_KK-F_Tsukuba_Car_レース_SCHD0487.MP4`. `SCHD0487` is the camera's own
   file counter; `a_0385` is the logger's. The driver/vehicle/championship/
   session strings come from RaceStudio, not from the devices: the logger's XRZ
   contains them only as **empty** version-1 messages, and the `.xrk` is the
   decompressed XRZ plus a **176-byte tail** of version-0 `RCR`/`VEH`/`CMP`/
   `VTY`/`NTE` strings (`CMD`, `KK-F`, `Tsukuba Race`, `レース`, the session
   note) that RaceStudio appended after download. The MP4 never contains them.

What does **not** link: `SRC` in the MP4 is the camera (model 635, serial
4118157); `SRC`/`iSLV` in the XRK list the logger (model 544, serial 8401203)
and its expansions (model 544/566/363) — the camera is not among them.

Once matched, synchronisation needs no search: payload timecodes **are** logger
timecodes, and §7 maps them to video time.

## 9. Quirks checklist for implementers

- Trailing `free` box with ASCII size `4096`: stop the box walk at the first
  box that overruns EOF; parse the remainder as `AimMetaDataTail`.
- `aimd` header `[0:2]` is 16-bit and wraps; use `[62:66]` for the length.
- `[0:2]` is big-endian, `[10:26]` and `[62:66]` are little-endian.
- Sample 0's `media_time_us` is bogus; take media time from `stts` or from
  sample 1 onward.
- `tref/tmcd` points at the telemetry track itself; ignore it.
- Track 3's `minf` has no `nmhd`; do not require one.
- `TMT` is UTC in the MP4, local in the XRK; `date` in the XML tail is
  DD/MM/YYYY while `TMD` is MM/DD/YYYY.
- `XLOG` chunks are up to 1 000 bytes of text, not NUL-terminated.
- Files longer than 4 GB would need `co64`; `spec/isobmff.py` accepts it but
  it has not been observed.

## 10. Open questions

- `aimd` header `[2:6]` = `40 00 00 00`: flags? sample-entry version?
- Sample 0 `media_time_us` = 11 393 646: time since camera boot at which the
  `CNF` was generated? (`logger_time_us` of sample 0 is consistent.)
- The constant 11–12 ms between header media time and `stts` DTS.
- `nlap_rec` in the XML tail (0 here).
- `CHS[4:6]` = 2120 on one camera channel (all others 613); 2120 also appears on
  9 channels of the logger's own `CNF`.
- Whether other SmartyCam models (SmartyCam 3 Sport/Dual, SmartyCam HD, GP HD)
  and firmware versions write the same track, header and tail; whether
  multi-file recordings (camera file-size splits) repeat the `CNF` and the tail.
- Why the camera re-serialises GPS with a different `numSV`.
- What the camera does when no logger is connected (does the track exist with
  only camera channels and its own GPS?).
