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

#[cfg(test)]
mod tests {
    use super::*;

    fn make_gpsr_data(gps_type: &[u8; 4], channel_idx: u16, u32_32: u32) -> Vec<u8> {
        let mut data = vec![0u8; 36];
        data[4..8].copy_from_slice(gps_type);
        data[22..24].copy_from_slice(&channel_idx.to_le_bytes());
        data[32..36].copy_from_slice(&u32_32.to_le_bytes());
        data
    }

    #[test]
    fn test_gpsr_parse_normal() {
        let data = make_gpsr_data(b"NAV\0", 42, 410);
        let gpsr = GpsrPayload::parse(&data);
        assert_eq!(gpsr.gps_type, "NAV");
        assert_eq!(gpsr.channel_index, 42);
    }

    #[test]
    fn test_gpsr_parse_other_u32_32() {
        // Seen in real logs instead of 410; the field is not decoded.
        for value in [688144, 688391] {
            let data = make_gpsr_data(b"NAV\0", 42, value);
            let gpsr = GpsrPayload::parse(&data);
            assert_eq!(gpsr.gps_type, "NAV");
            assert_eq!(gpsr.channel_index, 42);
        }
    }

    #[test]
    fn test_gpsr_channel_index() {
        let data = make_gpsr_data(b"SOL\0", 100, 410);
        let gpsr = GpsrPayload::parse(&data);
        assert_eq!(gpsr.channel_index, 100);
    }
}
