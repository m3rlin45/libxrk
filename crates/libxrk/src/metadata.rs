//! Metadata extraction from parsed header messages.
//!
//! Returns typed Rust structs instead of PyDict.
//! Equivalent to `_get_metadata()` in aim_xrk.pyx:805-897.

use std::collections::HashMap;

use crate::messages::{self, tokens, Payload};
use crate::parser::ParseResult;

/// Odometer entry with distance and time.
#[derive(Debug, Clone)]
pub struct OdometerEntry {
    pub name: String,
    pub distance_km: f64,
    pub time_formatted: String,
}

/// Calibration entry.
#[derive(Debug, Clone)]
pub struct Calibration {
    pub cal_type: u32,
    pub raw_1: f32,
    pub raw_2: f32,
    pub output_1: Option<f32>,
    pub output_2: Option<f32>,
    pub channel: Option<String>,
}

/// Expansion device info.
#[derive(Debug, Clone)]
pub struct ExpansionDevice {
    pub bus_unit: Option<String>,
    pub bus_type: Option<String>,
    pub version: Option<String>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub logger_id: Option<u32>,
    pub model_id: Option<u16>,
}

/// Vehicle electronics type value.
#[derive(Debug, Clone)]
pub enum VetValue {
    Mode(String),
    Value(u8),
}

/// All metadata extracted from an XRK file.
#[derive(Debug, Clone)]
pub struct Metadata {
    pub driver: Option<String>,
    pub vehicle: Option<String>,
    pub venue: Option<String>,
    pub log_date: Option<String>,
    pub log_time: Option<String>,
    pub session: Option<String>,
    pub series: Option<String>,
    pub long_comment: Option<String>,
    pub device_name: Option<String>,
    pub gps_receiver: Option<String>,
    pub race_mode: Option<String>,
    pub vehicle_electronics_type: Option<VetValue>,
    pub logger_id: Option<u32>,
    pub logger_model_id: Option<u16>,
    pub logger_model: Option<String>,
    pub odometer: Vec<OdometerEntry>,
    pub calibrations: Vec<Calibration>,
    pub expansion_devices: Vec<ExpansionDevice>,
}

/// Extract metadata from a ParseResult.
pub fn extract_metadata(result: &ParseResult) -> Metadata {
    let msgs = &result.header_messages;

    let get_last_string = |tok: u32| -> Option<String> {
        msgs.get(&tok).and_then(|list| list.last()).and_then(|msg| {
            if let Payload::StringMsg(s) = messages::dispatch_payload(msg) {
                Some(s)
            } else {
                None
            }
        })
    };

    let driver = get_last_string(tokens::rcr());
    let vehicle = get_last_string(tokens::veh());
    let log_date = get_last_string(tokens::tmd());
    let log_time = get_last_string(tokens::tmt());
    let session = get_last_string(tokens::vty());
    let series = get_last_string(tokens::cmp());
    let long_comment = get_last_string(tokens::nte());
    let device_name = get_last_string(tokens::ndv());

    // Track info (TRK)
    let venue = msgs
        .get(&tokens::trk())
        .and_then(|list| list.last())
        .and_then(|msg| {
            if let Payload::Trk(trk) = messages::dispatch_payload(msg) {
                Some(trk.name)
            } else {
                None
            }
        });

    // Odometer (ODO)
    let mut odometer = Vec::new();
    if let Some(msg_list) = msgs.get(&tokens::odo()) {
        if let Some(last_msg) = msg_list.last() {
            if let Payload::Odo(odo) = messages::dispatch_payload(last_msg) {
                for (name, record) in &odo.records {
                    let dist_km = record.dist as f64 / 1000.0;
                    let hours = record.time / 3600;
                    let minutes = (record.time / 60) % 60;
                    let seconds = record.time % 60;
                    odometer.push(OdometerEntry {
                        name: name.clone(),
                        distance_km: dist_km,
                        time_formatted: format!("{}:{:02}:{:02}", hours, minutes, seconds),
                    });
                }
            }
        }
    }

    // Logger identity (idn)
    let (logger_id, logger_model_id, logger_model) = msgs
        .get(&tokens::idn())
        .and_then(|list| list.last())
        .map(|msg| {
            if let Payload::Idn(idn) = messages::dispatch_payload(msg) {
                let model_name =
                    crate::tables::logger_model_name(idn.model_id).map(|s| s.to_string());
                (Some(idn.logger_id), Some(idn.model_id), model_name)
            } else {
                (None, None, None)
            }
        })
        .unwrap_or((None, None, None));

    // GPS receiver (GPSR)
    let gps_receiver = msgs
        .get(&tokens::gpsr())
        .and_then(|list| list.last())
        .and_then(|msg| {
            if let Payload::Gpsr(gpsr) = messages::dispatch_payload(msg) {
                Some(gpsr.gps_type)
            } else {
                None
            }
        });

    // Expansion devices (ENF)
    let mut expansion_devices = Vec::new();
    if !result.enf_sub_messages.is_empty() {
        for enf_msgs in &result.enf_sub_messages {
            let get_enf_string = |tok: u32| -> Option<String> {
                enf_msgs
                    .get(&tok)
                    .and_then(|list| list.last())
                    .and_then(|msg| {
                        if let Payload::StringMsg(s) = messages::dispatch_payload(msg) {
                            Some(s)
                        } else {
                            None
                        }
                    })
            };

            let device = ExpansionDevice {
                bus_unit: get_enf_string(tokens::dbun()),
                bus_type: get_enf_string(tokens::dbut()),
                version: get_enf_string(tokens::dver()),
                manufacturer: get_enf_string(tokens::manl()),
                model: get_enf_string(tokens::modl()),
                logger_id: None,
                model_id: None,
            };

            // Only add non-empty devices
            if device.bus_unit.is_some()
                || device.bus_type.is_some()
                || device.version.is_some()
                || device.manufacturer.is_some()
                || device.model.is_some()
            {
                expansion_devices.push(device);
            }
        }

        // Enrich with hardware IDs from iSLV messages (positional match)
        if let Some(islv_msgs) = msgs.get(&tokens::islv()) {
            let slave_idns: Vec<_> = islv_msgs
                .iter()
                .filter_map(|m| {
                    if let Payload::EmbeddedIdn(idn) = messages::dispatch_payload(m) {
                        Some(idn)
                    } else {
                        None
                    }
                })
                .collect();

            for (i, idn) in slave_idns.iter().enumerate() {
                if i < expansion_devices.len() {
                    expansion_devices[i].logger_id = Some(idn.logger_id);
                    expansion_devices[i].model_id = Some(idn.model_id);
                }
            }
        }
    }

    // Race mode (RACM)
    let mut race_mode = None;
    if let Some(msg_list) = msgs.get(&tokens::racm()) {
        for msg in msg_list {
            if let Payload::Racm(crate::payloads::racm::RacmPayload::Mode(mode)) =
                messages::dispatch_payload(msg)
            {
                race_mode = Some(mode);
            }
        }
    }

    // Vehicle Electronics Type (VET)
    let vehicle_electronics_type = msgs
        .get(&tokens::vet())
        .and_then(|list| list.last())
        .and_then(|msg| {
            if let Payload::Vet(vet) = messages::dispatch_payload(msg) {
                Some(match vet {
                    crate::payloads::vet::VetPayload::Mode(mode) => VetValue::Mode(mode),
                    crate::payloads::vet::VetPayload::Value(val) => VetValue::Value(val),
                })
            } else {
                None
            }
        });

    // Calibrations (CAL)
    let mut calibrations = Vec::new();
    if let Some(msg_list) = msgs.get(&tokens::cal()) {
        // Build map from (cal_val_1, cal_val_2) -> channel display name via
        // CHS fields. This replicates the Cython backend's semantics:
        //   - only channels that actually carry data participate
        //     (Cython builds the map from its data-bearing channels dict);
        //   - channels are visited in ascending index order with duplicate
        //     long_names disambiguated to "<name> dup <k>" (see
        //     parser::channel_display_names), so names are unique and on a
        //     cal-value collision the later index wins;
        //   - keys compare like Python floats: -0.0 == 0.0, NaN never matches.
        let gps_reserved: Vec<&str> = if result.gps_data.is_empty() {
            Vec::new()
        } else {
            crate::gps::GPS_CHANNEL_DEFS
                .iter()
                .map(|d| d.name)
                .collect()
        };
        let display_names = crate::parser::channel_display_names(&result.channels, &gps_reserved);
        let mut data_indices: Vec<u16> = result
            .channels
            .keys()
            .copied()
            .filter(|idx| result.channel_data.contains_key(idx))
            .collect();
        data_indices.sort_unstable();

        let cal_key = |a: f32, b: f32| -> Option<(u32, u32)> {
            if a.is_nan() || b.is_nan() {
                return None; // Python dicts never match a distinct NaN key
            }
            let norm = |v: f32| (if v == 0.0 { 0.0f32 } else { v }).to_bits();
            Some((norm(a), norm(b)))
        };

        let mut cal_to_channel: HashMap<(u32, u32), String> = HashMap::new();
        for idx in data_indices {
            let chs = &result.channels[&idx].chs;
            if let Some(key) = cal_key(chs.cal_value_1, chs.cal_value_2) {
                let name = display_names
                    .get(&idx)
                    .cloned()
                    .unwrap_or_else(|| chs.long_name());
                cal_to_channel.insert(key, name);
            }
        }

        for msg in msg_list {
            if let Payload::Cal(cal) = messages::dispatch_payload(msg) {
                let channel =
                    cal_key(cal.raw_1, cal.raw_2).and_then(|key| cal_to_channel.get(&key).cloned());
                calibrations.push(Calibration {
                    cal_type: cal.cal_type,
                    raw_1: cal.raw_1,
                    raw_2: cal.raw_2,
                    output_1: cal.output_1,
                    output_2: cal.output_2,
                    channel,
                });
            }
        }
    }

    Metadata {
        driver,
        vehicle,
        venue,
        log_date,
        log_time,
        session,
        series,
        long_comment,
        device_name,
        gps_receiver,
        race_mode,
        vehicle_electronics_type,
        logger_id,
        logger_model_id,
        logger_model,
        odometer,
        calibrations,
        expansion_devices,
    }
}
