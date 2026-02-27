//! Arrow bridge: build arrow-rs RecordBatch and convert to PyArrow via C Data Interface.
//!
//! Provides zero-copy transfer of Arrow data from Rust to Python.

use std::collections::HashMap;
use std::sync::Arc;

use arrow::array::{Array, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, RecordBatch, UInt8Array, UInt16Array, UInt32Array};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::pyarrow::ToPyArrow;
use pyo3::prelude::*;

use crate::gps_processing::{GpsChannelDef, GpsDecodeResult, GPS_CHANNEL_DEFS};
use crate::parser::{ChannelData, ChannelInfo, ChannelValues, ProcessedLap};

/// Build a PyArrow table for a single channel.
///
/// Returns a RecordBatch with `timecodes` (Int64) and `<channel_name>` columns,
/// with field-level metadata on the channel column.
pub fn build_channel_table(
    py: Python<'_>,
    name: &str,
    ch_data: ChannelData,
    ch_info: &ChannelInfo,
) -> PyResult<Py<PyAny>> {
    let chs = &ch_info.chs;

    // Build metadata matching Cython's _channel_to_table format
    let units = if chs.data_size == 1 {
        "".to_string()
    } else {
        chs.units().to_string()
    };
    let mut metadata = HashMap::new();
    metadata.insert("units".to_string(), units);
    metadata.insert("dec_pts".to_string(), chs.dec_pts().to_string());
    metadata.insert("interpolate".to_string(), if chs.interpolate() { "True" } else { "False" }.to_string());
    metadata.insert("function".to_string(), chs.function().to_string());
    metadata.insert("source_type".to_string(), chs.source_type.to_string());
    metadata.insert("source_channel_id".to_string(), chs.source_channel_id.to_string());
    metadata.insert("device_tag".to_string(), chs.device_tag());
    metadata.insert("cal_value_1".to_string(), format_float(chs.cal_value_1));
    metadata.insert("cal_value_2".to_string(), format_float(chs.cal_value_2));
    metadata.insert("display_range_min".to_string(), format_float(chs.display_range_min));
    metadata.insert("display_range_max".to_string(), format_float(chs.display_range_max));

    // Build typed Arrow array matching the native decoder type (parity with Cython)
    let timecodes = Int64Array::from(ch_data.timecodes);

    let (values_col, data_type): (Arc<dyn Array>, DataType) = match ch_data.values {
        ChannelValues::UInt8(v)   => (Arc::new(UInt8Array::from(v)),   DataType::UInt8),
        ChannelValues::UInt16(v)  => (Arc::new(UInt16Array::from(v)),  DataType::UInt16),
        ChannelValues::Int16(v)   => (Arc::new(Int16Array::from(v)),   DataType::Int16),
        ChannelValues::Int32(v)   => (Arc::new(Int32Array::from(v)),   DataType::Int32),
        ChannelValues::UInt32(v)  => (Arc::new(UInt32Array::from(v)),  DataType::UInt32),
        ChannelValues::Float32(v) => (Arc::new(Float32Array::from(v)), DataType::Float32),
        ChannelValues::Float64(v) => (Arc::new(Float64Array::from(v)), DataType::Float64),
    };

    let schema = Schema::new(vec![
        Field::new("timecodes", DataType::Int64, false),
        Field::new(name, data_type, false).with_metadata(metadata),
    ]);

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![Arc::new(timecodes), values_col],
    ).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    // Convert to PyArrow via C Data Interface (zero-copy)
    batch
        .to_pyarrow(py)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        .map(|b| b.unbind())
}

/// Build a PyArrow table for laps.
///
/// Returns a RecordBatch with columns: num (Int32), start_time (Int64), end_time (Int64).
pub fn build_laps_table(
    py: Python<'_>,
    laps: &[ProcessedLap],
) -> PyResult<Py<PyAny>> {
    let nums: Vec<i32> = laps.iter().map(|l| l.num).collect();
    let starts: Vec<i64> = laps.iter().map(|l| l.start_time).collect();
    let ends: Vec<i64> = laps.iter().map(|l| l.end_time).collect();

    let schema = Schema::new(vec![
        Field::new("num", DataType::Int32, false),
        Field::new("start_time", DataType::Int64, false),
        Field::new("end_time", DataType::Int64, false),
    ]);

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(Int32Array::from(nums)),
            Arc::new(Int64Array::from(starts)),
            Arc::new(Int64Array::from(ends)),
        ],
    ).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    batch
        .to_pyarrow(py)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        .map(|b| b.unbind())
}

/// Build all channel PyArrow tables from a ParseResult.
///
/// Returns a Vec of (channel_name, PyArrow RecordBatch) pairs.
pub fn build_all_channel_tables(
    py: Python<'_>,
    channel_data: HashMap<u16, ChannelData>,
    channels: &HashMap<u16, ChannelInfo>,
) -> PyResult<Vec<(String, Py<PyAny>)>> {
    let mut tables = Vec::new();

    for (ch_idx, ch_data) in channel_data.into_iter() {
        if let Some(ch_info) = channels.get(&ch_idx) {
            let name = ch_info.chs.long_name();
            let table = build_channel_table(py, &name, ch_data, ch_info)?;
            tables.push((name, table));
        }
    }

    Ok(tables)
}

/// Build a GPS channel table with appropriate metadata and data type.
fn build_gps_channel(
    py: Python<'_>,
    name: &str,
    def: &GpsChannelDef,
    timecodes: Vec<i64>,
    values_f64: Option<Vec<f64>>,
    values_f32: Option<Vec<f32>>,
) -> PyResult<Py<PyAny>> {
    // GPS channel metadata — matches Channel defaults for non-CHS channels
    let mut metadata = HashMap::new();
    metadata.insert("units".to_string(), def.units.to_string());
    metadata.insert("dec_pts".to_string(), def.dec_pts.to_string());
    metadata.insert(
        "interpolate".to_string(),
        if def.interpolate { "True" } else { "False" }.to_string(),
    );
    metadata.insert("function".to_string(), "".to_string());
    metadata.insert("source_type".to_string(), "0".to_string());
    metadata.insert("source_channel_id".to_string(), "0".to_string());
    metadata.insert("device_tag".to_string(), "".to_string());
    metadata.insert("cal_value_1".to_string(), "0.0".to_string());
    metadata.insert("cal_value_2".to_string(), "1.0".to_string());
    metadata.insert("display_range_min".to_string(), "0.0".to_string());
    metadata.insert("display_range_max".to_string(), "0.0".to_string());

    let tc_array = Int64Array::from(timecodes);

    let batch = if def.is_f64 {
        let vals = Float64Array::from(values_f64.unwrap());
        let schema = Schema::new(vec![
            Field::new("timecodes", DataType::Int64, false),
            Field::new(name, DataType::Float64, false).with_metadata(metadata),
        ]);
        RecordBatch::try_new(Arc::new(schema), vec![Arc::new(tc_array), Arc::new(vals)])
    } else {
        let vals = Float32Array::from(values_f32.unwrap());
        let schema = Schema::new(vec![
            Field::new("timecodes", DataType::Int64, false),
            Field::new(name, DataType::Float32, false).with_metadata(metadata),
        ]);
        RecordBatch::try_new(Arc::new(schema), vec![Arc::new(tc_array), Arc::new(vals)])
    }
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    batch
        .to_pyarrow(py)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        .map(|b| b.unbind())
}

/// Build PyArrow tables for all 12 GPS channels.
///
/// Returns a Vec of (channel_name, PyArrow RecordBatch) pairs.
pub fn build_gps_channel_tables(
    py: Python<'_>,
    gps: GpsDecodeResult,
) -> PyResult<Vec<(String, Py<PyAny>)>> {
    let mut tables = Vec::with_capacity(12);

    // Destructure to take ownership of all fields
    let GpsDecodeResult {
        timecodes,
        speed,
        latitude,
        longitude,
        altitude,
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
    } = gps;

    // Order must match GPS_CHANNEL_DEFS: 4 f64 channels then 8 f32 channels
    let mut f64_channels = vec![speed, latitude, longitude, altitude].into_iter();
    let mut f32_channels = vec![
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
    ].into_iter();

    for def in GPS_CHANNEL_DEFS {
        // Timecodes are shared across all 12 GPS channels, so clone is unavoidable
        let tc = timecodes.clone();
        let table = if def.is_f64 {
            let vals = f64_channels.next().unwrap();
            build_gps_channel(py, def.name, def, tc, Some(vals), None)?
        } else {
            let vals = f32_channels.next().unwrap();
            build_gps_channel(py, def.name, def, tc, None, Some(vals))?
        };
        tables.push((def.name.to_string(), table));
    }

    Ok(tables)
}

/// Format an f32 to match Python's `str(float(f32_value))` output.
///
/// Python promotes f32→f64, then uses shortest-representation formatting.
/// For values with no fractional part, Python appends ".0".
/// For very large (≥1e16) or very small (<1e-4) values, Python uses
/// scientific notation like "1.0000000150474662e+30".
fn format_float(v: f32) -> String {
    if !v.is_finite() {
        return format!("{}", v);
    }
    // Promote to f64, matching Python's implicit f32→float conversion.
    let f64_val = v as f64;
    // Use ryu for shortest-representation formatting (same algorithm as CPython).
    let mut buf = ryu::Buffer::new();
    let s = buf.format(f64_val);
    // ryu outputs "1e30" but Python outputs "1e+30" (explicit + for positive exponents).
    if let Some(e_pos) = s.find('e') {
        let (mantissa, exp_part) = s.split_at(e_pos);
        let exp_str = &exp_part[1..]; // skip 'e'
        if exp_str.starts_with('-') {
            format!("{}e{}", mantissa, exp_str)
        } else {
            format!("{}e+{}", mantissa, exp_str)
        }
    } else if s.contains('.') {
        s.to_string()
    } else {
        // Integer value without decimal point — append ".0"
        format!("{}.0", s)
    }
}

/// Build a test RecordBatch with field-level metadata and return it as a PyArrow table.
///
/// This validates that arrow-rs metadata survives the C Data Interface roundtrip.
#[pyfunction]
pub fn arrow_metadata_roundtrip_test(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let mut metadata = HashMap::new();
    metadata.insert("units".to_string(), "rpm".to_string());
    metadata.insert("dec_pts".to_string(), "0".to_string());
    metadata.insert("interpolate".to_string(), "True".to_string());
    metadata.insert("function".to_string(), "Engine RPM".to_string());
    metadata.insert("source_type".to_string(), "1".to_string());
    metadata.insert("source_channel_id".to_string(), "42".to_string());
    metadata.insert("device_tag".to_string(), "@AIM".to_string());
    metadata.insert("cal_value_1".to_string(), "0.0".to_string());
    metadata.insert("cal_value_2".to_string(), "1.0".to_string());
    metadata.insert("display_range_min".to_string(), "0.0".to_string());
    metadata.insert("display_range_max".to_string(), "18000.0".to_string());

    let schema = Schema::new(vec![
        Field::new("timecodes", DataType::Int64, false),
        Field::new("Engine RPM", DataType::Float32, false).with_metadata(metadata),
    ]);

    let timecodes = Int64Array::from(vec![0i64, 100, 200, 300]);
    let values = Float32Array::from(vec![800.0f32, 3200.0, 5600.0, 4100.0]);

    let batch = RecordBatch::try_new(Arc::new(schema), vec![Arc::new(timecodes), Arc::new(values)])
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let bound = batch
        .to_pyarrow(py)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(bound.unbind())
}
