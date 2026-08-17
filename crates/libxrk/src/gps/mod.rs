//! GPS processing pipeline: decode, timing fix, coordinate conversions, lap detection.

pub mod processing;
pub mod timing;
pub mod utils;

pub use processing::{decode_gps, GpsChannelDef, GpsDecodeResult, GPS_CHANNEL_DEFS};
pub use timing::{apply_corrections, detect_gap_corrections, GapCorrection};
pub use utils::{ecef2lla_vermeille2003, ecef_velocity_to_enu, find_laps, lla2ecef};
