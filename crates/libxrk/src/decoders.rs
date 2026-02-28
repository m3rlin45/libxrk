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
    fn test_half_to_f32_nan() {
        // 0x7C01 = NaN (mantissa != 0 with max exponent)
        assert!(half_to_f32(0x7C01).is_nan());
    }

    #[test]
    fn test_half_to_f32_negative_zero() {
        // 0x8000 = -0.0
        let v = half_to_f32(0x8000);
        assert_eq!(v, 0.0);
        assert!(v.is_sign_negative());
    }

    #[test]
    fn test_half_to_f32_subnormal() {
        // Smallest subnormal: 0x0001
        let v = half_to_f32(0x0001);
        assert!(v > 0.0);
        assert!(v < 1e-6);
    }

    #[test]
    fn test_gear_lookup() {
        assert_eq!(gear_lookup(0x4E), 0); // 'N'
        assert_eq!(gear_lookup(0x31), 1); // '1'
        assert_eq!(gear_lookup(0x36), 6); // '6'
    }

    #[test]
    fn test_gear_lookup_all_gears() {
        assert_eq!(gear_lookup(0x32), 2);
        assert_eq!(gear_lookup(0x33), 3);
        assert_eq!(gear_lookup(0x34), 4);
        assert_eq!(gear_lookup(0x35), 5);
    }

    #[test]
    fn test_gear_lookup_passthrough() {
        // Unknown values pass through unchanged
        assert_eq!(gear_lookup(0xFF), 0xFF);
        assert_eq!(gear_lookup(0x00), 0x00);
    }

    #[test]
    fn test_decode_sample_int32() {
        let data = 42i32.to_le_bytes();
        match decode_sample(0, &data).unwrap() {
            SampleValue::Int32(v) => assert_eq!(v, 42),
            _ => panic!("expected Int32"),
        }
    }

    #[test]
    fn test_decode_sample_uint16() {
        let data = 1234u16.to_le_bytes();
        match decode_sample(1, &data).unwrap() {
            SampleValue::UInt16(v) => assert_eq!(v, 1234),
            _ => panic!("expected UInt16"),
        }
    }

    #[test]
    fn test_decode_sample_int16() {
        let data = (-500i16).to_le_bytes();
        match decode_sample(4, &data).unwrap() {
            SampleValue::Int16(v) => assert_eq!(v, -500),
            _ => panic!("expected Int16"),
        }
    }

    #[test]
    fn test_decode_sample_float32() {
        let data = 1.5f32.to_le_bytes();
        match decode_sample(6, &data).unwrap() {
            SampleValue::Float32(v) => assert!((v - 1.5).abs() < 0.01),
            _ => panic!("expected Float32"),
        }
    }

    #[test]
    fn test_decode_sample_uint8() {
        match decode_sample(13, &[255]).unwrap() {
            SampleValue::UInt8(v) => assert_eq!(v, 255),
            _ => panic!("expected UInt8"),
        }
    }

    #[test]
    fn test_decode_sample_gear_lookup() {
        let data = 0x4Eu16.to_le_bytes(); // 'N'
        match decode_sample(15, &data).unwrap() {
            SampleValue::UInt16(v) => assert_eq!(v, 0), // Neutral
            _ => panic!("expected UInt16"),
        }
    }

    #[test]
    fn test_decode_sample_float16() {
        let data = 0x3C00u16.to_le_bytes(); // 1.0 in half precision
        match decode_sample(20, &data).unwrap() {
            SampleValue::Float32(v) => assert!((v - 1.0).abs() < 1e-6),
            _ => panic!("expected Float32"),
        }
    }

    #[test]
    fn test_decode_sample_unknown_type() {
        assert!(decode_sample(255, &[0, 0, 0, 0]).is_none());
    }

    #[test]
    fn test_decode_sample_too_short() {
        assert!(decode_sample(0, &[1, 2]).is_none()); // needs 4 bytes
        assert!(decode_sample(1, &[1]).is_none()); // needs 2 bytes
        assert!(decode_sample(13, &[]).is_none()); // needs 1 byte
    }

    #[test]
    fn test_decode_manual_gear() {
        // Test gear 3: bit 16-18 = 3, bit 19 = 0
        let val: u64 = 3 << 16;
        let data = val.to_le_bytes();
        match decode_manual_gear(&data).unwrap() {
            SampleValue::UInt32(v) => assert_eq!(v, 3),
            _ => panic!("expected UInt32"),
        }
    }

    #[test]
    fn test_decode_manual_gear_neutral() {
        // Neutral: bit 19 = 1
        let val: u64 = 1 << 19;
        let data = val.to_le_bytes();
        match decode_manual_gear(&data).unwrap() {
            SampleValue::UInt32(v) => assert_eq!(v, 0),
            _ => panic!("expected UInt32"),
        }
    }

    #[test]
    fn test_decode_manual_gear_too_short() {
        assert!(decode_manual_gear(&[0; 7]).is_none());
    }

    #[test]
    fn test_is_manual_decoder() {
        assert!(is_manual_decoder("Calculated_Gear"));
        assert!(is_manual_decoder("PreCalcGear"));
        assert!(!is_manual_decoder("Engine RPM"));
        assert!(!is_manual_decoder(""));
    }

    #[test]
    fn test_sample_byte_size() {
        assert_eq!(sample_byte_size(0), Some(4)); // i32
        assert_eq!(sample_byte_size(1), Some(2)); // u16
        assert_eq!(sample_byte_size(6), Some(4)); // f32
        assert_eq!(sample_byte_size(13), Some(1)); // u8
        assert_eq!(sample_byte_size(255), None); // unknown
    }

    #[test]
    fn test_sample_value_as_f64() {
        assert_eq!(SampleValue::Int32(42).as_f64(), 42.0);
        assert_eq!(SampleValue::UInt16(100).as_f64(), 100.0);
        assert_eq!(SampleValue::Float32(1.5).as_f64() as f32, 1.5);
        assert_eq!(SampleValue::Float64(2.5).as_f64(), 2.5);
    }

    #[test]
    fn test_sample_value_as_f32() {
        assert_eq!(SampleValue::Int32(42).as_f32(), 42.0);
        assert_eq!(SampleValue::Float32(1.5).as_f32(), 1.5);
    }

    #[test]
    fn test_all_int32_decoder_types() {
        let data = 99i32.to_le_bytes();
        for dt in [0, 3, 8, 12, 22, 24, 26, 27, 31, 32, 33, 37, 38, 39] {
            match decode_sample(dt, &data) {
                Some(SampleValue::Int32(v)) => assert_eq!(v, 99, "decoder_type={}", dt),
                other => panic!("decoder_type={}: expected Int32, got {:?}", dt, other),
            }
        }
    }
}
