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

    // GPS_Yaw_Rate = d(heading)/dt (deg/s) with ±180° wrap handling
    let mut yaw_rate = Vec::with_capacity(n);
    yaw_rate.push(0.0f32);
    for i in 0..dt_sec.len() {
        let mut dh = heading_deg[i + 1] - heading_deg[i];
        if dh > 180.0 {
            dh -= 360.0;
        }
        if dh < -180.0 {
            dh += 360.0;
        }
        yaw_rate.push((dh / dt_sec[i]) as f32);
    }

    // GPS_LateralAcc = speed × yaw_rate × π/180 / 9.81 (g)
    let lateral_acc: Vec<f32> = (0..n)
        .map(|i| (speed[i] * yaw_rate[i] as f64 * (PI / 180.0) / 9.81) as f32)
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

/// Fix timecodes for firmware that butchers the upper 16 bits.
///
/// If any timecode goes backwards, reconstruct from the bottom 16 bits by
/// phase-unwrapping: each sample takes the 65536ms multiple that places it
/// closest to the previous reconstructed time (half-range hysteresis).
///
/// The distinction that matters: a genuine 16-bit rollover appears as a LARGE
/// backwards step of the masked value (close to 65536 — e.g. 65500 → 20),
/// while newer Solo 2 firmware also writes GPS records with small
/// out-of-order jitter at buffer-block seams (time stepping back ~40-160ms).
/// The old rule counted ANY decrease as a rollover and added 65536ms per
/// jitter seam — 3,500+ times in a reported file — inflating a 16-minute
/// race into a "64-hour" session and desynchronizing GPS from every other
/// channel. With half-range hysteresis, jitter is preserved as-is (it is
/// re-ordered later by `sanitize_gps_records`) and only true rollovers
/// advance the wrap offset. Out-of-order stragglers from just before a
/// rollover also resolve to their correct pre-wrap time.
fn fix_timecodes(timecodes: &mut [i32]) {
    if timecodes.len() < 2 {
        return;
    }

    // Check if any timecode goes backwards
    let has_backwards = timecodes.windows(2).any(|w| w[1] < w[0]);
    if !has_backwards {
        return;
    }

    const WRAP: i64 = 65536;
    let base = (timecodes[0] as i64) - (timecodes[0] as i64 & (WRAP - 1));
    let mut prev = (timecodes[0] as i64 & (WRAP - 1)) + base;
    timecodes[0] = prev as i32;
    for tc in timecodes.iter_mut().skip(1) {
        let masked = *tc as i64 & (WRAP - 1);
        // Fold the masked delta into (-32768, 32768] relative to the previous
        // reconstructed time: the closest possible band.
        let mut delta = (masked + base - prev).rem_euclid(WRAP);
        if delta > WRAP / 2 {
            delta -= WRAP;
        }
        let cand = prev + delta;
        *tc = cand as i32;
        prev = cand;
    }
}

/// One GPS week in milliseconds — the u-blox `itow` field wraps at this.
const WEEK_MS: i64 = 604_800_000;

/// Repair a raw GPS record buffer (56-byte NAV-SOL records) whose logger
/// timecodes exhibit the 16-bit corruption (time running backwards).
///
/// The primary reconstruction uses the receiver's own clock: every NAV-SOL
/// record carries `itow` (GPS time of week, ms), which the logger firmware
/// bug cannot touch. Affected Solo 2 files also write EVERY GPS epoch twice
/// (two independent position solutions, ~4m apart, sharing one `itow`);
/// keeping both would weave a ~4m square-wave zigzag through an otherwise
/// centimeter-smooth track line, so exactly one record is kept per epoch
/// (the first in file order — each sub-stream is equally smooth). Records
/// are ordered by `itow` and their timecodes rebuilt as `itow + offset`,
/// where the offset is the median of (phase-unwrapped timecode − itow) —
/// robust to any residual straggler rows.
///
/// When `itow` is unusable (no-fix receivers may emit zeros), falls back to
/// phase-unwrapping the logger timecodes (see `fix_timecodes`) and
/// stable-sorting records by the repaired time.
///
/// Values are never altered — only the clock is repaired, the records
/// re-ordered, and duplicate-epoch twins dropped. Returns true when a repair
/// was made; clean buffers are left untouched byte-for-byte.
pub fn sanitize_gps_records(gps_data: &mut Vec<u8>) -> bool {
    const REC: usize = 56;
    if gps_data.len() < 2 * REC || !gps_data.len().is_multiple_of(REC) {
        return false;
    }
    let n = gps_data.len() / REC;

    let read_i32 = |data: &[u8], off: usize| {
        i32::from_le_bytes([data[off], data[off + 1], data[off + 2], data[off + 3]])
    };
    let raw_timecodes: Vec<i32> = (0..n).map(|i| read_i32(gps_data, i * REC)).collect();

    if !raw_timecodes.windows(2).any(|w| w[1] < w[0]) {
        return false;
    }

    let itows: Vec<i64> = (0..n)
        .map(|i| {
            u32::from_le_bytes([
                gps_data[i * REC + 4],
                gps_data[i * REC + 5],
                gps_data[i * REC + 6],
                gps_data[i * REC + 7],
            ]) as i64
        })
        .collect();

    // itow is usable when the values are in-range and (nearly) all non-zero.
    let zeros = itows.iter().filter(|&&t| t == 0).count();
    let in_range = itows.iter().all(|&t| (0..WEEK_MS).contains(&t));
    let itow_usable = in_range && zeros * 5 < n;

    let mut unwrapped = raw_timecodes.clone();
    fix_timecodes(&mut unwrapped);

    let order: Vec<usize>;
    let new_timecodes: Vec<i32>;
    if itow_usable {
        // Unwrap a mid-session GPS week rollover: if the itow span exceeds
        // half a week, the small values are from the following week.
        let (&min_itow, &max_itow) = match (itows.iter().min(), itows.iter().max()) {
            (Some(a), Some(b)) => (a, b),
            _ => return false,
        };
        let rollover = max_itow - min_itow > WEEK_MS / 2;
        let epoch_of = |i: usize| {
            let t = itows[i];
            if rollover && t < min_itow + WEEK_MS / 2 {
                t + WEEK_MS
            } else {
                t
            }
        };

        // Anchor the receiver clock to the logger clock: median of
        // (unwrapped timecode − epoch) over all records.
        let mut diffs: Vec<i64> = (0..n).map(|i| unwrapped[i] as i64 - epoch_of(i)).collect();
        diffs.sort_unstable();
        let offset = diffs[n / 2];

        // Keep the first record (file order) of each epoch, ordered by epoch.
        let mut first_by_epoch: std::collections::BTreeMap<i64, usize> = Default::default();
        for i in 0..n {
            first_by_epoch.entry(epoch_of(i)).or_insert(i);
        }
        order = first_by_epoch.values().copied().collect();
        new_timecodes = order
            .iter()
            .map(|&i| (epoch_of(i) + offset) as i32)
            .collect();
    } else {
        // Fallback: phase-unwrapped logger timecodes, stable order (ties keep
        // file order), all records kept.
        let mut idx: Vec<usize> = (0..n).collect();
        idx.sort_by_key(|&i| (unwrapped[i], i));
        new_timecodes = idx.iter().map(|&i| unwrapped[i]).collect();
        order = idx;
    }

    let mut out = Vec::with_capacity(order.len() * REC);
    for (k, &i) in order.iter().enumerate() {
        let off = i * REC;
        let mut record = [0u8; REC];
        record.copy_from_slice(&gps_data[off..off + REC]);
        record[0..4].copy_from_slice(&new_timecodes[k].to_le_bytes());
        out.extend_from_slice(&record);
    }
    *gps_data = out;
    true
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
    fn test_fix_timecodes_true_rollover_corrected() {
        // Masked value drops by ~65500 (a genuine 16-bit wrap): unwrap adds 65536.
        let mut tcs = vec![65400, 65440, 65480, 65520, 24, 64];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![65400, 65440, 65480, 65520, 65560, 65600]);
    }

    #[test]
    fn test_fix_timecodes_small_jitter_is_not_a_rollover() {
        // Newer Solo 2 firmware writes records with small out-of-order jitter at
        // block seams. The old any-decrease rule added 65536ms per seam,
        // inflating a 16-minute session into "64 hours". Jitter must pass
        // through untouched (ordering is restored by sanitize_gps_records).
        let mut tcs = vec![100, 200, 160, 240, 280, 239, 320];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![100, 200, 160, 240, 280, 239, 320]);
    }

    #[test]
    fn test_fix_timecodes_straggler_after_rollover_resolves_pre_wrap() {
        // A row from just before a rollover arriving just after it must land at
        // its true pre-wrap time, not a band away.
        let mut tcs = vec![65500, 65530, 20, 65510, 50];
        fix_timecodes(&mut tcs);
        assert_eq!(tcs, vec![65500, 65530, 65556, 65510, 65586]);
    }

    #[test]
    fn test_fix_timecodes_upper_bits_garbage_reconstructs_from_bottom_16() {
        // Upper 16 bits corrupted arbitrarily; bottom 16 carry a clean 40ms
        // cadence. Reconstruction only trusts the bottom bits.
        let truth: Vec<i64> = (0..8).map(|i| 500 + i * 40).collect();
        let mut tcs: Vec<i32> = truth
            .iter()
            .enumerate()
            .map(|(i, &t)| ((t & 65535) as i32) + if i % 3 == 2 { 65536 * 7 } else { 0 })
            .collect();
        fix_timecodes(&mut tcs);
        assert_eq!(tcs.iter().map(|&t| t as i64).collect::<Vec<_>>(), truth);
    }

    fn record_with_timecode_and_marker(timecode: i32, marker: u8) -> Vec<u8> {
        let mut r = make_gps_record(timecode);
        r[50] = marker; // reserved byte — rides along with the record
        r
    }

    #[test]
    fn test_sanitize_gps_records_clean_buffer_untouched() {
        let mut data = make_gps_record(1000);
        data.extend_from_slice(&make_gps_record(1040));
        let before = data.clone();
        assert!(!sanitize_gps_records(&mut data));
        assert_eq!(data, before);
    }

    #[test]
    fn test_sanitize_gps_records_reorders_jittered_records() {
        let mut data = Vec::new();
        for (tc, marker) in [(0, 1u8), (40, 2), (120, 4), (80, 3), (160, 5)] {
            data.extend_from_slice(&record_with_timecode_and_marker(tc, marker));
        }
        assert!(sanitize_gps_records(&mut data));
        let times: Vec<i32> = (0..5)
            .map(|i| {
                i32::from_le_bytes([
                    data[i * 56],
                    data[i * 56 + 1],
                    data[i * 56 + 2],
                    data[i * 56 + 3],
                ])
            })
            .collect();
        let markers: Vec<u8> = (0..5).map(|i| data[i * 56 + 50]).collect();
        assert_eq!(times, vec![0, 40, 80, 120, 160]);
        // Each record's payload moved with its timecode.
        assert_eq!(markers, vec![1, 2, 3, 4, 5]);
    }

    #[test]
    fn test_sanitize_gps_records_unwraps_and_orders_rollover_with_straggler() {
        // itow is zero in these records, so this exercises the fallback path.
        let mut data = Vec::new();
        for (tc, marker) in [(65500, 1u8), (65530, 2), (20, 4), (65510, 3), (50, 5)] {
            data.extend_from_slice(&record_with_timecode_and_marker(tc, marker));
        }
        assert!(sanitize_gps_records(&mut data));
        let times: Vec<i32> = (0..5)
            .map(|i| {
                i32::from_le_bytes([
                    data[i * 56],
                    data[i * 56 + 1],
                    data[i * 56 + 2],
                    data[i * 56 + 3],
                ])
            })
            .collect();
        let markers: Vec<u8> = (0..5).map(|i| data[i * 56 + 50]).collect();
        assert_eq!(times, vec![65500, 65510, 65530, 65556, 65586]);
        assert_eq!(markers, vec![1, 3, 2, 4, 5]);
    }

    fn record_with_itow(timecode: i32, itow: u32, marker: u8) -> Vec<u8> {
        let mut r = record_with_timecode_and_marker(timecode, marker);
        r[4..8].copy_from_slice(&itow.to_le_bytes());
        r
    }

    #[test]
    fn test_sanitize_gps_records_itow_rebuild_dedupes_doubled_epochs() {
        // The reported Solo 2 fault: every epoch written twice (two position
        // solutions sharing one itow), logger timecodes jittered. The receiver
        // clock (itow) reconstructs the timeline: one record per epoch, first
        // in file order wins, timecodes = itow + median offset (460 here).
        let mut data = Vec::new();
        for (tc, itow, marker) in [
            (500, 71_000_040u32, 1u8),
            (460, 71_000_000, 2), // backwards step → repair triggers
            (540, 71_000_080, 3),
            (501, 71_000_040, 4), // duplicate epoch
            (461, 71_000_000, 5), // duplicate epoch
            (580, 71_000_120, 6),
        ] {
            data.extend_from_slice(&record_with_itow(tc, itow, marker));
        }
        assert!(sanitize_gps_records(&mut data));
        assert_eq!(data.len() / 56, 4); // one record per epoch
        let times: Vec<i32> = (0..4)
            .map(|i| {
                i32::from_le_bytes([
                    data[i * 56],
                    data[i * 56 + 1],
                    data[i * 56 + 2],
                    data[i * 56 + 3],
                ])
            })
            .collect();
        let markers: Vec<u8> = (0..4).map(|i| data[i * 56 + 50]).collect();
        assert_eq!(times, vec![460, 500, 540, 580]);
        assert_eq!(markers, vec![2, 1, 3, 6]);
    }

    #[test]
    fn test_sanitize_gps_records_itow_week_rollover() {
        // Session crossing the GPS week boundary: itow wraps to 0 mid-stream.
        let week: i64 = 604_800_000;
        let mut data = Vec::new();
        data.extend_from_slice(&record_with_itow(1000, (week - 40) as u32, 1));
        data.extend_from_slice(&record_with_itow(990, 20, 2)); // backwards trigger; next week
        assert!(sanitize_gps_records(&mut data));
        let times: Vec<i32> = (0..2)
            .map(|i| {
                i32::from_le_bytes([
                    data[i * 56],
                    data[i * 56 + 1],
                    data[i * 56 + 2],
                    data[i * 56 + 3],
                ])
            })
            .collect();
        let markers: Vec<u8> = (0..2).map(|i| data[i * 56 + 50]).collect();
        // Epochs stay in true order across the wrap, 60ms apart (−40 → +20).
        assert_eq!(markers, vec![1, 2]);
        assert_eq!(times[1] - times[0], 60);
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
