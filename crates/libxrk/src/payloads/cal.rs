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
        let cal_u32_8 = u32::from_le_bytes([data[8], data[9], data[10], data[11]]);
        if cal_u32_8 != 1 {
            eprintln!(
                "libxrk: unexpected CAL u32[8]={} (expected 1). \
                 Please report at https://github.com/m3rlin45/libxrk/issues",
                cal_u32_8
            );
        }
        let cal_type = u32::from_le_bytes([data[20], data[21], data[22], data[23]]);
        if cal_type != 1 && cal_type != 20 {
            eprintln!(
                "libxrk: unexpected CAL type {} (expected 1 or 20). \
                 Please report at https://github.com/m3rlin45/libxrk/issues",
                cal_type
            );
        }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn make_cal_data(cal_u32_8: u32, cal_type: u32, raw1: f32, raw2: f32) -> Vec<u8> {
        let mut data = vec![0u8; 40];
        data[8..12].copy_from_slice(&cal_u32_8.to_le_bytes());
        data[20..24].copy_from_slice(&cal_type.to_le_bytes());
        data[24..28].copy_from_slice(&raw1.to_le_bytes());
        data[28..32].copy_from_slice(&raw2.to_le_bytes());
        data[32..36].copy_from_slice(&100.0f32.to_le_bytes());
        data[36..40].copy_from_slice(&200.0f32.to_le_bytes());
        data
    }

    #[test]
    fn test_cal_type_1_with_output() {
        let data = make_cal_data(1, 1, 0.0, 5000.0);
        let cal = CalPayload::parse(&data);
        assert_eq!(cal.cal_type, 1);
        assert_eq!(cal.raw_1, 0.0);
        assert_eq!(cal.raw_2, 5000.0);
        assert_eq!(cal.output_1, Some(100.0));
        assert_eq!(cal.output_2, Some(200.0));
    }

    #[test]
    fn test_cal_type_20_no_output() {
        let data = make_cal_data(1, 20, 1.0, 2.0);
        let cal = CalPayload::parse(&data);
        assert_eq!(cal.cal_type, 20);
        assert!(cal.output_1.is_none());
        assert!(cal.output_2.is_none());
    }

    #[test]
    fn test_cal_raw_values() {
        let data = make_cal_data(1, 1, 1.5, 2.5);
        let cal = CalPayload::parse(&data);
        assert!((cal.raw_1 - 1.5).abs() < 0.01);
        assert!((cal.raw_2 - 2.5).abs() < 0.01);
    }
}
