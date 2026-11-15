# SmartyCam MP4 Telemetry Investigation Report

**Date:** 2026-10-30
**Reference files** (one session, Tsukuba Circuit, 2026-10-25; not committed except as noted):

| File | Size | Notes |
|---|---|---|
| `CMD_KK-F_Tsukuba_Car_レース_SCHD0487.MP4` | 1 910 170 040 B | SmartyCam 3 GP, serial 4118157, fw 01.17.08 |
| `CMD_KK-F_Tsukuba_Car_レース_a_0385.xrz` | 16 969 728 B | logger session; committed as `tests/test_data/smartycam/CMD_KK-F_Tsukuba_Car_Race_a_0385.xrz` |
| `CMD_KK-F_Tsukuba_Car_レース_a_0385.xrk` | 51 892 058 B | = decompressed XRZ (51 891 882 B) + 176 B RaceStudio tail |

The committed fixture `tests/test_data/smartycam/SCHD0487_aimd_trimmed.mp4` (3 513 678 B)
is produced from the MP4 by `make_fixture.py --seconds 210`.

Every number below is printed by one of the scripts in this directory:

```
uv run python scripts/investigate_mp4/mp4_boxes.py <MP4>                 # §1, §2
uv run python scripts/investigate_mp4/aimd_extract.py <MP4> --out-dir D  # §3
uv run python scripts/investigate_mp4/compare_with_xrk.py <MP4> <XRZ>    # §4–§7
uv run python scripts/investigate_mp4/make_fixture.py <MP4> <out.mp4>    # fixture
```

The conclusions are specified in `spec/docs/mp4_format.md` and executable in
`spec/mp4_format.py`.

---

## 1. Box tree (`mp4_boxes.py`)

```
'ftyp' off=0 size=32            mp42; compatible mp42 mp41 isom iso2
'free' off=32 size=8
'mdat' off=40 size=1908829620
'moov' off=1908829660 size=1339355
  'mvhd'  timescale=6000 duration=8932969 (1488.828s)
          creation=2026-10-25 11:00:59 modification=2026-10-25 11:25:48   (camera local time)
  'trak' #1  tkhd track_id=1 1920x1080; elst [(323,-1),(8932093,0)]
          mdhd timescale=6000 duration=8932093; hdlr vide/VideoHandler; stsd avc1
          stts 89321 samples, deltas {100:48109, 99:20228, 101:18199, 102:1768, 98:799, 103:32}
          stsc 3415 entries (15/16 per chunk); stsz per-sample; stco 5732 chunks; stss present
  'trak' #2  tkhd track_id=2; elst [(8932096,0)]
          mdhd timescale=48000 duration=71456768; hdlr soun/SoundHandler; stsd mp4a
          stts 69782 x 1024; stsc [(1,12,1),(5816,2,1)]; stco 5816 chunks
  'trak' #3  tkhd track_id=3 duration=8932969; elst [(631,-1),(8932338,0)]
          tref/tmcd track_ids=[3]
          mdhd timescale=1000 duration=1488723; hdlr meta/MetaAimHandler; stsd aimd (16 bytes)
          minf: dinf + stbl only (no nmhd/gmhd)
          stts 7280 entries, 14850 samples, deltas {100:11161, 101:3656, 102:32, 103:1}
          stsc [(1,3,1)]; stsz per-sample; stco 4950 chunks, first 543626
  'udta'/meta/ilst (empty) on every trak and on moov
!! malformed box at 1910169015: size field b'4096' (= 875575606), type b'free'; 1025 bytes to EOF
```

## 2. Trailing XML (`mp4_boxes.py`)

8-byte header `4096free` followed by 1 017 bytes:

```xml
<?xml version="1.0"?>
<aim_meta_data version="0.0.0">
  <generic_info>
    <p n="track_name">Tsukuba_Car</p>
    <p n="track_lat">361500840</p>
    <p n="track_lon">1399193874</p>
    <p n="date">25/10/2026</p>
    <p n="hour">02:00:56</p>
    <p n="file_duration">1487</p>
    <p n="nlap_rec">0</p>
    <p n="nlap">20</p>
    <p n="best">59218</p>
    <p n="nbest">16</p>
    <p n="hwserial">4118157</p>
    <p n="release">01.17.08</p>
  </generic_info>
  <hw_info>
    <p n="board_id">SMC11</p>  <p n="board_rev">C</p>  <p n="data_collaudo">2025-10-07</p>
    <p n="first_fw_ver">01.05.00</p>  <p n="hwrelease">SMC11C</p>  <p n="hwserial">4118157</p>
    <p n="lens"/>  <p n="logger_code">613</p>  <p n="machine">sc3gp</p>
    <p n="mainboardinfo">SMC11C-103</p>  <p n="meccaniche">Smarty3_GP</p>
    <p n="pmicinfo">PMIC_FW[0.4]</p>  <p n="secboardinfo">CPC04A-101</p>
  </hw_info>
  <audio_conf>
    <p n="mic_int_gain">5</p>  <p n="mic_ext_gain">0</p>
  </audio_conf>
</aim_meta_data>
```

## 3. aimd samples (`aimd_extract.py`)

```
telemetry track id=3 timescale=1000 duration=1488723
samples=14850 chunks=4950 stsc=[(1, 3, 1)]
empty edit = 631 movie ticks = 105.2 ms
sample size histogram (top 8): [(1557, 2299), (1646, 2175), (1672, 934), (1570, 826), (1659, 804), (1583, 599), (1297, 437), (1685, 360)]
samples > 5000 bytes: [0, 7]                      # 0: camera CNF (6830 B); 7: syslog burst (71818 B)
all 14850 sample headers valid and round-trip byte-identically
total payload bytes: 23001542
sample 0 header: logger_us=14644543 media_us=11393646 (media value is an outlier)
logger-media offset (ms): first=14632.835 last=14735.908 min=14632.835 max=14735.908
                          drift=103.073 over 1488.5 s = 69.2 ppm
header media_time_us/1000 - stts dts (ms): min=10.970 max=12.076
both header clocks strictly increasing over regular samples: True
```

Header invariants asserted on every sample: `[0:2]` BE == (size−2) & 0xFFFF,
`[2:6]` == `40 00 00 00`, `[6:10]` == `amv0`, `[26:62]` == 0, `[62:66]` LE == size−66.

First 72 bytes of samples 0, 1 and 7:

```
0: 1a ac 40 00 00 00 61 6d 76 30 3f 75 df 00 00 00 00 00 6e da ad 00 00 00 00 00 ...00 6c 1a 00 00 3c 68 43 4e 46 00   <hCNF
1: 06 5f 40 00 00 00 61 6d 76 30 19 fd e0 00 00 00 00 00 96 b5 01 00 00 00 00 00 ...00 1f 06 00 00 3c 68 47 50 53 00   <hGPS
7: 18 88 40 00 00 00 61 6d 76 30 4c 2b ea 00 00 00 00 00 c9 e3 0a 00 00 00 00 00 ...00 48 18 01 00 28 53 93 3b 00 00   (S
```

## 4. Token tallies (`compare_with_xrk.py`)

```
[MP4] header tokens: {'GPS': 36592, 'XLOG': 442, 'LAP': 20, 'CNF': 1, 'RCR': 1, 'VEH': 1, 'CMP': 1,
                      'VTY': 1, 'NDV': 1, 'SRC': 1, 'TRK': 1, 'TMD': 1, 'TMT': 1}
[MP4] data messages: {'S': 1544931}
[MP4] CHS (incl. nested): 40
[XRK] header tokens: {'GPS': 36762, 'GSAT': 731, 'CAL': 24, 'LAP': 20, 'ODO': 5, 'iSLV': 3, 'BUCN': 3,
                      'CNF': 2, 'ENF': 2, 'RCR': 1, 'VEH': 1, 'CMP': 1, 'VTY': 1, 'NDV': 1, 'RACM': 1,
                      'VET': 1, 'SRC': 1, 'HWNF': 1, 'TRK': 1, 'PDLT': 1, 'TMD': 1, 'TMT': 1, 'GPSR': 1}
[XRK] data messages: {'G': 269875, 'c': 1677223, 'S': 1043714}
[XRK] CHS (incl. nested): 190
```

## 5. Identity messages

```
[MP4] RCR/VEH/CMP/VTY/NDV  v1 len=0  -> ''
[MP4] TMD  v1 -> '10/25/2026'        TMT v1 -> '02:00:56'   (UTC)
[MP4] SRC  v1 len=128 -> {'model_id': 635, 'logger_id': 4118157}   # the CAMERA
[MP4] TRK  v1 len=96  -> {'name': 'Tsukuba_Car', 'sf_lat': 36.150084, 'sf_long': 139.9193874}
[XRK] RCR/VEH/CMP/VTY/NDV  v1 len=1  -> ''                           # XRZ as written by the logger
[XRK] TMD  v1 -> '10/25/2026'        TMT v1 -> '10:59:46'   (local, UTC+9)
[XRK] SRC  v1 len=128 -> {'model_id': 544, 'logger_id': 8401203}   # the LOGGER
[XRK] TRK  v2 len=96  -> {'name': 'Tsukuba_Car', 'sf_lat': 36.150084, 'sf_long': 139.9193874}
[XRK] iSLV x3 -> model 544 serial 6202385; model 566 serial 6203172; model 363 serial 1200389
[XRK] GPSR -> {'type': 'GPS', 'channel_index': 187, 'u32_32': 410}
```

Camera serial `4118157` (LE `8d d6 3e 00`) does not occur anywhere in the XRK; none of
the strings `SMC11`, `Smarty`, `SCHD`, `sc3gp` occur in it either.

The `.xrk` written by RaceStudio is the decompressed `.xrz` byte-for-byte plus this 176-byte
tail of version-0 strings (the metadata typed in RaceStudio; also the source of both file
names):

```
<hRCR\0 03 00 00 00 00 >CMD<RCR\0 d4 00>  <hVEH\0 ...>KK-F<VEH...>  <hCMP\0 ...>Tsukuba Race<CMP...>
<hVTY\0 ...>レース<VTY...>  <hNTE\0 30 00 00 00 00 >Race\r\nstarted 4\r\nfinish 9\r\nslightly damp\r\nslicks<NTE...>
```

## 6. LAP and GPS equality

All 20 LAP messages: `equal=True` (same `lap_num`, `duration`, `end_time`), e.g.
lap 1 dur=46658 end=49202 … lap 16 dur=59218 (best) … lap 20 dur=59453 end=1305936.

```
MP4 fixes=36592 first tc=14601  iTOW=7275480 week=2442 UTC=2026-10-25 02:00:57.480
XRK fixes=36762 first tc=5497   iTOW=7266960 week=2442 UTC=2026-10-25 02:00:48.960
shared iTOW: 36549  same timecode: 36549  same ECEF: 36549
fields that differ on shared fixes: {'numSV': 29142}
```

## 7. Camera channel usage (`(S` messages, MP4)

```
idx   0 LoggerTemp         ~1.0 Hz    idx  12 Master_Clock        ~10.0 Hz   idx  35 Engine_RPM   ~49.1 Hz
idx   1 Dirty L            ~1.0 Hz    idx  26 Line Lap Distance L ~26.5 Hz   idx  36 Speed        ~49.1 Hz
idx   2 Writeback L        ~1.0 Hz    idx  27 Predictive_Time     ~2.4 Hz    idx  37 Gear         ~49.1 Hz
idx   3 VCC_PSPLL0 L       ~1.0 Hz    idx  28 +-_Test_Best_Time   ~2.4 Hz    idx  38..54 Water_Temp, Head_Temp,
idx   4 Battery            ~1.0 Hz    idx  31 SW Status L         ~1.0 Hz       Exhaust_Temp, Oil_Temp, Oil_Press,
idx   5 Input_Voltage      ~1.0 Hz    idx  32 HW Status L         ~1.0 Hz       Brake_Press, Throttle_Pos, Brake_Pos,
idx   6 VBOUT_INTERNAL L   ~1.0 Hz    idx  33 Master Mem On L     ~1.0 Hz       Clutch_Pos, Steering_Pos, Lambda,
idx   7 BULLET1 VSENSE L   ~1.0 Hz                                              Lateral_Acc, Inline_Acc, Fuel_Level,
idx 9-11 Charger Status/Fault, PMIC Status  ~1.0 Hz                             Battery_Voltage, Vertical_Acc,
                                                                                Heart_Rate  ~49.1 Hz each
(S timecode range 14648..1503354 (logger ms); 38 of 40 channels used; 20 LapPk and 55 GPS0 never sampled)
CHS[4:6]: 613 on 39 channels, 2120 on 1
```

Comparing MP4 `(S` values with XRK `(S` values at the same (timecode, index) finds no
equal pairs: the indices refer to different channel tables (camera vs logger) and the
camera encodes everything as float32.

## 8. Clock mapping

```
logger-media offset: first=14632.835 ms  last=14735.908 ms  samples=14849
lap 1 end logger=49202 ms  -> telemetry media=34569.2 ms  -> movie=34674.3 ms
lap 2 end logger=220967 ms -> telemetry media=206334.2 ms -> movie=206439.3 ms
lap 3 end logger=287243 ms -> telemetry media=272610.2 ms -> movie=272715.3 ms
```

## 9. Link report (`spec.mp4_format.link_report`)

```
laps_identical: True   common_laps: 20   gps_shared: 36549   gps_shared_identical: 36549
track_match: True      mp4_date == xrk_date == 10/25/2026
mp4_time_utc: 02:00:56   xrk_time_local: 10:59:46
matched: True
```
