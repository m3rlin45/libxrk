//! VET — Vehicle Electronics Type (string or value byte).
//!
//! Reference: aim_xrk.pyx:584-589.

use crate::messages::nullterm_string;

/// VET payload — vehicle electronics type.
#[derive(Debug, Clone)]
pub enum VetPayload {
    /// String mode: e.g. "..." placeholder
    Mode(String),
    /// Single-byte value (expected 0)
    Value(u8),
}

impl VetPayload {
    /// Parse a VET payload from raw bytes.
    pub fn parse(data: &[u8]) -> Self {
        if data.len() > 1 {
            VetPayload::Mode(nullterm_string(data))
        } else if !data.is_empty() {
            VetPayload::Value(data[0])
        } else {
            VetPayload::Value(0)
        }
    }
}
