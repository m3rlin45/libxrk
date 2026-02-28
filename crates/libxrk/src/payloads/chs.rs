//! CHS — Channel Definition (112 bytes).
//!
//! Reference: aim_xrk.pyx:434-524, spec/xrk_format.py:185-243.

use binrw::BinRead;

use crate::messages::nullterm_string;
use crate::tables;

/// CHS payload — 112 bytes.
#[derive(Debug, Clone, BinRead)]
#[br(little)]
pub struct ChsPayload {
    pub index: u16,               // [0:2]
    pub _pad_2_4: [u8; 2],        // [2:4]
    pub hardware_id: u16,         // [4:6]
    pub source_channel_id: u16,   // [6:8]
    pub hardware_ref: u32,        // [8:12]
    pub unit_type_byte: u8,       // [12]
    pub maybe_display_format: u8, // [13]
    pub maybe_config_flags: u16,  // [14:16]
    pub source_type: u8,          // [16]
    pub _pad_17_20: [u8; 3],      // [17:20]
    pub decoder_type: u8,         // [20]
    pub _pad_21_24: [u8; 3],      // [21:24]
    pub short_name_raw: [u8; 8],  // [24:32]
    pub long_name_raw: [u8; 24],  // [32:56]
    pub _pad_56_64: [u8; 8],      // [56:64]
    pub sample_period_us: u32,    // [64:68]
    pub data_offset: u16,         // [68:70]
    pub _pad_70_72: [u8; 2],      // [70:72]
    pub data_size: u8,            // [72]
    pub _pad_73_76: [u8; 3],      // [73:76]
    pub device_tag_raw: [u8; 4],  // [76:80]
    pub device_node_id: u8,       // [80]
    pub maybe_device_flags: u8,   // [81]
    pub _pad_82_84: [u8; 2],      // [82:84]
    pub maybe_output_type: u8,    // [84]
    pub _pad_85_88: [u8; 3],      // [85:88]
    pub display_index: u32,       // [88:92]
    pub maybe_output_size: u8,    // [92]
    pub _pad_93_96: [u8; 3],      // [93:96]
    pub cal_value_1: f32,         // [96:100]
    pub cal_value_2: f32,         // [100:104]
    pub display_range_min: f32,   // [104:108]
    pub display_range_max: f32,   // [108:112]
}

impl ChsPayload {
    /// Get the decoded long name (null-terminated ASCII).
    pub fn long_name(&self) -> String {
        nullterm_string(&self.long_name_raw)
    }

    /// Get the decoded short name (null-terminated ASCII).
    pub fn short_name(&self) -> String {
        nullterm_string(&self.short_name_raw)
    }

    /// Get the resolved unit string (handles calibrated flag -> "V").
    pub fn units(&self) -> &'static str {
        tables::resolve_unit(self.unit_type_byte)
    }

    /// Get the decimal points for display.
    pub fn dec_pts(&self) -> u8 {
        tables::unit_map(self.unit_type_byte).1
    }

    /// Get the interpolation flag from the decoder table.
    pub fn interpolate(&self) -> bool {
        tables::decoder_info(self.decoder_type)
            .map(|d| d.interpolate)
            .unwrap_or(false)
    }

    /// Get the device tag as string.
    pub fn device_tag(&self) -> String {
        if self.device_tag_raw.iter().any(|&b| b != 0) {
            let end = self
                .device_tag_raw
                .iter()
                .position(|&b| b == 0)
                .unwrap_or(self.device_tag_raw.len());
            String::from_utf8_lossy(&self.device_tag_raw[..end]).into_owned()
        } else {
            String::new()
        }
    }

    /// Resolve the channel function string from CHS fields.
    pub fn function(&self) -> &'static str {
        tables::resolve_function(
            self.maybe_display_format,
            self.unit_type_byte,
            self.maybe_config_flags,
        )
    }

    /// M-message sample period in milliseconds (sample_period_us / 1000).
    pub fn mms(&self) -> u32 {
        self.sample_period_us / 1000
    }

    /// Full raw bytes for the "unknown" field (used for decoder_type lookup, etc.)
    /// This is the full 112-byte CHS with index/names zeroed out.
    pub fn unknown_bytes(&self) -> [u8; 112] {
        // We need the raw bytes to extract decoder_type at offset 20
        // For compatibility, we just store the decoder_type directly
        let mut buf = [0u8; 112];
        buf[20] = self.decoder_type;
        buf
    }
}
