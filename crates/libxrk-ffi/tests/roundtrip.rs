//! Drives the C ABI exactly as a foreign caller would: opens the Fuji 86
//! sample, pulls every channel and the laps through `ArrowArrayStream`, and
//! checks the result against `libxrk::read_xrk` on the same bytes.

use std::ffi::{CStr, CString};
use std::path::PathBuf;

use arrow::array::{Array, Int64Array};
use arrow::ffi_stream::{ArrowArrayStreamReader, FFI_ArrowArrayStream};
use std::ffi::c_char;
use xrk_ffi::*;

fn sample_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrz")
}

unsafe fn read_stream(stream: FFI_ArrowArrayStream) -> arrow::array::RecordBatch {
    let mut reader = ArrowArrayStreamReader::try_new(stream).expect("import stream");
    let batch = reader.next().expect("one batch").expect("batch ok");
    assert!(reader.next().is_none(), "exactly one batch per stream");
    batch
}

#[test]
fn c_abi_round_trip_matches_read_xrk() {
    let path = sample_path();
    let bytes = std::fs::read(&path).expect("sample file (git lfs?)");
    let reference = libxrk::read_xrk(&bytes).expect("reference parse");

    unsafe {
        let c_path = CString::new(path.to_str().unwrap()).unwrap();
        let mut err: *mut c_char = std::ptr::null_mut();
        let session = xrk_open_path(c_path.as_ptr(), &mut err);
        assert!(!session.is_null(), "open failed: {:?}", CStr::from_ptr(err));
        assert!(err.is_null());

        let count = xrk_channel_count(session);
        let gps_channels = if reference.gps.is_some() { 12 } else { 0 };
        assert_eq!(count, reference.channels.len() + gps_channels);

        let mut total_rows = 0usize;
        for i in 0..count {
            let name_ptr = xrk_channel_name(session, i);
            let name = CStr::from_ptr(name_ptr).to_str().unwrap().to_string();
            xrk_string_free(name_ptr);

            let mut stream = FFI_ArrowArrayStream::empty();
            assert_eq!(xrk_channel_stream(session, i, &mut stream), 0);
            let batch = read_stream(stream);
            let schema = batch.schema();
            assert_eq!(schema.field(0).name(), "timecodes");
            assert_eq!(schema.field(1).name(), &name);
            assert!(
                schema.field(1).metadata().contains_key("units"),
                "{name} has field metadata"
            );
            let timecodes = batch
                .column(0)
                .as_any()
                .downcast_ref::<Int64Array>()
                .unwrap();
            total_rows += timecodes.len();

            if i < reference.channels.len() {
                let reference_channel = &reference.channels[i];
                assert_eq!(reference_channel.name, name);
                assert_eq!(reference_channel.timecodes.len(), timecodes.len());
                assert_eq!(
                    reference_channel.timecodes.first(),
                    timecodes.values().first()
                );
                assert_eq!(
                    reference_channel.timecodes.last(),
                    timecodes.values().last()
                );
            }
        }
        assert!(total_rows > 0);
        assert_eq!(
            xrk_channel_stream(session, count, &mut FFI_ArrowArrayStream::empty()),
            1
        );
        assert!(xrk_channel_name(session, count).is_null());

        let mut laps_stream = FFI_ArrowArrayStream::empty();
        assert_eq!(xrk_laps_stream(session, &mut laps_stream), 0);
        let laps = read_stream(laps_stream);
        assert_eq!(laps.num_rows(), reference.laps.len());
        assert_eq!(
            laps.schema()
                .fields()
                .iter()
                .map(|f| f.name().clone())
                .collect::<Vec<_>>(),
            ["num", "start_time", "end_time", "lap_type"]
        );

        let json_ptr = xrk_metadata_json(session);
        let json: serde_json::Value =
            serde_json::from_str(CStr::from_ptr(json_ptr).to_str().unwrap()).unwrap();
        xrk_string_free(json_ptr);
        assert_eq!(
            json["Logger ID"].as_u64().map(|v| v as u32),
            reference.metadata.logger_id
        );
        assert_eq!(
            json["Vehicle"].as_str().map(str::to_string),
            reference.metadata.vehicle
        );

        xrk_close(session);
    }
}

#[test]
fn errors_are_reported_not_thrown() {
    unsafe {
        let mut err: *mut c_char = std::ptr::null_mut();
        let missing = CString::new("/nonexistent/file.xrk").unwrap();
        assert!(xrk_open_path(missing.as_ptr(), &mut err).is_null());
        assert!(!err.is_null());
        assert!(CStr::from_ptr(err)
            .to_str()
            .unwrap()
            .contains("/nonexistent/file.xrk"));
        xrk_string_free(err);

        // libxrk accepts arbitrary bytes as an empty file; either outcome must
        // come back through the ABI cleanly, never as a panic.
        let garbage = [1u8, 2, 3, 4];
        let mut err: *mut c_char = std::ptr::null_mut();
        let session = xrk_open_bytes(garbage.as_ptr(), garbage.len(), &mut err);
        if session.is_null() {
            assert!(!err.is_null());
            xrk_string_free(err);
        } else {
            assert!(err.is_null());
            assert_eq!(xrk_channel_count(session), 0);
            xrk_close(session);
        }

        let mut err: *mut c_char = std::ptr::null_mut();
        assert!(xrk_open_bytes(std::ptr::null(), 0, &mut err).is_null());
        assert!(!err.is_null());
        xrk_string_free(err);

        assert_eq!(xrk_channel_count(std::ptr::null()), 0);
        xrk_close(std::ptr::null_mut());
        xrk_string_free(std::ptr::null_mut());
        assert!(!CStr::from_ptr(xrk_version()).to_str().unwrap().is_empty());
    }
}

#[test]
fn format_float_matches_python_str() {
    assert_eq!(format_float(1.0), "1.0");
    assert_eq!(format_float(0.1), "0.10000000149011612");
    assert_eq!(format_float(1e30), "1.0000000150474662e+30");
}
