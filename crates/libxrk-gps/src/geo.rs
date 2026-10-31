//! WGS-84 coordinate conversions.

use std::f64::consts::PI;

/// WGS-84 semi-major axis [m].
const A: f64 = 6378137.0;
/// WGS-84 first eccentricity.
const E: f64 = 8.181919084261345e-2;

/// ECEF [m] -> (latitude deg, longitude deg, altitude m).
///
/// Closed-form conversion after H. Vermeille, "Computing geodetic coordinates
/// from geocentric coordinates" (2003/2004).
///
/// The operation order mirrors the numpy implementation this was ported
/// from (same reciprocal-multiply constants, left-to-right association, pow
/// for the `e**n` constants) so results agree with it to within a few ulp.
pub fn ecef2lla(x: f64, y: f64, z: f64) -> (f64, f64, f64) {
    let a = A;
    let e = E;
    let e2 = e.powf(2.0);
    let e4 = e.powf(4.0);

    let p = (x * x + y * y) * (1.0 / (a * a));
    let q = (((1.0 - e * e) / (a * a)) * z) * z;
    let r = (p + q - e4) * (1.0 / 6.0);
    let s = (((e4 / 4.0) * p) * q) / r.powf(3.0);
    let t = (1.0 + s + (s * (2.0 + s)).sqrt()).cbrt();
    let u = r * (1.0 + t + 1.0 / t);
    let v = (u * u + e4 * q).sqrt();
    let u = u + v;
    let w = ((e2 / 2.0) * (u - q)) / v;
    let k = (u + w * w).sqrt() - w;
    let d = (k * (x * x + y * y).sqrt()) / (k + e2);
    let rt_dd_zz = (d * d + z * z).sqrt();

    let lat = ((180.0 / PI) * 2.0) * z.atan2(d + rt_dd_zz);
    let lon = (180.0 / PI) * y.atan2(x);
    let alt = ((k + e2 - 1.0) / k) * rt_dd_zz;

    (lat, lon, alt)
}

/// (latitude deg, longitude deg, altitude m) -> ECEF [m].
pub fn lla2ecef(lat_deg: f64, lon_deg: f64, alt: f64) -> (f64, f64, f64) {
    let e_sq = E * E;

    let lat = lat_deg * (PI / 180.0);
    let lon = lon_deg * (PI / 180.0);

    let clat = lat.cos();
    let slat = lat.sin();

    let n = A / (1.0 - e_sq * slat * slat).sqrt();

    let x = (n + alt) * clat * lon.cos();
    let y = (n + alt) * clat * lon.sin();
    let z = ((1.0 - e_sq) * n + alt) * slat;

    (x, y, z)
}

/// Rotate an ECEF velocity into the local ENU frame at (lat, lon).
///
/// Any consistent unit; returns (v_east, v_north) in the same unit.
pub fn ecef_velocity_to_enu(dx: f64, dy: f64, dz: f64, lat_rad: f64, lon_rad: f64) -> (f64, f64) {
    let sin_lat = lat_rad.sin();
    let cos_lat = lat_rad.cos();
    let sin_lon = lon_rad.sin();
    let cos_lon = lon_rad.cos();

    let v_east = -sin_lon * dx + cos_lon * dy;
    let v_north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz;
    (v_east, v_north)
}

/// Straight-line distance [m] between two (lat, lon) points at altitude 0.
pub fn distance_m(a: (f64, f64), b: (f64, f64)) -> f64 {
    let (ax, ay, az) = lla2ecef(a.0, a.1, 0.0);
    let (bx, by, bz) = lla2ecef(b.0, b.1, 0.0);
    ((ax - bx).powi(2) + (ay - by).powi(2) + (az - bz).powi(2)).sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lla_ecef_round_trip() {
        for &(lat, lon, alt) in &[
            (35.3725327, 138.9275985, 644.16),
            (-33.9, 151.2, 10.0),
            (0.0, 0.0, 0.0),
        ] {
            let (x, y, z) = lla2ecef(lat, lon, alt);
            let (lat2, lon2, alt2) = ecef2lla(x, y, z);
            assert!((lat - lat2).abs() < 1e-9, "{lat} vs {lat2}");
            assert!((lon - lon2).abs() < 1e-9, "{lon} vs {lon2}");
            assert!((alt - alt2).abs() < 1e-6, "{alt} vs {alt2}");
        }
    }

    #[test]
    fn enu_rotation_at_origin() {
        // At (0, 0): ECEF +Y is east, +Z is north.
        let (e, n) = ecef_velocity_to_enu(0.0, 1.0, 0.0, 0.0, 0.0);
        assert!((e - 1.0).abs() < 1e-12 && n.abs() < 1e-12);
        let (e, n) = ecef_velocity_to_enu(0.0, 0.0, 1.0, 0.0, 0.0);
        assert!(e.abs() < 1e-12 && (n - 1.0).abs() < 1e-12);
    }

    #[test]
    fn distance_of_one_degree_longitude_at_equator() {
        let d = distance_m((0.0, 0.0), (0.0, 1.0));
        // Chord of 1 degree on the equator: 2 a sin(0.5 deg).
        assert!((d - 2.0 * A * (0.5f64).to_radians().sin()).abs() < 1e-3);
    }
}
