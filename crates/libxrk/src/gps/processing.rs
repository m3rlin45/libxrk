//! GPS processing: decode NAV-SOL messages, compute derived channels.
//!
//! Port of `_decode_gps()` from aim_xrk.pyx.
//! Decodes 56-byte GPS NAV-SOL records into 12 GPS channels.

use std::f64::consts::PI;

use crate::gps::utils as gps_utils;

/// Result of GPS decoding: 12 GPS channels with shared timecodes.
#[derive(Debug)]
pub struct GpsDecodeResult {
    pub timecodes: Vec<i64>,
    // Float64 channels (position/speed)
    pub speed: Vec<f64>,
    pub latitude: Vec<f64>,
    pub longitude: Vec<f64>,
    pub altitude: Vec<f64>,
    // Float32 channels (NAV-SOL raw fields + derived)
    pub satellites: Vec<f32>,
    pub fix: Vec<f32>,
    pub pdop: Vec<f32>,
    pub position_accuracy: Vec<f32>,
    pub velocity_accuracy: Vec<f32>,
    pub inline_acc: Vec<f32>,
    pub lateral_acc: Vec<f32>,
    pub yaw_rate: Vec<f32>,
}

/// GPS channel definition for Arrow table metadata.
pub struct GpsChannelDef {
    pub name: &'static str,
    pub units: &'static str,
    pub dec_pts: u32,
    pub interpolate: bool,
    pub is_f64: bool,
}

/// All 12 GPS channel definitions in order.
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
];

/// Decode GPS NAV-SOL messages into 12 GPS channels.
///
/// Each GPS message is 56 bytes:
///   - Bytes 0-3: AIM logger timecode (int32)
///   - Bytes 4-55: u-blox NAV-SOL payload (52 bytes)
pub fn decode_gps(
    gps_data: &[u8],
    time_offset: i64,
) -> Result<Option<GpsDecodeResult>, crate::Error> {
    if gps_data.is_empty() {
        return Ok(None);
    }
    if !gps_data.len().is_multiple_of(56) {
        return Err(crate::Error::InvalidData(format!(
            "GPS data length {} is not a multiple of 56",
            gps_data.len()
        )));
    }

    let n = gps_data.len() / 56;

    // Extract raw fields from 56-byte records
    let mut raw_timecodes = Vec::with_capacity(n);
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

    for i in 0..n {
        let off = i * 56;
        raw_timecodes.push(i32::from_le_bytes([
            gps_data[off],
            gps_data[off + 1],
            gps_data[off + 2],
            gps_data[off + 3],
        ]));
        ecef_x_cm.push(i32::from_le_bytes([
            gps_data[off + 16],
            gps_data[off + 17],
            gps_data[off + 18],
            gps_data[off + 19],
        ]));
        ecef_y_cm.push(i32::from_le_bytes([
            gps_data[off + 20],
            gps_data[off + 21],
            gps_data[off + 22],
            gps_data[off + 23],
        ]));
        ecef_z_cm.push(i32::from_le_bytes([
            gps_data[off + 24],
            gps_data[off + 25],
            gps_data[off + 26],
            gps_data[off + 27],
        ]));
        posacc_cm.push(u32::from_le_bytes([
            gps_data[off + 28],
            gps_data[off + 29],
            gps_data[off + 30],
            gps_data[off + 31],
        ]));
        ecef_dx_cms.push(i32::from_le_bytes([
            gps_data[off + 32],
            gps_data[off + 33],
            gps_data[off + 34],
            gps_data[off + 35],
        ]));
        ecef_dy_cms.push(i32::from_le_bytes([
            gps_data[off + 36],
            gps_data[off + 37],
            gps_data[off + 38],
            gps_data[off + 39],
        ]));
        ecef_dz_cms.push(i32::from_le_bytes([
            gps_data[off + 40],
            gps_data[off + 41],
            gps_data[off + 42],
            gps_data[off + 43],
        ]));
        velacc_cms.push(u32::from_le_bytes([
            gps_data[off + 44],
            gps_data[off + 45],
            gps_data[off + 46],
            gps_data[off + 47],
        ]));
        gps_fix_raw.push(gps_data[off + 14]);
        pdop_raw.push(u16::from_le_bytes([gps_data[off + 48], gps_data[off + 49]]));
        nsat_raw.push(gps_data[off + 51]);
    }

    // Fix timecodes for old MXP firmware (upper 16-bit corruption)
    fix_timecodes(&mut raw_timecodes);

    // Apply time offset
    let timecodes: Vec<i64> = raw_timecodes
        .iter()
        .map(|&tc| tc as i64 - time_offset)
        .collect();

    // ECEF to LLA conversion (Vermeille 2003)
    let mut latitude = Vec::with_capacity(n);
    let mut longitude = Vec::with_capacity(n);
    let mut altitude = Vec::with_capacity(n);
    for i in 0..n {
        let (lat, lon, alt) = gps_utils::ecef2lla_vermeille2003(
            ecef_x_cm[i] as f64 / 100.0,
            ecef_y_cm[i] as f64 / 100.0,
            ecef_z_cm[i] as f64 / 100.0,
        );
        latitude.push(lat);
        longitude.push(lon);
        altitude.push(alt);
    }

    // GPS Speed = sqrt(Vx² + Vy² + Vz²) / 100.0 (m/s)
    let speed: Vec<f64> = (0..n)
        .map(|i| {
            let vx = ecef_dx_cms[i] as f64;
            let vy = ecef_dy_cms[i] as f64;
            let vz = ecef_dz_cms[i] as f64;
            (vx * vx + vy * vy + vz * vz).sqrt() / 100.0
        })
        .collect();

    // Heading from ECEF velocity using ENU transformation
    let heading_deg: Vec<f64> = (0..n)
        .map(|i| {
            let lat_rad = latitude[i] * (PI / 180.0);
            let lon_rad = longitude[i] * (PI / 180.0);
            let (v_east, v_north) = gps_utils::ecef_velocity_to_enu(
                ecef_dx_cms[i] as f64,
                ecef_dy_cms[i] as f64,
                ecef_dz_cms[i] as f64,
                lat_rad,
                lon_rad,
            );
            v_east.atan2(v_north) * (180.0 / PI)
        })
        .collect();

    // Time deltas (seconds), with zero-protection
    // dt_sec = diff(timecodes_raw) / 1000.0; where dt <= 0 → inf
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

    // GPS_InlineAcc = d(speed)/dt / 9.81 (g)
    let mut inline_acc = Vec::with_capacity(n);
    inline_acc.push(0.0f32);
    for i in 0..dt_sec.len() {
        let dv = speed[i + 1] - speed[i];
        inline_acc.push((dv / dt_sec[i] / 9.81) as f32);
    }

    // GPS_Yaw_Rate = d(heading)/dt (deg/s) with ±180° wrap handling.
    // Kept in float64 for the lateral-acceleration product (matching
    // Cython, which uses the intermediate float64 yaw rate there); the
    // yaw-rate channel itself is float32.
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

    // GPS_LateralAcc = speed × yaw_rate × π/180 / 9.81 (g)
    let lateral_acc: Vec<f32> = (0..n)
        .map(|i| (((speed[i] * yaw_rate_f64[i]) * (PI / 180.0)) / 9.81) as f32)
        .collect();

    // Float32 channels from NAV-SOL raw fields
    let satellites: Vec<f32> = nsat_raw.iter().map(|&v| v as f32).collect();
    let fix: Vec<f32> = gps_fix_raw.iter().map(|&v| v as f32).collect();
    let pdop: Vec<f32> = pdop_raw.iter().map(|&v| v as f32 / 100.0).collect();
    let position_accuracy: Vec<f32> = posacc_cm.iter().map(|&v| v as f32 / 100.0).collect();
    let velocity_accuracy: Vec<f32> = velacc_cms.iter().map(|&v| v as f32 / 100.0).collect();

    Ok(Some(GpsDecodeResult {
        timecodes,
        speed,
        latitude,
        longitude,
        altitude,
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
    }))
}

/// Fix timecodes for old MXP firmware that butchers the upper 16 bits.
///
/// If any timecode goes backwards, reconstruct using only the bottom 16 bits
/// with wrap-around detection.
fn fix_timecodes(timecodes: &mut [i32]) {
    if timecodes.len() < 2 {
        return;
    }

    // Check if any timecode goes backwards
    let has_backwards = timecodes.windows(2).any(|w| w[1] < w[0]);
    if !has_backwards {
        return;
    }

    // Phase unwrap: place each sample at the multiple of 65536 CLOSEST to its
    // predecessor, i.e. fold the low-16 delta into [-32768, +32767]. A
    // backwards step therefore only reads as a rollover when it is near 65536;
    // smaller ones (out-of-order records, a replayed block, an all-zero dropout
    // record) keep their true time instead of inflating every later sample by
    // 65536ms. See spec/xrk_format.py reconstruct_gps_timecodes().
    //
    // timecodes[i - 1] is already reconstructed here, but stays congruent to
    // the raw value mod 65536, so the low-16 delta is unaffected.
    #[allow(clippy::needless_range_loop)]
    for i in 1..timecodes.len() {
        let delta = (((timecodes[i].wrapping_sub(timecodes[i - 1])) & 0xFFFF) ^ 0x8000) - 0x8000;
        timecodes[i] = timecodes[i - 1].wrapping_add(delta);
    }
}

impl GpsDecodeResult {
    /// Get ECEF positions in meters for lap detection (XYZ array).
    pub fn ecef_positions(&self, gps_data: &[u8]) -> Vec<[f64; 3]> {
        let n = gps_data.len() / 56;
        (0..n)
            .map(|i| {
                let off = i * 56;
                let x = i32::from_le_bytes([
                    gps_data[off + 16],
                    gps_data[off + 17],
                    gps_data[off + 18],
                    gps_data[off + 19],
                ]) as f64
                    / 100.0;
                let y = i32::from_le_bytes([
                    gps_data[off + 20],
                    gps_data[off + 21],
                    gps_data[off + 22],
                    gps_data[off + 23],
                ]) as f64
                    / 100.0;
                let z = i32::from_le_bytes([
                    gps_data[off + 24],
                    gps_data[off + 25],
                    gps_data[off + 26],
                    gps_data[off + 27],
                ]) as f64
                    / 100.0;
                [x, y, z]
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Create a minimal 56-byte GPS NAV-SOL record with the given timecode.
    fn make_gps_record(timecode: i32) -> Vec<u8> {
        let mut record = vec![0u8; 56];
        record[0..4].copy_from_slice(&timecode.to_le_bytes());
        record[14] = 3; // GPS fix type (3D)
        let ecef_x: i32 = 637_813_700; // ~6378137m in cm
        record[16..20].copy_from_slice(&ecef_x.to_le_bytes());
        record[20..24].copy_from_slice(&0i32.to_le_bytes());
        record[24..28].copy_from_slice(&0i32.to_le_bytes());
        record[28..32].copy_from_slice(&100u32.to_le_bytes()); // pos acc 1m
        record[44..48].copy_from_slice(&50u32.to_le_bytes()); // vel acc 0.5 m/s
        record[48..50].copy_from_slice(&150u16.to_le_bytes()); // pDOP 1.50
        record[51] = 12; // 12 satellites
        record
    }

    #[test]
    fn test_decode_gps_empty_returns_none() {
        let result = decode_gps(&[], 0).unwrap();
        assert!(result.is_none());
    }

    #[test]
    fn test_decode_gps_invalid_length_returns_error() {
        let data = vec![0u8; 57];
        let result = decode_gps(&data, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.to_string().contains("not a multiple of 56"),
            "error was: {}",
            err
        );
    }

    #[test]
    fn test_decode_gps_invalid_length_55() {
        let data = vec![0u8; 55];
        assert!(decode_gps(&data, 0).is_err());
    }

    #[test]
    fn test_decode_gps_single_record() {
        let record = make_gps_record(1000);
        let result = decode_gps(&record, 0).unwrap().unwrap();
        assert_eq!(result.timecodes.len(), 1);
        assert_eq!(result.timecodes[0], 1000);
        assert_eq!(result.speed.len(), 1);
        assert_eq!(result.latitude.len(), 1);
        assert_eq!(result.longitude.len(), 1);
        assert_eq!(result.altitude.len(), 1);
        assert_eq!(result.satellites.len(), 1);
        assert_eq!(result.fix.len(), 1);
        assert_eq!(result.pdop.len(), 1);
        assert_eq!(result.position_accuracy.len(), 1);
        assert_eq!(result.velocity_accuracy.len(), 1);
        assert_eq!(result.inline_acc.len(), 1);
        assert_eq!(result.lateral_acc.len(), 1);
        assert_eq!(result.yaw_rate.len(), 1);
    }

    #[test]
    fn test_decode_gps_single_record_values() {
        let record = make_gps_record(5000);
        let result = decode_gps(&record, 1000).unwrap().unwrap();
        assert_eq!(result.timecodes[0], 4000);
        assert!(result.speed[0].abs() < 0.01);
        assert_eq!(result.satellites[0], 12.0);
        assert_eq!(result.fix[0], 3.0);
        assert!((result.pdop[0] - 1.50).abs() < 0.01);
        assert!((result.position_accuracy[0] - 1.0).abs() < 0.01);
        assert!((result.velocity_accuracy[0] - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_decode_gps_two_records() {
        let mut data = make_gps_record(1000);
        data.extend_from_slice(&make_gps_record(2000));
        let result = decode_gps(&data, 0).unwrap().unwrap();
        assert_eq!(result.timecodes.len(), 2);
        assert_eq!(result.timecodes[0], 1000);
        assert_eq!(result.timecodes[1], 2000);
    }

    #[test]
    fn test_decode_gps_time_offset_applied() {
        let data = make_gps_record(5000);
        let result = decode_gps(&data, 3000).unwrap().unwrap();
        assert_eq!(result.timecodes[0], 2000);
    }

    #[test]
    fn test_fix_timecodes_monotonic() {
        let mut tcs = vec![100, 200, 300, 400];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![100, 200, 300, 400]);
    }

    #[test]
    fn test_fix_timecodes_true_rollover_advances_one_band() {
        // A backwards step of ~65536 IS a 16-bit rollover: advance one band.
        let mut tcs = vec![65440, 65480, 65520, 24, 64];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![65440, 65480, 65520, 65560, 65600]);
    }

    #[test]
    fn test_fix_timecodes_replayed_block_is_not_a_rollover() {
        // A logger that re-emits a block of records steps time backwards by the
        // block duration. The old any-decrease rule added 65536ms here and
        // inflated every later sample; the true times must be reproduced.
        let mut tcs = vec![1000, 1040, 1080, 1000, 1040, 1080, 1120];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![1000, 1040, 1080, 1000, 1040, 1080, 1120]);
    }

    #[test]
    fn test_fix_timecodes_seam_jitter_is_not_a_rollover() {
        // Small out-of-order jitter at a buffer-block seam.
        let mut tcs = vec![100, 200, 160, 240, 280];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![100, 200, 160, 240, 280]);
    }

    #[test]
    fn test_fix_timecodes_straggler_after_rollover_resolves_pre_wrap() {
        // A record from just before a rollover, arriving just after it, must
        // land at its true pre-wrap time rather than a whole band later.
        let mut tcs = vec![65500, 65530, 20, 65510, 50];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![65500, 65530, 65556, 65510, 65586]);
    }

    #[test]
    fn test_fix_timecodes_zero_dropout_record_absorbed() {
        // A single all-zero record wedged into a clean stream must not shift
        // the records after it: the next real sample re-locks to its true time.
        let mut tcs = vec![66063, 66103, 66143, 0, 66183, 66223];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs[4], 66183);
        assert_eq!(tcs[5], 66223);
    }

    #[test]
    fn test_fix_timecodes_upper_bits_garbage_reconstructs_from_low_16() {
        // Upper 16 bits corrupted arbitrarily; the low 16 carry a clean 40ms
        // cadence. Reconstruction must trust only the low bits.
        let truth: Vec<i32> = (0..8).map(|i| 500 + i * 40).collect();
        let mut tcs: Vec<i32> = truth
            .iter()
            .enumerate()
            .map(|(i, &t)| (t & 65535) + if i % 3 == 2 { 65536 * 7 } else { 0 })
            .collect();
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, truth);
    }

    #[test]
    fn test_fix_timecodes_clean_stream_with_large_gap_untouched() {
        // A legitimate forward gap larger than the 32768ms half-range must be
        // preserved: a monotonic stream is never reconstructed.
        let mut tcs = vec![1000, 1040, 200_000, 200_040];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![1000, 1040, 200_000, 200_040]);
    }

    #[test]
    fn test_fix_timecodes_single() {
        let mut tcs = vec![100];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![100]);
    }

    #[test]
    fn test_fix_timecodes_empty() {
        let mut tcs: Vec<i32> = vec![];
        fix_timecodes(&mut tcs);
        assert!(tcs.is_empty());
    }
}
