//! LAP — Lap Marker (20 bytes).
//!
//! Reference: aim_xrk.pyx:529-531, spec/xrk_format.py:290-297.

use binrw::BinRead;

/// LAP payload — 20 bytes.
#[derive(Debug, Clone, BinRead)]
#[br(little)]
pub struct LapPayload {
    pub _pad: u8,           // [0]     padding
    pub segment: u8,        // [1]     segment number
    pub lap_num: u16,       // [2:4]   lap number
    pub duration: u32,      // [4:8]   lap duration [ms]
    pub _reserved: [u8; 8], // [8:16] reserved
    pub end_time: u32,      // [16:20] lap end time [ms]
}
