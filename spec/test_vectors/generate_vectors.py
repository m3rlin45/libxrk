#!/usr/bin/env python3
"""Generate YAML test vectors from real XRK files using the Construct spec.

Parses all test XRK files, extracts representative messages, and outputs
YAML files that can be used as test vectors for other implementations
(e.g., JavaScript/TypeScript).

Usage:
    poetry run poe spec-vectors
    # or
    poetry run python spec/test_vectors/generate_vectors.py
"""

import sys
from pathlib import Path

import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from spec.xrk_format import (
    DECODER_TABLE,
    parse_xrk_file,
)

# Test data paths
TEST_DATA_DIR = PROJECT_ROOT / "tests" / "test_data"
SFJ_XRK = TEST_DATA_DIR / "SFJ" / "CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"
FILE_86_XRK = TEST_DATA_DIR / "86" / "CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"

OUTPUT_DIR = Path(__file__).parent


def generate_channel_defs(sfj_result, file_86_result):
    """Generate channel definition test vectors."""
    vectors = []

    for label, result in [("sfj", sfj_result), ("86", file_86_result)]:
        for idx in sorted(result.channels.keys()):
            ch = result.channels[idx]
            name = ch.name
            decoder_type = ch.decoder_type

            # Get decoder info
            decoder_fmt = None
            decoder_interpolate = None
            if decoder_type in DECODER_TABLE:
                decoder_fmt, decoder_interpolate = DECODER_TABLE[decoder_type]

            vector = {
                "id": f"chs_{label}_{idx:03d}",
                "description": f"{name} channel from {label.upper()}",
                "file": label,
                "raw_hex": (
                    result.find_nested_messages("CHS")[idx].raw_payload.hex()
                    if idx < len(result.find_nested_messages("CHS"))
                    else None
                ),
                "parsed": {
                    "index": ch.index,
                    "short_name": ch.short_name_str,
                    "long_name": name,
                    "data_size": ch.data_size,
                    "decoder_type": decoder_type,
                    "source_type": ch.source_type,
                    "source_channel_id": ch.source_channel_id,
                    "device_tag": ch.device_tag_str,
                    "sample_period_us": ch.sample_period_us,
                    "unit_type_byte": ch.unit_type_byte,
                    "cal_value_1": float(ch.cal_value_1),
                    "cal_value_2": float(ch.cal_value_2),
                    "display_range_min": float(ch.display_range_min),
                    "display_range_max": float(ch.display_range_max),
                },
                "verification": {
                    "units": ch.units,
                    "dec_pts": ch.dec_pts,
                    "interpolate": ch.interpolate,
                    "sample_rate_hz": (
                        round(1_000_000 / ch.sample_period_us, 2) if ch.sample_period_us > 0 else 0
                    ),
                },
            }

            # Add decoder info if known
            if decoder_fmt:
                vector["verification"]["decoder_format"] = decoder_fmt
                vector["verification"]["decoder_interpolate"] = decoder_interpolate

            vectors.append(vector)

    return {"vectors": vectors}


def generate_header_messages(sfj_result, file_86_result):
    """Generate header message test vectors (non-CHS/GRP)."""
    vectors = []

    for label, result in [("sfj", sfj_result), ("86", file_86_result)]:
        # LAP messages
        for i, msg in enumerate(result.messages_by_token("LAP")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"lap_{label}_{i:03d}",
                    "description": f"LAP message {i} from {label.upper()}",
                    "type": "LAP",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": {
                        "segment": msg.payload.segment,
                        "lap_num": msg.payload.lap_num,
                        "duration": msg.payload.duration,
                        "end_time": msg.payload.end_time,
                    },
                }
            )

        # String messages
        for tok_str in ["RCR", "VEH", "TMD", "TMT", "VTY", "CMP", "NTE", "NDV"]:
            msgs = result.messages_by_token(tok_str)
            for i, msg in enumerate(msgs):
                if msg.payload is None:
                    continue
                vectors.append(
                    {
                        "id": f"{tok_str.lower()}_{label}_{i:03d}",
                        "description": f"{tok_str} message from {label.upper()}",
                        "type": tok_str,
                        "file": label,
                        "raw_hex": msg.raw_payload.hex(),
                        "parsed": {"value": msg.payload},
                    }
                )

        # TRK
        for i, msg in enumerate(result.messages_by_token("TRK")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"trk_{label}_{i:03d}",
                    "description": f"Track info from {label.upper()}",
                    "type": "TRK",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": msg.payload,
                }
            )

        # GPSR
        for i, msg in enumerate(result.messages_by_token("GPSR")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"gpsr_{label}_{i:03d}",
                    "description": f"GPS receiver from {label.upper()}",
                    "type": "GPSR",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": msg.payload,
                }
            )

        # RACM
        for i, msg in enumerate(result.messages_by_token("RACM")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"racm_{label}_{i:03d}",
                    "description": f"Race mode from {label.upper()}",
                    "type": "RACM",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": msg.payload,
                }
            )

        # VET
        for i, msg in enumerate(result.messages_by_token("VET")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"vet_{label}_{i:03d}",
                    "description": f"Vehicle electronics type from {label.upper()}",
                    "type": "VET",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": {"value": msg.payload},
                }
            )

        # CAL
        for i, msg in enumerate(result.messages_by_token("CAL")):
            if msg.payload is None:
                continue
            vectors.append(
                {
                    "id": f"cal_{label}_{i:03d}",
                    "description": f"Calibration {i} from {label.upper()}",
                    "type": "CAL",
                    "file": label,
                    "raw_hex": msg.raw_payload.hex(),
                    "parsed": msg.payload,
                }
            )

    return {"vectors": vectors}


def generate_gps_messages(sfj_result, file_86_result):
    """Generate GPS test vectors (first/last samples from each file)."""
    vectors = []

    for label, result in [("sfj", sfj_result), ("86", file_86_result)]:
        samples = result.get_gps_samples()
        if not samples:
            continue

        # First and last GPS samples
        for tag, sample in [("first", samples[0]), ("last", samples[-1])]:
            vectors.append(
                {
                    "id": f"gps_{label}_{tag}",
                    "description": f"{tag.capitalize()} GPS sample from {label.upper()}",
                    "file": label,
                    "raw_hex": result.gps_payloads[0 if tag == "first" else -1].hex(),
                    "parsed": {
                        "timecode": sample.timecode,
                        "iTOW": sample.iTOW,
                        "fTOW": sample.fTOW,
                        "week": sample.week,
                        "gpsFix": sample.gpsFix,
                        "flags": sample.flags,
                        "ecefX": sample.ecefX,
                        "ecefY": sample.ecefY,
                        "ecefZ": sample.ecefZ,
                        "pAcc": sample.pAcc,
                        "ecefVX": sample.ecefVX,
                        "ecefVY": sample.ecefVY,
                        "ecefVZ": sample.ecefVZ,
                        "sAcc": sample.sAcc,
                        "pDOP": sample.pDOP,
                        "reserved1": sample.reserved1,
                        "numSV": sample.numSV,
                        "reserved2": sample.reserved2,
                    },
                }
            )

    return {"vectors": vectors}


def generate_data_messages(sfj_result, file_86_result):
    """Generate data message test vectors (first few of each type)."""
    vectors = []

    for label, result in [("sfj", sfj_result), ("86", file_86_result)]:
        # Collect first message of each type
        seen_types = {}
        for msg in result.data_messages():
            key = msg.msg_type
            if key not in seen_types:
                seen_types[key] = msg
            if len(seen_types) >= 4:
                break

        for msg_type, msg in seen_types.items():
            p = msg.parsed
            vector = {
                "id": f"data_{label}_{msg_type}",
                "description": f"First ({msg_type} message from {label.upper()}",
                "type": msg_type,
                "file": label,
                "parsed": {
                    "timecode": p["timecode"],
                },
            }
            if "channel_index" in p:
                vector["parsed"]["channel_index"] = p["channel_index"]
                # Add channel info
                ch_idx = p["channel_index"]
                if ch_idx in result.channels:
                    ch = result.channels[ch_idx]
                    vector["channel_info"] = {
                        "name": ch.name,
                        "data_size": ch.data_size,
                        "decoder_type": ch.decoder_type,
                    }
            if "group_index" in p:
                vector["parsed"]["group_index"] = p["group_index"]
            if "count" in p:
                vector["parsed"]["count"] = p["count"]
            vector["parsed"]["data_hex"] = p["data"].hex()
            vectors.append(vector)

    return {"vectors": vectors}


def generate_metadata(sfj_result, file_86_result):
    """Generate metadata test vectors."""
    vectors = []

    for label, result in [("sfj", sfj_result), ("86", file_86_result)]:
        metadata = {}

        # String metadata
        for tok_str, key in [
            ("RCR", "Driver"),
            ("VEH", "Vehicle"),
            ("TMD", "Log Date"),
            ("TMT", "Log Time"),
            ("VTY", "Session"),
            ("CMP", "Series"),
            ("NTE", "Long Comment"),
            ("NDV", "Device Name"),
        ]:
            msgs = result.messages_by_token(tok_str)
            if msgs and msgs[-1].payload:
                metadata[key] = msgs[-1].payload

        # TRK
        trk_msgs = result.messages_by_token("TRK")
        if trk_msgs and trk_msgs[-1].payload:
            metadata["Venue"] = trk_msgs[-1].payload["name"]

        # SRC/idn
        src_msgs = result.messages_by_token("SRC")
        if src_msgs and src_msgs[-1].payload:
            metadata["Logger ID"] = src_msgs[-1].payload["logger_id"]
            metadata["Logger Model ID"] = src_msgs[-1].payload["model_id"]

        # GPSR
        gpsr_msgs = result.messages_by_token("GPSR")
        if gpsr_msgs and gpsr_msgs[-1].payload:
            metadata["GPS Receiver"] = gpsr_msgs[-1].payload["type"]

        # RACM
        racm_msgs = result.messages_by_token("RACM")
        for m in racm_msgs:
            if m.payload and m.payload["mode"] == "string":
                metadata["Race Mode"] = m.payload["value"]

        # VET
        vet_msgs = result.messages_by_token("VET")
        if vet_msgs and vet_msgs[-1].payload is not None:
            metadata["Vehicle Electronics Type"] = vet_msgs[-1].payload

        # Summary stats
        metadata["_stats"] = {
            "channel_count": len(result.channels),
            "group_count": len(result.groups),
            "gps_sample_count": len(result.gps_payloads),
            "gnfi_sample_count": len(result.gnfi_payloads),
            "lap_count": len(result.messages_by_token("LAP")),
        }

        vectors.append(
            {
                "id": f"metadata_{label}",
                "description": f"Full metadata from {label.upper()}",
                "file": label,
                "metadata": metadata,
            }
        )

    return {"vectors": vectors}


def main():
    print("Parsing SFJ XRK file...")
    sfj = parse_xrk_file(str(SFJ_XRK))
    print(f"  {len(sfj.messages)} messages, {len(sfj.channels)} channels")

    print("Parsing 86 XRK file...")
    file_86 = parse_xrk_file(str(FILE_86_XRK))
    print(f"  {len(file_86.messages)} messages, {len(file_86.channels)} channels")

    # Generate YAML files
    outputs = {
        "channel_defs.yaml": generate_channel_defs(sfj, file_86),
        "header_messages.yaml": generate_header_messages(sfj, file_86),
        "gps_messages.yaml": generate_gps_messages(sfj, file_86),
        "data_messages.yaml": generate_data_messages(sfj, file_86),
        "metadata.yaml": generate_metadata(sfj, file_86),
    }

    for filename, data in outputs.items():
        outpath = OUTPUT_DIR / filename
        with open(outpath, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, width=120)
        count = len(data.get("vectors", []))
        print(f"  Wrote {outpath.name} ({count} vectors)")

    print("Done!")


if __name__ == "__main__":
    main()
