//! Metadata extraction from parsed header messages.
//!
//! Returns typed Rust structs instead of PyDict.
//! Equivalent to `_get_metadata()` in _aim_xrk_cy.pyx:805-897.

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

/// A value in the metadata document built by [`Metadata::to_entries`].
#[derive(Debug, Clone, PartialEq)]
pub enum MetadataValue {
    /// A key that is present without a value (Python `None`).
    Null,
    Str(String),
    Int(u64),
    Float(f64),
    List(Vec<MetadataValue>),
    /// A nested document, in insertion order.
    Map(Vec<(String, MetadataValue)>),
}

impl From<&str> for MetadataValue {
    fn from(v: &str) -> Self {
        MetadataValue::Str(v.to_string())
    }
}

/// Append `key: value` when the string is present.
fn push_str(entries: &mut Vec<(String, MetadataValue)>, key: &str, value: &Option<String>) {
    if let Some(v) = value {
        entries.push((key.to_string(), v.as_str().into()));
    }
}

impl Metadata {
    /// The metadata as the ordered key/value document every binding exposes:
    /// the Python `LogFile.metadata` dict, and the same document as JSON
    /// through the C ABI.
    ///
    /// This is the one place that defines the key names, their order and
    /// which keys are omitted when a value is missing; the bindings only
    /// translate [`MetadataValue`] into their own types.
    pub fn to_entries(&self) -> Vec<(String, MetadataValue)> {
        let mut m = Vec::new();

        push_str(&mut m, "Driver", &self.driver);
        push_str(&mut m, "Vehicle", &self.vehicle);
        push_str(&mut m, "Log Date", &self.log_date);
        push_str(&mut m, "Log Time", &self.log_time);
        push_str(&mut m, "Session", &self.session);
        push_str(&mut m, "Series", &self.series);
        push_str(&mut m, "Long Comment", &self.long_comment);
        push_str(&mut m, "Venue", &self.venue);

        for entry in &self.odometer {
            m.push((
                format!("Odo/{} Distance (km)", entry.name),
                MetadataValue::Float(entry.distance_km),
            ));
            m.push((
                format!("Odo/{} Time", entry.name),
                entry.time_formatted.as_str().into(),
            ));
        }

        if let Some(v) = self.logger_id {
            m.push(("Logger ID".to_string(), MetadataValue::Int(v.into())));
        }
        if let Some(v) = self.logger_model_id {
            m.push(("Logger Model ID".to_string(), MetadataValue::Int(v.into())));
        }
        // A logger whose model ID is not in the table still gets the key.
        match &self.logger_model {
            Some(name) => m.push(("Logger Model".to_string(), name.as_str().into())),
            None if self.logger_model_id.is_some() => {
                m.push(("Logger Model".to_string(), MetadataValue::Null))
            }
            None => {}
        }

        push_str(&mut m, "Device Name", &self.device_name);
        push_str(&mut m, "GPS Receiver", &self.gps_receiver);

        if !self.expansion_devices.is_empty() {
            let devices = self
                .expansion_devices
                .iter()
                .map(|d| {
                    let mut dev = Vec::new();
                    push_str(&mut dev, "Bus Unit", &d.bus_unit);
                    push_str(&mut dev, "Bus Type", &d.bus_type);
                    push_str(&mut dev, "Version", &d.version);
                    push_str(&mut dev, "Manufacturer", &d.manufacturer);
                    push_str(&mut dev, "Model", &d.model);
                    if let Some(v) = d.logger_id {
                        dev.push(("Logger ID".to_string(), MetadataValue::Int(v.into())));
                    }
                    if let Some(v) = d.model_id {
                        dev.push(("Model ID".to_string(), MetadataValue::Int(v.into())));
                    }
                    MetadataValue::Map(dev)
                })
                .collect();
            m.push((
                "Expansion Devices".to_string(),
                MetadataValue::List(devices),
            ));
        }

        push_str(&mut m, "Race Mode", &self.race_mode);

        if let Some(vet) = &self.vehicle_electronics_type {
            let value = match vet {
                VetValue::Mode(mode) => mode.as_str().into(),
                VetValue::Value(val) => MetadataValue::Int((*val).into()),
            };
            m.push(("Vehicle Electronics Type".to_string(), value));
        }

        if !self.calibrations.is_empty() {
            let calibrations = self
                .calibrations
                .iter()
                .map(|c| {
                    let mut cal = vec![
                        ("type".to_string(), MetadataValue::Int(c.cal_type.into())),
                        ("raw_1".to_string(), MetadataValue::Float(c.raw_1.into())),
                        ("raw_2".to_string(), MetadataValue::Float(c.raw_2.into())),
                    ];
                    if let Some(v) = c.output_1 {
                        cal.push(("output_1".to_string(), MetadataValue::Float(v.into())));
                    }
                    if let Some(v) = c.output_2 {
                        cal.push(("output_2".to_string(), MetadataValue::Float(v.into())));
                    }
                    push_str(&mut cal, "channel", &c.channel);
                    MetadataValue::Map(cal)
                })
                .collect();
            m.push((
                "Calibrations".to_string(),
                MetadataValue::List(calibrations),
            ));
        }

        m
    }
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
                for record in &odo.records {
                    let dist_km = record.dist as f64 / 1000.0;
                    let hours = record.time / 3600;
                    let minutes = (record.time / 60) % 60;
                    let seconds = record.time % 60;
                    odometer.push(OdometerEntry {
                        name: record.name.clone(),
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

#[cfg(test)]
mod tests {
    use super::*;

    fn empty() -> Metadata {
        Metadata {
            driver: None,
            vehicle: None,
            venue: None,
            log_date: None,
            log_time: None,
            session: None,
            series: None,
            long_comment: None,
            device_name: None,
            gps_receiver: None,
            race_mode: None,
            vehicle_electronics_type: None,
            logger_id: None,
            logger_model_id: None,
            logger_model: None,
            odometer: Vec::new(),
            calibrations: Vec::new(),
            expansion_devices: Vec::new(),
        }
    }

    #[test]
    fn to_entries_omits_missing_values() {
        assert!(empty().to_entries().is_empty());
    }

    #[test]
    fn to_entries_keeps_the_python_dict_order_and_types() {
        let meta = Metadata {
            driver: Some("D".into()),
            venue: Some("Fuji".into()),
            device_name: Some("MXm".into()),
            race_mode: Some("Speed".into()),
            vehicle_electronics_type: Some(VetValue::Value(3)),
            logger_id: Some(7),
            logger_model_id: Some(9999),
            odometer: vec![OdometerEntry {
                name: "Total".into(),
                distance_km: 1.5,
                time_formatted: "0:01:02".into(),
            }],
            calibrations: vec![Calibration {
                cal_type: 20,
                raw_1: 0.5,
                raw_2: 1.0,
                output_1: None,
                output_2: Some(180.0),
                channel: Some("steering".into()),
            }],
            expansion_devices: vec![ExpansionDevice {
                bus_unit: Some("1".into()),
                bus_type: None,
                version: None,
                manufacturer: None,
                model: Some("TPMS".into()),
                logger_id: Some(42),
                model_id: None,
            }],
            ..empty()
        };

        let entries = meta.to_entries();
        let keys: Vec<&str> = entries.iter().map(|(k, _)| k.as_str()).collect();
        assert_eq!(
            keys,
            [
                "Driver",
                "Venue",
                "Odo/Total Distance (km)",
                "Odo/Total Time",
                "Logger ID",
                "Logger Model ID",
                "Logger Model",
                "Device Name",
                "Expansion Devices",
                "Race Mode",
                "Vehicle Electronics Type",
                "Calibrations",
            ]
        );
        let get = |key: &str| &entries.iter().find(|(k, _)| k == key).unwrap().1;
        assert_eq!(get("Odo/Total Distance (km)"), &MetadataValue::Float(1.5));
        assert_eq!(get("Logger ID"), &MetadataValue::Int(7));
        // An unknown model ID still yields the key, with no value.
        assert_eq!(get("Logger Model"), &MetadataValue::Null);
        assert_eq!(get("Vehicle Electronics Type"), &MetadataValue::Int(3));
        assert_eq!(
            get("Expansion Devices"),
            &MetadataValue::List(vec![MetadataValue::Map(vec![
                ("Bus Unit".into(), "1".into()),
                ("Model".into(), "TPMS".into()),
                ("Logger ID".into(), MetadataValue::Int(42)),
            ])])
        );
        assert_eq!(
            get("Calibrations"),
            &MetadataValue::List(vec![MetadataValue::Map(vec![
                ("type".into(), MetadataValue::Int(20)),
                ("raw_1".into(), MetadataValue::Float(0.5)),
                ("raw_2".into(), MetadataValue::Float(1.0)),
                ("output_2".into(), MetadataValue::Float(180.0)),
                ("channel".into(), "steering".into()),
            ])])
        );
    }
}
