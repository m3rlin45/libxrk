//! Decode u-blox NAV-SOL records into the 14 GPS channels.
//!
//! Each GPS record is 56 bytes: a 4-byte AIM logger timecode followed by the
//! 52-byte u-blox NAV-SOL (Navigation Solution) payload.
//!
//! ```text
//! Offset  NAV-SOL  Field      Type    Notes
//! 0       -        timecode   int32   AIM logger time [ms]
//! 4       0        iTOW       uint32  GPS time of week [ms]
//! 8       4        fTOW       int32   Fractional TOW [ns], +/-500000
//! 12      8        week       uint16  GPS week number
//! 14      10       gpsFix     uint8   Fix type (0=none, 2=2D, 3=3D)
//! 15      11       flags      uint8   Validity bitmask
//! 16      12       ecefX      int32   ECEF X position [cm]
//! 20      16       ecefY      int32   ECEF Y position [cm]
//! 24      20       ecefZ      int32   ECEF Z position [cm]
//! 28      24       pAcc       uint32  Position accuracy [cm]
//! 32      28       ecefVX     int32   ECEF X velocity [cm/s]
//! 36      32       ecefVY     int32   ECEF Y velocity [cm/s]
//! 40      36       ecefVZ     int32   ECEF Z velocity [cm/s]
//! 44      40       sAcc       uint32  Speed accuracy [cm/s]
//! 48      44       pDOP       uint16  Position DOP [*0.01]
//! 50      46       reserved1  uint8   u-blox reserved
//! 51      47       numSV      uint8   Number of satellites used
//! 52      48       reserved2  uint32  u-blox reserved (non-zero in some firmware)
//! ```
//!
//! The wire layout is `GPSPayload` in `spec/xrk_format.py`; the derived
//! channels are specified in `spec/docs/companion.md` section 5.

use std::f64::consts::PI;

use crate::geo;
use crate::Error;

/// Size of one GPS record: 4-byte logger timecode + 52-byte NAV-SOL payload.
pub const GPS_RECORD_SIZE: usize = 56;

/// The 14 GPS channels on their shared timebase.
#[derive(Debug, Clone, PartialEq)]
pub struct GpsChannels {
    /// Session-relative timecodes [ms], one per record.
    pub timecodes: Vec<i64>,
    // Float64 channels (position/speed)
    pub speed: Vec<f64>,
    pub latitude: Vec<f64>,
    pub longitude: Vec<f64>,
    pub altitude: Vec<f64>,
    /// GPS time of week [ms], raw from the receiver (uint32, wraps weekly).
    pub itow: Vec<f64>,
    // Float32 channels (NAV-SOL raw fields + derived)
    pub satellites: Vec<f32>,
    pub fix: Vec<f32>,
    pub pdop: Vec<f32>,
    pub position_accuracy: Vec<f32>,
    pub velocity_accuracy: Vec<f32>,
    pub inline_acc: Vec<f32>,
    pub lateral_acc: Vec<f32>,
    pub yaw_rate: Vec<f32>,
    /// GPS week number, raw from the receiver.
    pub week: Vec<f32>,
}

impl GpsChannels {
    /// Number of GPS records.
    pub fn len(&self) -> usize {
        self.timecodes.len()
    }

    /// True when there are no records.
    pub fn is_empty(&self) -> bool {
        self.timecodes.is_empty()
    }
}

/// Channel definition: name, units and display metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GpsChannelDef {
    pub name: &'static str,
    pub units: &'static str,
    pub dec_pts: u32,
    pub interpolate: bool,
    pub is_f64: bool,
}

/// All 14 GPS channel definitions, in the order the channels are exposed.
pub const GPS_CHANNEL_DEFS: &[GpsChannelDef] = &[
    GpsChannelDef {
        name: "GPS Speed",
        units: "m/s",
        dec_pts: 1,
        interpolate: true,
        is_f64: true,
    },
    GpsChannelDef {
        name: "GPS Latitude",
        units: "deg",
        dec_pts: 4,
        interpolate: true,
        is_f64: true,
    },
    GpsChannelDef {
        name: "GPS Longitude",
        units: "deg",
        dec_pts: 4,
        interpolate: true,
        is_f64: true,
    },
    GpsChannelDef {
        name: "GPS Altitude",
        units: "m",
        dec_pts: 1,
        interpolate: true,
        is_f64: true,
    },
    GpsChannelDef {
        name: "GPS_Satellites",
        units: "",
        dec_pts: 0,
        interpolate: false,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_Fix",
        units: "",
        dec_pts: 0,
        interpolate: false,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_pDOP",
        units: "",
        dec_pts: 2,
        interpolate: false,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_Position_Accuracy",
        units: "m",
        dec_pts: 2,
        interpolate: true,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_Velocity_Accuracy",
        units: "m/s",
        dec_pts: 2,
        interpolate: true,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_InlineAcc",
        units: "g",
        dec_pts: 2,
        interpolate: true,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_LateralAcc",
        units: "g",
        dec_pts: 2,
        interpolate: true,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_Yaw_Rate",
        units: "deg/s",
        dec_pts: 1,
        interpolate: true,
        is_f64: false,
    },
    GpsChannelDef {
        name: "GPS_ITOW",
        units: "ms",
        dec_pts: 0,
        interpolate: false,
        is_f64: true,
    },
    GpsChannelDef {
        name: "GPS_Week",
        units: "",
        dec_pts: 0,
        interpolate: false,
        is_f64: false,
    },
];

fn le_i32(b: &[u8], off: usize) -> i32 {
    i32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

fn le_u32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

fn le_u16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}

/// Decode concatenated GPS records into the 14 GPS channels.
///
/// Returns `Ok(None)` for empty input and an error when `gps_data` is not a
/// whole number of 56-byte records. Timecodes are reconstructed when the
/// stream is non-monotonic (see [`reconstruct_timecodes`]) and made
/// session-relative by subtracting `time_offset`.
pub fn decode_nav_sol(gps_data: &[u8], time_offset: i64) -> Result<Option<GpsChannels>, Error> {
    if gps_data.is_empty() {
        return Ok(None);
    }
    if !gps_data.len().is_multiple_of(GPS_RECORD_SIZE) {
        return Err(Error::InvalidData(format!(
            "GPS data length {} is not a multiple of {}",
            gps_data.len(),
            GPS_RECORD_SIZE
        )));
    }

    let n = gps_data.len() / GPS_RECORD_SIZE;

    let mut raw_timecodes = Vec::with_capacity(n);
    let mut itow_ms = Vec::with_capacity(n);
    let mut week_raw = Vec::with_capacity(n);
    let mut ecef_x_cm = Vec::with_capacity(n);
    let mut ecef_y_cm = Vec::with_capacity(n);
    let mut ecef_z_cm = Vec::with_capacity(n);
    let mut posacc_cm = Vec::with_capacity(n);
    let mut ecef_dx_cms = Vec::with_capacity(n);
    let mut ecef_dy_cms = Vec::with_capacity(n);
    let mut ecef_dz_cms = Vec::with_capacity(n);
    let mut velacc_cms = Vec::with_capacity(n);
    let mut gps_fix_raw = Vec::with_capacity(n);
    let mut pdop_raw = Vec::with_capacity(n);
    let mut nsat_raw = Vec::with_capacity(n);

    for rec in gps_data.chunks_exact(GPS_RECORD_SIZE) {
        raw_timecodes.push(le_i32(rec, 0));
        itow_ms.push(le_u32(rec, 4));
        week_raw.push(le_u16(rec, 12));
        gps_fix_raw.push(rec[14]);
        ecef_x_cm.push(le_i32(rec, 16));
        ecef_y_cm.push(le_i32(rec, 20));
        ecef_z_cm.push(le_i32(rec, 24));
        posacc_cm.push(le_u32(rec, 28));
        ecef_dx_cms.push(le_i32(rec, 32));
        ecef_dy_cms.push(le_i32(rec, 36));
        ecef_dz_cms.push(le_i32(rec, 40));
        velacc_cms.push(le_u32(rec, 44));
        pdop_raw.push(le_u16(rec, 48));
        nsat_raw.push(rec[51]);
    }

    // Some firmware corrupts the upper 16 bits of the logger timecode.
    reconstruct_timecodes(&mut raw_timecodes);

    let timecodes: Vec<i64> = raw_timecodes
        .iter()
        .map(|&tc| tc as i64 - time_offset)
        .collect();

    // ECEF [cm] -> WGS-84 latitude/longitude [deg], altitude [m].
    let mut latitude = Vec::with_capacity(n);
    let mut longitude = Vec::with_capacity(n);
    let mut altitude = Vec::with_capacity(n);
    for i in 0..n {
        let (lat, lon, alt) = geo::ecef2lla(
            ecef_x_cm[i] as f64 / 100.0,
            ecef_y_cm[i] as f64 / 100.0,
            ecef_z_cm[i] as f64 / 100.0,
        );
        latitude.push(lat);
        longitude.push(lon);
        altitude.push(alt);
    }

    // GPS Speed = |ECEF velocity| / 100 [m/s]
    let speed: Vec<f64> = (0..n)
        .map(|i| {
            let vx = ecef_dx_cms[i] as f64;
            let vy = ecef_dy_cms[i] as f64;
            let vz = ecef_dz_cms[i] as f64;
            (vx * vx + vy * vy + vz * vz).sqrt() / 100.0
        })
        .collect();

    // Heading from the ECEF velocity rotated into the local ENU frame.
    let heading_deg: Vec<f64> = (0..n)
        .map(|i| {
            let lat_rad = latitude[i] * (PI / 180.0);
            let lon_rad = longitude[i] * (PI / 180.0);
            let (v_east, v_north) = geo::ecef_velocity_to_enu(
                ecef_dx_cms[i] as f64,
                ecef_dy_cms[i] as f64,
                ecef_dz_cms[i] as f64,
                lat_rad,
                lon_rad,
            );
            v_east.atan2(v_north) * (180.0 / PI)
        })
        .collect();

    // Sample intervals [s]; a non-positive interval becomes +inf so the
    // differences below read as zero instead of dividing by zero.
    let dt_sec: Vec<f64> = (1..n)
        .map(|i| {
            let dt = (raw_timecodes[i] - raw_timecodes[i - 1]) as f64 / 1000.0;
            if dt > 0.0 {
                dt
            } else {
                f64::INFINITY
            }
        })
        .collect();

    // GPS_InlineAcc = d(speed)/dt / 9.81 [g]
    let mut inline_acc = Vec::with_capacity(n);
    inline_acc.push(0.0f32);
    for i in 0..dt_sec.len() {
        let dv = speed[i + 1] - speed[i];
        inline_acc.push((dv / dt_sec[i] / 9.81) as f32);
    }

    // GPS_Yaw_Rate = d(heading)/dt [deg/s] with +/-180 deg wrap handling.
    // Kept in float64 for the lateral-acceleration product; the channel
    // itself is float32.
    let mut yaw_rate_f64 = Vec::with_capacity(n);
    yaw_rate_f64.push(0.0f64);
    for i in 0..dt_sec.len() {
        let mut dh = heading_deg[i + 1] - heading_deg[i];
        if dh > 180.0 {
            dh -= 360.0;
        }
        if dh < -180.0 {
            dh += 360.0;
        }
        yaw_rate_f64.push(dh / dt_sec[i]);
    }
    let yaw_rate: Vec<f32> = yaw_rate_f64.iter().map(|&v| v as f32).collect();

    // GPS_LateralAcc = speed * yaw_rate * pi/180 / 9.81 [g]
    let lateral_acc: Vec<f32> = (0..n)
        .map(|i| (((speed[i] * yaw_rate_f64[i]) * (PI / 180.0)) / 9.81) as f32)
        .collect();

    let satellites: Vec<f32> = nsat_raw.iter().map(|&v| v as f32).collect();
    let fix: Vec<f32> = gps_fix_raw.iter().map(|&v| v as f32).collect();
    let pdop: Vec<f32> = pdop_raw.iter().map(|&v| v as f32 / 100.0).collect();
    let position_accuracy: Vec<f32> = posacc_cm.iter().map(|&v| v as f32 / 100.0).collect();
    let velocity_accuracy: Vec<f32> = velacc_cms.iter().map(|&v| v as f32 / 100.0).collect();
    let itow: Vec<f64> = itow_ms.iter().map(|&v| v as f64).collect();
    let week: Vec<f32> = week_raw.iter().map(|&v| v as f32).collect();

    Ok(Some(GpsChannels {
        timecodes,
        speed,
        latitude,
        longitude,
        altitude,
        itow,
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
        week,
    }))
}

/// Rebuild GPS timecodes whose upper 16 bits are unreliable.
///
/// Some AIM firmware periodically corrupts the upper 16 bits of the logger
/// timecode in GPS records, leaving only the low 16 bits trustworthy.
///
/// Reconstruction is a phase unwrap: each sample is placed at the multiple of
/// 65536 that puts it closest to its predecessor, i.e. the low-16 delta is
/// folded into [-32768, +32767]. A backwards step therefore only reads as a
/// 16-bit rollover when it is close to 65536; smaller ones (out-of-order
/// records, a replayed block, an all-zero dropout record) keep their true
/// time instead of inflating every later sample by 65536 ms.
///
/// Only applied when the stream is non-monotonic (the corruption signature);
/// clean streams are left untouched, which preserves legitimate forward gaps
/// larger than the 32768 ms half-range. See companion.md section 6.
pub fn reconstruct_timecodes(timecodes: &mut [i32]) {
    if timecodes.len() < 2 || timecodes.windows(2).all(|w| w[0] <= w[1]) {
        return;
    }
    // timecodes[i - 1] is already reconstructed here, but stays congruent to
    // the raw value mod 65536, so the low-16 delta is unaffected.
    #[allow(clippy::needless_range_loop)]
    for i in 1..timecodes.len() {
        let delta = (((timecodes[i].wrapping_sub(timecodes[i - 1])) & 0xFFFF) ^ 0x8000) - 0x8000;
        timecodes[i] = timecodes[i - 1].wrapping_add(delta);
    }
}

/// Builders for synthetic GPS records, shared by this crate's tests.
#[cfg(test)]
pub(crate) mod test_support {
    use super::GPS_RECORD_SIZE;

    /// One well-formed record with a 3D fix at ECEF (6378137 m, 0, 0),
    /// stationary, 12 satellites, pDOP 1.50, 1 m / 0.5 m/s accuracies.
    pub fn gps_record(timecode: i32) -> Vec<u8> {
        let mut r = vec![0u8; GPS_RECORD_SIZE];
        r[0..4].copy_from_slice(&timecode.to_le_bytes());
        r[14] = 3;
        r[16..20].copy_from_slice(&637_813_700i32.to_le_bytes());
        r[28..32].copy_from_slice(&100u32.to_le_bytes());
        r[44..48].copy_from_slice(&50u32.to_le_bytes());
        r[48..50].copy_from_slice(&150u16.to_le_bytes());
        r[51] = 12;
        r
    }

    /// Concatenated records at the given timecodes.
    pub fn records(timecodes: &[i32]) -> Vec<u8> {
        timecodes.iter().flat_map(|&t| gps_record(t)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{gps_record, records};
    use super::*;

    #[test]
    fn empty_input_is_none() {
        assert!(decode_nav_sol(&[], 0).unwrap().is_none());
    }

    #[test]
    fn partial_record_is_an_error() {
        for len in [55usize, 57] {
            let err = decode_nav_sol(&vec![0u8; len], 0).unwrap_err();
            assert!(err.to_string().contains("not a multiple of 56"), "{err}");
        }
    }

    #[test]
    fn channel_count_matches_defs() {
        assert_eq!(GPS_CHANNEL_DEFS.len(), 14);
        assert_eq!(GPS_CHANNEL_DEFS.iter().filter(|d| d.is_f64).count(), 5);
    }

    #[test]
    fn single_record_values() {
        let ch = decode_nav_sol(&gps_record(5000), 1000).unwrap().unwrap();
        assert_eq!(ch.len(), 1);
        assert_eq!(ch.timecodes, vec![4000]);
        assert!(ch.speed[0].abs() < 1e-9);
        assert_eq!(ch.satellites[0], 12.0);
        assert_eq!(ch.fix[0], 3.0);
        assert!((ch.pdop[0] - 1.50).abs() < 1e-6);
        assert!((ch.position_accuracy[0] - 1.0).abs() < 1e-6);
        assert!((ch.velocity_accuracy[0] - 0.5).abs() < 1e-6);
        // ECEF (a, 0, 0) is on the equator at the prime meridian, altitude 0.
        assert!(ch.latitude[0].abs() < 1e-9);
        assert!(ch.longitude[0].abs() < 1e-9);
        assert!(ch.altitude[0].abs() < 1e-6);
        // First sample of every difference channel is 0.
        assert_eq!(ch.inline_acc[0], 0.0);
        assert_eq!(ch.yaw_rate[0], 0.0);
        assert_eq!(ch.lateral_acc[0], 0.0);
    }

    #[test]
    fn fuji_speedway_position() {
        // The SFJ fixture's first record: the values pinned by the backend
        // tests for years (35.3725 N, 138.9276 E, 644.161 m).
        let mut r = gps_record(0);
        r[16..20].copy_from_slice(&(-392_555_554i32).to_le_bytes());
        r[20..24].copy_from_slice(&342_115_191i32.to_le_bytes());
        r[24..28].copy_from_slice(&367_201_827i32.to_le_bytes());
        let ch = decode_nav_sol(&r, 0).unwrap().unwrap();
        assert!((ch.latitude[0] - 35.3725327).abs() < 1e-6);
        assert!((ch.longitude[0] - 138.9275985).abs() < 1e-6);
        assert!((ch.altitude[0] - 644.1614).abs() < 1e-3);
    }

    #[test]
    fn speed_and_inline_acc_from_velocity() {
        // Two samples 1 s apart: 10 m/s then 20 m/s along +Z.
        let mut a = gps_record(0);
        a[40..44].copy_from_slice(&1000i32.to_le_bytes());
        let mut b = gps_record(1000);
        b[40..44].copy_from_slice(&2000i32.to_le_bytes());
        let data = [a, b].concat();
        let ch = decode_nav_sol(&data, 0).unwrap().unwrap();
        assert!((ch.speed[0] - 10.0).abs() < 1e-12);
        assert!((ch.speed[1] - 20.0).abs() < 1e-12);
        assert!((ch.inline_acc[1] - (10.0 / 9.81) as f32).abs() < 1e-6);
        // Heading north at (0, 0): +Z is due north, so no yaw.
        assert_eq!(ch.yaw_rate[1], 0.0);
        assert_eq!(ch.lateral_acc[1], 0.0);
    }

    #[test]
    fn zero_dt_does_not_divide_by_zero() {
        let mut a = gps_record(1000);
        a[40..44].copy_from_slice(&1000i32.to_le_bytes());
        let b = gps_record(1000);
        let ch = decode_nav_sol(&[a, b].concat(), 0).unwrap().unwrap();
        assert_eq!(ch.inline_acc[1], 0.0);
        assert_eq!(ch.yaw_rate[1], 0.0);
    }

    #[test]
    fn time_offset_is_subtracted() {
        let ch = decode_nav_sol(&records(&[1000, 2000]), 0).unwrap().unwrap();
        assert_eq!(ch.timecodes, vec![1000, 2000]);
        let ch = decode_nav_sol(&records(&[5000]), 3000).unwrap().unwrap();
        assert_eq!(ch.timecodes, vec![2000]);
    }

    fn reconstructed(mut tcs: Vec<i32>) -> Vec<i32> {
        reconstruct_timecodes(&mut tcs);
        tcs
    }

    #[test]
    fn reconstruct_clean_stream_untouched() {
        assert_eq!(
            reconstructed(vec![100, 200, 300, 400]),
            vec![100, 200, 300, 400]
        );
    }

    #[test]
    fn reconstruct_true_rollover_advances_one_band() {
        assert_eq!(
            reconstructed(vec![65440, 65480, 65520, 24, 64]),
            vec![65440, 65480, 65520, 65560, 65600]
        );
    }

    #[test]
    fn reconstruct_replayed_block_is_not_a_rollover() {
        let tcs = vec![1000, 1040, 1080, 1000, 1040, 1080, 1120];
        assert_eq!(reconstructed(tcs.clone()), tcs);
    }

    #[test]
    fn reconstruct_seam_jitter_is_not_a_rollover() {
        let tcs = vec![100, 200, 160, 240, 280];
        assert_eq!(reconstructed(tcs.clone()), tcs);
    }

    #[test]
    fn reconstruct_straggler_after_rollover_resolves_pre_wrap() {
        assert_eq!(
            reconstructed(vec![65500, 65530, 20, 65510, 50]),
            vec![65500, 65530, 65556, 65510, 65586]
        );
    }

    #[test]
    fn reconstruct_zero_dropout_record_absorbed() {
        let out = reconstructed(vec![66063, 66103, 66143, 0, 66183, 66223]);
        assert_eq!(out[4], 66183);
        assert_eq!(out[5], 66223);
    }

    #[test]
    fn reconstruct_upper_bits_garbage_from_low_16() {
        let truth: Vec<i32> = (0..8).map(|i| 500 + i * 40).collect();
        let corrupt: Vec<i32> = truth
            .iter()
            .enumerate()
            .map(|(i, &t)| (t & 65535) + if i % 3 == 2 { 65536 * 7 } else { 0 })
            .collect();
        assert_eq!(reconstructed(corrupt), truth);
    }

    #[test]
    fn reconstruct_clean_stream_with_large_gap_untouched() {
        let tcs = vec![1000, 1040, 200_000, 200_040];
        assert_eq!(reconstructed(tcs.clone()), tcs);
    }

    #[test]
    fn reconstruct_degenerate_inputs() {
        assert_eq!(reconstructed(vec![100]), vec![100]);
        assert_eq!(reconstructed(vec![]), Vec::<i32>::new());
    }
}
