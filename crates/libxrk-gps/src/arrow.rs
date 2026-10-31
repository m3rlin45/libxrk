//! Arrow RecordBatches for the GPS channels (behind the `arrow` feature).
//!
//! Both Python backends expose GPS channels built here, so their tables are
//! identical down to the field metadata strings.

use std::collections::HashMap;
use std::sync::Arc;

use arrow::array::{Array, Float32Array, Float64Array, Int64Array, RecordBatch};
use arrow::datatypes::{DataType, Field, Schema};

use crate::navsol::{GpsChannelDef, GpsChannels, GPS_CHANNEL_DEFS};

/// Field metadata for a GPS channel, with the same keys and formatting as a
/// CHS channel's metadata (`libxrk.base.ChannelMetadata`).
pub fn gps_channel_metadata(def: &GpsChannelDef) -> HashMap<String, String> {
    let mut m = HashMap::new();
    m.insert("units".to_string(), def.units.to_string());
    m.insert("dec_pts".to_string(), def.dec_pts.to_string());
    m.insert(
        "interpolate".to_string(),
        if def.interpolate { "True" } else { "False" }.to_string(),
    );
    m.insert("function".to_string(), String::new());
    m.insert("source_type".to_string(), "0".to_string());
    m.insert("source_channel_id".to_string(), "0".to_string());
    m.insert("device_tag".to_string(), String::new());
    m.insert("cal_value_1".to_string(), "0.0".to_string());
    m.insert("cal_value_2".to_string(), "1.0".to_string());
    m.insert("display_range_min".to_string(), "0.0".to_string());
    m.insert("display_range_max".to_string(), "0.0".to_string());
    m
}

fn batch(
    def: &GpsChannelDef,
    timecodes: &[i64],
    values: Arc<dyn Array>,
) -> arrow::error::Result<RecordBatch> {
    let schema = Schema::new(vec![
        Field::new("timecodes", DataType::Int64, false),
        Field::new(def.name, values.data_type().clone(), false)
            .with_metadata(gps_channel_metadata(def)),
    ]);
    RecordBatch::try_new(
        Arc::new(schema),
        vec![Arc::new(Int64Array::from(timecodes.to_vec())), values],
    )
}

/// Build one `(name, RecordBatch)` per GPS channel, in [`GPS_CHANNEL_DEFS`] order.
///
/// Each batch has a `timecodes` (Int64) column and the channel column
/// (Float64 or Float32 per the definition) carrying the field metadata.
pub fn build_gps_channel_batches(
    gps: GpsChannels,
) -> arrow::error::Result<Vec<(String, RecordBatch)>> {
    let GpsChannels {
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

    let mut batches = Vec::with_capacity(GPS_CHANNEL_DEFS.len());
    for def in GPS_CHANNEL_DEFS {
        let values: Arc<dyn Array> = if def.is_f64 {
            Arc::new(Float64Array::from(f64_channels.next().unwrap()))
        } else {
            Arc::new(Float32Array::from(f32_channels.next().unwrap()))
        };
        batches.push((def.name.to_string(), batch(def, &timecodes, values)?));
    }
    Ok(batches)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::navsol::decode_nav_sol;

    #[test]
    fn one_batch_per_channel_with_metadata() {
        let mut rec = vec![0u8; 56];
        rec[0..4].copy_from_slice(&1000i32.to_le_bytes());
        rec[16..20].copy_from_slice(&637_813_700i32.to_le_bytes());
        let gps = decode_nav_sol(&rec, 0).unwrap().unwrap();
        let batches = build_gps_channel_batches(gps).unwrap();
        assert_eq!(batches.len(), 14);
        let (name, b) = &batches[0];
        assert_eq!(name, "GPS Speed");
        assert_eq!(b.num_rows(), 1);
        assert_eq!(b.schema().field(1).data_type(), &DataType::Float64);
        let md = b.schema().field(1).metadata().clone();
        assert_eq!(md["units"], "m/s");
        assert_eq!(md["dec_pts"], "1");
        assert_eq!(md["interpolate"], "True");
        assert_eq!(md["cal_value_2"], "1.0");
        let (_, sats) = &batches[4];
        assert_eq!(sats.schema().field(1).data_type(), &DataType::Float32);
    }
}
