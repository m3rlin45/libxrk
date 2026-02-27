//! Static lookup tables: UNIT_MAP, DECODER_TABLE, LOGGER_MODELS.
//!
//! These are the Rust equivalents of the Python dicts in aim_xrk.pyx.

/// Unit type map: unit_type_byte -> (unit_string, decimal_points).
/// From aim_xrk.pyx:144-169.
pub fn unit_map(unit_type: u8) -> (&'static str, u8) {
    match unit_type & 0x7F {
        1 => ("%", 2),
        3 => ("g", 2),
        4 => ("deg", 1),
        5 => ("deg/s", 1),
        6 => ("", 0),
        9 => ("Hz", 0),
        11 => ("", 0),
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
