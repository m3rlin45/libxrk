//! ODO — Odometer Record (64 bytes per record).
//!
//! Reference: aim_xrk.pyx:622-631, spec/xrk_format.py:364-376.

use std::collections::HashMap;

use crate::messages::nullterm_string;

/// Single ODO record entry.
#[derive(Debug, Clone)]
pub struct OdoRecord {
    pub name: String,
    pub time: u32, // seconds
    pub dist: u32, // meters
}

/// ODO payload — collection of odometer records.
#[derive(Debug, Clone)]
pub struct OdoPayload {
    pub records: HashMap<String, OdoRecord>,
}

impl OdoPayload {
    /// Parse an ODO payload from raw bytes (N * 64-byte records).
    pub fn parse(data: &[u8]) -> Self {
        let mut records = HashMap::new();
        let mut offset = 0;
        while offset + 64 <= data.len() {
            let name = nullterm_string(&data[offset..offset + 16]);
            // Skip Fuel records (matches Python behavior)
            if !name.starts_with("Fuel") {
                let time = u32::from_le_bytes([
                    data[offset + 16],
                    data[offset + 17],
                    data[offset + 18],
                    data[offset + 19],
                ]);
                let dist = u32::from_le_bytes([
                    data[offset + 20],
                    data[offset + 21],
                    data[offset + 22],
                    data[offset + 23],
                ]);
                records.insert(name.clone(), OdoRecord { name, time, dist });
            }
            offset += 64;
        }
        OdoPayload { records }
    }
}
