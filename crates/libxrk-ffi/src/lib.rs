//! C ABI for libxrk.
//!
//! A session is parsed once into Arrow record batches (one per channel, one
//! for the laps table) and handed out as an opaque [`XrkSession`] pointer.
//! Channels and laps are exported through the Arrow C Data Interface
//! (`ArrowArrayStream`), so any Arrow implementation can consume them with no
//! copies; metadata is exported as a JSON document.
//!
//! What a session holds is what the Python `LogFile` of the Rust backend
//! holds: the same channels in the same order, with the same schemas and
//! field metadata, the same laps table, and the `LogFile.metadata` dict as
//! JSON with the same keys in the same order. `tests/test_ffi.py` asserts
//! that on every fixture.
//!
//! # Conventions
//!
//! - Every function is `unsafe extern "C"` and panic-safe: a panic is caught
//!   and reported as that function's error value.
//! - Strings returned as `*mut c_char` are owned by the caller and must be
//!   released with [`xrk_string_free`]. Error out-parameters receive such a
//!   string, or NULL on success. All strings are NUL-terminated UTF-8.
//! - A session is immutable once opened: any number of threads may read
//!   from the same session at the same time. [`xrk_close`] must not race
//!   with any other call on that session.
//! - An exported stream shares the session's buffers by reference count and
//!   stays valid after [`xrk_close`]; the memory is freed when the session
//!   and every stream exported from it have been released.

use std::ffi::{c_char, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

use arrow::array::{RecordBatch, RecordBatchIterator};
use arrow::ffi_stream::FFI_ArrowArrayStream;
use libxrk::arrow::{
    build_channel_batch, build_gps_channel_batches, build_laps_batch, format_float, ChannelMetadata,
};
use libxrk::gps::GPS_CHANNEL_DEFS;
use libxrk::metadata::{Metadata, MetadataValue};
use libxrk::parser::ChannelData;
use serde_json::{Map, Value};

/// [`xrk_channel_stream`] / [`xrk_laps_stream`]: the stream was exported.
pub const XRK_OK: i32 = 0;
/// [`xrk_channel_stream`]: `index` is not below [`xrk_channel_count`].
pub const XRK_ERR_INDEX: i32 = 1;
/// [`xrk_channel_stream`] / [`xrk_laps_stream`]: an argument was NULL.
pub const XRK_ERR_NULL: i32 = 2;
/// [`xrk_channel_stream`] / [`xrk_laps_stream`]: the library panicked.
pub const XRK_ERR_PANIC: i32 = 3;

/// A parsed session: channel batches in channel-index order (then the GPS
/// channels), the laps batch and the metadata JSON.
pub struct XrkSession {
    channels: Vec<(String, RecordBatch)>,
    laps: RecordBatch,
    metadata_json: String,
}

/// Parse `data` (.xrk or zlib-compressed .xrz bytes) into a session.
pub fn open_session(data: &[u8]) -> Result<XrkSession, String> {
    let mut file = libxrk::read_xrk(data).map_err(|e| e.to_string())?;

    let mut raw_channels = std::mem::take(&mut file.channels);
    raw_channels.sort_by_key(|ch| ch.index);
    let mut channels = Vec::with_capacity(raw_channels.len() + GPS_CHANNEL_DEFS.len());
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
    let metadata_json = metadata_to_json(&file.metadata).to_string();

    Ok(XrkSession {
        channels,
        laps,
        metadata_json,
    })
}

/// The metadata document ([`Metadata::to_entries`]) as a JSON object.
///
/// Keys keep the document's order. JSON has no NaN or infinity: a non-finite
/// number becomes `null`.
pub fn metadata_to_json(meta: &Metadata) -> Value {
    entries_to_json(&meta.to_entries())
}

fn entries_to_json(entries: &[(String, MetadataValue)]) -> Value {
    let mut map = Map::new();
    for (key, value) in entries {
        map.insert(key.clone(), value_to_json(value));
    }
    Value::Object(map)
}

fn value_to_json(value: &MetadataValue) -> Value {
    match value {
        MetadataValue::Null => Value::Null,
        MetadataValue::Str(v) => Value::String(v.clone()),
        MetadataValue::Int(v) => Value::from(*v),
        MetadataValue::Float(v) => Value::from(*v),
        MetadataValue::List(items) => Value::Array(items.iter().map(value_to_json).collect()),
        MetadataValue::Map(entries) => entries_to_json(entries),
    }
}

fn batch_stream(batch: &RecordBatch) -> FFI_ArrowArrayStream {
    let reader = RecordBatchIterator::new(vec![Ok(batch.clone())], batch.schema());
    FFI_ArrowArrayStream::new(Box::new(reader))
}

/// `s` as a caller-owned C string (an interior NUL cannot be represented and
/// is dropped), or NULL if that fails.
fn owned_string(s: &str) -> *mut c_char {
    CString::new(s.replace('\0', "")).map_or(ptr::null_mut(), CString::into_raw)
}

/// Run `f`, turning a panic into `on_panic`: unwinding must not cross the
/// `extern "C"` boundary.
fn guarded<T>(on_panic: T, f: impl FnOnce() -> T) -> T {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(on_panic)
}

unsafe fn open_guarded(
    err: *mut *mut c_char,
    open: impl FnOnce() -> Result<XrkSession, String>,
) -> *mut XrkSession {
    if !err.is_null() {
        *err = ptr::null_mut();
    }
    let outcome = guarded(
        Err("libxrk panicked while parsing the file".to_string()),
        open,
    );
    match outcome {
        Ok(session) => Box::into_raw(Box::new(session)),
        Err(message) => {
            if !err.is_null() {
                *err = owned_string(&message);
            }
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
        let data = std::fs::read(path).map_err(|e| format!("{}: {}", path, e))?;
        open_session(&data)
    })
}

/// Parse `len` bytes at `data`. The bytes are only read during the call.
/// Returns NULL and sets `*err` on failure.
///
/// # Safety
/// `data` must point to `len` readable bytes (it may be NULL when `len` is
/// 0); `err` may be NULL.
#[no_mangle]
pub unsafe extern "C" fn xrk_open_bytes(
    data: *const u8,
    len: usize,
    err: *mut *mut c_char,
) -> *mut XrkSession {
    open_guarded(err, || {
        if len == 0 {
            return open_session(&[]);
        }
        if data.is_null() {
            return Err("data is NULL".into());
        }
        open_session(std::slice::from_raw_parts(data, len))
    })
}

/// Release a session. NULL is ignored.
///
/// # Safety
/// `session` must come from `xrk_open_*` and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn xrk_close(session: *mut XrkSession) {
    if !session.is_null() {
        guarded((), || drop(Box::from_raw(session)));
    }
}

/// Number of channels (CHS channels, then the GPS channels). 0 for NULL.
///
/// # Safety
/// `session` must be NULL or a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_count(session: *const XrkSession) -> usize {
    session.as_ref().map_or(0, |s| s.channels.len())
}

/// The name of channel `index` as an owned string, or NULL when out of range.
///
/// # Safety
/// `session` must be NULL or a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_name(session: *const XrkSession, index: usize) -> *mut c_char {
    guarded(ptr::null_mut(), || {
        match session.as_ref().and_then(|s| s.channels.get(index)) {
            Some((name, _)) => owned_string(name),
            None => ptr::null_mut(),
        }
    })
}

/// The metadata JSON document as an owned string: one object with the keys
/// of the Python `LogFile.metadata` dict, in the same order (`"{}"` for
/// NULL). Non-finite numbers are `null`.
///
/// # Safety
/// `session` must be NULL or a live session pointer.
#[no_mangle]
pub unsafe extern "C" fn xrk_metadata_json(session: *const XrkSession) -> *mut c_char {
    guarded(ptr::null_mut(), || match session.as_ref() {
        Some(s) => owned_string(&s.metadata_json),
        None => owned_string("{}"),
    })
}

/// Export channel `index` into `out` as an Arrow array stream of one record
/// batch (`timecodes` Int64 ms, then the channel's values; field metadata
/// carries units, dec_pts, interpolate, ...). Returns [`XRK_OK`],
/// [`XRK_ERR_INDEX`], [`XRK_ERR_NULL`] or [`XRK_ERR_PANIC`]; `out` is only
/// written on [`XRK_OK`].
///
/// # Safety
/// `session` must be NULL or a live session pointer. `out` must point to
/// writable memory for one `ArrowArrayStream`; whatever it held is
/// overwritten, not released. The caller releases the exported stream.
#[no_mangle]
pub unsafe extern "C" fn xrk_channel_stream(
    session: *const XrkSession,
    index: usize,
    out: *mut FFI_ArrowArrayStream,
) -> i32 {
    guarded(XRK_ERR_PANIC, || {
        let Some(session) = session.as_ref() else {
            return XRK_ERR_NULL;
        };
        if out.is_null() {
            return XRK_ERR_NULL;
        }
        match session.channels.get(index) {
            Some((_, batch)) => {
                ptr::write(out, batch_stream(batch));
                XRK_OK
            }
            None => XRK_ERR_INDEX,
        }
    })
}

/// Export the laps table (`num` Int32, `start_time`/`end_time` Int64 ms,
/// `lap_type` Utf8) into `out`. Returns [`XRK_OK`], [`XRK_ERR_NULL`] or
/// [`XRK_ERR_PANIC`]; `out` is only written on [`XRK_OK`].
///
/// # Safety
/// As for [`xrk_channel_stream`].
#[no_mangle]
pub unsafe extern "C" fn xrk_laps_stream(
    session: *const XrkSession,
    out: *mut FFI_ArrowArrayStream,
) -> i32 {
    guarded(XRK_ERR_PANIC, || {
        let Some(session) = session.as_ref() else {
            return XRK_ERR_NULL;
        };
        if out.is_null() {
            return XRK_ERR_NULL;
        }
        ptr::write(out, batch_stream(&session.laps));
        XRK_OK
    })
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
