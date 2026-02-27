//! Metadata extraction from parsed header messages.
//!
//! Equivalent to `_get_metadata()` in aim_xrk.pyx:805-897.

use std::collections::HashMap;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::messages::{self, tokens, Payload};
use crate::parser::ParseResult;

/// Extract metadata from a ParseResult and return it as a Python dict.
pub fn extract_metadata(py: Python<'_>, result: &ParseResult) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);
    let msgs = &result.header_messages;

    // String metadata fields
    let string_fields: &[(u32, &str)] = &[
        (tokens::rcr(), "Driver"),
        (tokens::veh(), "Vehicle"),
        (tokens::tmd(), "Log Date"),
        (tokens::tmt(), "Log Time"),
        (tokens::vty(), "Session"),
        (tokens::cmp(), "Series"),
        (tokens::nte(), "Long Comment"),
    ];

    for &(tok, name) in string_fields {
        if let Some(msg_list) = msgs.get(&tok) {
            if let Some(last_msg) = msg_list.last() {
                let payload = messages::dispatch_payload(last_msg);
                if let Payload::StringMsg(s) = payload {
                    dict.set_item(name, &s)?;
                }
            }
        }
    }

    // Track info (TRK)
    if let Some(msg_list) = msgs.get(&tokens::trk()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::Trk(trk) = payload {
                dict.set_item("Venue", &trk.name)?;
            }
        }
    }

    // Odometer (ODO)
    if let Some(msg_list) = msgs.get(&tokens::odo()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::Odo(odo) = payload {
                for (name, record) in &odo.records {
                    let dist_km = record.dist as f64 / 1000.0;
                    dict.set_item(format!("Odo/{} Distance (km)", name), dist_km)?;
                    let hours = record.time / 3600;
                    let minutes = (record.time / 60) % 60;
                    let seconds = record.time % 60;
                    dict.set_item(
                        format!("Odo/{} Time", name),
                        format!("{}:{:02}:{:02}", hours, minutes, seconds),
                    )?;
                }
            }
        }
    }

    // Logger identity (idn)
    if let Some(msg_list) = msgs.get(&tokens::idn()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::Idn(idn) = payload {
                dict.set_item("Logger ID", idn.logger_id)?;
                dict.set_item("Logger Model ID", idn.model_id)?;
                let model_name = crate::tables::logger_model_name(idn.model_id);
                if let Some(name) = model_name {
                    dict.set_item("Logger Model", name)?;
                } else {
                    dict.set_item("Logger Model", py.None())?;
                }
            }
        }
    }

    // Device name (NDV)
    if let Some(msg_list) = msgs.get(&tokens::ndv()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::StringMsg(s) = payload {
                dict.set_item("Device Name", &s)?;
            }
        }
    }

    // GPS receiver (GPSR)
    if let Some(msg_list) = msgs.get(&tokens::gpsr()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::Gpsr(gpsr) = payload {
                dict.set_item("GPS Receiver", &gpsr.gps_type)?;
            }
        }
    }

    // Expansion devices (ENF)
    if !result.enf_sub_messages.is_empty() {
        let expansion_devices = PyList::empty(py);
        for enf_msgs in &result.enf_sub_messages {
            let device = PyDict::new(py);
            let enf_fields: &[(u32, &str)] = &[
                (tokens::dbun(), "Bus Unit"),
                (tokens::dbut(), "Bus Type"),
                (tokens::dver(), "Version"),
                (tokens::manl(), "Manufacturer"),
                (tokens::modl(), "Model"),
            ];
            for &(tok, key) in enf_fields {
                if let Some(tok_msgs) = enf_msgs.get(&tok) {
                    if let Some(last_msg) = tok_msgs.last() {
                        let payload = messages::dispatch_payload(last_msg);
                        if let Payload::StringMsg(s) = payload {
                            device.set_item(key, &s)?;
                        }
                    }
                }
            }
            if !device.is_empty() {
                expansion_devices.append(device)?;
            }
        }

        // Enrich with hardware IDs from iSLV messages (positional match)
        if let Some(islv_msgs) = msgs.get(&tokens::islv()) {
            let slave_idns: Vec<_> = islv_msgs
                .iter()
                .filter_map(|m| {
                    let payload = messages::dispatch_payload(m);
                    if let Payload::EmbeddedIdn(idn) = payload {
                        Some(idn)
                    } else {
                        None
                    }
                })
                .collect();

            for (i, idn) in slave_idns.iter().enumerate() {
                if i < expansion_devices.len() {
                    let device = expansion_devices.get_item(i)?;
                    device.set_item("Logger ID", idn.logger_id)?;
                    device.set_item("Model ID", idn.model_id)?;
                }
            }
        }

        if !expansion_devices.is_empty() {
            dict.set_item("Expansion Devices", expansion_devices)?;
        }
    }

    // Race mode (RACM)
    if let Some(msg_list) = msgs.get(&tokens::racm()) {
        for msg in msg_list {
            let payload = messages::dispatch_payload(msg);
            if let Payload::Racm(crate::payloads::racm::RacmPayload::Mode(mode)) = payload {
                dict.set_item("Race Mode", &mode)?;
            }
        }
    }

    // Vehicle Electronics Type (VET)
    if let Some(msg_list) = msgs.get(&tokens::vet()) {
        if let Some(last_msg) = msg_list.last() {
            let payload = messages::dispatch_payload(last_msg);
            if let Payload::Vet(vet) = payload {
                match vet {
                    crate::payloads::vet::VetPayload::Mode(mode) => {
                        dict.set_item("Vehicle Electronics Type", &mode)?;
                    }
                    crate::payloads::vet::VetPayload::Value(val) => {
                        dict.set_item("Vehicle Electronics Type", val)?;
                    }
                }
            }
        }
    }

    // Calibrations (CAL)
    if let Some(msg_list) = msgs.get(&tokens::cal()) {
        // Build map from (cal_val_1, cal_val_2) -> channel name via CHS fields
        let mut cal_to_channel: HashMap<(u32, u32), String> = HashMap::new();
        for ch_info in result.channels.values() {
            let key = (
                ch_info.chs.cal_value_1.to_bits(),
                ch_info.chs.cal_value_2.to_bits(),
            );
            cal_to_channel.insert(key, ch_info.chs.long_name());
        }

        let calibrations = PyList::empty(py);
        for msg in msg_list {
            let payload = messages::dispatch_payload(msg);
            if let Payload::Cal(cal) = payload {
                let cal_dict = PyDict::new(py);
                cal_dict.set_item("type", cal.cal_type)?;
                cal_dict.set_item("raw_1", cal.raw_1)?;
                cal_dict.set_item("raw_2", cal.raw_2)?;
                if let Some(output_1) = cal.output_1 {
                    cal_dict.set_item("output_1", output_1)?;
                }
                if let Some(output_2) = cal.output_2 {
                    cal_dict.set_item("output_2", output_2)?;
                }
                // Cross-reference with channel
                let key = (cal.raw_1.to_bits(), cal.raw_2.to_bits());
                if let Some(ch_name) = cal_to_channel.get(&key) {
                    cal_dict.set_item("channel", ch_name)?;
                }
                calibrations.append(cal_dict)?;
            }
        }
        if !calibrations.is_empty() {
            dict.set_item("Calibrations", calibrations)?;
        }
    }

    Ok(dict.into_any().unbind())
}
