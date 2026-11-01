//! PyO3 bindings for the libxrk crate.
//!
//! Thin wrapper that reads data from Python sources, runs the Rust parser,
//! hands the GPS material to the shared GPS crate (`libxrk::process_gps`),
//! and converts the results to PyArrow tables and Python dicts.
//!
//! The GPS pipeline is also exposed on its own (`process_gps` and friends)
//! for the Cython backend, which collects the same raw material and calls
//! it here, and for the spec's tests.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

pub mod arrow_bridge;
pub mod metadata_bridge;

/// AIM XRK/XRZ telemetry file parser implemented in Rust.
#[pymodule]
fn _aim_xrk_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(aim_xrk, m)?)?;
    m.add_function(wrap_pyfunction!(aim_track_dbg, m)?)?;
    m.add_function(wrap_pyfunction!(process_gps, m)?)?;
    m.add_function(wrap_pyfunction!(decode_gps, m)?)?;
    m.add_function(wrap_pyfunction!(reconstruct_gps_timecodes, m)?)?;
    m.add_function(wrap_pyfunction!(detect_gps_gap_corrections, m)?)?;
    m.add_function(wrap_pyfunction!(
        arrow_bridge::arrow_metadata_roundtrip_test,
        m
    )?)?;
    let names: Vec<&str> = libxrk::gps::GPS_CHANNEL_DEFS
        .iter()
        .map(|d| d.name)
        .collect();
    m.add("GPS_CHANNEL_NAMES", PyTuple::new(m.py(), names)?)?;
    Ok(())
}

fn runtime_err(e: impl std::fmt::Display) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
}

/// A pyarrow.Table from an Arrow RecordBatch.
fn batch_to_table<'py>(
    py: Python<'py>,
    batch: arrow::array::RecordBatch,
) -> PyResult<Bound<'py, PyAny>> {
    let py_batch = arrow_bridge::batch_to_pyarrow(py, batch)?;
    py.import("pyarrow")?
        .getattr("Table")?
        .call_method1("from_batches", (vec![py_batch.bind(py)],))
}

/// The GPS channels as `{name: pyarrow.Table}` (empty without GPS data).
fn gps_channels_to_pydict<'py>(
    py: Python<'py>,
    channels: Option<libxrk::GpsDecodeResult>,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    if let Some(gps) = channels {
        for (name, batch) in libxrk::arrow::build_gps_channel_batches(gps).map_err(runtime_err)? {
            dict.set_item(name, batch_to_table(py, batch)?)?;
        }
    }
    Ok(dict)
}

/// The laps table in the LogFile layout (num, start_time, end_time, lap_type).
fn laps_to_pytable<'py>(
    py: Python<'py>,
    laps: &[libxrk::ProcessedLap],
) -> PyResult<Bound<'py, PyAny>> {
    batch_to_table(
        py,
        libxrk::arrow::build_laps_batch(laps).map_err(runtime_err)?,
    )
}

type LapRow = (i32, i64, i64, String);

/// Run the shared GPS pipeline on a parser's raw material.
///
/// Returns ``({name: pyarrow.Table}, laps pyarrow.Table)``: the 14 GPS
/// channels (none without GPS data) and the finished lap table. See
/// `libxrk_gps::process`.
#[pyfunction]
#[pyo3(signature = (gps_data, gnfi_data, time_offset, non_gps_max_end_time, start_finish, laps, expected_dt_ms=libxrk::gps::DEFAULT_EXPECTED_DT_MS))]
#[allow(clippy::too_many_arguments)]
fn process_gps<'py>(
    py: Python<'py>,
    gps_data: &[u8],
    gnfi_data: &[u8],
    time_offset: i64,
    non_gps_max_end_time: Option<i64>,
    start_finish: Option<(f64, f64)>,
    laps: Vec<LapRow>,
    expected_dt_ms: f64,
) -> PyResult<(Bound<'py, PyDict>, Bound<'py, PyAny>)> {
    let laps = laps
        .into_iter()
        .map(
            |(num, start_time, end_time, lap_type)| libxrk::ProcessedLap {
                num,
                start_time,
                end_time,
                lap_type,
            },
        )
        .collect();
    let out = libxrk::gps::process(libxrk::gps::Input {
        gps_data,
        gnfi_data,
        time_offset,
        non_gps_max_end_time,
        start_finish,
        laps,
        expected_dt_ms,
    })
    .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok((
        gps_channels_to_pydict(py, out.channels)?,
        laps_to_pytable(py, &out.laps)?,
    ))
}

/// The 14 GPS channels as `{name: pyarrow.Table}`, without the timing fix.
#[pyfunction]
fn decode_gps<'py>(
    py: Python<'py>,
    gps_data: &[u8],
    time_offset: i64,
) -> PyResult<Bound<'py, PyDict>> {
    let channels = libxrk::gps::decode_gps(gps_data, time_offset)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    gps_channels_to_pydict(py, channels)
}

/// Rebuild GPS timecodes whose upper 16 bits are unreliable (companion.md section 6).
#[pyfunction]
fn reconstruct_gps_timecodes(mut timecodes: Vec<i32>) -> Vec<i32> {
    libxrk::gps::fix_timecodes(&mut timecodes);
    timecodes
}

/// Detect the 65533 ms GPS timing bug; returns `[(gap_time, correction), ...]`.
#[pyfunction]
#[pyo3(signature = (gps_timecodes, gnfi_timecodes, non_gps_max_end_time=None, expected_dt_ms=libxrk::gps::DEFAULT_EXPECTED_DT_MS))]
fn detect_gps_gap_corrections(
    gps_timecodes: Vec<i64>,
    gnfi_timecodes: Vec<i64>,
    non_gps_max_end_time: Option<i64>,
    expected_dt_ms: f64,
) -> Vec<(i64, i64)> {
    libxrk::gps::detect_gap_corrections(
        &gps_timecodes,
        &gnfi_timecodes,
        non_gps_max_end_time,
        expected_dt_ms,
    )
    .into_iter()
    .map(|c| (c.gap_time, c.correction))
    .collect()
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

    // GPS channels and the finished lap table from the shared GPS crate.
    // Needs channel_data (for the timing fix's indirect method), so it runs
    // before channel_data is moved out.
    let gps_out = libxrk::process_gps(&result)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // Build metadata while channel_data is still populated (calibration
    // attribution only considers channels that carry data, like Cython).
    let metadata_struct = libxrk::metadata::extract_metadata(&result);

    // Move channel_data out of result (avoids cloning every channel)
    let channel_data = std::mem::take(&mut result.channel_data);

    // Build Arrow RecordBatches using the core crate's arrow module.
    // When the file carries GPS data, the synthesized GPS channel names
    // are reserved so a CHS channel colliding with one is exposed as
    // "<name> dup 2" instead of being shadowed by the GPS channel.
    let channel_batches = libxrk::arrow::build_all_channel_batches(
        channel_data,
        &result.channels,
        &libxrk::gps_reserved_names(&result),
        arrow_bridge::format_float,
    )
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    // Convert to PyArrow tables
    let channels_dict = PyDict::new(py);
    for (name, batch) in channel_batches {
        channels_dict.set_item(name, batch_to_table(py, batch)?)?;
    }
    channels_dict.update(gps_channels_to_pydict(py, gps_out.channels)?.as_mapping())?;

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

    let laps_table = laps_to_pytable(py, &gps_out.laps)?;

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
