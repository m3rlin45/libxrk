//! Channel type decoders: decode raw sample bytes to typed values.
//!
//! Maps decoder_type to decode function. Equivalent to `_decoders` in aim_xrk.pyx.

/// Decoded sample value — preserves the native type from each decoder.
#[derive(Debug, Clone, Copy)]
pub enum SampleValue {
    UInt8(u8),
    UInt16(u16),
    Int16(i16),
    Int32(i32),
    UInt32(u32),
    Float32(f32),
    Float64(f64),
}

impl SampleValue {
    pub fn as_f64(self) -> f64 {
        match self {
            SampleValue::UInt8(v) => v as f64,
            SampleValue::UInt16(v) => v as f64,
            SampleValue::Int16(v) => v as f64,
            SampleValue::Int32(v) => v as f64,
            SampleValue::UInt32(v) => v as f64,
            SampleValue::Float32(v) => v as f64,
            SampleValue::Float64(v) => v,
        }
    }

    pub fn as_f32(self) -> f32 {
        match self {
            SampleValue::UInt8(v) => v as f32,
            SampleValue::UInt16(v) => v as f32,
            SampleValue::Int16(v) => v as f32,
            SampleValue::Int32(v) => v as f32,
            SampleValue::UInt32(v) => v as f32,
            SampleValue::Float32(v) => v,
            SampleValue::Float64(v) => v as f32,
        }
    }
}

/// Gear lookup table: maps uint16 decoded value to gear number.
/// Matches aim_xrk.pyx:102-109.
fn gear_lookup(val: u16) -> u16 {
    match val {
        0x4E => 0, // 'N'
        0x31 => 1, // '1'
        0x32 => 2, // '2'
        0x33 => 3, // '3'
        0x34 => 4, // '4'
        0x35 => 5, // '5'
        0x36 => 6, // '6'
        other => other,
    }
}

/// Decode a single sample from raw bytes given the decoder type.
/// Returns (value, is_float16_or_gear) for special post-processing.
pub fn decode_sample(decoder_type: u8, data: &[u8]) -> Option<SampleValue> {
    match decoder_type {
        // int32 types
        0 | 3 | 8 | 12 | 22 | 24 | 26 | 27 | 31 | 32 | 33 | 37 | 38 | 39 => {
            if data.len() < 4 {
                return None;
            }
            let v = i32::from_le_bytes([data[0], data[1], data[2], data[3]]);
            Some(SampleValue::Int32(v))
        }
        // uint16 with interpolation (type 1)
        1 => {
            if data.len() < 2 {
                return None;
            }
            let v = u16::from_le_bytes([data[0], data[1]]);
            Some(SampleValue::UInt16(v))
        }
        // int16 types (type 4, 11)
        4 | 11 => {
            if data.len() < 2 {
                return None;
            }
            let v = i16::from_le_bytes([data[0], data[1]]);
            Some(SampleValue::Int16(v))
        }
        // float32 (type 6)
        6 => {
            if data.len() < 4 {
                return None;
            }
            let v = f32::from_le_bytes([data[0], data[1], data[2], data[3]]);
            Some(SampleValue::Float32(v))
        }
        // uint8 (type 13)
        13 => {
            if data.is_empty() {
                return None;
            }
            Some(SampleValue::UInt8(data[0]))
        }
        // uint16 gear lookup (type 15)
        15 => {
            if data.len() < 2 {
                return None;
            }
            let v = u16::from_le_bytes([data[0], data[1]]);
            Some(SampleValue::UInt16(gear_lookup(v)))
        }
        // float16 encoded as uint16 (type 20)
        20 => {
            if data.len() < 2 {
                return None;
            }
            let bits = u16::from_le_bytes([data[0], data[1]]);
            let f = half_to_f32(bits);
            Some(SampleValue::Float32(f))
        }
        _ => None,
    }
}

/// Decode a manual decoder (Calculated_Gear, PreCalcGear).
/// These have 8-byte samples decoded as uint64, then bit-extracted.
pub fn decode_manual_gear(data: &[u8]) -> Option<SampleValue> {
    if data.len() < 8 {
        return None;
    }
    let v = u64::from_le_bytes([
        data[0], data[1], data[2], data[3], data[4], data[5], data[6], data[7],
    ]);
    let gear = if v & 0x80000 != 0 {
        0u32
    } else {
        ((v >> 16) & 7) as u32
    };
    Some(SampleValue::UInt32(gear))
}

/// Check if a channel name is a manual decoder type.
pub fn is_manual_decoder(name: &str) -> bool {
    name == "Calculated_Gear" || name == "PreCalcGear"
}

/// Convert IEEE 754 half-precision (float16) to float32.
fn half_to_f32(bits: u16) -> f32 {
    // Use the standard conversion: sign, exponent, mantissa
    let sign = ((bits >> 15) & 1) as u32;
    let exp = ((bits >> 10) & 0x1F) as u32;
    let mantissa = (bits & 0x3FF) as u32;

    if exp == 0 {
        if mantissa == 0 {
            // Zero
            f32::from_bits(sign << 31)
        } else {
            // Subnormal: normalize
            let mut m = mantissa;
            let mut e: i32 = -14;
            while m & 0x400 == 0 {
                m <<= 1;
                e -= 1;
            }
            m &= 0x3FF;
            let f32_exp = ((e + 127) as u32) & 0xFF;
            f32::from_bits((sign << 31) | (f32_exp << 23) | (m << 13))
        }
    } else if exp == 31 {
        // Inf or NaN
        let f32_mantissa = mantissa << 13;
        f32::from_bits((sign << 31) | (0xFF << 23) | f32_mantissa)
    } else {
        // Normal
        let f32_exp = (exp as i32 - 15 + 127) as u32;
        f32::from_bits((sign << 31) | (f32_exp << 23) | (mantissa << 13))
    }
}

/// Get the byte size of a sample for the given decoder type.
pub fn sample_byte_size(decoder_type: u8) -> Option<u8> {
    crate::tables::decoder_info(decoder_type).map(|d| d.byte_size)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_half_to_f32() {
        // 0x0000 = 0.0
        assert_eq!(half_to_f32(0x0000), 0.0);
        // 0x3C00 = 1.0
        assert!((half_to_f32(0x3C00) - 1.0).abs() < 1e-6);
        // 0xBC00 = -1.0
        assert!((half_to_f32(0xBC00) - (-1.0)).abs() < 1e-6);
        // 0x7C00 = inf
        assert!(half_to_f32(0x7C00).is_infinite());
    }

    #[test]
    fn test_gear_lookup() {
        assert_eq!(gear_lookup(0x4E), 0); // 'N'
        assert_eq!(gear_lookup(0x31), 1); // '1'
        assert_eq!(gear_lookup(0x36), 6); // '6'
    }
}
