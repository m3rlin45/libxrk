//! GPS processing: decode NAV-SOL messages, compute derived channels.
//!
//! Port of `_decode_gps()` from aim_xrk.pyx.
//! Decodes 56-byte GPS NAV-SOL records into 12 GPS channels.

use std::f64::consts::PI;

use crate::gps_utils;

/// Result of GPS decoding: 12 GPS channels with shared timecodes.
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
    GpsChannelDef { name: "GPS Speed", units: "m/s", dec_pts: 1, interpolate: true, is_f64: true },
    GpsChannelDef { name: "GPS Latitude", units: "deg", dec_pts: 4, interpolate: true, is_f64: true },
    GpsChannelDef { name: "GPS Longitude", units: "deg", dec_pts: 4, interpolate: true, is_f64: true },
    GpsChannelDef { name: "GPS Altitude", units: "m", dec_pts: 1, interpolate: true, is_f64: true },
    GpsChannelDef { name: "GPS_Satellites", units: "", dec_pts: 0, interpolate: false, is_f64: false },
    GpsChannelDef { name: "GPS_Fix", units: "", dec_pts: 0, interpolate: false, is_f64: false },
    GpsChannelDef { name: "GPS_pDOP", units: "", dec_pts: 2, interpolate: false, is_f64: false },
    GpsChannelDef { name: "GPS_Position_Accuracy", units: "m", dec_pts: 2, interpolate: true, is_f64: false },
    GpsChannelDef { name: "GPS_Velocity_Accuracy", units: "m/s", dec_pts: 2, interpolate: true, is_f64: false },
    GpsChannelDef { name: "GPS_InlineAcc", units: "g", dec_pts: 2, interpolate: true, is_f64: false },
    GpsChannelDef { name: "GPS_LateralAcc", units: "g", dec_pts: 2, interpolate: true, is_f64: false },
    GpsChannelDef { name: "GPS_Yaw_Rate", units: "deg/s", dec_pts: 1, interpolate: true, is_f64: false },
];

/// Decode GPS NAV-SOL messages into 12 GPS channels.
///
/// Each GPS message is 56 bytes:
///   - Bytes 0-3: AIM logger timecode (int32)
///   - Bytes 4-55: u-blox NAV-SOL payload (52 bytes)
pub fn decode_gps(gps_data: &[u8], time_offset: i64) -> Option<GpsDecodeResult> {
    if gps_data.is_empty() {
        return None;
    }
    if gps_data.len() % 56 != 0 {
        return None;
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

    Some(GpsDecodeResult {
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
    })
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

    // Step 1: mask to bottom 16 bits + preserve base from first timecode
    let base = timecodes[0] - (timecodes[0] & 65535);
    for tc in timecodes.iter_mut() {
        *tc = (*tc & 65535) + base;
    }

    // Step 2: fix wrap-arounds (track previous unmodified value)
    let mut prev_masked = timecodes[0];
    let mut cum: i32 = 0;
    for i in 1..timecodes.len() {
        let current_masked = timecodes[i];
        if current_masked < prev_masked {
            cum += 65536;
        }
        prev_masked = current_masked;
        timecodes[i] += cum;
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
