//! GPS processing for AIM XRK files, shared by every libxrk implementation.
//!
//! The XRK parsers (Cython, Rust and the Construct spec) only *collect* GPS
//! material: the raw 56-byte GPS records, the raw 32-byte GNFI records, the
//! session time offset, the lap table built from LAP messages and the TRK
//! start/finish marker. Everything derived from that material lives here and
//! nowhere else, so the three implementations cannot drift apart:
//!
//! - [`navsol`]: decode the u-blox NAV-SOL records into the 14 GPS channels,
//!   including the reconstruction of corrupted timecodes;
//! - [`timing`]: detect and remove the 65533 ms firmware gap;
//! - [`laps`]: classify the last lap as an in-lap from the GPS position, and
//!   detect laps from GPS alone when a file has no LAP messages;
//! - [`geo`]: the WGS-84 conversions the above are built on.
//!
//! [`process`] runs the whole pipeline on one file's material. The Rust
//! backend calls it natively; the Cython backend and the spec call it through
//! the `libxrk._gps_rs` Python module built from this crate.
//!
//! See `spec/docs/companion.md` sections 5-7 for the algorithms.

pub mod geo;
pub mod laps;
pub mod navsol;
pub mod timing;

#[cfg(feature = "arrow")]
pub mod arrow;

pub use laps::{find_laps, finish_laps, Lap};
pub use navsol::{
    decode_nav_sol, reconstruct_timecodes, GpsChannelDef, GpsChannels, GPS_CHANNEL_DEFS,
    GPS_RECORD_SIZE,
};
pub use timing::{apply_corrections, decode_gnfi_timecodes, detect_gap_corrections, GapCorrection};

/// Sample period the timing fix assumes for the GPS stream (25 Hz).
pub const DEFAULT_EXPECTED_DT_MS: f64 = 40.0;

/// Errors from GPS processing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The GPS material is malformed (e.g. not a whole number of records).
    InvalidData(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::InvalidData(msg) => write!(f, "invalid data: {}", msg),
        }
    }
}

impl std::error::Error for Error {}

/// Everything a parser hands over for GPS processing.
#[derive(Debug, Clone)]
pub struct Input<'a> {
    /// Concatenated 56-byte GPS records (GPS and GPS1 header messages), in file order.
    pub gps_data: &'a [u8],
    /// Concatenated 32-byte GNFI records (logger internal clock), in file order.
    pub gnfi_data: &'a [u8],
    /// Session start in logger time; subtracted from every timecode.
    pub time_offset: i64,
    /// Last timecode (session-relative) over the non-GPS channels, if any.
    /// Used by the indirect gap detection (companion.md section 6).
    pub non_gps_max_end_time: Option<i64>,
    /// Start/finish (latitude, longitude) in degrees from the TRK message.
    pub start_finish: Option<(f64, f64)>,
    /// Lap table built from LAP messages, session-relative, 0-based, typed
    /// out/full/in by the parser (out-lap and final partial lap). Empty when
    /// the file has no LAP messages.
    pub laps: Vec<Lap>,
    /// Expected GPS sample period in ms; [`DEFAULT_EXPECTED_DT_MS`] unless known otherwise.
    pub expected_dt_ms: f64,
}

/// What GPS processing produces.
#[derive(Debug, Clone, PartialEq)]
pub struct Output {
    /// The 14 GPS channels, or None when the file carries no GPS records.
    pub channels: Option<GpsChannels>,
    /// The final lap table: the parser's laps with the GPS in-lap
    /// classification applied, or laps detected from GPS when there were none.
    pub laps: Vec<Lap>,
}

/// Run the complete GPS pipeline on one file's material.
///
/// 1. Decode the NAV-SOL records into channels (reconstructing corrupted
///    timecodes and applying `time_offset`).
/// 2. Detect the 65533 ms firmware gap (GNFI, direct, then indirect method)
///    and shift the GPS timecodes after it.
/// 3. Finish the lap table: mark the last lap "in" when the car is more than
///    30 m from start/finish at its end, or detect laps from the GPS track
///    when the parser found no LAP messages. Detection runs on the repaired
///    timecodes, so GPS-detected laps never need a correction of their own.
pub fn process(input: Input<'_>) -> Result<Output, Error> {
    let mut channels = decode_nav_sol(input.gps_data, input.time_offset)?;
    if let Some(ch) = channels.as_mut() {
        let gnfi = decode_gnfi_timecodes(input.gnfi_data, input.time_offset);
        let corrections = detect_gap_corrections(
            &ch.timecodes,
            &gnfi,
            input.non_gps_max_end_time,
            input.expected_dt_ms,
        );
        apply_corrections(&mut ch.timecodes, &corrections);
    }
    let laps = finish_laps(input.laps, channels.as_ref(), input.start_finish);
    Ok(Output { channels, laps })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::navsol::test_support::{gps_record, records};

    fn input<'a>(gps: &'a [u8], laps: Vec<Lap>) -> Input<'a> {
        Input {
            gps_data: gps,
            gnfi_data: &[],
            time_offset: 0,
            non_gps_max_end_time: None,
            start_finish: None,
            laps,
            expected_dt_ms: DEFAULT_EXPECTED_DT_MS,
        }
    }

    #[test]
    fn no_gps_material_passes_laps_through() {
        let laps = vec![Lap::new(0, 0, 1000, "full")];
        let out = process(input(&[], laps.clone())).unwrap();
        assert!(out.channels.is_none());
        assert_eq!(out.laps, laps);
    }

    #[test]
    fn malformed_material_is_an_error() {
        let err = process(input(&[0u8; 57], Vec::new())).unwrap_err();
        assert!(err.to_string().contains("not a multiple of 56"));
    }

    #[test]
    fn gap_is_repaired_before_laps_are_finished() {
        // 50 clean samples, then the 65533 ms firmware jump.
        let mut tcs: Vec<i32> = (0..100).map(|i| i * 40).collect();
        for t in &mut tcs[50..] {
            *t += 65533;
        }
        let data = records(&tcs);
        let out = process(input(&data, Vec::new())).unwrap();
        let ch = out.channels.unwrap();
        let max_dt = ch.timecodes.windows(2).map(|w| w[1] - w[0]).max().unwrap();
        assert!(max_dt <= 80, "gap not repaired: {max_dt}");
    }

    #[test]
    fn time_offset_applies_to_channels() {
        let data = gps_record(5000);
        let mut inp = input(&data, Vec::new());
        inp.time_offset = 3000;
        let out = process(inp).unwrap();
        assert_eq!(out.channels.unwrap().timecodes, vec![2000]);
    }
}
