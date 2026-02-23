//! idn — Logger Identity (min 10 bytes).
//!
//! Reference: aim_xrk.pyx:539-546, spec/xrk_format.py:325-330.

/// idn payload — extracted fields from min 10-byte payload.
#[derive(Debug, Clone)]
pub struct IdnPayload {
    pub model_id: u16,
    pub logger_id: u32,
}

impl IdnPayload {
    /// Parse an idn payload from raw bytes.
    /// Expects at least 10 bytes: model_id(u16) at offset 0, logger_id(u32) at offset 6.
    pub fn parse(data: &[u8]) -> Self {
        let model_id = u16::from_le_bytes([data[0], data[1]]);
        let logger_id = u32::from_le_bytes([data[6], data[7], data[8], data[9]]);
        IdnPayload {
            model_id,
            logger_id,
        }
    }
}
