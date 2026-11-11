//! Arrow → PyArrow bridge: converts RecordBatch to PyArrow via C Data Interface.
//!
//! Re-exports the core crate's `format_float()` (Python-matching float formatting).

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

pub use libxrk::arrow::format_float;

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
