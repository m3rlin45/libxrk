//! LAP — Lap Marker (20 bytes for v0/v1, 32 bytes for v2).
//!
//! Reference: aim_xrk.pyx:529-531, spec/xrk_format.py (LAPPayload).

use binrw::BinRead;

/// LAP payload — 20 bytes (v0/v1) or 32 bytes (v2).
#[derive(Debug, Clone, BinRead)]
#[br(little)]
pub struct LapPayload {
    pub _pad: u8,           // [0]     padding
    pub segment: u8,        // [1]     segment number
    pub lap_num: u16,       // [2:4]   lap number
    pub duration: u32,      // [4:8]   lap duration [ms]
    pub _reserved: [u8; 8], // [8:16] reserved
    pub end_time: u32,      // [16:20] v1: lap end time [ms]; v2: ~duration
}

impl LapPayload {
    /// Length-aware parse handling both LAP layouts.
    ///
    /// Version 2 (32-byte payload) moves the absolute lap end time to
    /// [28:32]; the v1 end-time slot [16:20] instead tracks the duration.
    /// See spec/docs/unknown_regions.md ("LAP version 2"). Returns None
    /// for payloads shorter than 20 bytes.
    pub fn parse(data: &[u8]) -> Option<Self> {
        if data.len() < 20 {
            return None;
        }
        let mut lap = LapPayload::read(&mut std::io::Cursor::new(data)).ok()?;
        if data.len() >= 32 {
            lap.end_time = u32::from_le_bytes([data[28], data[29], data[30], data[31]]);
        }
        Some(lap)
    }
}
