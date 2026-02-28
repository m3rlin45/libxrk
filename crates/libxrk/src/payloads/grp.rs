//! GRP — Group Definition (variable size).
//!
//! Reference: aim_xrk.pyx:418-421, spec/xrk_format.py:248-252.

/// GRP payload — variable size.
#[derive(Debug, Clone)]
pub struct GrpPayload {
    pub index: u16,
    pub count: u16,
    pub channel_indices: Vec<u16>,
}

impl GrpPayload {
    /// Parse a GRP payload from raw bytes.
    pub fn parse(data: &[u8]) -> Result<Self, &'static str> {
        if data.len() < 4 {
            return Err("GRP payload too short");
        }
        let index = u16::from_le_bytes([data[0], data[1]]);
        let count = u16::from_le_bytes([data[2], data[3]]);
        let expected_len = 4 + (count as usize) * 2;
        if data.len() < expected_len {
            return Err("GRP payload truncated");
        }
        let mut channel_indices = Vec::with_capacity(count as usize);
        for i in 0..count as usize {
            let off = 4 + i * 2;
            channel_indices.push(u16::from_le_bytes([data[off], data[off + 1]]));
        }
        if channel_indices.len() != count as usize {
            return Err("GRP channel count mismatch");
        }
        Ok(GrpPayload {
            index,
            count,
            channel_indices,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_grp_parse_valid() {
        // index=5, count=2, channels=[10, 11]
        let data = [
            5, 0, // index=5
            2, 0, // count=2
            10, 0, // channel 10
            11, 0, // channel 11
        ];
        let grp = GrpPayload::parse(&data).unwrap();
        assert_eq!(grp.index, 5);
        assert_eq!(grp.count, 2);
        assert_eq!(grp.channel_indices, vec![10, 11]);
    }

    #[test]
    fn test_grp_parse_single_channel() {
        let data = [
            0, 0, // index=0
            1, 0, // count=1
            42, 0, // channel 42
        ];
        let grp = GrpPayload::parse(&data).unwrap();
        assert_eq!(grp.count, 1);
        assert_eq!(grp.channel_indices, vec![42]);
    }

    #[test]
    fn test_grp_too_short() {
        let data = [0, 0, 1]; // only 3 bytes
        assert_eq!(
            GrpPayload::parse(&data).unwrap_err(),
            "GRP payload too short"
        );
    }

    #[test]
    fn test_grp_truncated() {
        // count=2 but only 1 channel provided
        let data = [
            0, 0, // index=0
            2, 0, // count=2
            10, 0, // only 1 channel
        ];
        assert_eq!(
            GrpPayload::parse(&data).unwrap_err(),
            "GRP payload truncated"
        );
    }

    #[test]
    fn test_grp_zero_channels() {
        let data = [
            3, 0, // index=3
            0, 0, // count=0
        ];
        let grp = GrpPayload::parse(&data).unwrap();
        assert_eq!(grp.index, 3);
        assert_eq!(grp.count, 0);
        assert!(grp.channel_indices.is_empty());
    }
}
