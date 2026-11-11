//! Arrow RecordBatch building for XRK data.
//!
//! Behind the `arrow` feature flag. Converts parsed channel data into
//! Arrow RecordBatch objects for zero-copy interop.

use std::collections::HashMap;
use std::sync::Arc;

use arrow::array::{
    Array, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, RecordBatch,
    StringArray, UInt16Array, UInt32Array, UInt8Array,
};
use arrow::datatypes::{DataType, Field, Schema};

use crate::gps::{GpsChannelDef, GpsDecodeResult, GPS_CHANNEL_DEFS};
use crate::parser::{ChannelData, ChannelInfo, ChannelValues, ProcessedLap};

/// Format an f32 exactly as Python's `str(float(v))` does.
///
/// This is the float format of the Arrow field metadata values
/// (`cal_value_1`, `display_range_min`, ...). The Cython backend writes them
/// with `str()`, so every binding built on this crate has to produce the same
/// text. Python widens the f32 to f64 and prints the shortest digits that
/// round-trip, breaking ties to even. `ryu` picks the same digits (Rust's own
/// float formatting does not: it breaks ties upwards, and widened f32 values
/// hit ties often), so only the layout needs adjusting:
///
/// - positional notation for `1e-4 <= |v| < 1e16`, always with a fractional
///   part (`"1.0"`); `ryu` stays positional down to `1e-5`
/// - otherwise scientific notation with a signed exponent of at least two
///   digits (`"1e-05"`, `"1.0000000150474662e+30"`); `ryu` writes `"1e-5"`
///   and `"1.0000000150474662e30"`
/// - `"nan"`, `"inf"` and `"-inf"` for the non-finite values
pub fn format_float(v: f32) -> String {
    let v = v as f64;
    if v.is_nan() {
        return "nan".to_string();
    }
    if v.is_infinite() {
        return if v > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    let mut buf = ryu::Buffer::new();
    let text = buf.format_finite(v);
    let (sign, body) = match text.strip_prefix('-') {
        Some(body) => ("-", body),
        None => ("", text),
    };
    if let Some((mantissa, exponent)) = body.split_once('e') {
        let exponent: i32 = exponent.parse().expect("ryu exponent is an integer");
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        format!(
            "{}{}e{}{:02}",
            sign,
            mantissa,
            exponent_sign,
            exponent.abs()
        )
    } else if let Some(digits) = body.strip_prefix("0.0000") {
        // [1e-5, 1e-4): the first significant digit is the fifth decimal.
        let (first, rest) = digits.split_at(1);
        if rest.is_empty() {
            format!("{}{}e-05", sign, first)
        } else {
            format!("{}{}.{}e-05", sign, first, rest)
        }
    } else {
        text.to_string()
    }
}

/// Channel metadata keys used in Arrow field metadata.
pub struct ChannelMetadata {
    pub units: String,
    pub dec_pts: u8,
    pub interpolate: bool,
    pub function: String,
    pub source_type: u8,
    pub source_channel_id: u16,
    pub device_tag: String,
    pub cal_value_1: f32,
    pub cal_value_2: f32,
    pub display_range_min: f32,
    pub display_range_max: f32,
}

impl ChannelMetadata {
    /// Build metadata from a ChannelInfo.
    pub fn from_channel_info(ch_info: &ChannelInfo) -> Self {
        let chs = &ch_info.chs;
        let units = if chs.data_size == 1 {
            "".to_string()
        } else {
            chs.units().to_string()
        };
        ChannelMetadata {
            units,
            dec_pts: chs.dec_pts(),
            interpolate: chs.interpolate(),
            function: chs.function().to_string(),
            source_type: chs.source_type,
            source_channel_id: chs.source_channel_id,
            device_tag: chs.device_tag(),
            cal_value_1: chs.cal_value_1,
            cal_value_2: chs.cal_value_2,
            display_range_min: chs.display_range_min,
            display_range_max: chs.display_range_max,
        }
    }

    /// Convert to a HashMap suitable for Arrow field metadata.
    pub fn to_hashmap(&self, format_f32: impl Fn(f32) -> String) -> HashMap<String, String> {
        let mut m = HashMap::new();
        m.insert("units".to_string(), self.units.clone());
        m.insert("dec_pts".to_string(), self.dec_pts.to_string());
        m.insert(
            "interpolate".to_string(),
            if self.interpolate { "True" } else { "False" }.to_string(),
        );
        m.insert("function".to_string(), self.function.clone());
        m.insert("source_type".to_string(), self.source_type.to_string());
        m.insert(
            "source_channel_id".to_string(),
            self.source_channel_id.to_string(),
        );
        m.insert("device_tag".to_string(), self.device_tag.clone());
        m.insert("cal_value_1".to_string(), format_f32(self.cal_value_1));
        m.insert("cal_value_2".to_string(), format_f32(self.cal_value_2));
        m.insert(
            "display_range_min".to_string(),
            format_f32(self.display_range_min),
        );
        m.insert(
            "display_range_max".to_string(),
            format_f32(self.display_range_max),
        );
        m
    }

    /// Build default GPS metadata.
    pub fn for_gps_channel(def: &GpsChannelDef) -> Self {
        ChannelMetadata {
            units: def.units.to_string(),
            dec_pts: def.dec_pts as u8,
            interpolate: def.interpolate,
            function: "".to_string(),
            source_type: 0,
            source_channel_id: 0,
            device_tag: "".to_string(),
            cal_value_1: 0.0,
            cal_value_2: 1.0,
            display_range_min: 0.0,
            display_range_max: 0.0,
        }
    }
}

/// Build an Arrow RecordBatch for a single channel.
///
/// Returns a RecordBatch with `timecodes` (Int64) and `<name>` columns,
/// with field-level metadata on the channel column.
pub fn build_channel_batch(
    name: &str,
    ch_data: ChannelData,
    metadata: HashMap<String, String>,
) -> arrow::error::Result<RecordBatch> {
    let timecodes = Int64Array::from(ch_data.timecodes);

    let (values_col, data_type): (Arc<dyn Array>, DataType) = match ch_data.values {
        ChannelValues::UInt8(v) => (Arc::new(UInt8Array::from(v)), DataType::UInt8),
        ChannelValues::UInt16(v) => (Arc::new(UInt16Array::from(v)), DataType::UInt16),
        ChannelValues::Int16(v) => (Arc::new(Int16Array::from(v)), DataType::Int16),
        ChannelValues::Int32(v) => (Arc::new(Int32Array::from(v)), DataType::Int32),
        ChannelValues::UInt32(v) => (Arc::new(UInt32Array::from(v)), DataType::UInt32),
        ChannelValues::Float32(v) => (Arc::new(Float32Array::from(v)), DataType::Float32),
        ChannelValues::Float64(v) => (Arc::new(Float64Array::from(v)), DataType::Float64),
    };

    let schema = Schema::new(vec![
        Field::new("timecodes", DataType::Int64, false),
        Field::new(name, data_type, false).with_metadata(metadata),
    ]);

    RecordBatch::try_new(Arc::new(schema), vec![Arc::new(timecodes), values_col])
}

/// Build Arrow RecordBatches for all channels.
///
/// Returns a Vec of (channel_name, RecordBatch) pairs.
pub fn build_all_channel_batches(
    channel_data: HashMap<u16, ChannelData>,
    channels: &HashMap<u16, ChannelInfo>,
    reserved_names: &[&str],
    format_f32: impl Fn(f32) -> String,
) -> arrow::error::Result<Vec<(String, RecordBatch)>> {
    let mut batches = Vec::new();

    // Duplicate long_names are distinct channels; expose every one, with
    // later occurrences renamed "<name> dup <k>" per the AIM DLL
    // convention (see parser::channel_display_names). Iterate in ascending
    // channel-index order for deterministic output.
    let display_names = crate::parser::channel_display_names(channels, reserved_names);
    let mut entries: Vec<(u16, ChannelData)> = channel_data.into_iter().collect();
    entries.sort_by_key(|(idx, _)| *idx);

    for (ch_idx, ch_data) in entries {
        if let Some(ch_info) = channels.get(&ch_idx) {
            let name = display_names
                .get(&ch_idx)
                .cloned()
                .unwrap_or_else(|| ch_info.chs.long_name());
            let metadata = ChannelMetadata::from_channel_info(ch_info).to_hashmap(&format_f32);
            let batch = build_channel_batch(&name, ch_data, metadata)?;
            batches.push((name, batch));
        }
    }

    Ok(batches)
}

/// Build an Arrow RecordBatch for laps.
///
/// Columns: num (Int32), start_time (Int64), end_time (Int64), lap_type (Utf8).
pub fn build_laps_batch(laps: &[ProcessedLap]) -> arrow::error::Result<RecordBatch> {
    let nums: Vec<i32> = laps.iter().map(|l| l.num).collect();
    let starts: Vec<i64> = laps.iter().map(|l| l.start_time).collect();
    let ends: Vec<i64> = laps.iter().map(|l| l.end_time).collect();
    let types: Vec<&str> = laps.iter().map(|l| l.lap_type.as_str()).collect();

    let schema = Schema::new(vec![
        Field::new("num", DataType::Int32, false),
        Field::new("start_time", DataType::Int64, false),
        Field::new("end_time", DataType::Int64, false),
        Field::new("lap_type", DataType::Utf8, false),
    ]);

    RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(Int32Array::from(nums)),
            Arc::new(Int64Array::from(starts)),
            Arc::new(Int64Array::from(ends)),
            Arc::new(StringArray::from(types)),
        ],
    )
}

/// Build a GPS channel RecordBatch.
fn build_gps_channel_batch(
    name: &str,
    def: &GpsChannelDef,
    timecodes: Vec<i64>,
    values_f64: Option<Vec<f64>>,
    values_f32: Option<Vec<f32>>,
    format_f32: &impl Fn(f32) -> String,
) -> arrow::error::Result<RecordBatch> {
    let metadata = ChannelMetadata::for_gps_channel(def).to_hashmap(format_f32);
    let tc_array = Int64Array::from(timecodes);

    if def.is_f64 {
        let vals = Float64Array::from(values_f64.unwrap());
        let schema = Schema::new(vec![
            Field::new("timecodes", DataType::Int64, false),
            Field::new(name, DataType::Float64, false).with_metadata(metadata),
        ]);
        RecordBatch::try_new(Arc::new(schema), vec![Arc::new(tc_array), Arc::new(vals)])
    } else {
        let vals = Float32Array::from(values_f32.unwrap());
        let schema = Schema::new(vec![
            Field::new("timecodes", DataType::Int64, false),
            Field::new(name, DataType::Float32, false).with_metadata(metadata),
        ]);
        RecordBatch::try_new(Arc::new(schema), vec![Arc::new(tc_array), Arc::new(vals)])
    }
}

/// Build Arrow RecordBatches for all 14 GPS channels.
///
/// Returns a Vec of (channel_name, RecordBatch) pairs.
pub fn build_gps_channel_batches(
    gps: GpsDecodeResult,
    format_f32: impl Fn(f32) -> String,
) -> arrow::error::Result<Vec<(String, RecordBatch)>> {
    let mut batches = Vec::with_capacity(14);

    let GpsDecodeResult {
        timecodes,
        speed,
        latitude,
        longitude,
        altitude,
        itow,
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
        week,
    } = gps;

    let mut f64_channels = vec![speed, latitude, longitude, altitude, itow].into_iter();
    let mut f32_channels = vec![
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
        week,
    ]
    .into_iter();

    for def in GPS_CHANNEL_DEFS {
        let tc = timecodes.clone();
        let batch = if def.is_f64 {
            let vals = f64_channels.next().unwrap();
            build_gps_channel_batch(def.name, def, tc, Some(vals), None, &format_f32)?
        } else {
            let vals = f32_channels.next().unwrap();
            build_gps_channel_batch(def.name, def, tc, None, Some(vals), &format_f32)?
        };
        batches.push((def.name.to_string(), batch));
    }

    Ok(batches)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Expected strings are `str(float(numpy.float32(x)))` from CPython.
    #[test]
    fn format_float_matches_python_str() {
        for (value, expected) in [
            (0.0f32, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (-180.0, "-180.0"),
            (18000.0, "18000.0"),
            (0.1, "0.10000000149011612"),
            (1e-4, "9.999999747378752e-05"),
            (1.5e-4, "0.0001500000071246177"),
            (1e-5, "9.999999747378752e-06"),
            (1e-7, "1.0000000116860974e-07"),
            (9.5e15, "9499999507316736.0"),
            (1e16, "1.0000000272564224e+16"),
            (1e30, "1.0000000150474662e+30"),
            (f32::from_bits(1), "1.401298464324817e-45"),
            (f32::MAX, "3.4028234663852886e+38"),
            (2.0f32.powi(-14), "6.103515625e-05"),
            (2.0f32.powi(-13), "0.0001220703125"),
            (2.0f32.powi(60), "1.152921504606847e+18"),
            (f32::NAN, "nan"),
            (f32::INFINITY, "inf"),
            (f32::NEG_INFINITY, "-inf"),
        ] {
            assert_eq!(format_float(value), expected, "{:?}", value);
        }
    }
}
