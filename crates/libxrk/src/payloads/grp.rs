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
        Ok(GrpPayload {
            index,
            count,
            channel_indices,
        })
    }
}
