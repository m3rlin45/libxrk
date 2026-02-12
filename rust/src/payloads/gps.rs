//! GPS — 56-byte u-blox NAV-SOL payload.
//!
//! Reference: aim_xrk.pyx:910-937, spec/xrk_format.py:258-277.

use binrw::BinRead;

/// GPS payload — 56 bytes (4-byte AIM timecode + 52-byte NAV-SOL).
#[derive(Debug, Clone, BinRead)]
#[br(little)]
pub struct GpsPayload {
    pub timecode: i32,    // [0:4]   AIM logger time [ms]
    pub itow: u32,        // [4:8]   GPS time of week [ms]
    pub ftow: i32,        // [8:12]  Fractional TOW [ns]
    pub week: u16,        // [12:14] GPS week number
    pub gps_fix: u8,      // [14]    Fix type (0=none, 2=2D, 3=3D)
    pub flags: u8,        // [15]    Validity bitmask
    pub ecef_x: i32,      // [16:20] ECEF X position [cm]
    pub ecef_y: i32,      // [20:24] ECEF Y position [cm]
    pub ecef_z: i32,      // [24:28] ECEF Z position [cm]
    pub p_acc: u32,       // [28:32] Position accuracy [cm]
    pub ecef_vx: i32,     // [32:36] ECEF X velocity [cm/s]
    pub ecef_vy: i32,     // [36:40] ECEF Y velocity [cm/s]
    pub ecef_vz: i32,     // [40:44] ECEF Z velocity [cm/s]
    pub s_acc: u32,       // [44:48] Speed accuracy [cm/s]
    pub pdop: u16,        // [48:50] Position DOP [*0.01]
    pub reserved1: u8,    // [50]    u-blox reserved
    pub num_sv: u8,       // [51]    Number of satellites used
    pub reserved2: u32,   // [52:56] u-blox reserved
}
