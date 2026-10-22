//! C ABI for libxrk.
//!
//! A session is parsed once into Arrow record batches (one per channel, one
//! for the laps table) and handed out as an opaque [`XrkSession`] pointer.
//! Channels and laps are exported through the Arrow C Data Interface
//! (`ArrowArrayStream`), so any Arrow implementation can consume them with no
//! copies; metadata is exported as a JSON document with the same keys and
//! value types as the Python `LogFile.metadata` dict.
//!
//! Conventions: every function is `unsafe extern "C"` and panic-safe (panics
//! are caught and reported as errors). Strings returned as `*mut c_char` are
//! owned by the caller and must be released with [`xrk_string_free`]. Error
//! out-parameters receive such a string, or NULL on success.

use std::collections::HashMap;
use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

use arrow::array::{RecordBatch, RecordBatchIterator};
use arrow::ffi_stream::FFI_ArrowArrayStream;
use libxrk::arrow::{
    build_channel_batch, build_gps_channel_batches, build_laps_batch, ChannelMetadata,
};
use libxrk::metadata::{Metadata, VetValue};
use libxrk::parser::ChannelData;
use serde_json::{json, Map, Value};

/// A parsed session: channel batches in channel-index order (then the GPS
/// channels), the laps batch and the metadata JSON.
pub struct XrkSession {
    channels: Vec<(String, RecordBatch)>,
    laps: RecordBatch,
    metadata_json: CString,
}

/// Format an f32 as Python's `str(float(v))` would, so field metadata strings
/// are byte-identical to the Python wrapper's (copied from libxrk-python).
pub fn format_float(v: f32) -> String {
    if !v.is_finite() {
        return format!("{}", v);
    }
    let mut buf = ryu::Buffer::new();
    let s = buf.format(v as f64);
    if let Some(e_pos) = s.find('e') {
        let (mantissa, exp_part) = s.split_at(e_pos);
        let exp_str = &exp_part[1..];
        if exp_str.starts_with('-') {
            format!("{}e{}", mantissa, exp_str)
        } else {
            format!("{}e+{}", mantissa, exp_str)
        }
    } else if s.contains('.') {
        s.to_string()
    } else {
        format!("{}.0", s)
    }
}

/// Parse `data` (.xrk or zlib-compressed .xrz bytes) into a session.
pub fn open_session(data: &[u8]) -> Result<XrkSession, String> {
    let mut file = libxrk::read_xrk(data).map_err(|e| e.to_string())?;

    let mut raw_channels = std::mem::take(&mut file.channels);
    raw_channels.sort_by_key(|ch| ch.index);
    let mut channels = Vec::with_capacity(raw_channels.len() + 12);
    for ch in raw_channels {
        let info = file
            .raw
            .channels
            .get(&ch.index)
            .ok_or_else(|| format!("channel {} has no CHS info", ch.index))?;
        let metadata = ChannelMetadata::from_channel_info(info).to_hashmap(format_float);
        let data = ChannelData {
            timecodes: ch.timecodes,
            values: ch.values,
        };
        let batch = build_channel_batch(&ch.name, data, metadata).map_err(|e| e.to_string())?;
        channels.push((ch.name, batch));
    }
    if let Some(gps) = file.gps.take() {
        channels.extend(build_gps_channel_batches(gps, format_float).map_err(|e| e.to_string())?);
    }

    let laps = build_laps_batch(&file.laps).map_err(|e| e.to_string())?;
    let metadata_json =
        CString::new(metadata_to_json(&file.metadata).to_string()).map_err(|e| e.to_string())?;

    Ok(XrkSession {
        channels,
        laps,
        metadata_json,
    })
}

/// The metadata as JSON with the Python dict's keys and value types
/// (see libxrk-python's `metadata_bridge.rs`).
pub fn metadata_to_json(meta: &Metadata) -> Value {
    fn set_str(m: &mut Map<String, Value>, key: &str, v: &Option<String>) {
        if let Some(v) = v {
            m.insert(key.to_string(), Value::String(v.clone()));
        }
    }

    let mut m = Map::new();
    set_str(&mut m, "Driver", &meta.driver);
    set_str(&mut m, "Vehicle", &meta.vehicle);
    set_str(&mut m, "Log Date", &meta.log_date);
    set_str(&mut m, "Log Time", &meta.log_time);
    set_str(&mut m, "Session", &meta.session);
    set_str(&mut m, "Series", &meta.series);
    set_str(&mut m, "Long Comment", &meta.long_comment);
    set_str(&mut m, "Venue", &meta.venue);

    for entry in &meta.odometer {
        m.insert(
            format!("Odo/{} Distance (km)", entry.name),
            json!(entry.distance_km),
        );
        m.insert(
            format!("Odo/{} Time", entry.name),
            Value::String(entry.time_formatted.clone()),
        );
    }

    if let Some(v) = meta.logger_id {
        m.insert("Logger ID".into(), json!(v));
    }
    if let Some(v) = meta.logger_model_id {
        m.insert("Logger Model ID".into(), json!(v));
    }
    match &meta.logger_model {
        Some(name) => {
            m.insert("Logger Model".into(), Value::String(name.clone()));
        }
        None => {
            if meta.logger_model_id.is_some() {
                m.insert("Logger Model".into(), Value::Null);
            }
        }
    }

    set_str(&mut m, "Device Name", &meta.device_name);
    set_str(&mut m, "GPS Receiver", &meta.gps_receiver);

    if !meta.expansion_devices.is_empty() {
        let devices: Vec<Value> = meta
            .expansion_devices
            .iter()
            .map(|d| {
                let mut dev = Map::new();
                for (key, v) in [
                    ("Bus Unit", &d.bus_unit),
                    ("Bus Type", &d.bus_type),
                    ("Version", &d.version),
                    ("Manufacturer", &d.manufacturer),
                    ("Model", &d.model),
                ] {
                    if let Some(v) = v {
                        dev.insert(key.into(), Value::String(v.clone()));
                    }
                }
                if let Some(v) = d.logger_id {
                    dev.insert("Logger ID".into(), json!(v));
                }
                if let Some(v) = d.model_id {
                    dev.insert("Model ID".into(), json!(v));
                }
                Value::Object(dev)
            })
            .collect();
        m.insert("Expansion Devices".into(), Value::Array(devices));
    }

    set_str(&mut m, "Race Mode", &meta.race_mode);

    if let Some(vet) = &meta.vehicle_electronics_type {
        let v = match vet {
            VetValue::Mode(mode) => Value::String(mode.clone()),
            VetValue::Value(val) => json!(val),
        };
        m.insert("Vehicle Electronics Type".into(), v);
    }

    if !meta.calibrations.is_empty() {
        let cals: Vec<Value> = meta
            .calibrations
            .iter()
            .map(|c| {
                let mut cal = Map::new();
                cal.insert("type".into(), json!(c.cal_type));
                cal.insert("raw_1".into(), json!(c.raw_1));
                cal.insert("raw_2".into(), json!(c.raw_2));
                if let Some(v) = c.output_1 {
                    cal.insert("output_1".into(), json!(v));
                }
                if let Some(v) = c.output_2 {
                    cal.insert("output_2".into(), json!(v));
                }
                if let Some(v) = &c.channel {
                    cal.insert("channel".into(), Value::String(v.clone()));
                }
                Value::Object(cal)
            })
            .collect();
        m.insert("Calibrations".into(), Value::Array(cals));
    }

    Value::Object(m)
}

impl XrkSession {
    /// Channel names in export order.
    pub fn channel_names(&self) -> impl Iterator<Item = &str> {
        self.channels.iter().map(|(name, _)| name.as_str())
    }

    /// The batch of channel `i`.
    pub fn channel(&self, i: usize) -> Option<&RecordBatch> {
        self.channels.get(i).map(|(_, b)| b)
    }

    /// The laps batch.
    pub fn laps(&self) -> &RecordBatch {
        &self.laps
    }

    /// The metadata JSON text.
    pub fn metadata_json(&self) -> &str {
        self.metadata_json.to_str().unwrap_or("{}")
    }
}

fn batch_stream(batch: &RecordBatch) -> FFI_ArrowArrayStream {
    let reader = RecordBatchIterator::new(vec![Ok(batch.clone())], batch.schema());
    FFI_ArrowArrayStream::new(Box::new(reader))
}

fn owned_string(s: &str) -> *mut c_char {
    CString::new(s.replace('\0', "")).map_or(ptr::null_mut(), CString::into_raw)
}

unsafe fn set_error(err: *mut *mut c_char, message: &str) {
    if !err.is_null() {
        *err = owned_string(message);
    }
}

unsafe fn open_guarded(
    err: *mut *mut c_char,
    read: impl FnOnce() -> Result<Vec<u8>, String>,
) -> *mut XrkSession {
    if !err.is_null() {
        *err = ptr::null_mut();
    }
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        read().and_then(|data| open_session(&data))
    }));
    match outcome {
        Ok(Ok(session)) => Box::into_raw(Box::new(session)),
        Ok(Err(message)) => {
            set_error(err, &message);
            ptr::null_mut()
        }
        Err(_) => {
            set_error(err, "libxrk panicked while parsing the file");
            ptr::null_mut()
        }
    }
}

/// Parse the file at `path` (UTF-8). Returns NULL and sets `*err` on failure.
///
/// # Safety
/// `path` must be a valid NUL-terminated string; `err` may be NULL.
#[no_mangle]
pub unsafe extern "C" fn xrk_open_path(
    path: *const c_char,
    err: *mut *mut c_char,
) -> *mut XrkSession {
    open_guarded(err, || {
        if path.is_null() {
            return Err("path is NULL".into());
        }
        let path = CStr::from_ptr(path).to_str().map_err(|e| e.to_string())?;
        std::fs::read(path).map_err(|e| format!("{}: {}", path, e))
    })
}

/// Parse `len` bytes at `data`. Returns NULL and sets `*err` on failure.
///
/// # Safety
/// `data` must point to `len` readable bytes; `err` may be NULL.
#[no_mangle]
pub unsafe extern "C" fn xrk_open_bytes(
    data: *const u8,
    len: usize,
    err: *mut *mut c_char,
) -> *mut XrkSession {
    open_guarded(err, || {
        if data.is_null() {
            return Err("data is NULL".into());
        }
        Ok(std::slice::from_raw_parts(data, len).to_vec())
    })
}

/// Release a session. NULL is ignored.
///
/// # Safety
/// `session` must come from `xrk_open_*` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn xrk_close(session: *mut XrkSession) {
    if !session.is_null() {
        drop(Box::from_raw(session));
    }
}

/// Number of channels (CHS channels, then the GPS channels).
///
/// # Safety
/// `session` must be a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_count(session: *const XrkSession) -> usize {
    session.as_ref().map_or(0, |s| s.channels.len())
}

/// The name of channel `index` as an owned string, or NULL when out of range.
///
/// # Safety
/// `session` must be a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_name(session: *const XrkSession, index: usize) -> *mut c_char {
    match session.as_ref().and_then(|s| s.channels.get(index)) {
        Some((name, _)) => owned_string(name),
        None => ptr::null_mut(),
    }
}

/// The metadata JSON document as an owned string.
///
/// # Safety
/// `session` must be a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_metadata_json(session: *const XrkSession) -> *mut c_char {
    match session.as_ref() {
        Some(s) => owned_string(s.metadata_json()),
        None => owned_string("{}"),
    }
}

/// Export channel `index` into `out` as an Arrow array stream of one record
/// batch (`timecodes` Int64 ms, then the channel's values; field metadata
/// carries units, dec_pts, interpolate, ...). Returns 0 on success, 1 when
/// `index` is out of range, 2 for a NULL argument.
///
/// # Safety
/// `out` must point to an `ArrowArrayStream` the caller owns and will release.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_stream(
    session: *const XrkSession,
    index: usize,
    out: *mut FFI_ArrowArrayStream,
) -> i32 {
    let Some(session) = session.as_ref() else {
        return 2;
    };
    if out.is_null() {
        return 2;
    }
    match session.channels.get(index) {
        Some((_, batch)) => {
            ptr::write(out, batch_stream(batch));
            0
        }
        None => 1,
    }
}

/// Export the laps table (`num` Int32, `start_time`/`end_time` Int64 ms,
/// `lap_type` Utf8) into `out`. Returns 0 on success, 2 for a NULL argument.
///
/// # Safety
/// `out` must point to an `ArrowArrayStream` the caller owns and will release.
#[no_mangle]
pub unsafe extern "C" fn xrk_laps_stream(
    session: *const XrkSession,
    out: *mut FFI_ArrowArrayStream,
) -> i32 {
    let Some(session) = session.as_ref() else {
        return 2;
    };
    if out.is_null() {
        return 2;
    }
    ptr::write(out, batch_stream(&session.laps));
    0
}

/// Release a string returned by this library. NULL is ignored.
///
/// # Safety
/// `s` must come from this library and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn xrk_string_free(s: *mut c_char) {
    if !s.is_null() {
        drop(CString::from_raw(s));
    }
}

/// This crate's version as a static string (do not free).
#[no_mangle]
pub extern "C" fn xrk_version() -> *const c_char {
    static VERSION: &str = concat!(env!("CARGO_PKG_VERSION"), "\0");
    VERSION.as_ptr() as *const c_char
}

/// Convenience for the metadata JSON (used by tests and other Rust callers).
pub fn metadata_map(meta: &Metadata) -> HashMap<String, Value> {
    match metadata_to_json(meta) {
        Value::Object(m) => m.into_iter().collect(),
        _ => HashMap::new(),
    }
}
