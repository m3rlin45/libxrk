//! Static lookup tables: UNIT_MAP, DECODER_TABLE, LOGGER_MODELS.
//!
//! These are the Rust equivalents of the Python dicts in aim_xrk.pyx.

/// Unit type map: unit_type_byte -> (unit_string, decimal_points).
/// From aim_xrk.pyx:144-169.
///
/// Checked against the official AIM `MatLabXRK` DLL over the whole test
/// corpus (`get_channel_units`, 253 channel observations). Every entry the
/// DLL also reports agrees, except unit code 11 — see the test at the bottom
/// of this file.
pub fn unit_map(unit_type: u8) -> (&'static str, u8) {
    match unit_type & 0x7F {
        1 => ("%", 2),
        3 => ("g", 2),
        4 => ("deg", 1),
        5 => ("deg/s", 1),
        6 => ("", 0),   // "number" — not exposed by the AIM DLL on any
        // corpus file, so no ground truth; left as-is.
        9 => ("Hz", 0),
        11 => ("#", 0), // "number" — the AIM DLL reports "#", see below.
        12 => ("mm", 0),
        14 => ("bar", 2),
        15 => ("rpm", 0),
        16 => ("km/h", 0),
        17 => ("C", 1),
        18 => ("ms", 0),
        19 => ("Nm", 0),
        20 => ("km/h", 0),
        21 => ("mV", 1),
        22 => ("l", 1),
        24 => ("l/s", 0),
        26 => ("time?", 0),
        27 => ("A", 0),
        30 => ("lambda", 2),
        31 => ("gear", 0),
        33 => ("%", 2),
        43 => ("kg", 3),
        _ => ("", 0),
    }
}

/// Check if a unit type byte maps to a known unit in the unit map.
/// Returns false for unit types not in the Cython `_unit_map` dictionary.
pub fn is_known_unit(unit_type_byte: u8) -> bool {
    matches!(
        unit_type_byte & 0x7F,
        1 | 3
            | 4
            | 5
            | 6
            | 9
            | 11
            | 12
            | 14
            | 15
            | 16
            | 17
            | 18
            | 19
            | 20
            | 21
            | 22
            | 24
            | 26
            | 27
            | 30
            | 31
            | 33
            | 43
    )
}

/// Resolve the display unit, handling the calibrated flag (high bit of unit_type_byte).
/// When the calibrated flag is set and base unit is "mV", display as "V".
pub fn resolve_unit(unit_type_byte: u8) -> &'static str {
    let (base_unit, _) = unit_map(unit_type_byte);
    if unit_type_byte & 0x80 != 0 && base_unit == "mV" {
        "V"
    } else {
        base_unit
    }
}

/// Decoder type info: (struct_format_char, interpolate, byte_size).
/// From aim_xrk.pyx:111-135.
pub struct DecoderInfo {
    pub format: char,
    pub interpolate: bool,
    pub byte_size: u8,
}

pub fn decoder_info(decoder_type: u8) -> Option<DecoderInfo> {
    match decoder_type {
        0 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        1 => Some(DecoderInfo {
            format: 'H',
            interpolate: true,
            byte_size: 2,
        }),
        3 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        4 => Some(DecoderInfo {
            format: 'h',
            interpolate: false,
            byte_size: 2,
        }),
        6 => Some(DecoderInfo {
            format: 'f',
            interpolate: true,
            byte_size: 4,
        }),
        8 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        11 => Some(DecoderInfo {
            format: 'h',
            interpolate: false,
            byte_size: 2,
        }),
        12 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        13 => Some(DecoderInfo {
            format: 'B',
            interpolate: false,
            byte_size: 1,
        }),
        15 => Some(DecoderInfo {
            format: 'H',
            interpolate: false,
            byte_size: 2,
        }),
        20 => Some(DecoderInfo {
            format: 'H',
            interpolate: true,
            byte_size: 2,
        }),
        22 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        24 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        26 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        27 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        31 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        32 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        33 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        37 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        38 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        39 => Some(DecoderInfo {
            format: 'i',
            interpolate: false,
            byte_size: 4,
        }),
        _ => None,
    }
}

/// Resolve the channel function string from CHS fields.
///
/// Uses `(maybe_display_format, unit_type_byte)` as the primary lookup key,
/// with `config_flags` as a tiebreaker for ambiguous cases.
/// Returns empty string for unknown combinations.
///
/// Derived from RS3 channel properties. See `channel_function_observations.md`.
pub fn resolve_function(display_format: u8, unit_type_byte: u8, config_flags: u16) -> &'static str {
    // Override: (0, 0x11, config_flags=1) -> "Device Temperature"
    if display_format == 0 && unit_type_byte == 0x11 && config_flags == 1 {
        return "Device Temperature";
    }

    match (display_format, unit_type_byte) {
        // display_format=0: generic CAN/ECU channels, function by unit_type_byte
        (0, 0x01) => "Percent",
        (0, 0x03) => "Acceleration",
        (0, 0x04) => "Angle",
        (0, 0x05) => "Angular Rate",
        (0, 0x0b) => "Number",
        (0, 0x0c) => "Distance",
        (0, 0x0e) => "Pressure",
        (0, 0x0f) => "Engine RPM",
        (0, 0x10) => "Rear Wheel Speed",
        (0, 0x11) => "Temperature",
        (0, 0x12) => "Time",
        (0, 0x15) => "Voltage",
        (0, 0x91) => "Exhaust Temperature",
        (0, 0x9a) => "Lap Time",
        // display_format>0: AIM-assigned function categories
        (1, 0x95) => "Battery Voltage",
        (2, 0x9a) => "Total Odometer",
        (3, 0x1a) => "Reset Odometer",
        (5, 0x1a) => "Best Lap Time",
        (5, 0x9a) => "Rolling Lap Time",
        (6, 0x06) | (6, 0x1f) => "Gear",
        (9, 0x1e) => "Lambda",
        (11, 0x91) => "Oil Temperature",
        (13, 0x84) => "Steering Angle",
        (14, 0x81) => "Percentage Throttle Load",
        (16, 0x91) | (144, 0x91) => "Water Temperature",
        (17, 0x03) | (145, 0x03) => "Inline Acceleration",
        (17, 0x05) => "Roll Rate",
        (17, 0x83) | (145, 0x83) => "Lateral Acceleration",
        (17, 0x85) => "Pitch Rate",
        (18, 0x03) => "Vertical Acceleration",
        (18, 0x05) | (146, 0x05) => "Yaw Rate",
        (21, 0x12) => "Master Clock",
        (26, 0x21) => "Device Brightness",
        (27, 0x92) => "Best Run Diff",
        (28, 0x12) => "Prev Lap Diff",
        (28, 0x92) => "Ref Lap Diff",
        (35, 0x92) => "Best Today Diff",
        (128, 0x10) => "Vehicle Speed",
        (128, 0x91) => "Intake Air Temperature",
        (128, 0x9a) => "Lap Time",
        (129, 0x9a) => "GPS Time",
        (130, 0x0e) => "Brake Circuit Pressure",
        (169, 0x8c) => "LF Shock Position",
        _ => "",
    }
}

/// Logger model ID to name mapping.
pub fn logger_model_name(model_id: u16) -> Option<&'static str> {
    match model_id {
        649 => Some("MXP 1.3"),
        793 => Some("MXm"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Unit code 11 is "#", not the empty string.
    ///
    /// Both maps carried `11: ('', 0)` with the comment `# number`. The
    /// comment was right about the meaning and wrong about the string: the
    /// official AIM DLL reports "#" for these channels.
    ///
    /// Measured with `tests/reference_dll` over the whole corpus — every
    /// channel of every `.xrk`, matched by name between `get_channel_units`
    /// and the CHS unit byte:
    ///
    /// ```text
    ///  code   libxrk    AIM DLL     observations
    ///    11      ""         "#"               28
    ///   139      ""         "#"                8      (11 | calibrated flag)
    /// ```
    ///
    /// Channels seen: StartRec, BrakeSw, ClutchSw, CH, TPMS_ALM_{LF,LR,RF,RR},
    /// Brake, Clutch, TIRE_ALM, Oil_Temp, Water_Temp, Lateral Grip — counts
    /// and flags, which is what "number" should be. No corpus channel with
    /// code 11 was reported as anything but "#".
    ///
    /// Codes the DLL never exposes (6, 26, 28, 146, 154, 156) are left
    /// untouched: no ground truth, so no change.
    #[test]
    fn unit_code_11_is_hash_not_empty() {
        assert_eq!(unit_map(11), ("#", 0));
        assert_eq!(resolve_unit(11), "#");
        // The calibrated flag must not turn it into something else: that
        // rewrite exists only for mV -> V.
        assert_eq!(resolve_unit(139), "#");
    }

    #[test]
    fn test_unit_map_known_types() {
        assert_eq!(unit_map(15), ("rpm", 0));
        assert_eq!(unit_map(17), ("C", 1));
        assert_eq!(unit_map(1), ("%", 2));
        assert_eq!(unit_map(14), ("bar", 2));
        assert_eq!(unit_map(21), ("mV", 1));
        assert_eq!(unit_map(43), ("kg", 3));
    }

    #[test]
    fn test_unit_map_unknown_returns_empty() {
        assert_eq!(unit_map(99), ("", 0));
        assert_eq!(unit_map(0), ("", 0));
        assert_eq!(unit_map(2), ("", 0));
    }

    #[test]
    fn test_unit_map_masks_high_bit() {
        // unit_type_byte with high bit set should still look up correctly
        assert_eq!(unit_map(0x80 | 15), ("rpm", 0));
        assert_eq!(unit_map(0x80 | 21), ("mV", 1));
    }

    #[test]
    fn test_is_known_unit() {
        assert!(is_known_unit(15)); // rpm
        assert!(is_known_unit(1)); // %
        assert!(is_known_unit(43)); // kg
        assert!(!is_known_unit(0));
        assert!(!is_known_unit(2));
        assert!(!is_known_unit(99));
    }

    #[test]
    fn test_is_known_unit_masks_high_bit() {
        assert!(is_known_unit(0x80 | 15));
        assert!(!is_known_unit(0x80 | 2));
    }

    #[test]
    fn test_resolve_unit_calibrated_mv_to_v() {
        // unit_type_byte=21 is mV; with high bit set (calibrated) → "V"
        assert_eq!(resolve_unit(0x80 | 21), "V");
    }

    #[test]
    fn test_resolve_unit_uncalibrated_mv_stays_mv() {
        assert_eq!(resolve_unit(21), "mV");
    }

    #[test]
    fn test_resolve_unit_calibrated_non_mv() {
        // High bit set but not mV — should return base unit
        assert_eq!(resolve_unit(0x80 | 15), "rpm");
    }

    #[test]
    fn test_decoder_info_known_types() {
        let d = decoder_info(0).unwrap();
        assert_eq!(d.format, 'i');
        assert!(!d.interpolate);
        assert_eq!(d.byte_size, 4);

        let d = decoder_info(1).unwrap();
        assert_eq!(d.format, 'H');
        assert!(d.interpolate);
        assert_eq!(d.byte_size, 2);

        let d = decoder_info(6).unwrap();
        assert_eq!(d.format, 'f');
        assert!(d.interpolate);
        assert_eq!(d.byte_size, 4);

        let d = decoder_info(13).unwrap();
        assert_eq!(d.format, 'B');
        assert!(!d.interpolate);
        assert_eq!(d.byte_size, 1);
    }

    #[test]
    fn test_decoder_info_unknown_returns_none() {
        assert!(decoder_info(2).is_none());
        assert!(decoder_info(5).is_none());
        assert!(decoder_info(255).is_none());
    }

    #[test]
    fn test_resolve_function_known() {
        assert_eq!(resolve_function(0, 0x0f, 0), "Engine RPM");
        assert_eq!(resolve_function(0, 0x11, 0), "Temperature");
        assert_eq!(resolve_function(6, 0x06, 0), "Gear");
        assert_eq!(resolve_function(128, 0x10, 0), "Vehicle Speed");
    }

    #[test]
    fn test_resolve_function_device_temperature_override() {
        assert_eq!(resolve_function(0, 0x11, 1), "Device Temperature");
        // Same display_format+unit_type but different config_flags → generic
        assert_eq!(resolve_function(0, 0x11, 0), "Temperature");
    }

    #[test]
    fn test_resolve_function_unknown() {
        assert_eq!(resolve_function(255, 255, 0), "");
    }

    #[test]
    fn test_logger_model_name() {
        assert_eq!(logger_model_name(649), Some("MXP 1.3"));
        assert_eq!(logger_model_name(793), Some("MXm"));
        assert_eq!(logger_model_name(0), None);
    }
}
