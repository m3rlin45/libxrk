use pyo3::prelude::*;
use pyo3::types::PyDict;

pub mod arrow_bridge;
pub mod decoders;
pub mod gps_processing;
pub mod gps_timing;
pub mod gps_utils;
pub mod messages;
pub mod metadata;
pub mod parser;
pub mod payloads;
pub mod tables;

/// AIM XRK/XRZ telemetry file parser implemented in Rust.
#[pymodule]
fn _aim_xrk_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(aim_xrk, m)?)?;
    m.add_function(wrap_pyfunction!(aim_track_dbg, m)?)?;
    m.add_function(wrap_pyfunction!(arrow_bridge::arrow_metadata_roundtrip_test, m)?)?;
    Ok(())
}

/// Read raw bytes from a Python source (path, bytes, BytesIO, etc.).
fn read_source_bytes(_py: Python<'_>, source: &Bound<'_, PyAny>) -> PyResult<Vec<u8>> {
    // bytes or bytearray
    if let Ok(bytes_val) = source.extract::<Vec<u8>>() {
        return Ok(bytes_val);
    }

    // memoryview → convert to bytes first
    if source.is_instance_of::<pyo3::types::PyMemoryView>() {
        let bytes_obj = source.call_method0("tobytes")?;
        return bytes_obj.extract::<Vec<u8>>();
    }

    // File-like object (has 'read' method)
    if source.hasattr("read")? {
        source.call_method1("seek", (0,))?;
        let data = source.call_method0("read")?;
        return data.extract::<Vec<u8>>();
    }

    // File path (str or PathLike)
    let path_str: String = if let Ok(s) = source.extract::<String>() {
        s
    } else if source.hasattr("__fspath__")? {
        source.call_method0("__fspath__")?.extract::<String>()?
    } else {
        return Err(pyo3::exceptions::PyTypeError::new_err(
            "Expected str, bytes, bytearray, memoryview, PathLike, or file-like object",
        ));
    };

    std::fs::read(&path_str).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read {}: {}", path_str, e))
    })
}

/// Decompress zlib-compressed data if detected, otherwise return as-is.
/// XRZ files are XRK files compressed with zlib.
fn decompress_if_zlib(data: Vec<u8>) -> Vec<u8> {
    if data.len() < 2 {
        return data;
    }
    // Check for zlib magic bytes
    if data[0] == 0x78 && matches!(data[1], 0x01 | 0x9C | 0xDA) {
        use std::io::Read;
        let mut decoder = flate2::read::ZlibDecoder::new(&data[..]);
        let mut decompressed = Vec::new();
        match decoder.read_to_end(&mut decompressed) {
            Ok(_) => decompressed,
            Err(_) => {
                // Truncated stream — return whatever we got
                if decompressed.is_empty() { data } else { decompressed }
            }
        }
    } else {
        data
    }
}

/// Parse an AIM XRK/XRZ file and return a LogFile.
#[pyfunction]
#[pyo3(signature = (fname, progress=None))]
fn aim_xrk(
    py: Python<'_>,
    fname: Py<PyAny>,
    progress: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    let fname_bound = fname.bind(py);

    // Read raw bytes
    let raw_data = read_source_bytes(py, fname_bound)?;

    // Decompress if XRZ
    let data = decompress_if_zlib(raw_data);

    // Parse with Rust parser
    let progress_cb: Option<Box<dyn Fn(usize, usize)>> = progress.map(|cb| {
        let closure: Box<dyn Fn(usize, usize)> = Box::new(move |current, total| {
            Python::attach(|py| {
                let _ = cb.call1(py, (current, total));
            });
        });
        closure
    });

    let mut result = parser::parse_xrk(
        &data,
        progress_cb.as_ref().map(|cb| cb.as_ref()),
    );

    // Compute non-GPS max end time before moving channel_data out
    let non_gps_max_end_time: Option<i64> = result.channel_data.values()
        .filter_map(|ch| ch.timecodes.last().copied())
        .max();

    // Move channel_data out of result (avoids cloning every channel)
    let channel_data = std::mem::take(&mut result.channel_data);
    let channel_tables = arrow_bridge::build_all_channel_tables(py, channel_data, &result.channels)?;

    // Build channels dict
    let channels_dict = PyDict::new(py);
    // Import pyarrow.Table to convert RecordBatch → Table
    let pa = py.import("pyarrow")?;
    let pa_table_class = pa.getattr("Table")?;

    for (name, batch) in &channel_tables {
        let table = pa_table_class.call_method1("from_batches", (vec![batch.bind(py)],))?;
        channels_dict.set_item(name, table)?;
    }

    // Decode GPS channels
    let mut gps_result = gps_processing::decode_gps(&result.gps_data, result.time_offset);

    // Apply GPS timing fix in Rust (before building Arrow tables)
    if let Some(ref mut gps) = gps_result {
        // Extract GNFI timecodes from raw data
        let gnfi_timecodes: Vec<i64> = if !result.gnfi_data.is_empty() && result.gnfi_data.len() % 32 == 0 {
            let n_records = result.gnfi_data.len() / 32;
            (0..n_records)
                .map(|i| {
                    let offset = i * 32;
                    i32::from_le_bytes([
                        result.gnfi_data[offset], result.gnfi_data[offset + 1],
                        result.gnfi_data[offset + 2], result.gnfi_data[offset + 3],
                    ]) as i64 - result.time_offset
                })
                .collect()
        } else {
            Vec::new()
        };

        // Detect and apply corrections to GPS timecodes
        let corrections = gps_timing::detect_gap_corrections(
            &gps.timecodes,
            &gnfi_timecodes,
            non_gps_max_end_time,
            40.0,
        );
        if !corrections.is_empty() {
            gps_timing::apply_corrections(&mut gps.timecodes, &corrections);
        }
    }

    // Build metadata
    let metadata_dict = metadata::extract_metadata(py, &result)?;

    // Determine file_name
    let file_name: String = if fname_bound.is_instance_of::<pyo3::types::PyBytes>()
        || fname_bound.is_instance_of::<pyo3::types::PyByteArray>()
        || fname_bound.is_instance_of::<pyo3::types::PyMemoryView>()
        || fname_bound.hasattr("read")?
    {
        "<bytes>".to_string()
    } else if let Ok(s) = fname_bound.extract::<String>() {
        s
    } else if fname_bound.hasattr("__fspath__")? {
        fname_bound.call_method0("__fspath__")?.extract::<String>()?
    } else {
        "<unknown>".to_string()
    };

    // Build laps — GPS timing fix was applied above, so GPS timecodes are corrected
    let mut processed_laps = parser::get_processed_laps(&result);

    // Validate LAP-message-based laps: some AIM firmware writes relative end_times
    // (≈ duration) instead of absolute session times, producing negative start_times.
    // Fall back to GPS-based detection when this happens.
    if processed_laps.iter().any(|l| l.start_time < 0 || l.end_time <= l.start_time) {
        processed_laps.clear();
    }

    if processed_laps.is_empty() {
        // GPS-based lap detection: use corrected GPS data directly from Rust
        if let Some(trk_marker) = get_trk_marker(&result) {
            if let Some(ref gps) = gps_result {
                // Convert lat/lon to ECEF for lap detection
                let xyz: Vec<[f64; 3]> = gps.latitude.iter().zip(gps.longitude.iter())
                    .map(|(&lat, &lon)| {
                        let (x, y, z) = gps_utils::lla2ecef(lat, lon, 0.0);
                        [x, y, z]
                    })
                    .collect();

                let lap_markers = gps_utils::find_laps(&xyz, &gps.timecodes, trk_marker);

                if !lap_markers.is_empty() {
                    let session_end = *gps.timecodes.last().unwrap_or(&0);
                    let mut all_markers = lap_markers.clone();
                    all_markers.push(session_end as f64);

                    for (i, window) in all_markers.windows(2).enumerate() {
                        processed_laps.push(parser::ProcessedLap {
                            num: i as i32,
                            start_time: window[0] as i64,
                            end_time: window[1] as i64,
                        });
                    }
                }
            }
        }
    }

    // Build GPS Arrow tables after lap detection (consumes gps_result by value)
    if let Some(gps) = gps_result {
        let gps_tables = arrow_bridge::build_gps_channel_tables(py, gps)?;
        for (name, batch) in &gps_tables {
            let table = pa_table_class.call_method1("from_batches", (vec![batch.bind(py)],))?;
            channels_dict.set_item(name, table)?;
        }
    }

    let laps_batch = arrow_bridge::build_laps_table(py, &processed_laps)?;
    let laps_table = pa_table_class.call_method1("from_batches", (vec![laps_batch.bind(py)],))?;

    let base_module = py.import("libxrk.base")?;
    let logfile_class = base_module.getattr("LogFile")?;
    let logfile = logfile_class.call1((
        channels_dict,
        laps_table,
        metadata_dict.bind(py),
        file_name,
    ))?;

    Ok(logfile.unbind())
}

/// Debug function to extract track data from an AIM XRK file.
#[pyfunction]
fn aim_track_dbg(py: Python<'_>, fname: Py<PyAny>) -> PyResult<Py<PyAny>> {
    let fname_bound = fname.bind(py);
    let raw_data = read_source_bytes(py, fname_bound)?;
    let data = decompress_if_zlib(raw_data);

    let result = parser::parse_xrk(&data, None);

    // Return {tokenc(k): v} for all messages
    let dict = PyDict::new(py);
    for (&tok, msgs) in &result.header_messages {
        let tok_str = messages::tokenc(tok);
        let py_msgs = pyo3::types::PyList::empty(py);
        for msg in msgs {
            let payload = messages::dispatch_payload(msg);
            let py_payload = payload_to_python(py, &payload)?;
            py_msgs.append(py_payload)?;
        }
        dict.set_item(tok_str, py_msgs)?;
    }

    Ok(dict.into_any().unbind())
}

/// Extract TRK start/finish marker coordinates from a ParseResult.
fn get_trk_marker(result: &parser::ParseResult) -> Option<(f64, f64)> {
    let trk_msgs = result.header_messages.get(&messages::tokens::trk())?;
    let last_msg = trk_msgs.last()?;
    let payload = messages::dispatch_payload(last_msg);
    if let messages::Payload::Trk(trk) = payload {
        Some((trk.sf_lat, trk.sf_long))
    } else {
        None
    }
}

/// Convert a Payload to a Python object for aim_track_dbg.
fn payload_to_python(py: Python<'_>, payload: &messages::Payload) -> PyResult<Py<PyAny>> {
    match payload {
        messages::Payload::StringMsg(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        messages::Payload::Vet(v) => Ok((*v).into_pyobject(py)?.into_any().unbind()),
        messages::Payload::Unknown(data) => Ok(pyo3::types::PyBytes::new(py, data).into_any().unbind()),
        _ => Ok(py.None()),
    }
}
