//! Error type for the libxrk core crate.

use std::fmt;

/// Errors that can occur during XRK file parsing and processing.
#[derive(Debug)]
pub enum Error {
    /// I/O error reading the file.
    Io(std::io::Error),
    /// Decompression failed (XRZ files).
    Decompression(String),
    /// Invalid or corrupt data in the XRK stream.
    InvalidData(String),
    /// Arrow conversion error (only with `arrow` feature).
    #[cfg(feature = "arrow")]
    Arrow(arrow::error::ArrowError),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(e) => write!(f, "I/O error: {}", e),
            Error::Decompression(msg) => write!(f, "decompression error: {}", msg),
            Error::InvalidData(msg) => write!(f, "invalid data: {}", msg),
            #[cfg(feature = "arrow")]
            Error::Arrow(e) => write!(f, "Arrow error: {}", e),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Io(e) => Some(e),
            #[cfg(feature = "arrow")]
            Error::Arrow(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

#[cfg(feature = "arrow")]
impl From<arrow::error::ArrowError> for Error {
    fn from(e: arrow::error::ArrowError) -> Self {
        Error::Arrow(e)
    }
}
