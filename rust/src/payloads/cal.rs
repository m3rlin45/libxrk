//! CAL — Calibration (40-byte prefix + variable rest).
//!
//! Reference: aim_xrk.pyx:597-615, spec/xrk_format.py:310-320.

/// CAL payload — calibration data.
#[derive(Debug, Clone)]
pub struct CalPayload {
    pub cal_type: u32,
    pub raw_1: f32,
    pub raw_2: f32,
    pub output_1: Option<f32>,
    pub output_2: Option<f32>,
}

impl CalPayload {
    /// Parse a CAL payload from raw bytes (minimum 40 bytes).
    pub fn parse(data: &[u8]) -> Self {
        let cal_type = u32::from_le_bytes([data[20], data[21], data[22], data[23]]);
        let raw_1 = f32::from_le_bytes([data[24], data[25], data[26], data[27]]);
        let raw_2 = f32::from_le_bytes([data[28], data[29], data[30], data[31]]);
        let (output_1, output_2) = if cal_type == 1 && data.len() >= 40 {
            (
                Some(f32::from_le_bytes([data[32], data[33], data[34], data[35]])),
                Some(f32::from_le_bytes([data[36], data[37], data[38], data[39]])),
            )
        } else {
            (None, None)
        };
        CalPayload {
            cal_type,
            raw_1,
            raw_2,
            output_1,
            output_2,
        }
    }
}
