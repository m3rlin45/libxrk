//! Lap table finishing: GPS in-lap classification and GPS lap detection.
//!
//! The parsers build the lap table from LAP messages (companion.md section
//! 7). Two steps need the GPS track and live here:
//!
//! - the last lap is an **in-lap** when the car is more than 30 m from the
//!   start/finish line at the lap's end, i.e. the session ended on the way to
//!   the pits rather than at the line;
//! - a file with **no LAP messages** gets its laps from the GPS track crossing
//!   the start/finish line ([`find_laps`]).

use crate::geo;
use crate::navsol::GpsChannels;

/// How far from the start/finish line the car may be at the end of its last
/// lap for that lap to still count as a full lap [m]. Also the radius within
/// which [`find_laps`] accepts a crossing.
pub const MARKER_SIZE_M: f64 = 30.0;

/// One lap of the lap table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Lap {
    /// 0-based lap number.
    pub num: i32,
    /// Session-relative start [ms].
    pub start_time: i64,
    /// Session-relative end [ms].
    pub end_time: i64,
    /// "out", "full" or "in".
    pub lap_type: String,
}

impl Lap {
    pub fn new(num: i32, start_time: i64, end_time: i64, lap_type: &str) -> Self {
        Lap {
            num,
            start_time,
            end_time,
            lap_type: lap_type.to_string(),
        }
    }
}

/// Finish a parser's lap table with the GPS track.
///
/// With laps from LAP messages, the last lap becomes "in" when the GPS
/// position at its end is more than [`MARKER_SIZE_M`] from `start_finish`;
/// an out-lap is never reclassified. With no laps, they are detected from
/// the GPS track instead, each crossing to the next, the last one ending at
/// the final GPS sample and typed "in". Without GPS data or a start/finish
/// marker the laps are returned unchanged.
pub fn finish_laps(
    mut laps: Vec<Lap>,
    gps: Option<&GpsChannels>,
    start_finish: Option<(f64, f64)>,
) -> Vec<Lap> {
    let (Some(gps), Some(marker)) = (gps, start_finish) else {
        return laps;
    };
    if gps.is_empty() {
        return laps;
    }

    if let Some(last) = laps.last_mut() {
        if last.lap_type != "out" {
            let idx = gps
                .timecodes
                .partition_point(|&t| t < last.end_time)
                .min(gps.len() - 1);
            let here = (gps.latitude[idx], gps.longitude[idx]);
            if geo::distance_m(marker, here) > MARKER_SIZE_M {
                last.lap_type = "in".to_string();
            }
        }
        return laps;
    }

    let xyz: Vec<[f64; 3]> = gps
        .latitude
        .iter()
        .zip(&gps.longitude)
        .map(|(&lat, &lon)| {
            let (x, y, z) = geo::lla2ecef(lat, lon, 0.0);
            [x, y, z]
        })
        .collect();
    let mut markers = find_laps(&xyz, &gps.timecodes, marker);
    if markers.is_empty() {
        return laps;
    }
    markers.push(*gps.timecodes.last().unwrap_or(&0) as f64);
    let count = markers.len() - 1;
    markers
        .windows(2)
        .enumerate()
        .map(|(i, w)| {
            Lap::new(
                i as i32,
                w[0] as i64,
                w[1] as i64,
                if i == count - 1 { "in" } else { "full" },
            )
        })
        .collect()
}

fn dot3(a: &[f64; 3], b: &[f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// Detect laps from start/finish line crossings of a GPS track.
///
/// The start/finish line is the vertical plane through `marker` (latitude,
/// longitude in degrees) whose normal is along the direction of travel; a
/// lap boundary is where the track crosses it within [`MARKER_SIZE_M`] while
/// moving at least 4 m/s. A first pass collects candidate crossings, their
/// velocity-weighted normals are averaged into one line normal, and a second
/// pass with that normal gives consistent crossing times.
///
/// `xyz` are ECEF positions [m], `timecodes` their times [ms]. Returns the
/// crossing times [ms] with sub-sample interpolation.
pub fn find_laps(xyz: &[[f64; 3]], timecodes: &[i64], marker: (f64, f64)) -> Vec<f64> {
    let n = xyz.len();
    if n < 2 {
        return Vec::new();
    }

    // Start/finish line: vertical line from altitude 0 to 1000 m
    let (so_x, so_y, so_z) = geo::lla2ecef(marker.0, marker.1, 0.0);
    let so = [so_x, so_y, so_z];
    let sd = {
        let (x, y, z) = geo::lla2ecef(marker.0, marker.1, 1000.0);
        [x - so_x, y - so_y, z - so_z]
    };

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

    let marker_size_sq = MARKER_SIZE_M * MARKER_SIZE_M;

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
    let compute_dist = |t_val: f64, i: usize| -> f64 {
        let p = [
            o[i][0] + t_val * d[i][0],
            o[i][1] + t_val * d[i][1],
            o[i][2] + t_val * d[i][2],
        ];
        dot3(&p, &p)
    };

    let t: Vec<f64> = (0..n_seg).map(|i| compute_t(&sn[i], i)).collect();
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

    // Second pass with the averaged normal
    let t: Vec<f64> = (0..n_seg).map(|i| compute_t(&avg_sn, i)).collect();
    let dist: Vec<f64> = (0..n_seg).map(|i| compute_dist(t[i], i)).collect();
    let pick: Vec<bool> = (0..n_seg.saturating_sub(1))
        .map(|i| t[i + 1] <= 1.0 && t[i] > 1.0 && dist[i + 1] < marker_size_sq)
        .collect();

    let mut lap_markers: Vec<f64> = vec![0.0];
    for (i, &picked) in pick.iter().enumerate() {
        if !picked {
            continue;
        }
        let mut idx = i + 1;
        if (timecodes[idx] as f64) <= *lap_markers.last().unwrap() {
            continue;
        }
        if !minspeed[idx] {
            // Advance to the next segment where the car is moving
            if let Some(offset) = minspeed[idx..].iter().position(|&s| s) {
                idx += offset;
            } else {
                continue;
            }
        }
        if idx + 1 >= n {
            continue;
        }
        lap_markers
            .push(timecodes[idx] as f64 + t[idx] * (timecodes[idx + 1] - timecodes[idx]) as f64);
    }

    lap_markers[1..].to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;

    const SF: (f64, f64) = (35.3725, 138.9276);

    /// A car going round a circle of `radius` m centred north-east of the
    /// start/finish, which lies on the circle, at `speed` m/s, 25 Hz.
    fn circuit(laps: f64, radius: f64, speed: f64) -> GpsChannels {
        // Local tangent plane approximations are fine at this scale.
        let m_per_deg_lat = 111_132.0;
        let m_per_deg_lon = 111_132.0 * (SF.0 * PI / 180.0).cos();
        let centre = (SF.0 + radius / m_per_deg_lat, SF.1);
        let circumference = 2.0 * PI * radius;
        let dt = 0.04;
        let n = (laps * circumference / speed / dt) as usize;
        let mut lat = Vec::with_capacity(n);
        let mut lon = Vec::with_capacity(n);
        let mut tcs = Vec::with_capacity(n);
        for i in 0..n {
            // Start at the bottom of the circle (the start/finish), go clockwise.
            let theta = -PI / 2.0 + (i as f64 * dt * speed) / radius;
            lat.push(centre.0 + radius * theta.sin() / m_per_deg_lat);
            lon.push(centre.1 + radius * theta.cos() / m_per_deg_lon);
            tcs.push((i as f64 * dt * 1000.0) as i64);
        }
        let zeros = vec![0.0f32; n];
        GpsChannels {
            timecodes: tcs,
            speed: vec![speed; n],
            latitude: lat,
            longitude: lon,
            altitude: vec![0.0; n],
            itow: vec![0.0; n],
            satellites: zeros.clone(),
            fix: zeros.clone(),
            pdop: zeros.clone(),
            position_accuracy: zeros.clone(),
            velocity_accuracy: zeros.clone(),
            inline_acc: zeros.clone(),
            lateral_acc: zeros.clone(),
            yaw_rate: zeros.clone(),
            week: zeros,
        }
    }

    #[test]
    fn no_gps_or_no_marker_leaves_laps_alone() {
        let laps = vec![Lap::new(0, 0, 1000, "full")];
        assert_eq!(finish_laps(laps.clone(), None, Some(SF)), laps);
        let gps = circuit(1.0, 200.0, 20.0);
        assert_eq!(finish_laps(laps.clone(), Some(&gps), None), laps);
    }

    #[test]
    fn last_lap_far_from_start_finish_is_an_in_lap() {
        let gps = circuit(2.5, 200.0, 20.0);
        let end = *gps.timecodes.last().unwrap();
        let laps = vec![
            Lap::new(0, 0, end / 2, "full"),
            Lap::new(1, end / 2, end, "full"),
        ];
        let out = finish_laps(laps, Some(&gps), Some(SF));
        assert_eq!(out[0].lap_type, "full");
        assert_eq!(out[1].lap_type, "in");
    }

    #[test]
    fn last_lap_ending_at_the_line_stays_full() {
        let gps = circuit(2.0, 200.0, 20.0);
        let end = *gps.timecodes.last().unwrap() + 40;
        let laps = vec![
            Lap::new(0, 0, end / 2, "full"),
            Lap::new(1, end / 2, end, "full"),
        ];
        let out = finish_laps(laps, Some(&gps), Some(SF));
        assert_eq!(out[1].lap_type, "full");
    }

    #[test]
    fn unknown_position_at_lap_end_does_not_classify() {
        // The car's position at the lap end is unknown (no-fix record):
        // there is no evidence it is away from the line, so "full" stays.
        let mut gps = circuit(2.5, 200.0, 20.0);
        let last = gps.len() - 1;
        gps.latitude[last] = f64::NAN;
        gps.longitude[last] = f64::NAN;
        let end = *gps.timecodes.last().unwrap();
        let out = finish_laps(vec![Lap::new(0, 0, end, "full")], Some(&gps), Some(SF));
        assert_eq!(out[0].lap_type, "full");
    }

    #[test]
    fn an_out_lap_is_never_reclassified() {
        let gps = circuit(0.5, 200.0, 20.0);
        let end = *gps.timecodes.last().unwrap();
        let out = finish_laps(vec![Lap::new(0, 0, end, "out")], Some(&gps), Some(SF));
        assert_eq!(out[0].lap_type, "out");
    }

    #[test]
    fn laps_are_detected_from_the_track_when_there_are_none() {
        let gps = circuit(3.5, 200.0, 20.0);
        let lap_time_ms = (2.0 * PI * 200.0 / 20.0 * 1000.0) as i64;
        let out = finish_laps(Vec::new(), Some(&gps), Some(SF));
        // Crossings at laps 1, 2 and 3 give laps 1-2, 2-3 and 3-end.
        assert_eq!(out.len(), 3, "{out:?}");
        for (i, lap) in out.iter().enumerate() {
            assert_eq!(lap.num, i as i32);
            let expected_start = (i as i64 + 1) * lap_time_ms;
            assert!(
                (lap.start_time - expected_start).abs() < 60,
                "lap {i} starts at {} not {expected_start}",
                lap.start_time
            );
        }
        assert_eq!(out[0].lap_type, "full");
        assert_eq!(out[1].lap_type, "full");
        assert_eq!(out[2].lap_type, "in");
        assert_eq!(out[2].end_time, *gps.timecodes.last().unwrap());
    }

    #[test]
    fn a_track_that_never_crosses_the_line_gives_no_laps() {
        let gps = circuit(2.0, 200.0, 20.0);
        let far = (SF.0 + 0.05, SF.1);
        assert!(finish_laps(Vec::new(), Some(&gps), Some(far)).is_empty());
    }

    #[test]
    fn find_laps_needs_two_points() {
        assert!(find_laps(&[[0.0; 3]], &[0], SF).is_empty());
    }
}
