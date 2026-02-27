//! Metadata bridge: converts `libxrk::Metadata` to a Python dict.
//!
//! Produces the exact same key names and formatting as the original
//! PyDict-based metadata extraction.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use libxrk::metadata::{Metadata, VetValue};

/// Convert a Metadata struct to a Python dict with the exact same format as the original.
pub fn metadata_to_pydict(py: Python<'_>, meta: &Metadata) -> PyResult<Py<PyAny>> {
    let dict = PyDict::new(py);

    // String metadata fields
    if let Some(ref v) = meta.driver {
        dict.set_item("Driver", v)?;
    }
    if let Some(ref v) = meta.vehicle {
        dict.set_item("Vehicle", v)?;
    }
    if let Some(ref v) = meta.log_date {
        dict.set_item("Log Date", v)?;
    }
    if let Some(ref v) = meta.log_time {
        dict.set_item("Log Time", v)?;
    }
    if let Some(ref v) = meta.session {
        dict.set_item("Session", v)?;
    }
    if let Some(ref v) = meta.series {
        dict.set_item("Series", v)?;
    }
    if let Some(ref v) = meta.long_comment {
        dict.set_item("Long Comment", v)?;
    }

    // Track info
    if let Some(ref v) = meta.venue {
        dict.set_item("Venue", v)?;
    }

    // Odometer
    for entry in &meta.odometer {
        dict.set_item(
            format!("Odo/{} Distance (km)", entry.name),
            entry.distance_km,
        )?;
        dict.set_item(format!("Odo/{} Time", entry.name), &entry.time_formatted)?;
    }

    // Logger identity
    if let Some(v) = meta.logger_id {
        dict.set_item("Logger ID", v)?;
    }
    if let Some(v) = meta.logger_model_id {
        dict.set_item("Logger Model ID", v)?;
    }
    match &meta.logger_model {
        Some(name) => dict.set_item("Logger Model", name)?,
        None => {
            if meta.logger_model_id.is_some() {
                dict.set_item("Logger Model", py.None())?;
            }
        }
    }

    // Device name
    if let Some(ref v) = meta.device_name {
        dict.set_item("Device Name", v)?;
    }

    // GPS receiver
    if let Some(ref v) = meta.gps_receiver {
        dict.set_item("GPS Receiver", v)?;
    }

    // Expansion devices
    if !meta.expansion_devices.is_empty() {
        let expansion_devices = PyList::empty(py);
        for device in &meta.expansion_devices {
            let dev_dict = PyDict::new(py);
            if let Some(ref v) = device.bus_unit {
                dev_dict.set_item("Bus Unit", v)?;
            }
            if let Some(ref v) = device.bus_type {
                dev_dict.set_item("Bus Type", v)?;
            }
            if let Some(ref v) = device.version {
                dev_dict.set_item("Version", v)?;
            }
            if let Some(ref v) = device.manufacturer {
                dev_dict.set_item("Manufacturer", v)?;
            }
            if let Some(ref v) = device.model {
                dev_dict.set_item("Model", v)?;
            }
            if let Some(v) = device.logger_id {
                dev_dict.set_item("Logger ID", v)?;
            }
            if let Some(v) = device.model_id {
                dev_dict.set_item("Model ID", v)?;
            }
            expansion_devices.append(dev_dict)?;
        }
        if !expansion_devices.is_empty() {
            dict.set_item("Expansion Devices", expansion_devices)?;
        }
    }

    // Race mode
    if let Some(ref v) = meta.race_mode {
        dict.set_item("Race Mode", v)?;
    }

    // Vehicle Electronics Type
    if let Some(ref vet) = meta.vehicle_electronics_type {
        match vet {
            VetValue::Mode(mode) => dict.set_item("Vehicle Electronics Type", mode)?,
            VetValue::Value(val) => dict.set_item("Vehicle Electronics Type", val)?,
        }
    }

    // Calibrations
    if !meta.calibrations.is_empty() {
        let calibrations = PyList::empty(py);
        for cal in &meta.calibrations {
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
            if let Some(ref ch_name) = cal.channel {
                cal_dict.set_item("channel", ch_name)?;
            }
            calibrations.append(cal_dict)?;
        }
        if !calibrations.is_empty() {
            dict.set_item("Calibrations", calibrations)?;
        }
    }

    Ok(dict.into_any().unbind())
}
