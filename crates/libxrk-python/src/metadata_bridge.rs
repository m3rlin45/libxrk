//! Metadata bridge: converts `libxrk::Metadata` to a Python dict.
//!
//! The key names, their order and the value types come from
//! `Metadata::to_entries` in the core crate; this module only maps
//! `MetadataValue` onto Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use libxrk::metadata::{Metadata, MetadataValue};

/// Convert a Metadata struct to the `LogFile.metadata` dict.
pub fn metadata_to_pydict(py: Python<'_>, meta: &Metadata) -> PyResult<Py<PyAny>> {
    Ok(entries_to_pydict(py, &meta.to_entries())?
        .into_any()
        .unbind())
}

fn entries_to_pydict<'py>(
    py: Python<'py>,
    entries: &[(String, MetadataValue)],
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (key, value) in entries {
        dict.set_item(key, value_to_py(py, value)?)?;
    }
    Ok(dict)
}

fn value_to_py<'py>(py: Python<'py>, value: &MetadataValue) -> PyResult<Bound<'py, PyAny>> {
    Ok(match value {
        MetadataValue::Null => py.None().into_bound(py),
        MetadataValue::Str(v) => v.into_pyobject(py)?.into_any(),
        MetadataValue::Int(v) => v.into_pyobject(py)?.into_any(),
        MetadataValue::Float(v) => v.into_pyobject(py)?.into_any(),
        MetadataValue::List(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(value_to_py(py, item)?)?;
            }
            list.into_any()
        }
        MetadataValue::Map(entries) => entries_to_pydict(py, entries)?.into_any(),
    })
}
