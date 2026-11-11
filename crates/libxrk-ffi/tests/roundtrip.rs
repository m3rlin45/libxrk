//! Drives the C ABI exactly as a foreign caller would: opens the Fuji 86
//! sample, pulls every channel and the laps through `ArrowArrayStream`, and
//! checks the result against the core crate on the same bytes.
//!
//! Parity with the Python `LogFile` (every fixture, GPS channels and the
//! metadata document included) is asserted from Python, in `tests/test_ffi.py`.

use std::ffi::{c_char, CStr, CString};
use std::path::PathBuf;

use arrow::array::{Array, Int64Array, RecordBatch};
use arrow::ffi_stream::{ArrowArrayStreamReader, FFI_ArrowArrayStream};
use libxrk::arrow::{build_all_channel_batches, format_float};
use libxrk::gps::GPS_CHANNEL_DEFS;
use xrk_ffi::*;

fn sample_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrz")
}

unsafe fn read_stream(stream: FFI_ArrowArrayStream) -> RecordBatch {
    let mut reader = ArrowArrayStreamReader::try_new(stream).expect("import stream");
    let batch = reader.next().expect("one batch").expect("batch ok");
    assert!(reader.next().is_none(), "exactly one batch per stream");
    batch
}

unsafe fn take_string(s: *mut c_char) -> String {
    assert!(!s.is_null());
    let text = CStr::from_ptr(s).to_str().unwrap().to_string();
    xrk_string_free(s);
    text
}

/// The CHS channel batches as the Python binding builds them: straight from
/// the parse result, not through `read_xrk`.
fn reference_channel_batches(bytes: &[u8]) -> Vec<(String, RecordBatch)> {
    let data = libxrk::decompress_if_zlib(bytes);
    let mut result = libxrk::parser::parse_xrk(&data, None).expect("reference parse");
    let channel_data = std::mem::take(&mut result.channel_data);
    let gps_reserved: Vec<&str> = if result.gps_data.is_empty() {
        Vec::new()
    } else {
        GPS_CHANNEL_DEFS.iter().map(|d| d.name).collect()
    };
    build_all_channel_batches(channel_data, &result.channels, &gps_reserved, format_float)
        .expect("reference batches")
}

#[test]
fn c_abi_round_trip_matches_the_core_crate() {
    let path = sample_path();
    let bytes = std::fs::read(&path).expect("sample file");
    let reference = libxrk::read_xrk(&bytes).expect("reference parse");
    let reference_batches = reference_channel_batches(&bytes);
    assert!(reference.gps.is_some(), "the sample carries GPS data");

    unsafe {
        let c_path = CString::new(path.to_str().unwrap()).unwrap();
        let mut err: *mut c_char = std::ptr::null_mut();
        let session = xrk_open_path(c_path.as_ptr(), &mut err);
        assert!(!session.is_null(), "open failed: {:?}", CStr::from_ptr(err));
        assert!(err.is_null());

        // CHS channels in index order, then every GPS channel.
        let count = xrk_channel_count(session);
        assert_eq!(count, reference_batches.len() + GPS_CHANNEL_DEFS.len());

        let mut batches = Vec::with_capacity(count);
        for i in 0..count {
            let name = take_string(xrk_channel_name(session, i));
            let mut stream = FFI_ArrowArrayStream::empty();
            assert_eq!(xrk_channel_stream(session, i, &mut stream), XRK_OK);
            batches.push((name, read_stream(stream)));
        }

        for ((name, batch), (reference_name, reference_batch)) in
            batches.iter().zip(&reference_batches)
        {
            assert_eq!(name, reference_name);
            // Schema (names, types, field metadata) and every value.
            assert_eq!(batch, reference_batch, "{name}");
        }

        let gps = reference.gps.as_ref().unwrap();
        for ((name, batch), def) in batches[reference_batches.len()..]
            .iter()
            .zip(GPS_CHANNEL_DEFS)
        {
            assert_eq!(name, def.name);
            let schema = batch.schema();
            assert_eq!(schema.field(0).name(), "timecodes");
            assert_eq!(schema.field(1).name(), def.name);
            assert_eq!(schema.field(1).metadata()["units"], def.units);
            let timecodes = batch
                .column(0)
                .as_any()
                .downcast_ref::<Int64Array>()
                .unwrap();
            assert_eq!(timecodes.values(), gps.timecodes.as_slice(), "{name}");
        }

        // Out-of-range and NULL arguments are reported, and leave `out` alone.
        let mut untouched = FFI_ArrowArrayStream::empty();
        assert_eq!(
            xrk_channel_stream(session, count, &mut untouched),
            XRK_ERR_INDEX
        );
        assert!(untouched.release.is_none());
        assert_eq!(
            xrk_channel_stream(session, 0, std::ptr::null_mut()),
            XRK_ERR_NULL
        );
        assert_eq!(
            xrk_channel_stream(std::ptr::null(), 0, &mut untouched),
            XRK_ERR_NULL
        );
        assert_eq!(xrk_laps_stream(session, std::ptr::null_mut()), XRK_ERR_NULL);
        assert!(xrk_channel_name(session, count).is_null());

        let mut laps_stream = FFI_ArrowArrayStream::empty();
        assert_eq!(xrk_laps_stream(session, &mut laps_stream), XRK_OK);
        let laps = read_stream(laps_stream);
        assert_eq!(
            laps,
            libxrk::arrow::build_laps_batch(&reference.laps).unwrap()
        );
        assert!(laps.num_rows() > 0);

        let json = take_string(xrk_metadata_json(session));
        let document: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(document, metadata_to_json(&reference.metadata));
        assert_eq!(
            document["Logger ID"].as_u64().map(|v| v as u32),
            reference.metadata.logger_id
        );
        assert_eq!(
            document["Vehicle"].as_str().map(str::to_string),
            reference.metadata.vehicle
        );
        // The keys come out in the document's order, not sorted.
        let keys: Vec<&String> = document.as_object().unwrap().keys().collect();
        let expected: Vec<String> = reference
            .metadata
            .to_entries()
            .into_iter()
            .map(|(key, _)| key)
            .collect();
        assert_eq!(keys, expected.iter().collect::<Vec<_>>());
        assert!(keys.windows(2).any(|pair| pair[0] > pair[1]));

        // A stream exported before the session is closed outlives it.
        let mut stream = FFI_ArrowArrayStream::empty();
        assert_eq!(xrk_channel_stream(session, 0, &mut stream), XRK_OK);
        xrk_close(session);
        assert_eq!(read_stream(stream), batches[0].1);
    }
}

#[test]
fn open_bytes_matches_open_path() {
    let path = sample_path();
    let bytes = std::fs::read(&path).expect("sample file");
    unsafe {
        let mut err: *mut c_char = std::ptr::null_mut();
        let from_bytes = xrk_open_bytes(bytes.as_ptr(), bytes.len(), &mut err);
        assert!(!from_bytes.is_null() && err.is_null());
        let c_path = CString::new(path.to_str().unwrap()).unwrap();
        let from_path = xrk_open_path(c_path.as_ptr(), &mut err);
        assert!(!from_path.is_null() && err.is_null());

        let count = xrk_channel_count(from_bytes);
        assert_eq!(count, xrk_channel_count(from_path));
        for i in 0..count {
            let mut a = FFI_ArrowArrayStream::empty();
            let mut b = FFI_ArrowArrayStream::empty();
            assert_eq!(xrk_channel_stream(from_bytes, i, &mut a), XRK_OK);
            assert_eq!(xrk_channel_stream(from_path, i, &mut b), XRK_OK);
            assert_eq!(read_stream(a), read_stream(b));
        }
        assert_eq!(
            take_string(xrk_metadata_json(from_bytes)),
            take_string(xrk_metadata_json(from_path))
        );
        xrk_close(from_bytes);
        xrk_close(from_path);
    }
}

#[test]
fn errors_are_reported_not_thrown() {
    unsafe {
        let mut err: *mut c_char = std::ptr::null_mut();
        let missing = CString::new("/nonexistent/file.xrk").unwrap();
        assert!(xrk_open_path(missing.as_ptr(), &mut err).is_null());
        assert!(take_string(err).contains("/nonexistent/file.xrk"));

        // A NULL error out-parameter is allowed.
        assert!(xrk_open_path(missing.as_ptr(), std::ptr::null_mut()).is_null());
        assert!(xrk_open_path(std::ptr::null(), &mut err).is_null());
        assert_eq!(take_string(err), "path is NULL");

        // libxrk accepts arbitrary bytes as an empty file; either outcome must
        // come back through the ABI cleanly, never as a panic. An empty
        // buffer may be passed as NULL (what a C# `fixed` over an empty span
        // yields); NULL with a length is a caller bug.
        let garbage = [1u8, 2, 3, 4];
        for (data, len) in [
            (garbage.as_ptr(), garbage.len()),
            (garbage.as_ptr(), 0),
            (std::ptr::null(), 0),
        ] {
            let mut err: *mut c_char = std::ptr::null_mut();
            let session = xrk_open_bytes(data, len, &mut err);
            if session.is_null() {
                assert!(!take_string(err).is_empty());
            } else {
                assert!(err.is_null());
                assert_eq!(xrk_channel_count(session), 0);
                let mut laps = FFI_ArrowArrayStream::empty();
                assert_eq!(xrk_laps_stream(session, &mut laps), XRK_OK);
                assert_eq!(read_stream(laps).num_rows(), 0);
                xrk_close(session);
            }
        }

        let mut err: *mut c_char = std::ptr::null_mut();
        assert!(xrk_open_bytes(std::ptr::null(), 4, &mut err).is_null());
        assert_eq!(take_string(err), "data is NULL");

        assert_eq!(xrk_channel_count(std::ptr::null()), 0);
        assert!(xrk_channel_name(std::ptr::null(), 0).is_null());
        assert_eq!(take_string(xrk_metadata_json(std::ptr::null())), "{}");
        xrk_close(std::ptr::null_mut());
        xrk_string_free(std::ptr::null_mut());
        assert_eq!(
            CStr::from_ptr(xrk_version()).to_str().unwrap(),
            env!("CARGO_PKG_VERSION")
        );
    }
}
