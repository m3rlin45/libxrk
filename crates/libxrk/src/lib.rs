//! Pure Rust library for reading AIM XRK/XRZ motorsports telemetry files.
//!
//! This crate provides a standalone parser with no Python dependencies.
//! Arrow support is behind the `arrow` feature flag.
//!
//! # Example
//! ```no_run
//! let file = libxrk::read_xrk_file("session.xrk").unwrap();
//! println!("Channels: {}", file.channels.len());
//! println!("Laps: {}", file.laps.len());
//! if let Some(driver) = &file.metadata.driver {
//!     println!("Driver: {}", driver);
//! }
//! ```

pub mod decoders;
pub mod error;
pub mod gps;
pub mod messages;
pub mod metadata;
pub mod parser;
pub mod payloads;
pub mod tables;

#[cfg(feature = "arrow")]
pub mod arrow;

use std::path::Path;

pub use error::Error;
pub use gps::GpsDecodeResult;
pub use metadata::Metadata;
pub use parser::{ChannelData, ChannelInfo, ChannelValues, ParseResult, ProcessedLap};

/// Result type alias for libxrk operations.
pub type Result<T> = std::result::Result<T, Error>;

/// A parsed XRK/XRZ telemetry file.
pub struct XrkFile {
    /// Parsed channel data, keyed by channel index.
    pub channels: Vec<Channel>,
    /// Lap boundaries.
    pub laps: Vec<ProcessedLap>,
    /// File metadata (driver, vehicle, venue, etc.).
    pub metadata: Metadata,
    /// Raw parse result for advanced use.
    pub raw: ParseResult,
    /// GPS decode result (if GPS data was present).
    pub gps: Option<GpsDecodeResult>,
}

/// A decoded telemetry channel with metadata.
pub struct Channel {
    pub index: u16,
    pub name: String,
    pub group: Option<String>,
    pub timecodes: Vec<i64>,
    pub values: ChannelValues,
    pub units: String,
    pub dec_pts: u8,
    pub interpolate: bool,
    pub source_type: u8,
    pub source_channel_id: u16,
    pub device_tag: String,
    pub cal_value_1: f32,
    pub cal_value_2: f32,
    pub display_range_min: f32,
    pub display_range_max: f32,
}

/// Parse an XRK/XRZ file from raw bytes.
///
/// Handles zlib decompression (XRZ), GPS timing fix, and lap detection
/// automatically.
pub fn read_xrk(data: &[u8]) -> Result<XrkFile> {
    read_xrk_with_progress(data, None)
}

/// Parse an XRK/XRZ file from a filesystem path.
pub fn read_xrk_file(path: impl AsRef<Path>) -> Result<XrkFile> {
    let data = std::fs::read(path.as_ref())?;
    read_xrk(&data)
}

/// Parse with an optional progress callback.
pub fn read_xrk_with_progress(
    data: &[u8],
    progress: Option<&dyn Fn(usize, usize)>,
) -> Result<XrkFile> {
    // Decompress if XRZ (zlib-compressed XRK)
    let data = decompress_if_zlib(data);

    // Parse with the Rust streaming parser
    let mut result = parser::parse_xrk(&data, progress)?;

    // Compute non-GPS max end time before moving channel_data out
    let non_gps_max_end_time: Option<i64> = result
        .channel_data
        .values()
        .filter_map(|ch| ch.timecodes.last().copied())
        .max();

    // Move channel_data out of result (avoids cloning every channel)
    let channel_data = std::mem::take(&mut result.channel_data);

    // Build Channel structs from channel_data + channel info
    let channels: Vec<Channel> = channel_data
        .into_iter()
        .filter_map(|(ch_idx, ch_data)| {
            let ch_info = result.channels.get(&ch_idx)?;
            let group_name = ch_info.group_index.and_then(|gi| {
                result.groups.get(&gi).map(|g| {
                    // Use group's first channel name as group name (convention)
                    g.grp.index.to_string()
                })
            });
            Some(Channel {
                index: ch_idx,
                name: ch_info.chs.long_name(),
                group: group_name,
                timecodes: ch_data.timecodes,
                values: ch_data.values,
                units: ch_info.chs.units().to_string(),
                dec_pts: ch_info.chs.dec_pts(),
                interpolate: ch_info.chs.interpolate(),
                source_type: ch_info.chs.source_type,
                source_channel_id: ch_info.chs.source_channel_id,
                device_tag: ch_info.chs.device_tag(),
                cal_value_1: ch_info.chs.cal_value_1,
                cal_value_2: ch_info.chs.cal_value_2,
                display_range_min: ch_info.chs.display_range_min,
                display_range_max: ch_info.chs.display_range_max,
            })
        })
        .collect();

    // Decode GPS channels
    let mut gps_result = gps::decode_gps(&result.gps_data, result.time_offset)?;

    // Apply GPS timing fix
    if let Some(ref mut gps) = gps_result {
        let gnfi_timecodes: Vec<i64> =
            if !result.gnfi_data.is_empty() && result.gnfi_data.len().is_multiple_of(32) {
                let n_records = result.gnfi_data.len() / 32;
                (0..n_records)
                    .map(|i| {
                        let offset = i * 32;
                        i32::from_le_bytes([
                            result.gnfi_data[offset],
                            result.gnfi_data[offset + 1],
                            result.gnfi_data[offset + 2],
                            result.gnfi_data[offset + 3],
                        ]) as i64
                            - result.time_offset
                    })
                    .collect()
            } else {
                Vec::new()
            };

        let corrections = gps::timing::detect_gap_corrections(
            &gps.timecodes,
            &gnfi_timecodes,
            non_gps_max_end_time,
            40.0,
        );
        if !corrections.is_empty() {
            gps::timing::apply_corrections(&mut gps.timecodes, &corrections);
        }
    }

    // Build metadata
    let metadata = metadata::extract_metadata(&result);

    // Build laps — GPS timing fix was applied above
    let mut processed_laps = parser::get_processed_laps(&result)?;

    // Validate LAP-message-based laps
    if processed_laps
        .iter()
        .any(|l| l.start_time < 0 || l.end_time <= l.start_time)
    {
        processed_laps.clear();
    }

    // Classify last lap as "in" if GPS shows car is not near S/F at end
    if !processed_laps.is_empty() && processed_laps.last().unwrap().lap_type != "out" {
        if let (Some(trk_marker), Some(ref gps)) = (get_trk_marker(&result), &gps_result) {
            if !gps.timecodes.is_empty() {
                let (sf_lat, sf_lon) = trk_marker;
                let (sf_x, sf_y, sf_z) = gps::utils::lla2ecef(sf_lat, sf_lon, 0.0);

                let end_time = processed_laps.last().unwrap().end_time;
                let idx = gps
                    .timecodes
                    .partition_point(|&t| t < end_time)
                    .min(gps.timecodes.len() - 1);
                let (ex, ey, ez) = gps::utils::lla2ecef(gps.latitude[idx], gps.longitude[idx], 0.0);

                let dist = ((sf_x - ex).powi(2) + (sf_y - ey).powi(2) + (sf_z - ez).powi(2)).sqrt();
                if dist > 30.0 {
                    let last_idx = processed_laps.len() - 1;
                    processed_laps[last_idx].lap_type = "in".to_string();
                }
            }
        }
    }

    if processed_laps.is_empty() {
        // GPS-based lap detection
        if let Some(trk_marker) = get_trk_marker(&result) {
            if let Some(ref gps) = gps_result {
                let xyz: Vec<[f64; 3]> = gps
                    .latitude
                    .iter()
                    .zip(gps.longitude.iter())
                    .map(|(&lat, &lon)| {
                        let (x, y, z) = gps::utils::lla2ecef(lat, lon, 0.0);
                        [x, y, z]
                    })
                    .collect();

                let lap_markers = gps::utils::find_laps(&xyz, &gps.timecodes, trk_marker);

                if !lap_markers.is_empty() {
                    let session_end = *gps.timecodes.last().unwrap_or(&0);
                    let mut all_markers = lap_markers.clone();
                    all_markers.push(session_end as f64);

                    let lap_count = all_markers.windows(2).len();
                    for (i, window) in all_markers.windows(2).enumerate() {
                        processed_laps.push(ProcessedLap {
                            num: i as i32,
                            start_time: window[0] as i64,
                            end_time: window[1] as i64,
                            lap_type: if i == lap_count - 1 {
                                "in".to_string()
                            } else {
                                "full".to_string()
                            },
                        });
                    }
                }
            }
        }
    }

    Ok(XrkFile {
        channels,
        laps: processed_laps,
        metadata,
        raw: result,
        gps: gps_result,
    })
}

/// Extract TRK start/finish marker coordinates from a ParseResult.
fn get_trk_marker(result: &ParseResult) -> Option<(f64, f64)> {
    let trk_msgs = result.header_messages.get(&messages::tokens::trk())?;
    let last_msg = trk_msgs.last()?;
    let payload = messages::dispatch_payload(last_msg);
    if let messages::Payload::Trk(trk) = payload {
        Some((trk.sf_lat, trk.sf_long))
    } else {
        None
    }
}

/// Decompress zlib-compressed data if detected, otherwise return as-is.
/// XRZ files are XRK files compressed with zlib.
pub fn decompress_if_zlib(data: &[u8]) -> Vec<u8> {
    if data.len() < 2 {
        return data.to_vec();
    }
    if data[0] == 0x78 && matches!(data[1], 0x01 | 0x9C | 0xDA) {
        use std::io::Read;
        let mut decoder = flate2::read::ZlibDecoder::new(data);
        let mut decompressed = Vec::new();
        match decoder.read_to_end(&mut decompressed) {
            Ok(_) => decompressed,
            Err(_) => {
                if decompressed.is_empty() {
                    data.to_vec()
                } else {
                    decompressed
                }
            }
        }
    } else {
        data.to_vec()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decompress_if_zlib_passthrough_non_zlib() {
        let data = b"hello world";
        let result = decompress_if_zlib(data);
        assert_eq!(result, data);
    }

    #[test]
    fn test_decompress_if_zlib_empty() {
        let result = decompress_if_zlib(&[]);
        assert!(result.is_empty());
    }

    #[test]
    fn test_decompress_if_zlib_single_byte() {
        let result = decompress_if_zlib(&[0x78]);
        assert_eq!(result, vec![0x78]);
    }

    #[test]
    fn test_decompress_if_zlib_valid_zlib() {
        use std::io::Write;
        // Compress some data with zlib
        let original = b"test data for compression";
        let mut encoder =
            flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(original).unwrap();
        let compressed = encoder.finish().unwrap();

        let result = decompress_if_zlib(&compressed);
        assert_eq!(result, original);
    }

    #[test]
    fn test_decompress_if_zlib_fake_header() {
        // Starts with zlib magic but isn't valid zlib — should return original data
        let data = [0x78, 0x9C, 0xFF, 0xFF, 0xFF, 0xFF];
        let result = decompress_if_zlib(&data);
        // Returns original since decompression fails and partial is empty
        assert_eq!(result, data);
    }
}
