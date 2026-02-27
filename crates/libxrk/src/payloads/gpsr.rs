//! GPSR — GPS Receiver (36 bytes).
//!
//! Reference: aim_xrk.pyx:560-570, spec/xrk_format.py:352-359.

use crate::messages::nullterm_string;

/// GPSR payload — GPS receiver info.
#[derive(Debug, Clone)]
pub struct GpsrPayload {
    pub gps_type: String,
    pub channel_index: u16,
}

impl GpsrPayload {
    /// Parse a GPSR payload from raw bytes.
    pub fn parse(data: &[u8]) -> Self {
        let gps_type = nullterm_string(&data[4..8]);
        let channel_index = u16::from_le_bytes([data[22], data[23]]);
        GpsrPayload {
            gps_type,
            channel_index,
        }
    }
}
