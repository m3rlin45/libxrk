//! Pure Rust library for reading AIM XRK/XRZ motorsports telemetry files.
//!
//! This crate provides a standalone parser with no Python dependencies.
//! Arrow support is behind the `arrow` feature flag. GPS processing (the 14
//! GPS channels, the timing fix, GPS lap detection) is the separate
//! `libxrk-gps` crate, shared with the Cython backend and the spec; this
//! crate only collects the raw material and hands it over ([`process_gps`]).
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
pub mod messages;
pub mod metadata;
pub mod parser;
pub mod payloads;
pub mod tables;

#[cfg(feature = "arrow")]
pub mod arrow;

use std::path::Path;

pub use error::Error;
pub use libxrk_gps::{self as gps, GpsChannels};
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
    /// The GPS channels (if GPS data was present).
    pub gps: Option<GpsChannels>,
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
    read_xrk_with_options(data, progress, parser::DEFAULT_PROGRESS_INTERVAL)
}

/// Parse with an optional progress callback, choosing how often it is called.
///
/// `progress_interval` is a number of input bytes, clamped to
/// [`parser::MIN_PROGRESS_INTERVAL`].
pub fn read_xrk_with_options(
    data: &[u8],
    progress: Option<&dyn Fn(usize, usize)>,
    progress_interval: usize,
) -> Result<XrkFile> {
    // Decompress if XRZ (zlib-compressed XRK)
    let data = decompress_if_zlib(data);

    // Parse with the Rust streaming parser
    let mut result = parser::parse_xrk_with_interval(&data, progress, progress_interval)?;

    // GPS channels and the finished lap table, from the raw material the
    // parser collected. Needs channel_data for the timing fix's indirect
    // method, so it runs before channel_data is moved out below.
    let gps_out = process_gps(&result)?;

    // Build metadata while channel_data is still populated (calibration
    // attribution only considers channels that carry data, like Cython).
    let metadata = metadata::extract_metadata(&result);

    // Move channel_data out of result (avoids cloning every channel)
    let channel_data = std::mem::take(&mut result.channel_data);

    // Build Channel structs from channel_data + channel info, in ascending
    // channel-index order. Duplicate long_names are exposed with the AIM
    // DLL's "<name> dup <k>" convention (parser::channel_display_names);
    // when the file carries GPS data, the synthesized GPS channel names
    // are reserved so CHS channels colliding with them get the suffix.
    let display_names =
        parser::channel_display_names(&result.channels, &gps_reserved_names(&result));
    let mut channel_data: Vec<(u16, ChannelData)> = channel_data.into_iter().collect();
    channel_data.sort_by_key(|(idx, _)| *idx);
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
                name: display_names
                    .get(&ch_idx)
                    .cloned()
                    .unwrap_or_else(|| ch_info.chs.long_name()),
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

    Ok(XrkFile {
        channels,
        laps: gps_out.laps,
        metadata,
        raw: result,
        gps: gps_out.channels,
    })
}

/// Names reserved for the synthesized GPS channels when the file carries GPS
/// data, so a CHS channel with the same name is exposed as "<name> dup 2".
pub fn gps_reserved_names(result: &ParseResult) -> Vec<&'static str> {
    if result.gps_data.is_empty() {
        Vec::new()
    } else {
        gps::GPS_CHANNEL_DEFS.iter().map(|d| d.name).collect()
    }
}

/// Run the shared GPS pipeline (`libxrk_gps::process`) on a parse result.
///
/// Hands over the raw GPS and GNFI records, the time offset, the last
/// timecode of the non-GPS channels, the TRK start/finish marker and the
/// lap table built from LAP messages, and gets back the GPS channels and the
/// finished lap table. The parser's lap table is discarded (so laps are
/// detected from GPS instead) when it is inconsistent: a negative start or
/// a lap that does not end after it starts.
pub fn process_gps(result: &ParseResult) -> Result<gps::Output> {
    let non_gps_max_end_time: Option<i64> = result
        .channel_data
        .values()
        .filter_map(|ch| ch.timecodes.last().copied())
        .max();

    let mut laps = parser::get_processed_laps(result)?;
    if laps
        .iter()
        .any(|l| l.start_time < 0 || l.end_time <= l.start_time)
    {
        laps.clear();
    }

    Ok(gps::process(gps::Input {
        gps_data: &result.gps_data,
        gnfi_data: &result.gnfi_data,
        time_offset: result.time_offset,
        non_gps_max_end_time,
        start_finish: start_finish(result),
        laps,
        expected_dt_ms: gps::DEFAULT_EXPECTED_DT_MS,
    })?)
}

/// Start/finish (latitude, longitude) in degrees from the last TRK message.
pub fn start_finish(result: &ParseResult) -> Option<(f64, f64)> {
    let trk_msgs = result.header_messages.get(&messages::tokens::trk())?;
    let last_msg = trk_msgs.last()?;
    if let messages::Payload::Trk(trk) = messages::dispatch_payload(last_msg) {
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
