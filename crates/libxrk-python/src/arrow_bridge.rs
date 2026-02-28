//! Arrow → PyArrow bridge: converts RecordBatch to PyArrow via C Data Interface.
//!
//! Also provides `format_float()` for Python-matching float formatting.

use std::collections::HashMap;
use std::sync::Arc;

use arrow::array::{Float32Array, Int64Array, RecordBatch};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::pyarrow::ToPyArrow;
use pyo3::prelude::*;

/// Convert an arrow-rs RecordBatch to a PyArrow RecordBatch via C Data Interface (zero-copy).
pub fn batch_to_pyarrow(py: Python<'_>, batch: RecordBatch) -> PyResult<Py<PyAny>> {
    batch
        .to_pyarrow(py)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        .map(|b| b.unbind())
}

/// Format an f32 to match Python's `str(float(f32_value))` output.
///
/// Python promotes f32→f64, then uses shortest-representation formatting.
/// For values with no fractional part, Python appends ".0".
/// For very large (≥1e16) or very small (<1e-4) values, Python uses
/// scientific notation like "1.0000000150474662e+30".
pub fn format_float(v: f32) -> String {
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

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![Arc::new(timecodes), Arc::new(values)],
    )
    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    batch_to_pyarrow(py, batch)
}
