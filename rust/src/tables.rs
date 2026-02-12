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
        0 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        1 => Some(DecoderInfo { format: 'H', interpolate: true, byte_size: 2 }),
        3 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        4 => Some(DecoderInfo { format: 'h', interpolate: false, byte_size: 2 }),
        6 => Some(DecoderInfo { format: 'f', interpolate: true, byte_size: 4 }),
        8 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        11 => Some(DecoderInfo { format: 'h', interpolate: false, byte_size: 2 }),
        12 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        13 => Some(DecoderInfo { format: 'B', interpolate: false, byte_size: 1 }),
        15 => Some(DecoderInfo { format: 'H', interpolate: false, byte_size: 2 }),
        20 => Some(DecoderInfo { format: 'H', interpolate: true, byte_size: 2 }),
        22 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        24 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        26 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        27 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        31 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        32 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        33 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        37 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        38 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        39 => Some(DecoderInfo { format: 'i', interpolate: false, byte_size: 4 }),
        _ => None,
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
