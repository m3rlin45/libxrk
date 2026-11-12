//! Static lookup tables: UNIT_MAP, DECODER_TABLE, LOGGER_MODELS.
//!
//! These are the Rust equivalents of the Python dicts in _aim_xrk_cy.pyx.

/// Unit type map: unit_type_byte -> (unit_string, decimal_points).
/// From _aim_xrk_cy.pyx:144-169.
///
/// Checked against the official AIM `MatLabXRK` DLL over the whole test
/// corpus (`get_channel_units`, 253 channel observations): apart from code
/// 30, kept as "lambda" on purpose (companion.md section 9), every entry the
/// DLL reports agrees. Code 11 was corrected to "#" on that evidence.
///
/// Entries marked "DLL" are codes no available file carries. Their strings
/// are what the DLL reports for a channel patched to that code
/// (spec/docs/companion.md section 9); their decimal points are not known
/// and are left at 0.
pub fn unit_map(unit_type: u8) -> (&'static str, u8) {
    match unit_type & 0x7F {
        // No unit: an unconfigured input (channels named NONE_0, NONE_3 on a
        // MyChron 5S, issue #81). The AIM DLL reports "" too.
        0 => ("", 0),
        1 => ("%", 2),
        2 => ("%/s", 0), // DLL
        3 => ("g", 2),
        4 => ("deg", 1),
        5 => ("deg/s", 1),
        6 => ("#", 0),     // "number"; the AIM DLL reports "#", as for code 11
        7 => ("Coord", 0), // DLL
        8 => ("m", 0),     // Mileage / Distance Lap; RaceStudio3 exports Mileage as "m"
        9 => ("Hz", 0),
        10 => ("kg/s", 0), // DLL
        11 => ("#", 0),    // "number"; the AIM DLL reports "#" (see test below)
        12 => ("mm", 0),
        13 => ("kW", 0), // DLL
        14 => ("bar", 2),
        15 => ("rpm", 0),
        16 => ("km/h", 0),
        17 => ("C", 1),
        18 => ("ms", 0),
        19 => ("Nm", 0),
        20 => ("km/h", 0),
        21 => ("mV", 1),
        22 => ("l", 1),
        23 => ("l*Lap", 0), // DLL
        24 => ("l/s", 0),
        25 => ("Date", 0), // DLL
        26 => ("time?", 0),
        27 => ("A", 0),
        28 => ("%", 2), // Luminosity on the official AIM sample; RaceStudio3 displays "%"
        29 => ("rpm/s", 0), // DLL
        30 => ("lambda", 2),
        31 => ("gear", 0),
        // DLL. Volts as stored: unlike the "V" of code 21 with the calibrated
        // flag (`is_volts`), samples are not divided by 1000.
        32 => ("V", 0),
        33 => ("%", 2),
        34 => ("l/hkm", 0), // DLL
        35 => ("km/l", 0),  // DLL
        36 => ("ft/s2", 0), // DLL
        37 => ("mG", 0),    // DLL. MagnetomX/Y/Z on a MyChron 5S (issue #80)
        38 => ("krpm", 0),  // DLL
        43 => ("kg", 3),
        _ => ("", 0),
    }
}

/// Check if a unit type byte maps to a known unit in the unit map.
/// Returns false for unit types not in the Cython `_unit_map` dictionary.
pub fn is_known_unit(unit_type_byte: u8) -> bool {
    matches!(unit_type_byte & 0x7F, 0..=38 | 43)
}

/// Unit code of millivolts.
const MILLIVOLTS_UNIT: u8 = 21;

/// True for millivolts with the calibrated flag (high bit): the channel is
/// shown in "V" and its samples are divided by 1000.
///
/// This is a property of the unit byte, not of the unit string: code 32 is
/// also "V" and its samples are left as stored.
pub fn is_volts(unit_type_byte: u8) -> bool {
    unit_type_byte == 0x80 | MILLIVOLTS_UNIT
}

/// Resolve the display unit, handling the calibrated flag (high bit of unit_type_byte).
/// When the calibrated flag is set and base unit is "mV", display as "V".
pub fn resolve_unit(unit_type_byte: u8) -> &'static str {
    if is_volts(unit_type_byte) {
        "V"
    } else {
        unit_map(unit_type_byte).0
    }
}

/// Decoder type info: (struct_format_char, interpolate, byte_size).
/// From _aim_xrk_cy.pyx:111-135.
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

    /// Unit code 11 ("number") is "#": the AIM DLL reports "#" for every
    /// code-11 channel in the corpus (36 observations, including 139 =
    /// 11 | calibrated). Mirrors `UNIT_MAP` in `spec/xrk_format.py`.
    #[test]
    fn unit_code_11_is_hash_not_empty() {
        assert_eq!(unit_map(11), ("#", 0));
        assert_eq!(resolve_unit(11), "#");
        // The calibrated flag must not turn it into something else: that
        // rewrite exists only for mV -> V.
        assert_eq!(resolve_unit(139), "#");
    }

    /// Unit code 8 is "m": RaceStudio3 exports Mileage (code 8) as "m" in
    /// every corpus CSV that has it. Mirrors `UNIT_MAP` in `spec/xrk_format.py`.
    #[test]
    fn unit_code_8_is_metres() {
        assert_eq!(unit_map(8), ("m", 0));
        assert!(is_known_unit(8));
    }

    /// Unit code 0 is "no unit", not an unknown code: the AIM DLL exposes a
    /// channel carrying it with an empty unit string. Mirrors `UNIT_MAP` in
    /// `spec/xrk_format.py`.
    #[test]
    fn unit_code_0_is_no_unit() {
        assert_eq!(unit_map(0), ("", 0));
        assert!(is_known_unit(0));
        assert_eq!(resolve_unit(0), "");
        assert_eq!(resolve_unit(0x80), "");
    }

    /// Unit code 6 ("number") is "#", like code 11: the AIM DLL reports "#"
    /// for a channel patched to it. Mirrors `UNIT_MAP` in `spec/xrk_format.py`.
    #[test]
    fn unit_code_6_is_hash() {
        assert_eq!(unit_map(6), ("#", 0));
        assert_eq!(resolve_unit(0x80 | 6), "#");
    }

    /// The codes the AIM DLL names that no available file carries. Mirrors
    /// `UNIT_MAP` in `spec/xrk_format.py`.
    #[test]
    fn unit_codes_named_by_the_dll() {
        for (code, unit) in [
            (2, "%/s"),
            (7, "Coord"),
            (10, "kg/s"),
            (13, "kW"),
            (23, "l*Lap"),
            (25, "Date"),
            (29, "rpm/s"),
            (32, "V"),
            (34, "l/hkm"),
            (35, "km/l"),
            (36, "ft/s2"),
            (37, "mG"),
            (38, "krpm"),
        ] {
            assert_eq!(unit_map(code), (unit, 0), "code {code}");
            assert!(is_known_unit(code), "code {code}");
        }
    }

    /// Only millivolts with the calibrated flag is divided by 1000. Code 32
    /// is "V" as stored, with or without the flag: the AIM DLL returns its
    /// samples unchanged.
    #[test]
    fn volts_conversion_is_keyed_on_the_unit_byte() {
        assert!(is_volts(0x80 | 21));
        assert!(!is_volts(21));
        assert_eq!(resolve_unit(32), "V");
        assert!(!is_volts(32));
        assert_eq!(resolve_unit(0x80 | 32), "V");
        assert!(!is_volts(0x80 | 32));
    }

    /// Unit code 28 is "%": Luminosity on the official AIM sample carries it
    /// and RaceStudio3 displays "%". Mirrors `UNIT_MAP` in `spec/xrk_format.py`.
    #[test]
    fn unit_code_28_is_percent() {
        assert_eq!(unit_map(28), ("%", 2));
        assert!(is_known_unit(28));
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
        assert_eq!(unit_map(39), ("", 0));
        assert_eq!(unit_map(127), ("", 0));
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
        assert!(is_known_unit(0)); // no unit
        assert!(is_known_unit(38)); // krpm, the last code the AIM DLL names
        assert!(!is_known_unit(39));
        assert!(!is_known_unit(42));
        assert!(!is_known_unit(99));
    }

    #[test]
    fn test_is_known_unit_masks_high_bit() {
        assert!(is_known_unit(0x80 | 15));
        assert!(!is_known_unit(0x80 | 39));
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
