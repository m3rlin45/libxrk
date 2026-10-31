//! The 65533 ms GPS timing bug: detection and correction.
//!
//! Some AIM loggers write GPS timecodes with a spurious jump of about
//! 65533 ms (0xFFED, 2^16 - 3) where the interval should be one sample. Every
//! GPS timecode after the jump is too large by that amount. GNFI messages run
//! on the logger's internal clock and are immune, which gives a reference to
//! detect the bug even when signal loss hides the jump inside a larger gap.
//! See companion.md section 6.

/// A gap correction: every timecode after `gap_time` is reduced by `correction`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GapCorrection {
    pub gap_time: i64,
    pub correction: i64,
}

/// Size of one GNFI record: 4-byte logger timecode + 28 bytes not used here.
pub const GNFI_RECORD_SIZE: usize = 32;

const OVERFLOW_BUG_MS: i64 = 65533;
const OVERFLOW_GAP_MIN: i64 = 60000;
const OVERFLOW_GAP_MAX: i64 = 70000;
const END_TOLERANCE_MS: i64 = 5000;

/// Session-relative GNFI timecodes from concatenated 32-byte GNFI records.
///
/// Empty when there are none or the data is not a whole number of records.
pub fn decode_gnfi_timecodes(gnfi_data: &[u8], time_offset: i64) -> Vec<i64> {
    if gnfi_data.is_empty() || !gnfi_data.len().is_multiple_of(GNFI_RECORD_SIZE) {
        return Vec::new();
    }
    gnfi_data
        .chunks_exact(GNFI_RECORD_SIZE)
        .map(|rec| i32::from_le_bytes([rec[0], rec[1], rec[2], rec[3]]) as i64 - time_offset)
        .collect()
}

/// Detect the GPS timing bug with GNFI as the reference clock.
///
/// GNFI ends at the true session end. If the GPS stream ends about 65533 ms
/// later, the bug is present; the correction is placed at the largest gap in
/// the GPS stream. A gap itself of about 65533 ms is corrected by its excess
/// over one sample; a smaller gap (the jump hidden by signal loss) by the
/// full 65533 ms.
pub fn detect_gps_timing_offset_from_gnfi(
    gps_timecodes: &[i64],
    gnfi_timecodes: &[i64],
    expected_dt_ms: f64,
) -> Vec<GapCorrection> {
    if gnfi_timecodes.len() < 2 || gps_timecodes.len() < 2 {
        return Vec::new();
    }

    let gps_end = *gps_timecodes.last().unwrap_or(&0);
    let gnfi_end = *gnfi_timecodes.last().unwrap_or(&0);
    let offset = gps_end - gnfi_end;
    let bug_range = OVERFLOW_BUG_MS - END_TOLERANCE_MS..=OVERFLOW_BUG_MS + END_TOLERANCE_MS;
    if !bug_range.contains(&offset) {
        return Vec::new();
    }

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

    let gap_size = largest_gap_size as i64;
    let correction = if bug_range.contains(&gap_size) {
        gap_size - expected_dt_ms as i64
    } else {
        OVERFLOW_BUG_MS
    };
    vec![GapCorrection {
        gap_time: gps_timecodes[idx],
        correction,
    }]
}

/// Detect the GPS timing bug and compute the corrections to apply.
///
/// Three methods, in order of preference, the first that finds something wins:
/// 1. GNFI-based: the GPS stream ends about 65533 ms after the GNFI stream.
/// 2. Direct: a GPS gap between 60 and 70 s.
/// 3. Indirect: the GPS stream ends 60-70 s after every non-GPS channel; the
///    jump is then hidden inside the largest gap.
///
/// Returns nothing when the GPS stream has no gap wider than ten sample
/// periods, so a clean stream is never touched.
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
    let gap_indices: Vec<usize> = (0..gps_timecodes.len() - 1)
        .filter(|&i| (gps_timecodes[i + 1] - gps_timecodes[i]) as f64 > gap_threshold)
        .collect();
    if gap_indices.is_empty() {
        return Vec::new();
    }

    // Method 1: GNFI reference clock.
    if !gnfi_timecodes.is_empty() {
        let corrections =
            detect_gps_timing_offset_from_gnfi(gps_timecodes, gnfi_timecodes, expected_dt_ms);
        if !corrections.is_empty() {
            return corrections;
        }
    }

    // Method 2: a gap of the bug's own size.
    let mut corrections = Vec::new();
    for &gap_idx in &gap_indices {
        let gap_size = gps_timecodes[gap_idx + 1] - gps_timecodes[gap_idx];
        if (OVERFLOW_GAP_MIN..=OVERFLOW_GAP_MAX).contains(&gap_size) {
            corrections.push(GapCorrection {
                gap_time: gps_timecodes[gap_idx],
                correction: gap_size - expected_dt_ms as i64,
            });
        }
    }
    if !corrections.is_empty() {
        return corrections;
    }

    // Method 3: the GPS stream outlives the other channels by the bug's size.
    if let Some(max_non_gps_end) = non_gps_max_end_time {
        let gps_end = *gps_timecodes.last().unwrap_or(&0);
        let end_offset = gps_end - max_non_gps_end;
        if (OVERFLOW_GAP_MIN..=OVERFLOW_GAP_MAX).contains(&end_offset) {
            if let Some(largest_gap_idx) = gap_indices
                .iter()
                .copied()
                .max_by_key(|&i| gps_timecodes[i + 1] - gps_timecodes[i])
            {
                corrections.push(GapCorrection {
                    gap_time: gps_timecodes[largest_gap_idx],
                    correction: OVERFLOW_BUG_MS,
                });
            }
        }
    }

    corrections
}

/// Apply gap corrections to timecodes in place.
pub fn apply_corrections(timecodes: &mut [i64], corrections: &[GapCorrection]) {
    if corrections.is_empty() {
        return;
    }
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

    fn clean(n: i64) -> Vec<i64> {
        (0..n).map(|i| i * 40).collect()
    }

    fn with_jump(n: i64, at: usize, jump: i64) -> Vec<i64> {
        let mut t = clean(n);
        for tc in &mut t[at..] {
            *tc += jump;
        }
        t
    }

    #[test]
    fn gnfi_records_decode_and_offset() {
        let mut data = Vec::new();
        for tc in [1000i32, 2000, 3000] {
            let mut rec = vec![0u8; GNFI_RECORD_SIZE];
            rec[0..4].copy_from_slice(&tc.to_le_bytes());
            data.extend(rec);
        }
        assert_eq!(decode_gnfi_timecodes(&data, 500), vec![500, 1500, 2500]);
        assert!(decode_gnfi_timecodes(&[], 0).is_empty());
        assert!(decode_gnfi_timecodes(&data[..40], 0).is_empty());
    }

    #[test]
    fn no_corrections_without_gaps() {
        assert!(detect_gap_corrections(&clean(100), &clean(100), None, 40.0).is_empty());
        assert!(detect_gap_corrections(&clean(100), &[], Some(3960), 40.0).is_empty());
    }

    #[test]
    fn direct_detection_of_the_jump() {
        let gps = with_jump(50, 25, 65533);
        let c = detect_gap_corrections(&gps, &[], None, 40.0);
        assert_eq!(
            c,
            vec![GapCorrection {
                gap_time: gps[24],
                correction: 65533
            }]
        );
    }

    #[test]
    fn direct_detection_ignores_legitimate_gaps() {
        // A 30 s pit stop is not the firmware bug.
        let gps = with_jump(50, 25, 30_000);
        assert!(detect_gap_corrections(&gps, &[], None, 40.0).is_empty());
    }

    #[test]
    fn gnfi_detection_of_a_visible_jump() {
        let gps = with_jump(2500, 1001, 65533 - 40);
        let gnfi = clean(2500);
        let c = detect_gps_timing_offset_from_gnfi(&gps, &gnfi, 40.0);
        assert_eq!(c.len(), 1);
        assert_eq!(c[0].gap_time, gps[1000]);
        assert_eq!(c[0].correction, 65533 - 40);
    }

    #[test]
    fn gnfi_detection_of_a_jump_hidden_in_signal_loss() {
        // The GPS stream loses 10 s of samples around the jump, so the gap is
        // 10 s + 65533 ms: not the bug's own size, but GNFI still ends 65533 ms
        // before GPS, and the full overflow is removed.
        let mut gps = clean(2500);
        gps.drain(1000..1250);
        for tc in &mut gps[1000..] {
            *tc += 65533;
        }
        let gnfi = clean(2500);
        let c = detect_gap_corrections(&gps, &gnfi, None, 40.0);
        assert_eq!(c.len(), 1);
        assert_eq!(c[0].correction, 65533);
    }

    #[test]
    fn gnfi_detection_is_silent_when_clean_or_short() {
        assert!(detect_gps_timing_offset_from_gnfi(&clean(2500), &clean(2500), 40.0).is_empty());
        assert!(detect_gps_timing_offset_from_gnfi(&clean(250), &[], 40.0).is_empty());
        assert!(detect_gps_timing_offset_from_gnfi(&clean(250), &[0], 40.0).is_empty());
    }

    #[test]
    fn indirect_detection_from_non_gps_end() {
        // A 5 s gap hides the jump; GPS ends 65533 ms after the other channels.
        let gps = with_jump(200, 100, 5000 + 65533);
        let non_gps_end = Some(199 * 40 + 5000);
        let c = detect_gap_corrections(&gps, &[], non_gps_end, 40.0);
        assert_eq!(
            c,
            vec![GapCorrection {
                gap_time: gps[99],
                correction: 65533
            }]
        );
        // Without the reference the same stream is left alone.
        assert!(detect_gap_corrections(&gps, &[], None, 40.0).is_empty());
    }

    #[test]
    fn apply_shifts_only_samples_after_the_gap() {
        let mut tc = vec![0, 40, 80, 120, 160];
        apply_corrections(
            &mut tc,
            &[GapCorrection {
                gap_time: 80,
                correction: 65533,
            }],
        );
        assert_eq!(tc, vec![0, 40, 80, 120 - 65533, 160 - 65533]);
    }

    #[test]
    fn two_jumps_are_both_repaired() {
        let mut gps = clean(300);
        for tc in &mut gps[51..] {
            *tc += 65533 - 40;
        }
        for tc in &mut gps[151..] {
            *tc += 65533 - 40;
        }
        let c = detect_gap_corrections(&gps, &[], None, 40.0);
        assert_eq!(c.len(), 2);
        apply_corrections(&mut gps, &c);
        assert_eq!(gps, clean(300));
    }
}
