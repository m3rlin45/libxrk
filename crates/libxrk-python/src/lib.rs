//! PyO3 bindings for the libxrk crate.
//!
//! Thin wrapper that reads data from Python sources, calls `libxrk::read_xrk()`,
//! and converts the results to PyArrow tables and Python dicts.

use pyo3::prelude::*;
use pyo3::types::PyDict;

pub mod arrow_bridge;
pub mod metadata_bridge;

/// AIM XRK/XRZ telemetry file parser implemented in Rust.
#[pymodule]
fn _aim_xrk_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(aim_xrk, m)?)?;
    m.add_function(wrap_pyfunction!(aim_track_dbg, m)?)?;
    m.add_function(wrap_pyfunction!(
        arrow_bridge::arrow_metadata_roundtrip_test,
        m
    )?)?;
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

/// Parse an AIM XRK/XRZ file and return a LogFile.
#[pyfunction]
#[pyo3(signature = (fname, progress=None))]
fn aim_xrk(py: Python<'_>, fname: Py<PyAny>, progress: Option<Py<PyAny>>) -> PyResult<Py<PyAny>> {
    let fname_bound = fname.bind(py);

    // Read raw bytes from Python source
    let raw_data = read_source_bytes(py, fname_bound)?;

    // Decompress if XRZ
    let data = libxrk::decompress_if_zlib(&raw_data);

    // Parse with Rust parser
    let progress_cb: Option<Box<dyn Fn(usize, usize)>> = progress.map(|cb| {
        let closure: Box<dyn Fn(usize, usize)> = Box::new(move |current, total| {
            Python::attach(|py| {
                let _ = cb.call1(py, (current, total));
            });
        });
        closure
    });

    let mut result = libxrk::parser::parse_xrk(&data, progress_cb.as_ref().map(|cb| cb.as_ref()))
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // Compute non-GPS max end time before moving channel_data out
    let non_gps_max_end_time: Option<i64> = result
        .channel_data
        .values()
        .filter_map(|ch| ch.timecodes.last().copied())
        .max();

    // Build metadata while channel_data is still populated (calibration
    // attribution only considers channels that carry data, like Cython).
    let metadata_struct = libxrk::metadata::extract_metadata(&result);

    // Move channel_data out of result (avoids cloning every channel)
    let channel_data = std::mem::take(&mut result.channel_data);

    // Build Arrow RecordBatches using the core crate's arrow module
    let channel_batches = libxrk::arrow::build_all_channel_batches(
        channel_data,
        &result.channels,
        arrow_bridge::format_float,
    )
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    // Convert to PyArrow tables
    let channels_dict = PyDict::new(py);
    let pa = py.import("pyarrow")?;
    let pa_table_class = pa.getattr("Table")?;

    for (name, batch) in channel_batches {
        let py_batch = arrow_bridge::batch_to_pyarrow(py, batch)?;
        let table = pa_table_class.call_method1("from_batches", (vec![py_batch.bind(py)],))?;
        channels_dict.set_item(name, table)?;
    }

    // Decode GPS channels
    let mut gps_result = libxrk::gps::decode_gps(&result.gps_data, result.time_offset)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // Apply GPS timing fix
    if let Some(ref mut gps) = gps_result {
        let gnfi_timecodes: Vec<i64> =
            if !result.gnfi_data.is_empty() && result.gnfi_data.len().is_multiple_of(32) {
                let n_records = result.gnfi_data.len() / 32;
                (0..n_records)
                    .map(|i| {
                        let offset = i * 32;
                        i32::from_le_bytes([
                            result.gnfi_data[offset],
                            result.gnfi_data[offset + 1],
                            result.gnfi_data[offset + 2],
                            result.gnfi_data[offset + 3],
                        ]) as i64
                            - result.time_offset
                    })
                    .collect()
            } else {
                Vec::new()
            };

        let corrections = libxrk::gps::timing::detect_gap_corrections(
            &gps.timecodes,
            &gnfi_timecodes,
            non_gps_max_end_time,
            40.0,
        );
        if !corrections.is_empty() {
            libxrk::gps::timing::apply_corrections(&mut gps.timecodes, &corrections);
        }
    }

    // Convert metadata (extracted above, before channel_data was moved out)
    let metadata_dict = metadata_bridge::metadata_to_pydict(py, &metadata_struct)?;

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
        fname_bound
            .call_method0("__fspath__")?
            .extract::<String>()?
    } else {
        "<unknown>".to_string()
    };

    // Build laps
    let mut processed_laps = libxrk::parser::get_processed_laps(&result)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // Validate LAP-message-based laps
    if processed_laps
        .iter()
        .any(|l| l.start_time < 0 || l.end_time <= l.start_time)
    {
        processed_laps.clear();
    }

    // Classify last lap as "in" if GPS shows car is not near S/F at end
    if !processed_laps.is_empty() && processed_laps.last().unwrap().lap_type != "out" {
        if let (Some(trk_marker), Some(ref gps)) = (get_trk_marker(&result), &gps_result) {
            if !gps.timecodes.is_empty() {
                let (sf_lat, sf_lon) = trk_marker;
                let (sf_x, sf_y, sf_z) = libxrk::gps::utils::lla2ecef(sf_lat, sf_lon, 0.0);

                let end_time = processed_laps.last().unwrap().end_time;
                let idx = gps
                    .timecodes
                    .partition_point(|&t| t < end_time)
                    .min(gps.timecodes.len() - 1);
                let (ex, ey, ez) =
                    libxrk::gps::utils::lla2ecef(gps.latitude[idx], gps.longitude[idx], 0.0);

                let dist = ((sf_x - ex).powi(2) + (sf_y - ey).powi(2) + (sf_z - ez).powi(2)).sqrt();
                if dist > 30.0 {
                    let last_idx = processed_laps.len() - 1;
                    processed_laps[last_idx].lap_type = "in".to_string();
                }
            }
        }
    }

    if processed_laps.is_empty() {
        // GPS-based lap detection
        if let Some(trk_marker) = get_trk_marker(&result) {
            if let Some(ref gps) = gps_result {
                let xyz: Vec<[f64; 3]> = gps
                    .latitude
                    .iter()
                    .zip(gps.longitude.iter())
                    .map(|(&lat, &lon)| {
                        let (x, y, z) = libxrk::gps::utils::lla2ecef(lat, lon, 0.0);
                        [x, y, z]
                    })
                    .collect();

                let lap_markers = libxrk::gps::utils::find_laps(&xyz, &gps.timecodes, trk_marker);

                if !lap_markers.is_empty() {
                    let session_end = *gps.timecodes.last().unwrap_or(&0);
                    let mut all_markers = lap_markers.clone();
                    all_markers.push(session_end as f64);

                    let lap_count = all_markers.windows(2).len();
                    for (i, window) in all_markers.windows(2).enumerate() {
                        processed_laps.push(libxrk::ProcessedLap {
                            num: i as i32,
                            start_time: window[0] as i64,
                            end_time: window[1] as i64,
                            lap_type: if i == lap_count - 1 {
                                "in".to_string()
                            } else {
                                "full".to_string()
                            },
                        });
                    }
                }
            }
        }
    }

    // Build GPS Arrow tables after lap detection (consumes gps_result by value)
    if let Some(gps) = gps_result {
        let gps_batches = libxrk::arrow::build_gps_channel_batches(gps, arrow_bridge::format_float)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        for (name, batch) in gps_batches {
            let py_batch = arrow_bridge::batch_to_pyarrow(py, batch)?;
            let table = pa_table_class.call_method1("from_batches", (vec![py_batch.bind(py)],))?;
            channels_dict.set_item(name, table)?;
        }
    }

    let laps_batch = libxrk::arrow::build_laps_batch(&processed_laps)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    let laps_py = arrow_bridge::batch_to_pyarrow(py, laps_batch)?;
    let laps_table = pa_table_class.call_method1("from_batches", (vec![laps_py.bind(py)],))?;

    let base_module = py.import("libxrk.base")?;
    let logfile_class = base_module.getattr("LogFile")?;
    let logfile =
        logfile_class.call1((channels_dict, laps_table, metadata_dict.bind(py), file_name))?;

    Ok(logfile.unbind())
}

/// Debug function to extract track data from an AIM XRK file.
#[pyfunction]
fn aim_track_dbg(py: Python<'_>, fname: Py<PyAny>) -> PyResult<Py<PyAny>> {
    let fname_bound = fname.bind(py);
    let raw_data = read_source_bytes(py, fname_bound)?;
    let data = libxrk::decompress_if_zlib(&raw_data);

    let result = libxrk::parser::parse_xrk(&data, None)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let dict = PyDict::new(py);
    for (&tok, msgs) in &result.header_messages {
        let tok_str = libxrk::messages::tokenc(tok);
        let py_msgs = pyo3::types::PyList::empty(py);
        for msg in msgs {
            let payload = libxrk::messages::dispatch_payload(msg);
            let py_payload = payload_to_python(py, &payload)?;
            py_msgs.append(py_payload)?;
        }
        dict.set_item(tok_str, py_msgs)?;
    }

    Ok(dict.into_any().unbind())
}

/// Extract TRK start/finish marker coordinates from a ParseResult.
fn get_trk_marker(result: &libxrk::ParseResult) -> Option<(f64, f64)> {
    let trk_msgs = result
        .header_messages
        .get(&libxrk::messages::tokens::trk())?;
    let last_msg = trk_msgs.last()?;
    let payload = libxrk::messages::dispatch_payload(last_msg);
    if let libxrk::messages::Payload::Trk(trk) = payload {
        Some((trk.sf_lat, trk.sf_long))
    } else {
        None
    }
}

/// Convert a Payload to a Python object for aim_track_dbg.
fn payload_to_python(py: Python<'_>, payload: &libxrk::messages::Payload) -> PyResult<Py<PyAny>> {
    match payload {
        libxrk::messages::Payload::StringMsg(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        libxrk::messages::Payload::Vet(v) => match v {
            libxrk::payloads::vet::VetPayload::Mode(s) => {
                Ok(s.into_pyobject(py)?.into_any().unbind())
            }
            libxrk::payloads::vet::VetPayload::Value(val) => {
                Ok(val.into_pyobject(py)?.into_any().unbind())
            }
        },
        libxrk::messages::Payload::Unknown(data) => {
            Ok(pyo3::types::PyBytes::new(py, data).into_any().unbind())
        }
        _ => Ok(py.None()),
    }
}
