//! GPS timing fix: detect and correct 16-bit overflow timing gaps.
//!
//! Port of fix_gps_timing_gaps / detect_gps_timing_offset_from_gnfi from gps.py.
//!
//! Some AIM data loggers produce GPS data with spurious timestamp jumps
//! (e.g., 65533ms gaps that should be ~40ms). This is caused by a 16-bit
//! overflow bug in the logger firmware where the upper 16 bits of the
//! timecode are corrupted, resulting in a gap of approximately 65533ms
//! (0xFFED, or 2^16 - 3).

/// A gap correction: all timecodes after `gap_time` should be reduced by `correction`.
#[derive(Debug, Clone)]
pub struct GapCorrection {
    pub gap_time: i64,
    pub correction: i64,
}

const OVERFLOW_BUG_MS: i64 = 65533;
const OVERFLOW_GAP_MIN: i64 = 60000;
const OVERFLOW_GAP_MAX: i64 = 70000;

/// Detect GPS timing offset using GNFI as reference clock.
///
/// GNFI messages run on the logger's internal clock (NOT the buggy GPS timecode
/// stream). They are continuous with no gaps and end at the true session end time.
/// By comparing the GPS end time to GNFI end time, we can detect if the GPS
/// firmware bug added ~65533ms to GPS timecodes.
pub fn detect_gps_timing_offset_from_gnfi(
    gps_timecodes: &[i64],
    gnfi_timecodes: &[i64],
    expected_dt_ms: f64,
) -> Vec<GapCorrection> {
    if gnfi_timecodes.len() < 2 || gps_timecodes.len() < 2 {
        return Vec::new();
    }

    let tolerance: i64 = 5000;
    // Safety: both slices have len >= 2 (checked above), so .last() is always Some.
    let gps_end = *gps_timecodes.last().unwrap_or(&0);
    let gnfi_end = *gnfi_timecodes.last().unwrap_or(&0);
    let offset = gps_end - gnfi_end;

    // Check if GPS extends ~65533ms beyond GNFI (the bug signature)
    if !(OVERFLOW_BUG_MS - tolerance..=OVERFLOW_BUG_MS + tolerance).contains(&offset) {
        return Vec::new();
    }

    // Bug detected! Find the gap where it likely occurred
    let gap_threshold = expected_dt_ms * 10.0;

    let mut largest_gap_idx: Option<usize> = None;
    let mut largest_gap_size: f64 = 0.0;

    for i in 0..gps_timecodes.len() - 1 {
        let dt = (gps_timecodes[i + 1] - gps_timecodes[i]) as f64;
        if dt > gap_threshold && dt > largest_gap_size {
            largest_gap_size = dt;
            largest_gap_idx = Some(i);
        }
    }

    let Some(idx) = largest_gap_idx else {
        return Vec::new();
    };

    let gap_time = gps_timecodes[idx];
    let gap_size = largest_gap_size as i64;

    // For direct gaps (60000-70000ms), use gap_size - expected_dt as correction
    // For hidden bugs (detected via GNFI), use OVERFLOW_BUG_MS as correction
    let correction =
        if (OVERFLOW_BUG_MS - tolerance..=OVERFLOW_BUG_MS + tolerance).contains(&gap_size) {
            gap_size - expected_dt_ms as i64
        } else {
            OVERFLOW_BUG_MS
        };

    vec![GapCorrection {
        gap_time,
        correction,
    }]
}

/// Detect and compute GPS timing gap corrections.
///
/// Uses three detection methods in order of preference:
/// 1. GNFI-based: compare GPS end time to GNFI end time
/// 2. Direct: gaps between 60000ms and 70000ms
/// 3. Indirect: GPS extends ~65533ms beyond non-GPS channels
pub fn detect_gap_corrections(
    gps_timecodes: &[i64],
    gnfi_timecodes: &[i64],
    non_gps_max_end_time: Option<i64>,
    expected_dt_ms: f64,
) -> Vec<GapCorrection> {
    if gps_timecodes.len() < 2 {
        return Vec::new();
    }

    let gap_threshold = expected_dt_ms * 10.0;

    // Find gap indices
    let gap_indices: Vec<usize> = (0..gps_timecodes.len() - 1)
        .filter(|&i| {
            let dt = (gps_timecodes[i + 1] - gps_timecodes[i]) as f64;
            dt > gap_threshold
        })
        .collect();

    if gap_indices.is_empty() {
        return Vec::new();
    }

    // Method 1: GNFI-based detection (most reliable)
    if !gnfi_timecodes.is_empty() {
        let corrections =
            detect_gps_timing_offset_from_gnfi(gps_timecodes, gnfi_timecodes, expected_dt_ms);
        if !corrections.is_empty() {
            return corrections;
        }
    }

    // Method 2: Direct detection - gaps between 60000ms and 70000ms
    let mut corrections = Vec::new();
    for &gap_idx in &gap_indices {
        let gap_time = gps_timecodes[gap_idx];
        let gap_size = gps_timecodes[gap_idx + 1] - gps_timecodes[gap_idx];

        if (OVERFLOW_GAP_MIN..=OVERFLOW_GAP_MAX).contains(&gap_size) {
            let correction = gap_size - expected_dt_ms as i64;
            corrections.push(GapCorrection {
                gap_time,
                correction,
            });
        }
    }
    if !corrections.is_empty() {
        return corrections;
    }

    // Method 3: Indirect detection - GPS extends ~65533ms beyond other channels
    if let Some(max_non_gps_end) = non_gps_max_end_time {
        // Safety: gps_timecodes.len() >= 2 checked at function entry
        let gps_end = *gps_timecodes.last().unwrap_or(&0);
        let end_offset = gps_end - max_non_gps_end;

        if (OVERFLOW_GAP_MIN..=OVERFLOW_GAP_MAX).contains(&end_offset) {
            // Find the largest gap — gap_indices is non-empty (checked at line 109),
            // but guard against panics on malformed data regardless.
            if let Some(largest_gap_idx) = gap_indices
                .iter()
                .copied()
                .max_by_key(|&i| gps_timecodes[i + 1] - gps_timecodes[i])
            {
                let gap_time = gps_timecodes[largest_gap_idx];
                corrections.push(GapCorrection {
                    gap_time,
                    correction: OVERFLOW_BUG_MS,
                });
            }
        }
    }

    corrections
}

/// Apply gap corrections to a timecodes array in place.
pub fn apply_corrections(timecodes: &mut [i64], corrections: &[GapCorrection]) {
    for tc in timecodes.iter_mut() {
        for correction in corrections {
            if *tc > correction.gap_time {
                *tc -= correction.correction;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_no_corrections_when_no_gaps() {
        let gps_tc: Vec<i64> = (0..100).map(|i| i * 40).collect();
        let gnfi_tc: Vec<i64> = (0..100).map(|i| i * 40).collect();
        let corrections = detect_gap_corrections(&gps_tc, &gnfi_tc, None, 40.0);
        assert!(corrections.is_empty());
    }

    #[test]
    fn test_direct_detection_65533ms_gap() {
        let mut gps_tc: Vec<i64> = (0..50).map(|i| i * 40).collect();
        // Insert a 65533ms gap
        for tc in &mut gps_tc[25..50] {
            *tc += 65533;
        }
        let corrections = detect_gap_corrections(&gps_tc, &[], None, 40.0);
        assert_eq!(corrections.len(), 1);
        assert_eq!(corrections[0].gap_time, gps_tc[24]);
        // correction should be gap_size - expected_dt = 65533 - 40 = 65493
        // But gap_size is 65533+40 = 65573, correction = 65573 - 40 = 65533
        assert!((corrections[0].correction - 65533).abs() <= 1);
    }

    #[test]
    fn test_gnfi_based_detection() {
        // GPS extends 65533ms beyond GNFI due to bug
        let mut gps_tc: Vec<i64> = (0..100).map(|i| i * 40).collect();
        let gnfi_tc: Vec<i64> = (0..100).map(|i| i * 40).collect();

        // Add a 5-second gap at sample 50 (too small to be detected directly)
        // but also shift all subsequent samples by 65533ms total
        for tc in &mut gps_tc[50..100] {
            *tc += 65533;
        }

        let corrections = detect_gps_timing_offset_from_gnfi(&gps_tc, &gnfi_tc, 40.0);
        assert_eq!(corrections.len(), 1);
    }

    #[test]
    fn test_apply_corrections() {
        let mut tc: Vec<i64> = vec![0, 40, 80, 120, 160];
        let corrections = vec![GapCorrection {
            gap_time: 80,
            correction: 65533,
        }];
        apply_corrections(&mut tc, &corrections);
        assert_eq!(tc, vec![0, 40, 80, 120 - 65533, 160 - 65533]);
    }
}
