//! TRK — Track Info (min 44 bytes).
//!
//! Reference: aim_xrk.pyx:618-621, spec/xrk_format.py:335-347.

use crate::messages::nullterm_string;

/// TRK payload — track name and start/finish coordinates.
#[derive(Debug, Clone)]
pub struct TrkPayload {
    pub name: String,
    pub sf_lat: f64,  // start/finish latitude (degrees)
    pub sf_long: f64, // start/finish longitude (degrees)
}

impl TrkPayload {
    /// Parse a TRK payload from raw bytes.
    pub fn parse(data: &[u8]) -> Self {
        let name = nullterm_string(&data[..32]);
        let sf_lat_raw = i32::from_le_bytes([data[36], data[37], data[38], data[39]]);
        let sf_long_raw = i32::from_le_bytes([data[40], data[41], data[42], data[43]]);
        TrkPayload {
            name,
            sf_lat: sf_lat_raw as f64 / 1e7,
            sf_long: sf_long_raw as f64 / 1e7,
        }
    }
}
