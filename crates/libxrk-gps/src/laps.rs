//! Lap table finishing: GPS in-lap classification and GPS lap detection.
//!
//! The parsers build the lap table from LAP messages (companion.md section
//! 7). Two steps need the GPS track and live here:
//!
//! - the last lap is an **in-lap** when the car is more than 30 m from the
//!   start/finish line at the lap's end, i.e. the session ended on the way to
//!   the pits rather than at the line;
//! - a file with **no LAP messages** gets its laps from the GPS track crossing
//!   the start/finish line (`utils::find_laps`).
//!
//! One step needs no GPS but runs here too, because it decides the last lap's
//! type together with the in-lap rule: when the recording continues past the
//! last LAP message, the AIM DLL reports a **final partial lap** from there to
//! the end of the data (companion.md section 7, "Final partial lap").

use crate::processing::GpsDecodeResult;
use crate::utils::{find_laps, lla2ecef};

/// How far from the start/finish line the car may be at the end of its last
/// lap for that lap to still count as a full lap [m]. The same radius within
/// which `find_laps` accepts a crossing.
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

/// Straight-line distance [m] between two (lat, lon) points at altitude 0.
fn distance_m(a: (f64, f64), b: (f64, f64)) -> f64 {
    let (ax, ay, az) = lla2ecef(a.0, a.1, 0.0);
    let (bx, by, bz) = lla2ecef(b.0, b.1, 0.0);
    ((ax - bx).powi(2) + (ay - by).powi(2) + (az - bz).powi(2)).sqrt()
}

/// Finish a parser's lap table: the final partial lap and the GPS steps.
///
/// With laps from LAP messages:
///
/// - When at least one lap was completed (the table has two laps or more:
///   the first one may be the out-lap) and `end_of_data` is later than the
///   last lap's end, a final partial lap from that end to `end_of_data` is
///   appended, typed "in", as the AIM DLL does. The lap before it ended at a
///   LAP message, so it was completed and is "full"; the in-lap rule does not
///   apply to it.
/// - Otherwise the last lap becomes "in" when the GPS position at its end is
///   more than [`MARKER_SIZE_M`] from `start_finish`; an out-lap is never
///   reclassified.
///
/// With no laps, they are detected from the GPS track instead, each crossing
/// to the next, the last one ending at the final GPS sample and typed "in";
/// `end_of_data` plays no part. Without GPS data or a start/finish marker the
/// GPS steps are skipped; the final partial lap does not need them.
///
/// `end_of_data` is the session-relative header timecode of the last data
/// message in file order (not the latest sample of any channel), or None
/// without data messages.
pub fn finish_laps(
    mut laps: Vec<Lap>,
    gps: Option<&GpsDecodeResult>,
    start_finish: Option<(f64, f64)>,
    end_of_data: Option<i64>,
) -> Vec<Lap> {
    if laps.len() >= 2 {
        let last = laps.last().expect("two laps or more");
        if let Some(end) = end_of_data.filter(|&end| end > last.end_time) {
            let partial = Lap::new(last.num + 1, last.end_time, end, "in");
            laps.push(partial);
            return laps;
        }
    }
    let (Some(gps), Some(marker)) = (gps, start_finish) else {
        return laps;
    };
    let n = gps.timecodes.len();
    if n == 0 {
        return laps;
    }

    if let Some(last) = laps.last_mut() {
        if last.lap_type != "out" {
            let idx = gps
                .timecodes
                .partition_point(|&t| t < last.end_time)
                .min(n - 1);
            let here = (gps.latitude[idx], gps.longitude[idx]);
            if distance_m(marker, here) > MARKER_SIZE_M {
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
            let (x, y, z) = lla2ecef(lat, lon, 0.0);
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

#[cfg(test)]
mod tests {
    use super::*;

    // The in-lap rule and GPS lap detection are tested against the SFJ
    // fixture's beacon laps in tests/test_gps_processing.py.

    #[test]
    fn no_gps_or_no_marker_leaves_laps_alone() {
        let laps = vec![Lap::new(0, 0, 1000, "full")];
        assert_eq!(
            finish_laps(laps.clone(), None, Some((0.0, 0.0)), None),
            laps
        );
    }

    fn two_laps() -> Vec<Lap> {
        vec![
            Lap::new(0, 0, 60_000, "out"),
            Lap::new(1, 60_000, 120_000, "full"),
        ]
    }

    #[test]
    fn data_past_the_last_lap_adds_a_final_partial_lap() {
        let laps = finish_laps(two_laps(), None, None, Some(150_000));
        assert_eq!(
            laps,
            vec![
                Lap::new(0, 0, 60_000, "out"),
                Lap::new(1, 60_000, 120_000, "full"),
                Lap::new(2, 120_000, 150_000, "in"),
            ]
        );
    }

    #[test]
    fn no_partial_lap_when_data_ends_at_the_last_lap() {
        for end in [None, Some(120_000), Some(110_000)] {
            assert_eq!(
                finish_laps(two_laps(), None, None, end),
                two_laps(),
                "{end:?}"
            );
        }
    }

    #[test]
    fn no_partial_lap_after_an_out_lap_alone() {
        let laps = vec![Lap::new(0, 0, 60_000, "out")];
        assert_eq!(finish_laps(laps.clone(), None, None, Some(150_000)), laps);
    }
}
