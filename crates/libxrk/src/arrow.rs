//! Arrow RecordBatch building for XRK data.
//!
//! Behind the `arrow` feature flag. Converts parsed channel data into
//! Arrow RecordBatch objects for zero-copy interop.

use std::collections::HashMap;
use std::sync::Arc;

use arrow::array::{
    Array, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, RecordBatch,
    UInt16Array, UInt32Array, UInt8Array,
};
use arrow::datatypes::{DataType, Field, Schema};

use crate::gps::{GpsChannelDef, GpsDecodeResult, GPS_CHANNEL_DEFS};
use crate::parser::{ChannelData, ChannelInfo, ChannelValues, ProcessedLap};

/// Channel metadata keys used in Arrow field metadata.
pub struct ChannelMetadata {
    pub units: String,
    pub dec_pts: String,
    pub interpolate: String,
    pub function: String,
    pub source_type: String,
    pub source_channel_id: String,
    pub device_tag: String,
    pub cal_value_1: String,
    pub cal_value_2: String,
    pub display_range_min: String,
    pub display_range_max: String,
}

impl ChannelMetadata {
    /// Build metadata from a ChannelInfo, using a formatter for f32 values.
    pub fn from_channel_info(ch_info: &ChannelInfo, format_f32: impl Fn(f32) -> String) -> Self {
        let chs = &ch_info.chs;
        let units = if chs.data_size == 1 {
            "".to_string()
        } else {
            chs.units().to_string()
        };
        ChannelMetadata {
            units,
            dec_pts: chs.dec_pts().to_string(),
            interpolate: if chs.interpolate() { "True" } else { "False" }.to_string(),
            function: chs.function().to_string(),
            source_type: chs.source_type.to_string(),
            source_channel_id: chs.source_channel_id.to_string(),
            device_tag: chs.device_tag(),
            cal_value_1: format_f32(chs.cal_value_1),
            cal_value_2: format_f32(chs.cal_value_2),
            display_range_min: format_f32(chs.display_range_min),
            display_range_max: format_f32(chs.display_range_max),
        }
    }

    /// Convert to a HashMap suitable for Arrow field metadata.
    pub fn to_hashmap(&self) -> HashMap<String, String> {
        let mut m = HashMap::new();
        m.insert("units".to_string(), self.units.clone());
        m.insert("dec_pts".to_string(), self.dec_pts.clone());
        m.insert("interpolate".to_string(), self.interpolate.clone());
        m.insert("function".to_string(), self.function.clone());
        m.insert("source_type".to_string(), self.source_type.clone());
        m.insert(
            "source_channel_id".to_string(),
            self.source_channel_id.clone(),
        );
        m.insert("device_tag".to_string(), self.device_tag.clone());
        m.insert("cal_value_1".to_string(), self.cal_value_1.clone());
        m.insert("cal_value_2".to_string(), self.cal_value_2.clone());
        m.insert(
            "display_range_min".to_string(),
            self.display_range_min.clone(),
        );
        m.insert(
            "display_range_max".to_string(),
            self.display_range_max.clone(),
        );
        m
    }

    /// Build default GPS metadata.
    pub fn for_gps_channel(def: &GpsChannelDef) -> Self {
        ChannelMetadata {
            units: def.units.to_string(),
            dec_pts: def.dec_pts.to_string(),
            interpolate: if def.interpolate { "True" } else { "False" }.to_string(),
            function: "".to_string(),
            source_type: "0".to_string(),
            source_channel_id: "0".to_string(),
            device_tag: "".to_string(),
            cal_value_1: "0.0".to_string(),
            cal_value_2: "1.0".to_string(),
            display_range_min: "0.0".to_string(),
            display_range_max: "0.0".to_string(),
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
    format_f32: impl Fn(f32) -> String,
) -> arrow::error::Result<Vec<(String, RecordBatch)>> {
    let mut batches = Vec::new();

    for (ch_idx, ch_data) in channel_data.into_iter() {
        if let Some(ch_info) = channels.get(&ch_idx) {
            let name = ch_info.chs.long_name();
            let metadata = ChannelMetadata::from_channel_info(ch_info, &format_f32).to_hashmap();
            let batch = build_channel_batch(&name, ch_data, metadata)?;
            batches.push((name, batch));
        }
    }

    Ok(batches)
}

/// Build an Arrow RecordBatch for laps.
///
/// Columns: num (Int32), start_time (Int64), end_time (Int64).
pub fn build_laps_batch(laps: &[ProcessedLap]) -> arrow::error::Result<RecordBatch> {
    let nums: Vec<i32> = laps.iter().map(|l| l.num).collect();
    let starts: Vec<i64> = laps.iter().map(|l| l.start_time).collect();
    let ends: Vec<i64> = laps.iter().map(|l| l.end_time).collect();

    let schema = Schema::new(vec![
        Field::new("num", DataType::Int32, false),
        Field::new("start_time", DataType::Int64, false),
        Field::new("end_time", DataType::Int64, false),
    ]);

    RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(Int32Array::from(nums)),
            Arc::new(Int64Array::from(starts)),
            Arc::new(Int64Array::from(ends)),
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
) -> arrow::error::Result<RecordBatch> {
    let metadata = ChannelMetadata::for_gps_channel(def).to_hashmap();
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

/// Build Arrow RecordBatches for all 12 GPS channels.
///
/// Returns a Vec of (channel_name, RecordBatch) pairs.
pub fn build_gps_channel_batches(
    gps: GpsDecodeResult,
) -> arrow::error::Result<Vec<(String, RecordBatch)>> {
    let mut batches = Vec::with_capacity(12);

    let GpsDecodeResult {
        timecodes,
        speed,
        latitude,
        longitude,
        altitude,
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
    } = gps;

    let mut f64_channels = vec![speed, latitude, longitude, altitude].into_iter();
    let mut f32_channels = vec![
        satellites,
        fix,
        pdop,
        position_accuracy,
        velocity_accuracy,
        inline_acc,
        lateral_acc,
        yaw_rate,
    ]
    .into_iter();

    for def in GPS_CHANNEL_DEFS {
        let tc = timecodes.clone();
        let batch = if def.is_f64 {
            let vals = f64_channels.next().unwrap();
            build_gps_channel_batch(def.name, def, tc, Some(vals), None)?
        } else {
            let vals = f32_channels.next().unwrap();
            build_gps_channel_batch(def.name, def, tc, None, Some(vals))?
        };
        batches.push((def.name.to_string(), batch));
    }

    Ok(batches)
}
