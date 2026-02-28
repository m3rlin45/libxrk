//! GPS utilities: coordinate conversion, lap detection, Web Mercator projection.
//!
//! Port of gps.py functions.

use std::f64::consts::PI;

/// ECEF to LLA conversion using Vermeille 2003 algorithm.
///
/// Reference: "Computing geodetic coordinates from geocentric coordinates"
/// H. Vermeille, 2003/2004
///
/// Input: ECEF coordinates in meters.
/// Returns (latitude_deg, longitude_deg, altitude_m).
pub fn ecef2lla_vermeille2003(x: f64, y: f64, z: f64) -> (f64, f64, f64) {
    let a: f64 = 6378137.0;
    let e: f64 = 8.181919084261345e-2;
    let e2 = e * e;
    let e4 = e2 * e2;

    let p = (x * x + y * y) / (a * a);
    let q = ((1.0 - e2) / (a * a)) * z * z;
    let r = (p + q - e4) / 6.0;
    let s = (e4 / 4.0) * p * q / (r * r * r);
    let t = (1.0 + s + (s * (2.0 + s)).sqrt()).cbrt();
    let u = r * (1.0 + t + 1.0 / t);
    let v = (u * u + e4 * q).sqrt();
    let u = u + v; // u += v
    let w = (e2 / 2.0) * (u - q) / v;
    let k = (u + w * w).sqrt() - w;
    let d = k * (x * x + y * y).sqrt() / (k + e2);
    let rt_dd_zz = (d * d + z * z).sqrt();

    let lat = (180.0 / PI) * 2.0 * z.atan2(d + rt_dd_zz);
    let lon = (180.0 / PI) * y.atan2(x);
    let alt = (k + e2 - 1.0) / k * rt_dd_zz;

    (lat, lon, alt)
}

/// Convert LLA to ECEF coordinates.
///
/// Input: lat/lon in degrees, alt in meters.
/// Returns (x, y, z) in meters.
pub fn lla2ecef(lat_deg: f64, lon_deg: f64, alt: f64) -> (f64, f64, f64) {
    let a: f64 = 6378137.0;
    let e: f64 = 8.181919084261345e-2;
    let e_sq = e * e;

    let lat = lat_deg * (PI / 180.0);
    let lon = lon_deg * (PI / 180.0);

    let clat = lat.cos();
    let slat = lat.sin();

    let n = a / (1.0 - e_sq * slat * slat).sqrt();

    let x = (n + alt) * clat * lon.cos();
    let y = (n + alt) * clat * lon.sin();
    let z = ((1.0 - e_sq) * n + alt) * slat;

    (x, y, z)
}

/// Convert ECEF velocity to ENU (East-North-Up) frame.
///
/// Input velocities in any consistent unit (e.g., cm/s).
/// Returns (V_east, V_north) in the same unit.
pub fn ecef_velocity_to_enu(dx: f64, dy: f64, dz: f64, lat_rad: f64, lon_rad: f64) -> (f64, f64) {
    let sin_lat = lat_rad.sin();
    let cos_lat = lat_rad.cos();
    let sin_lon = lon_rad.sin();
    let cos_lon = lon_rad.cos();

    let v_east = -sin_lon * dx + cos_lon * dy;
    let v_north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz;
    (v_east, v_north)
}

fn dot3(a: &[f64; 3], b: &[f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// GPS-based lap detection using start/finish line crossing.
///
/// Finds where the vehicle trajectory crosses a vertical plane through the
/// start/finish marker, weighted by velocity direction.
///
/// Args:
///   xyz: Nx3 ECEF coordinates in meters
///   timecodes: N timecodes in ms
///   marker: (lat, lon) of start/finish in degrees
///
/// Returns: List of lap crossing times (ms, as f64 for sub-sample precision)
pub fn find_laps(xyz: &[[f64; 3]], timecodes: &[i64], marker: (f64, f64)) -> Vec<f64> {
    let n = xyz.len();
    if n < 2 {
        return Vec::new();
    }

    // Start/finish line: vertical line from altitude 0 to 1000m
    let (so_x, so_y, so_z) = lla2ecef(marker.0, marker.1, 0.0);
    let so = [so_x, so_y, so_z];
    let (sd_x, sd_y, sd_z) = {
        let (x, y, z) = lla2ecef(marker.0, marker.1, 1000.0);
        (x - so_x, y - so_y, z - so_z)
    };
    let sd = [sd_x, sd_y, sd_z];

    let n_seg = n - 1;

    // O[i] = XYZ[i] - SO, D[i] = XYZ[i+1] - XYZ[i]
    let o: Vec<[f64; 3]> = (0..n_seg)
        .map(|i| [xyz[i][0] - so[0], xyz[i][1] - so[1], xyz[i][2] - so[2]])
        .collect();

    let d: Vec<[f64; 3]> = (0..n_seg)
        .map(|i| {
            [
                xyz[i + 1][0] - xyz[i][0],
                xyz[i + 1][1] - xyz[i][1],
                xyz[i + 1][2] - xyz[i][2],
            ]
        })
        .collect();

    let marker_size: f64 = 30.0;
    let marker_size_sq = marker_size * marker_size;

    // minspeed[i]: was the vehicle going at least 4 m/s during segment i?
    let minspeed: Vec<bool> = (0..n_seg)
        .map(|i| {
            let dd = dot3(&d[i], &d[i]);
            let dt = (timecodes[i + 1] - timecodes[i]) as f64 * (4.0 / 1000.0);
            dd > dt * dt
        })
        .collect();

    // First pass: per-segment normal SN
    let sd_dot_sd = dot3(&sd, &sd);
    let sn: Vec<[f64; 3]> = (0..n_seg)
        .map(|i| {
            let sd_dot_d = dot3(&sd, &d[i]);
            [
                sd_dot_sd * d[i][0] - sd_dot_d * sd[0],
                sd_dot_sd * d[i][1] - sd_dot_d * sd[1],
                sd_dot_sd * d[i][2] - sd_dot_d * sd[2],
            ]
        })
        .collect();

    // t[i] = max(-dot(SN[i], O[i]) / dot(SN[i], D[i]), 0)
    let compute_t = |sn_vec: &[f64; 3], i: usize| -> f64 {
        let sn_dot_d = dot3(sn_vec, &d[i]);
        if sn_dot_d.abs() < 1e-30 {
            0.0
        } else {
            (-dot3(sn_vec, &o[i]) / sn_dot_d).max(0.0)
        }
    };

    let t: Vec<f64> = (0..n_seg).map(|i| compute_t(&sn[i], i)).collect();

    let compute_dist = |t_val: f64, i: usize| -> f64 {
        let p = [
            o[i][0] + t_val * d[i][0],
            o[i][1] + t_val * d[i][1],
            o[i][2] + t_val * d[i][2],
        ];
        dot3(&p, &p)
    };

    let dist: Vec<f64> = (0..n_seg).map(|i| compute_dist(t[i], i)).collect();

    // pick[i]: crossing detected between segments i and i+1
    let pick: Vec<bool> = (0..n_seg.saturating_sub(1))
        .map(|i| t[i + 1] <= 1.0 && t[i] > 1.0 && dist[i + 1] < marker_size_sq)
        .collect();

    // Velocity-weighted average normal from valid crossings
    let mut avg_sn = [0.0f64; 3];
    for i in 0..pick.len() {
        if pick[i] && minspeed[i + 1] {
            avg_sn[0] += sn[i + 1][0];
            avg_sn[1] += sn[i + 1][1];
            avg_sn[2] += sn[i + 1][2];
        }
    }

    // Second pass with averaged normal
    let t: Vec<f64> = (0..n_seg).map(|i| compute_t(&avg_sn, i)).collect();
    let dist: Vec<f64> = (0..n_seg).map(|i| compute_dist(t[i], i)).collect();
    let pick: Vec<bool> = (0..n_seg.saturating_sub(1))
        .map(|i| t[i + 1] <= 1.0 && t[i] > 1.0 && dist[i + 1] < marker_size_sq)
        .collect();

    // Build lap markers
    let mut lap_markers: Vec<f64> = vec![0.0];
    for (i, &picked) in pick.iter().enumerate() {
        if !picked {
            continue;
        }
        let mut idx = i + 1; // np.nonzero(pick)[0] + 1
        let tc_at_idx = timecodes[idx] as f64;
        if tc_at_idx <= *lap_markers.last().unwrap() {
            continue;
        }
        if !minspeed[idx] {
            // Find next segment where speed is above minimum
            if let Some(offset) = minspeed[idx..].iter().position(|&s| s) {
                idx += offset;
            } else {
                continue;
            }
        }
        if idx + 1 >= n {
            continue;
        }
        let marker_time =
            timecodes[idx] as f64 + t[idx] * (timecodes[idx + 1] - timecodes[idx]) as f64;
        lap_markers.push(marker_time);
    }

    // Return all markers except the sentinel 0
    lap_markers[1..].to_vec()
}
