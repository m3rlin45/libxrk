//! `libxrk._gps_rs`: the shared GPS processing (crate `libxrk-gps`) for Python.
//!
//! The Cython backend and the Construct spec run GPS processing through this
//! module; the Rust backend links the crate directly. All three therefore
//! produce GPS channels and lap tables from one implementation.

use arrow::pyarrow::ToPyArrow;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

type LapRow = (i32, i64, i64, String);

fn to_py_err(e: libxrk_gps::Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn lap_rows(laps: Vec<libxrk_gps::Lap>) -> Vec<LapRow> {
    laps.into_iter()
        .map(|l| (l.num, l.start_time, l.end_time, l.lap_type))
        .collect()
}

fn laps_from_rows(rows: Vec<LapRow>) -> Vec<libxrk_gps::Lap> {
    rows.into_iter()
        .map(|(num, start_time, end_time, lap_type)| libxrk_gps::Lap {
            num,
            start_time,
            end_time,
            lap_type,
        })
        .collect()
}

/// GPS channels as a dict of channel name -> pyarrow.Table.
fn channels_to_pydict(
    py: Python<'_>,
    channels: Option<libxrk_gps::GpsChannels>,
) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    if let Some(gps) = channels {
        let table_class = py.import("pyarrow")?.getattr("Table")?;
        let batches = libxrk_gps::arrow::build_gps_channel_batches(gps)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        for (name, batch) in batches {
            let py_batch = batch
                .to_pyarrow(py)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            let table = table_class.call_method1("from_batches", (vec![py_batch],))?;
            dict.set_item(name, table)?;
        }
    }
    Ok(dict.unbind())
}

/// Run the complete GPS pipeline on one file's raw material.
///
/// Returns ``(channels, laps)``: the 14 GPS channels as ``{name: pyarrow.Table}``
/// (empty when ``gps_data`` is empty) and the finished lap table as
/// ``[(num, start_time, end_time, lap_type), ...]``.
#[pyfunction]
#[pyo3(signature = (gps_data, gnfi_data, time_offset, non_gps_max_end_time, start_finish, laps, expected_dt_ms=libxrk_gps::DEFAULT_EXPECTED_DT_MS))]
#[allow(clippy::too_many_arguments)]
fn process(
    py: Python<'_>,
    gps_data: &[u8],
    gnfi_data: &[u8],
    time_offset: i64,
    non_gps_max_end_time: Option<i64>,
    start_finish: Option<(f64, f64)>,
    laps: Vec<LapRow>,
    expected_dt_ms: f64,
) -> PyResult<(Py<PyDict>, Vec<LapRow>)> {
    let out = libxrk_gps::process(libxrk_gps::Input {
        gps_data,
        gnfi_data,
        time_offset,
        non_gps_max_end_time,
        start_finish,
        laps: laps_from_rows(laps),
        expected_dt_ms,
    })
    .map_err(to_py_err)?;
    Ok((channels_to_pydict(py, out.channels)?, lap_rows(out.laps)))
}

/// Decode GPS records into the 14 GPS channels, without the timing fix.
///
/// Returns ``{name: pyarrow.Table}``, empty for empty input.
#[pyfunction]
fn decode(py: Python<'_>, gps_data: &[u8], time_offset: i64) -> PyResult<Py<PyDict>> {
    let channels = libxrk_gps::decode_nav_sol(gps_data, time_offset).map_err(to_py_err)?;
    channels_to_pydict(py, channels)
}

/// Rebuild GPS timecodes whose upper 16 bits are unreliable (companion.md section 6).
#[pyfunction]
fn reconstruct_timecodes(mut timecodes: Vec<i32>) -> Vec<i32> {
    libxrk_gps::reconstruct_timecodes(&mut timecodes);
    timecodes
}

/// Detect the 65533 ms GPS timing bug; returns ``[(gap_time, correction), ...]``.
#[pyfunction]
#[pyo3(signature = (gps_timecodes, gnfi_timecodes, non_gps_max_end_time=None, expected_dt_ms=libxrk_gps::DEFAULT_EXPECTED_DT_MS))]
fn detect_gap_corrections(
    gps_timecodes: Vec<i64>,
    gnfi_timecodes: Vec<i64>,
    non_gps_max_end_time: Option<i64>,
    expected_dt_ms: f64,
) -> Vec<(i64, i64)> {
    libxrk_gps::detect_gap_corrections(
        &gps_timecodes,
        &gnfi_timecodes,
        non_gps_max_end_time,
        expected_dt_ms,
    )
    .into_iter()
    .map(|c| (c.gap_time, c.correction))
    .collect()
}

/// Start/finish crossing times [ms] of a GPS track given as latitude and
/// longitude [deg] per sample, with sub-sample interpolation.
#[pyfunction]
fn find_laps(
    latitude: Vec<f64>,
    longitude: Vec<f64>,
    timecodes: Vec<i64>,
    start_finish: (f64, f64),
) -> PyResult<Vec<f64>> {
    if latitude.len() != longitude.len() || latitude.len() != timecodes.len() {
        return Err(PyValueError::new_err(
            "latitude, longitude and timecodes must have the same length",
        ));
    }
    let xyz: Vec<[f64; 3]> = latitude
        .iter()
        .zip(&longitude)
        .map(|(&lat, &lon)| {
            let (x, y, z) = libxrk_gps::geo::lla2ecef(lat, lon, 0.0);
            [x, y, z]
        })
        .collect();
    Ok(libxrk_gps::find_laps(&xyz, &timecodes, start_finish))
}

/// ECEF [m] -> (latitude deg, longitude deg, altitude m).
#[pyfunction]
fn ecef2lla(x: f64, y: f64, z: f64) -> (f64, f64, f64) {
    libxrk_gps::geo::ecef2lla(x, y, z)
}

/// (latitude deg, longitude deg, altitude m) -> ECEF [m].
#[pyfunction]
fn lla2ecef(lat: f64, lon: f64, alt: f64) -> (f64, f64, f64) {
    libxrk_gps::geo::lla2ecef(lat, lon, alt)
}

/// Shared GPS processing for AIM XRK files.
#[pymodule]
fn _gps_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process, m)?)?;
    m.add_function(wrap_pyfunction!(decode, m)?)?;
    m.add_function(wrap_pyfunction!(reconstruct_timecodes, m)?)?;
    m.add_function(wrap_pyfunction!(detect_gap_corrections, m)?)?;
    m.add_function(wrap_pyfunction!(find_laps, m)?)?;
    m.add_function(wrap_pyfunction!(ecef2lla, m)?)?;
    m.add_function(wrap_pyfunction!(lla2ecef, m)?)?;
    let names: Vec<&str> = libxrk_gps::GPS_CHANNEL_DEFS
        .iter()
        .map(|d| d.name)
        .collect();
    m.add(
        "GPS_CHANNEL_NAMES",
        pyo3::types::PyTuple::new(m.py(), names)?,
    )?;
    m.add("GPS_RECORD_SIZE", libxrk_gps::GPS_RECORD_SIZE)?;
    m.add("MARKER_SIZE_M", libxrk_gps::laps::MARKER_SIZE_M)?;
    Ok(())
}
