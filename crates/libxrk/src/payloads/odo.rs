//! ODO — Odometer Record (64 bytes per record).
//!
//! Reference: _aim_xrk_cy.pyx:622-631, spec/xrk_format.py:364-376.

use crate::messages::nullterm_string;

/// Single ODO record entry.
#[derive(Debug, Clone)]
pub struct OdoRecord {
    pub name: String,
    pub time: u32, // seconds
    pub dist: u32, // meters
}

/// ODO payload — the odometer records in file order.
///
/// Names are unique: a record that repeats a name replaces the earlier one
/// in place, as the Cython backend's dict does.
#[derive(Debug, Clone)]
pub struct OdoPayload {
    pub records: Vec<OdoRecord>,
}

impl OdoPayload {
    /// Parse an ODO payload from raw bytes (N * 64-byte records).
    pub fn parse(data: &[u8]) -> Self {
        let mut records: Vec<OdoRecord> = Vec::new();
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
                let record = OdoRecord { name, time, dist };
                match records.iter_mut().find(|r| r.name == record.name) {
                    Some(existing) => *existing = record,
                    None => records.push(record),
                }
            }
            offset += 64;
        }
        OdoPayload { records }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(name: &str, time: u32, dist: u32) -> Vec<u8> {
        let mut data = vec![0u8; 64];
        data[..name.len()].copy_from_slice(name.as_bytes());
        data[16..20].copy_from_slice(&time.to_le_bytes());
        data[20..24].copy_from_slice(&dist.to_le_bytes());
        data
    }

    #[test]
    fn records_keep_file_order() {
        let data = [
            record("System", 1, 10),
            record("Usr 1", 2, 20),
            record("Fuel Used", 3, 30),
            record("Usr 3", 4, 40),
            record("Usr 2", 5, 50),
            record("Usr 1", 6, 60),
        ]
        .concat();
        let odo = OdoPayload::parse(&data);
        let seen: Vec<(&str, u32, u32)> = odo
            .records
            .iter()
            .map(|r| (r.name.as_str(), r.time, r.dist))
            .collect();
        // File order, Fuel records skipped, a repeated name replaced in place.
        assert_eq!(
            seen,
            [
                ("System", 1, 10),
                ("Usr 1", 6, 60),
                ("Usr 3", 4, 40),
                ("Usr 2", 5, 50)
            ]
        );
    }
}
