//! Streaming state machine: scan for opcodes, accumulate channel data.
//!
//! Equivalent to `_decode_sequence()` in aim_xrk.pyx.
//! This is the core parsing engine that processes the XRK binary stream.

use std::collections::HashMap;

use crate::error::Error;
use crate::messages::{self, tokens, HeaderMessage, Payload};
use crate::payloads::chs::ChsPayload;
use crate::payloads::grp::GrpPayload;
use crate::payloads::lap::LapPayload;
use crate::tables;

/// Accumulator for a single channel or group's data messages.
#[derive(Debug)]
struct Accum {
    last_timecode: i32,
    /// For S/G/c messages: stride = data_size + header overhead
    add_helper: usize,
    /// M-message sample period in ms
    mms: u32,
    /// Raw accumulated data bytes (timecode + sample data interleaved)
    data: Vec<u8>,
    /// M-message timecodes (stored separately because M-messages expand)
    timecodes: Vec<i32>,
}

impl Accum {
    fn new() -> Self {
        Accum {
            last_timecode: -1,
            add_helper: 1,
            mms: 0,
            data: Vec::new(),
            timecodes: Vec::new(),
        }
    }
}

/// Channel info accumulated during parsing.
#[derive(Debug, Clone)]
pub struct ChannelInfo {
    pub chs: ChsPayload,
    pub group_index: Option<u16>,
    pub group_offset: Option<usize>,
}

/// Group info accumulated during parsing.
#[derive(Debug, Clone)]
pub struct GroupInfo {
    pub grp: GrpPayload,
}

/// Result of parsing the stream — raw data before decode/Arrow conversion.
pub struct ParseResult {
    /// What the parser had to work around. Never printed; see
    /// [`crate::diagnostics`].
    pub diagnostics: crate::diagnostics::Diagnostics,
    pub channels: HashMap<u16, ChannelInfo>,
    pub groups: HashMap<u16, GroupInfo>,
    pub header_messages: HashMap<u32, Vec<HeaderMessage>>,
    pub gps_data: Vec<u8>,
    pub gnfi_data: Vec<u8>,
    pub time_offset: i64,
    pub last_time: i64,
    /// Decoded channel data: channel_index -> (timecodes, sample_values)
    pub channel_data: HashMap<u16, ChannelData>,
    /// Lap info from LAP messages
    pub laps: Vec<LapInfo>,
    /// ENF sub-messages: each entry is the sub-parsed messages from one ENF message
    pub enf_sub_messages: Vec<HashMap<u32, Vec<HeaderMessage>>>,
}

/// Typed channel value storage — preserves the native Arrow type from each decoder.
pub enum ChannelValues {
    UInt8(Vec<u8>),
    UInt16(Vec<u16>),
    Int16(Vec<i16>),
    Int32(Vec<i32>),
    UInt32(Vec<u32>),
    Float32(Vec<f32>),
    Float64(Vec<f64>),
}

impl ChannelValues {
    /// Create a new typed vector with the right variant based on decoder/channel properties.
    ///
    /// V-units channels with integer decoders use Float64 (numpy 2.x: np.divide(int, 1000) → f64).
    /// V-units channels with float decoders stay Float32 (numpy 2.x: np.divide(f32, 1000) → f32).
    /// Manual gear channels use UInt32.
    /// Otherwise: decoder type determines the variant.
    pub fn new(decoder_type: u8, units: &str, is_manual: bool, capacity: usize) -> Self {
        if units == "V" {
            // V conversion (mV→V via /1000): numpy 2.x scalar promotion rules
            // - int / int_scalar → float64
            // - float32 / int_scalar → float32
            return match decoder_type {
                6 | 20 => ChannelValues::Float32(Vec::with_capacity(capacity)),
                _ => ChannelValues::Float64(Vec::with_capacity(capacity)),
            };
        }
        if is_manual {
            return ChannelValues::UInt32(Vec::with_capacity(capacity));
        }
        match decoder_type {
            1 | 15 => ChannelValues::UInt16(Vec::with_capacity(capacity)),
            4 | 11 => ChannelValues::Int16(Vec::with_capacity(capacity)),
            6 | 20 => ChannelValues::Float32(Vec::with_capacity(capacity)),
            13 => ChannelValues::UInt8(Vec::with_capacity(capacity)),
            _ => ChannelValues::Int32(Vec::with_capacity(capacity)),
        }
    }

    /// Push a decoded sample value into the typed vector.
    pub fn push(&mut self, val: crate::decoders::SampleValue) {
        use crate::decoders::SampleValue;
        match self {
            ChannelValues::Float64(v) => v.push(val.as_f64()),
            ChannelValues::UInt8(v) => match val {
                SampleValue::UInt8(x) => v.push(x),
                _ => v.push(val.as_f64() as u8),
            },
            ChannelValues::UInt16(v) => match val {
                SampleValue::UInt16(x) => v.push(x),
                _ => v.push(val.as_f64() as u16),
            },
            ChannelValues::Int16(v) => match val {
                SampleValue::Int16(x) => v.push(x),
                _ => v.push(val.as_f64() as i16),
            },
            ChannelValues::Int32(v) => match val {
                SampleValue::Int32(x) => v.push(x),
                _ => v.push(val.as_f64() as i32),
            },
            ChannelValues::UInt32(v) => match val {
                SampleValue::UInt32(x) => v.push(x),
                _ => v.push(val.as_f64() as u32),
            },
            ChannelValues::Float32(v) => match val {
                SampleValue::Float32(x) => v.push(x),
                _ => v.push(val.as_f32()),
            },
        }
    }

    /// Push a zero/default value of the appropriate type.
    pub fn push_default(&mut self) {
        match self {
            ChannelValues::UInt8(v) => v.push(0),
            ChannelValues::UInt16(v) => v.push(0),
            ChannelValues::Int16(v) => v.push(0),
            ChannelValues::Int32(v) => v.push(0),
            ChannelValues::UInt32(v) => v.push(0),
            ChannelValues::Float32(v) => v.push(0.0),
            ChannelValues::Float64(v) => v.push(0.0),
        }
    }

    /// Apply mV→V conversion (divide by 1000). Valid for Float64 and Float32 variants.
    pub fn apply_mv_to_v(&mut self) {
        match self {
            ChannelValues::Float64(v) => {
                for val in v.iter_mut() {
                    *val /= 1000.0;
                }
            }
            ChannelValues::Float32(v) => {
                for val in v.iter_mut() {
                    *val /= 1000.0;
                }
            }
            _ => {}
        }
    }
}

/// Decoded channel data ready for Arrow conversion.
pub struct ChannelData {
    pub timecodes: Vec<i64>,
    pub values: ChannelValues,
}

/// Processed lap info.
#[derive(Debug, Clone)]
pub struct LapInfo {
    pub segment: u8,
    pub lap_num: u16,
    pub duration: u32,
    pub end_time: u32,
}

/// Parse an XRK byte stream.
///
/// This is the main entry point equivalent to `_decode_sequence()`.
pub fn parse_xrk(
    data: &[u8],
    progress: Option<&dyn Fn(usize, usize)>,
) -> Result<ParseResult, Error> {
    let mut state = ParserState::new();

    // Pre-scan: learn channel structure from headers and count data sizes.
    // This allows pre-allocating accumulator Vecs to eliminate realloc churn
    // during the main scan (~22% of pure Rust time was in Vec::grow_amortized).
    let hints = prescan(data, &mut state);
    state.apply_prescan_hints(&hints);

    let final_pos = scan_stream(data, &mut state, progress);
    if final_pos != data.len() {
        return Err(Error::InvalidData(format!(
            "Parser did not consume entire input: pos={}, len={}",
            final_pos,
            data.len()
        )));
    }
    resolve_c_variants(&mut state);
    state.finalize()
}

/// Internal parser state.
struct ParserState {
    /// Four accumulator categories:
    /// [0] = G messages (groups), [1] = S messages (samples),
    /// [2] = c messages (expansion), [3] = M messages (multi-sample)
    gc_data: [Vec<Accum>; 4],
    channels: HashMap<u16, ChannelInfo>,
    groups: HashMap<u16, GroupInfo>,
    header_messages: HashMap<u32, Vec<HeaderMessage>>,
    gps_data: Vec<u8>,
    gnfi_data: Vec<u8>,
    /// Channel sizes: channel_index -> data_size in bytes
    channel_sizes: HashMap<u16, u8>,
    /// Group sizes: group_index -> total data size
    group_sizes: HashMap<u16, usize>,
    time_offset: Option<i64>,
    diagnostics: crate::diagnostics::Diagnostics,
    last_time: Option<i64>,
    /// ENF sub-messages: each entry is the sub-parsed messages from one ENF message
    enf_sub_messages: Vec<HashMap<u32, Vec<HeaderMessage>>>,
    /// Buffer for V2/V3 (c)-message variants — see spec/xrk_format.py
    /// :_resolve_c_variants. Keyed by channel_field. Entries are
    /// (synthesized_or_embedded_tc, sample_bytes, priority, file_order).
    /// priority: 3 = V2(base), 2 = V3, 1 = V2(+4). Higher wins at tc collision.
    v2v3_by_cf: HashMap<u16, Vec<(i32, Vec<u8>, u8, u32)>>,
    /// Most-recent V2(base).tc per base ch_field (for V3 tc synthesis).
    last_v2_base_tc: HashMap<u16, (i32, u32)>,
    /// Most-recent V2(+4).tc per plus4 ch_field.
    last_v2_plus4_tc: HashMap<u16, (i32, u32)>,
    /// File-order counter for c-message variants.
    c_var_pos: u32,
}

/// Pre-scan results used to pre-allocate accumulator capacity.
/// Uses Vec<usize> indexed by channel index for O(1) access (no hashing).
struct PrescanResult {
    /// Total data bytes per accumulator category [0..4] per index.
    data_bytes: [Vec<usize>; 4],
    /// M-message timecode count per channel index.
    m_timecode_count: Vec<usize>,
    /// Total GPS payload bytes.
    gps_bytes: usize,
    /// Total GNFI payload bytes.
    gnfi_bytes: usize,
}

impl PrescanResult {
    fn new() -> Self {
        PrescanResult {
            data_bytes: [Vec::new(), Vec::new(), Vec::new(), Vec::new()],
            m_timecode_count: Vec::new(),
            gps_bytes: 0,
            gnfi_bytes: 0,
        }
    }

    #[inline]
    fn add_data_bytes(&mut self, cat: usize, idx: usize, bytes: usize) {
        if self.data_bytes[cat].len() <= idx {
            self.data_bytes[cat].resize(idx + 1, 0);
        }
        self.data_bytes[cat][idx] += bytes;
    }

    #[inline]
    fn add_timecodes(&mut self, idx: usize, count: usize) {
        if self.m_timecode_count.len() <= idx {
            self.m_timecode_count.resize(idx + 1, 0);
        }
        self.m_timecode_count[idx] += count;
    }
}

impl ParserState {
    fn new() -> Self {
        ParserState {
            gc_data: [Vec::new(), Vec::new(), Vec::new(), Vec::new()],
            channels: HashMap::new(),
            groups: HashMap::new(),
            header_messages: HashMap::new(),
            gps_data: Vec::new(),
            gnfi_data: Vec::new(),
            channel_sizes: HashMap::new(),
            group_sizes: HashMap::new(),
            time_offset: None,
            diagnostics: Default::default(),
            last_time: None,
            enf_sub_messages: Vec::new(),
            v2v3_by_cf: HashMap::new(),
            last_v2_base_tc: HashMap::new(),
            last_v2_plus4_tc: HashMap::new(),
            c_var_pos: 0,
        }
    }

    fn ensure_accum(&mut self, cat: usize, idx: usize) {
        while self.gc_data[cat].len() <= idx {
            self.gc_data[cat].push(Accum::new());
        }
    }

    /// Pre-allocate accumulator Vecs based on prescan byte counts.
    fn apply_prescan_hints(&mut self, hints: &PrescanResult) {
        for cat in 0..4 {
            for (idx, &bytes) in hints.data_bytes[cat].iter().enumerate() {
                if bytes > 0 && idx < self.gc_data[cat].len() {
                    self.gc_data[cat][idx].data.reserve(bytes);
                }
            }
        }
        for (idx, &count) in hints.m_timecode_count.iter().enumerate() {
            if count > 0 && idx < self.gc_data[3].len() {
                self.gc_data[3][idx].timecodes.reserve(count);
            }
        }
        self.gps_data.reserve(hints.gps_bytes);
        self.gnfi_data.reserve(hints.gnfi_bytes);
    }

    fn register_chs(&mut self, chs: &ChsPayload) {
        let idx = chs.index as usize;

        // A later re-definition of an existing index reflects the logger's
        // current configuration: the last write wins on names. The AIM
        // official sample renames "Temperature 1" -> "Exhaust Temp" in its
        // final CNF, and the official DLL exposes the new name carrying
        // the full data stream. Other CHS fields and the accumulator
        // layout are assumed stable across re-definitions. Matches the
        // spec's _merge_cnf_result and the Cython backend.
        if let Some(existing) = self.channels.get_mut(&chs.index) {
            existing.chs.short_name_raw = chs.short_name_raw;
            existing.chs.long_name_raw = chs.long_name_raw;
        }

        // Warn for unknown unit types (I)
        if !tables::is_known_unit(chs.unit_type_byte) {
            self.diagnostics.push(crate::diagnostics::Diagnostic::UnknownUnit {
                code: chs.unit_type_byte & 0x7F,
                channel: chs.long_name(),
            });
        }

        // Warn for non-zero CHS padding bytes (I)
        let has_nonzero_pad = chs._pad_2_4.iter().any(|&b| b != 0)
            || chs._pad_17_20.iter().any(|&b| b != 0)
            || chs._pad_21_24.iter().any(|&b| b != 0)
            || chs._pad_56_64.iter().any(|&b| b != 0)
            || chs._pad_70_72.iter().any(|&b| b != 0)
            || chs._pad_73_76.iter().any(|&b| b != 0)
            || chs._pad_85_88.iter().any(|&b| b != 0)
            || chs._pad_93_96.iter().any(|&b| b != 0);
        if has_nonzero_pad {
            let mut details = Vec::new();
            for (base, pad) in [
                (2usize, &chs._pad_2_4[..]),
                (17, &chs._pad_17_20[..]),
                (21, &chs._pad_21_24[..]),
                (56, &chs._pad_56_64[..]),
                (70, &chs._pad_70_72[..]),
                (73, &chs._pad_73_76[..]),
                (85, &chs._pad_85_88[..]),
                (93, &chs._pad_93_96[..]),
            ] {
                for (i, &b) in pad.iter().enumerate() {
                    if b != 0 {
                        details.push(format!("[{}]=0x{:02x}", base + i, b));
                    }
                }
            }
            self.diagnostics
                .push(crate::diagnostics::Diagnostic::UnexpectedChsPadding {
                    channel: chs.long_name(),
                    details: details.join(", "),
                });
        }

        // Register in channel_sizes
        self.channel_sizes.insert(chs.index, chs.data_size);

        // Set up accumulators for S, c, M message categories
        // S messages: add_helper = data_size + 9 (2 op + 4 tc + 2 idx + 1 close)
        self.ensure_accum(1, idx);
        self.gc_data[1][idx].add_helper = chs.data_size as usize + 9;

        // c messages: add_helper = data_size + 12
        self.ensure_accum(2, idx);
        self.gc_data[2][idx].add_helper = chs.data_size as usize + 12;

        // M messages: add_helper = data_size (just the sample data per count)
        self.ensure_accum(3, idx);
        self.gc_data[3][idx].add_helper = chs.data_size as usize;
        self.gc_data[3][idx].mms = chs.mms();

        // Store channel info
        self.channels
            .entry(chs.index)
            .or_insert_with(|| ChannelInfo {
                chs: chs.clone(),
                group_index: None,
                group_offset: None,
            });
    }

    fn register_grp(&mut self, grp: &GrpPayload) {
        let idx = grp.index as usize;

        // Compute group data size from member channels
        let grp_size: usize = grp
            .channel_indices
            .iter()
            .map(|&ch| *self.channel_sizes.get(&ch).unwrap_or(&0) as usize)
            .sum();

        self.group_sizes.insert(grp.index, grp_size);

        // Set up G message accumulator: add_helper = 9 + grp_size
        // (2 op + 4 tc + 2 idx + grp_size + 1 close)
        self.ensure_accum(0, idx);
        self.gc_data[0][idx].add_helper = 9 + grp_size;

        // Assign group membership to channels
        let mut offset = 6; // initial offset within G message data
        for &ch_idx in &grp.channel_indices {
            if let Some(ch_info) = self.channels.get_mut(&ch_idx) {
                ch_info.group_index = Some(grp.index);
                ch_info.group_offset = Some(offset);
                offset += ch_info.chs.data_size as usize;
            }
        }

        self.groups
            .insert(grp.index, GroupInfo { grp: grp.clone() });
    }

    fn finalize(self) -> Result<ParseResult, Error> {
        // Compute time_offset and last_time
        let mut tc_min_candidates: Vec<i64> = Vec::new();
        let mut tc_max_candidates: Vec<i64> = Vec::new();

        if let Some(to) = self.time_offset {
            tc_min_candidates.push(to);
        }
        if let Some(lt) = self.last_time {
            tc_max_candidates.push(lt);
        }

        // Scan gc_data[0..2] for first/last timecodes
        for cat_idx in 0..3 {
            for acc in &self.gc_data[cat_idx] {
                if !acc.data.is_empty() {
                    let stride = acc.add_helper - if cat_idx < 2 { 3 } else { 8 };
                    if stride == 0 {
                        continue;
                    }
                    // First timecode
                    if acc.data.len() >= 4 {
                        let tc = i32::from_le_bytes([
                            acc.data[0],
                            acc.data[1],
                            acc.data[2],
                            acc.data[3],
                        ]);
                        tc_min_candidates.push(tc as i64);
                    }
                    // Last timecode
                    let n_rows = acc.data.len() / stride;
                    if n_rows > 0 {
                        let last_offset = (n_rows - 1) * stride;
                        if last_offset + 4 <= acc.data.len() {
                            let tc = i32::from_le_bytes([
                                acc.data[last_offset],
                                acc.data[last_offset + 1],
                                acc.data[last_offset + 2],
                                acc.data[last_offset + 3],
                            ]);
                            tc_max_candidates.push(tc as i64);
                        }
                    }
                }
            }
        }

        // gc_data[3] (M messages): timecodes in separate vector
        for acc in &self.gc_data[3] {
            if !acc.timecodes.is_empty() {
                tc_min_candidates.push(acc.timecodes[0] as i64);
                tc_max_candidates.push(*acc.timecodes.last().unwrap_or(&0) as i64);
            }
        }

        // GPS messages: 56-byte records, first 4 bytes = int32 timecode
        if self.gps_data.len() >= 56 {
            let tc = i32::from_le_bytes([
                self.gps_data[0],
                self.gps_data[1],
                self.gps_data[2],
                self.gps_data[3],
            ]);
            tc_min_candidates.push(tc as i64);
            let last_offset = self.gps_data.len() - 56;
            let tc = i32::from_le_bytes([
                self.gps_data[last_offset],
                self.gps_data[last_offset + 1],
                self.gps_data[last_offset + 2],
                self.gps_data[last_offset + 3],
            ]);
            tc_max_candidates.push(tc as i64);
        }

        let time_offset = tc_min_candidates.into_iter().min().unwrap_or(0);
        let last_time = tc_max_candidates.into_iter().max().unwrap_or(0);

        // Now decode channels
        let channel_data =
            decode_all_channels(&self.gc_data, &self.channels, &self.groups, time_offset)?;

        // Extract and process lap info from LAP messages
        let mut diagnostics = self.diagnostics;
        let laps = process_laps(&self.header_messages, time_offset, &mut diagnostics);

        Ok(ParseResult {
            diagnostics,
            channels: self.channels,
            groups: self.groups,
            header_messages: self.header_messages,
            gps_data: self.gps_data,
            gnfi_data: self.gnfi_data,
            time_offset,
            last_time,
            channel_data,
            laps,
            enf_sub_messages: self.enf_sub_messages,
        })
    }
}

/// Fast pre-scan to count data sizes per channel, enabling pre-allocation.
///
/// Processes header messages (CHS/GRP/CNF) to learn channel structure, then
/// counts bytes for each data message type. Over-counts slightly (ignores
/// timecode dedup/overlap) which is intentional — extra capacity is harmless.
fn prescan(data: &[u8], state: &mut ParserState) -> PrescanResult {
    let mut hints = PrescanResult::new();
    let len = data.len();
    let mut pos: usize = 0;

    while pos < len {
        if pos + 3 > len {
            pos += 1;
            continue;
        }

        let op0 = data[pos];
        let op1 = data[pos + 1];

        if op0 == b'(' {
            match op1 {
                b'G' => {
                    if pos + 9 <= len {
                        let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;
                        if index < state.gc_data[0].len() {
                            let total_size = state.gc_data[0][index].add_helper;
                            if total_size > 3
                                && pos + total_size <= len
                                && data[pos + total_size - 1] == b')'
                            {
                                hints.add_data_bytes(0, index, total_size - 3);
                                pos += total_size;
                                continue;
                            }
                        }
                    }
                }
                b'S' => {
                    if pos + 9 <= len {
                        let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;
                        if index < state.gc_data[1].len() {
                            let total_size = state.gc_data[1][index].add_helper;
                            if total_size > 3
                                && pos + total_size <= len
                                && data[pos + total_size - 1] == b')'
                            {
                                hints.add_data_bytes(1, index, total_size - 3);
                                pos += total_size;
                                continue;
                            }
                        }
                    }
                }
                b'M' => {
                    if pos + 11 <= len {
                        let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;
                        let count = u16::from_le_bytes([data[pos + 8], data[pos + 9]]) as usize;
                        if index < state.gc_data[3].len() {
                            let sample_size = state.gc_data[3][index].add_helper;
                            let mms = state.gc_data[3][index].mms;
                            if mms > 0 && sample_size > 0 {
                                let total_size = 10 + sample_size * count + 1;
                                if pos + total_size <= len && data[pos + total_size - 1] == b')' {
                                    hints.add_data_bytes(3, index, sample_size * count);
                                    hints.add_timecodes(index, count);
                                    pos += total_size;
                                    continue;
                                }
                            }
                        }
                    }
                }
                b'c' => {
                    if pos + 7 <= len {
                        let unk1 = data[pos + 2];
                        let channel_field = u16::from_le_bytes([data[pos + 3], data[pos + 4]]);
                        let unk3 = data[pos + 5];
                        let unk4 = data[pos + 6];
                        if unk3 == 0x84 {
                            // V1 prescan (existing): count payload bytes for
                            // the resolved channel_index's gc_data[2] slot.
                            if unk1 == 0 && unk4 == 6 && (channel_field & 7) == 4 {
                                let index = (channel_field >> 3) as usize;
                                if index < state.gc_data[2].len() {
                                    let total_size = state.gc_data[2][index].add_helper;
                                    if total_size > 8
                                        && pos + total_size <= len
                                        && data[pos + total_size - 1] == b')'
                                    {
                                        hints.add_data_bytes(2, index, total_size - 8);
                                        pos += total_size;
                                        continue;
                                    }
                                }
                            }
                            // V2/V3 prescan: we don't know channel_index yet
                            // (resolved after the full scan), so we can't
                            // attribute bytes to a specific gc_data[2] slot.
                            // Just advance past the known-size frame so the
                            // scanner doesn't fall into bad-bytes recovery.
                            // Over/under-allocation of the target slot is
                            // harmless — organic Vec growth covers it.
                            if unk1 == 0 && unk4 == 8 && pos + 16 <= len && data[pos + 15] == b')' {
                                pos += 16;
                                continue;
                            }
                            if unk1 == 1 && unk4 == 2 && pos + 10 <= len && data[pos + 9] == b')' {
                                pos += 10;
                                continue;
                            }
                        }
                    }
                }
                _ => {}
            }
        } else if op0 == b'<' && op1 == b'h' {
            if let Ok((msg, consumed)) = HeaderMessage::parse(data, pos) {
                prescan_header_message(&msg, state, &mut hints);
                pos += consumed;
                continue;
            }
        }

        pos += 1;
    }

    hints
}

/// Handle a header message during prescan: register channels/groups, count GPS/GNFI bytes.
fn prescan_header_message(msg: &HeaderMessage, state: &mut ParserState, hints: &mut PrescanResult) {
    let token = msg.token;

    if token == tokens::gps() || token == tokens::gps1() {
        hints.gps_bytes += msg.payload.len();
        return;
    }
    if token == tokens::gnfi() {
        hints.gnfi_bytes += msg.payload.len();
        return;
    }
    if token == tokens::cnf() {
        // Recursively discover channels from CNF payload.
        let mut sub_state = ParserState::new();
        scan_stream(&msg.payload, &mut sub_state, None);
        for ch_info in sub_state.channels.values() {
            if !state.channels.contains_key(&ch_info.chs.index) {
                state.register_chs(&ch_info.chs);
            }
        }
        for grp_info in sub_state.groups.values() {
            if !state.groups.contains_key(&grp_info.grp.index) {
                state.register_grp(&grp_info.grp);
            }
        }
        return;
    }

    let payload = messages::dispatch_payload(msg);
    match &payload {
        Payload::Chs(chs) => state.register_chs(chs),
        Payload::Grp(grp) => state.register_grp(grp),
        _ => {}
    }
}

/// Print accumulated bad bytes as a diagnostic warning.
fn flush_bad_bytes(
    #[cfg_attr(not(debug_assertions), allow(unused_variables))] data: &[u8],
    bad_pos: usize,
    bad_count: usize,
    diagnostics: &mut crate::diagnostics::Diagnostics,
) {
    if bad_count > 0 {
        debug_assert!(bad_pos + bad_count <= data.len());
        diagnostics.push(crate::diagnostics::Diagnostic::BadBytes {
            offset: bad_pos as u64,
            len: bad_count,
        });
    }
}

/// Scan the byte stream and populate the parser state.
/// Returns the final position after scanning.
fn scan_stream(
    data: &[u8],
    state: &mut ParserState,
    progress: Option<&dyn Fn(usize, usize)>,
) -> usize {
    let len = data.len();
    let mut pos: usize = 0;
    let mut bad_pos: usize = 0;
    let mut bad_count: usize = 0;
    let progress_interval: usize = 8_000_000;
    let mut next_progress: usize = progress_interval;

    while pos < len {
        if pos + 3 > len {
            if bad_count == 0 {
                bad_pos = pos;
            }
            bad_count += 1;
            pos += 1;
            continue;
        }

        let op0 = data[pos];
        let op1 = data[pos + 1];

        // Try to match opcodes
        if op0 == b'(' {
            match op1 {
                b'G' => {
                    // G message: group data
                    if let Some(consumed) = try_parse_g_message(data, pos, state) {
                        flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);
                        bad_count = 0;
                        pos += consumed;
                        continue;
                    }
                }
                b'S' => {
                    // S message: single channel sample
                    if let Some(consumed) = try_parse_s_message(data, pos, state) {
                        flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);
                        bad_count = 0;
                        pos += consumed;
                        continue;
                    }
                }
                b'M' => {
                    // M message: multi-sample burst
                    if let Some(consumed) = try_parse_m_message(data, pos, state) {
                        flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);
                        bad_count = 0;
                        pos += consumed;
                        continue;
                    }
                }
                b'c' => {
                    // c message: expansion device channel
                    if let Some(consumed) = try_parse_c_message(data, pos, state) {
                        flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);
                        bad_count = 0;
                        pos += consumed;
                        continue;
                    }
                }
                _ => {}
            }
        } else if op0 == b'<' && op1 == b'h' {
            // Header message
            if pos >= next_progress {
                next_progress += progress_interval;
                if let Some(ref cb) = progress {
                    cb(pos, len);
                }
            }

            if let Ok((msg, consumed)) = HeaderMessage::parse(data, pos) {
                flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);
                bad_count = 0;
                handle_header_message(&msg, state);
                pos += consumed;
                continue;
            }
        }

        // No match — bad byte
        if bad_count == 0 {
            bad_pos = pos;
        }
        bad_count += 1;
        pos += 1;
    }

    // Flush any remaining bad bytes
    flush_bad_bytes(data, bad_pos, bad_count, &mut state.diagnostics);

    pos
}

/// Handle a parsed header message: dispatch payload and update state.
fn handle_header_message(msg: &HeaderMessage, state: &mut ParserState) {
    let token = msg.token;

    // GPS fast path
    if token == tokens::gps() || token == tokens::gps1() {
        state.gps_data.extend_from_slice(&msg.payload);
        return;
    }

    // GNFI fast path
    if token == tokens::gnfi() {
        state.gnfi_data.extend_from_slice(&msg.payload);
        return;
    }

    // CNF: recursive parse
    if token == tokens::cnf() {
        // Reset last_timecode for all accumulators (timecodes may restart)
        for cat in &mut state.gc_data {
            for acc in cat.iter_mut() {
                acc.last_timecode = -1;
            }
        }

        // Recursively parse the CNF payload
        let mut sub_state = ParserState::new();
        scan_stream(&msg.payload, &mut sub_state, None);

        // Merge CHS and GRP from sub-parse. A later CNF re-definition wins
        // on names (matching the AIM DLL, which exposes the renamed channel
        // carrying the full data stream); other fields and the accumulator
        // layout stay as first registered.
        for (idx, ch_info) in &sub_state.channels {
            if let Some(existing) = state.channels.get_mut(idx) {
                existing.chs.short_name_raw = ch_info.chs.short_name_raw;
                existing.chs.long_name_raw = ch_info.chs.long_name_raw;
            } else {
                state.register_chs(&ch_info.chs);
            }
        }
        for (idx, grp_info) in &sub_state.groups {
            if !state.groups.contains_key(idx) {
                state.register_grp(&grp_info.grp);
            }
        }

        // The Cython parser only extracts CHS/GRP from CNF sub-parses;
        // all other message types (LAP, TRK, etc.) are discarded.
        // Do NOT merge sub-messages into the main header_messages.
        return;
    }

    // ENF: recursive parse — store sub-messages separately per ENF
    if token == tokens::enf() {
        let mut sub_state = ParserState::new();
        scan_stream(&msg.payload, &mut sub_state, None);
        // Store each ENF's sub-messages separately for expansion device extraction
        state.enf_sub_messages.push(sub_state.header_messages);
        return;
    }

    // Dispatch payload for state updates
    let payload = messages::dispatch_payload(msg);

    match &payload {
        Payload::Chs(chs) => {
            state.register_chs(chs);
        }
        Payload::Grp(grp) => {
            state.register_grp(grp);
        }
        Payload::Lap(lap) => {
            // Track time_offset from first LAP message
            // Convert to i64 BEFORE subtraction to avoid u32 underflow
            // (duration can exceed end_time in some LAP records)
            if state.time_offset.is_none() {
                state.time_offset = Some(lap.end_time as i64 - lap.duration as i64);
            }
            state.last_time = Some(lap.end_time as i64);
        }
        Payload::EmbeddedIdn(idn) => {
            // SRC/iSLV messages contain embedded idn data.
            // SRC messages should also store their idn under the 'idn' token
            // to match Cython behavior (used for Logger ID metadata extraction).
            if token == tokens::src() {
                // Create a synthetic idn HeaderMessage
                let synthetic_msg = HeaderMessage {
                    token: tokens::idn(),
                    version: msg.version,
                    payload: msg.payload[6..].to_vec(), // Skip the 6-byte header (idn + ver + len)
                };
                state
                    .header_messages
                    .entry(tokens::idn())
                    .or_default()
                    .push(synthetic_msg);
            }
            // Also store under original token for iSLV extraction
            let _ = idn; // used above
        }
        _ => {}
    }

    // Store message for metadata extraction
    state
        .header_messages
        .entry(token)
        .or_default()
        .push(msg.clone());
}

/// Try to parse a G (group) message at the given position.
/// Returns bytes consumed on success.
fn try_parse_g_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    // (G + timecode(4) + index(2) + data(N) + )
    if pos + 9 > data.len() {
        return None;
    }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;

    if index >= state.gc_data[0].len() {
        return None;
    }

    let total_size = state.gc_data[0][index].add_helper;
    if pos + total_size > data.len() {
        return None;
    }
    if data[pos + total_size - 1] != b')' {
        return None;
    }

    let acc = &mut state.gc_data[0][index];
    if timecode > acc.last_timecode {
        acc.last_timecode = timecode;
        // Append timecode + data (skip opcode 2 bytes, include tc+idx+data)
        acc.data
            .extend_from_slice(&data[pos + 2..pos + total_size - 1]);
    }

    Some(total_size)
}

/// Try to parse an S (single sample) message.
fn try_parse_s_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 9 > data.len() {
        return None;
    }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;

    if index >= state.gc_data[1].len() {
        return None;
    }

    let total_size = state.gc_data[1][index].add_helper;
    if pos + total_size > data.len() {
        return None;
    }
    if data[pos + total_size - 1] != b')' {
        return None;
    }

    let acc = &mut state.gc_data[1][index];
    if timecode > acc.last_timecode {
        acc.last_timecode = timecode;
        acc.data
            .extend_from_slice(&data[pos + 2..pos + total_size - 1]);
    }

    Some(total_size)
}

/// Try to parse an M (multi-sample) message.
fn try_parse_m_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 11 > data.len() {
        return None;
    }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;
    let count = u16::from_le_bytes([data[pos + 8], data[pos + 9]]) as usize;

    if index >= state.gc_data[3].len() {
        return None;
    }

    let sample_size = state.gc_data[3][index].add_helper;
    let mms = state.gc_data[3][index].mms;
    if mms == 0 {
        // Warn about missing sample period (G) — matches Cython error-recovery behavior
        let ch_name = state
            .channels
            .get(&(index as u16))
            .map(|c| c.chs.long_name())
            .unwrap_or_else(|| "unknown".to_string());
        state
            .diagnostics
            .push(crate::diagnostics::Diagnostic::NoSampleInterval { channel: ch_name });
        return None;
    }

    let total_size = 10 + sample_size * count + 1; // header + data + ')'
    if pos + total_size > data.len() {
        return None;
    }
    if data[pos + total_size - 1] != b')' {
        return None;
    }

    let acc = &mut state.gc_data[3][index];

    // Compute how many samples to skip (overlap with already-accepted data)
    let m_skip = if timecode <= acc.last_timecode && mms > 0 {
        ((acc.last_timecode - timecode) as u32 / mms + 1) as usize
    } else {
        0
    };

    if m_skip < count {
        acc.last_timecode = timecode + (count as i32 - 1) * mms as i32;

        // Add timecodes for non-skipped samples
        for m_tc in m_skip..count {
            acc.timecodes.push(timecode + m_tc as i32 * mms as i32);
        }

        // Add data for non-skipped samples
        let data_start = pos + 10 + m_skip * sample_size;
        let data_end = pos + 10 + count * sample_size;
        acc.data.extend_from_slice(&data[data_start..data_end]);
    }

    Some(total_size)
}

/// Try to parse a c (expansion channel) message.
///
/// Three variants coexist on AIM loggers — see spec/docs/unknown_regions.md
/// and spec/xrk_format.py:_resolve_c_variants. Dispatches on (unk1, unk4);
/// V1 writes into gc_data[2] directly, V2/V3 buffer into v2v3_by_cf for
/// post-parse channel_index resolution and tc synthesis.
fn try_parse_c_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 7 > data.len() {
        return None;
    }

    let unk1 = data[pos + 2];
    let channel_field = u16::from_le_bytes([data[pos + 3], data[pos + 4]]);
    let unk3 = data[pos + 5];
    let unk4 = data[pos + 6];
    if unk3 != 0x84 {
        return None;
    }

    match (unk1, unk4) {
        // V1: CHS-sized payload, channel_index = channel_field >> 3.
        (0, 6) => {
            if pos + 12 > data.len() {
                return None;
            }
            if (channel_field & 7) != 4 {
                return None;
            }
            let timecode =
                i32::from_le_bytes([data[pos + 7], data[pos + 8], data[pos + 9], data[pos + 10]]);
            let index = (channel_field >> 3) as usize;
            if index >= state.gc_data[2].len() {
                return None;
            }
            let total_size = state.gc_data[2][index].add_helper;
            if pos + total_size > data.len() {
                return None;
            }
            if data[pos + total_size - 1] != b')' {
                return None;
            }
            let acc = &mut state.gc_data[2][index];
            if timecode > acc.last_timecode {
                acc.last_timecode = timecode;
                acc.data
                    .extend_from_slice(&data[pos + 7..pos + total_size - 1]);
            }
            Some(total_size)
        }
        // V2 long: 16 bytes, two fp16 samples.
        //   base ch_field (low nibble 0/8): samples at (tc, tc-4)
        //   +4 ch_field   (low nibble 4/c): samples at (tc-2, tc-4)
        (0, 8) => {
            if pos + 16 > data.len() || data[pos + 15] != b')' {
                return None;
            }
            let timecode =
                i32::from_le_bytes([data[pos + 7], data[pos + 8], data[pos + 9], data[pos + 10]]);
            let b0 = data[pos + 11..pos + 13].to_vec();
            let b1 = data[pos + 13..pos + 15].to_vec();
            let low = channel_field & 0xF;
            let entries = state.v2v3_by_cf.entry(channel_field).or_default();
            let pos_ctr = state.c_var_pos;
            state.c_var_pos = pos_ctr.wrapping_add(1);
            if low == 0 || low == 8 {
                // V2(base): V2[0]@tc, V2[1]@tc-4, priority 3
                entries.push((timecode, b0, 3, pos_ctr));
                entries.push((timecode - 4, b1, 3, pos_ctr));
                state
                    .last_v2_base_tc
                    .insert(channel_field, (timecode, pos_ctr));
            } else {
                // V2(+4): V2[0]@tc-2, V2[1]@tc-4, priority 1
                entries.push((timecode - 2, b0, 1, pos_ctr));
                entries.push((timecode - 4, b1, 1, pos_ctr));
                state
                    .last_v2_plus4_tc
                    .insert(channel_field, (timecode, pos_ctr));
            }
            Some(16)
        }
        // V3 short: 10 bytes, one fp16 sample, synthesized tc.
        (1, 2) => {
            if pos + 10 > data.len() || data[pos + 9] != b')' {
                return None;
            }
            let b = data[pos + 7..pos + 9].to_vec();
            let base_cf = channel_field ^ 0x4;
            let base_entry = state.last_v2_base_tc.get(&base_cf).copied();
            let plus4_entry = state.last_v2_plus4_tc.get(&channel_field).copied();
            let synth_tc = match (base_entry, plus4_entry) {
                (Some((tc_b, pos_b)), Some((tc_p, pos_p))) => {
                    if pos_b >= pos_p {
                        Some(tc_b - 2)
                    } else {
                        Some(tc_p + 2)
                    }
                }
                (Some((tc_b, _)), None) => Some(tc_b - 2),
                (None, Some((tc_p, _))) => Some(tc_p + 2),
                (None, None) => None,
            };
            if let Some(tc) = synth_tc {
                let entries = state.v2v3_by_cf.entry(channel_field).or_default();
                let pos_ctr = state.c_var_pos;
                entries.push((tc, b, 2, pos_ctr));
            }
            state.c_var_pos = state.c_var_pos.wrapping_add(1);
            Some(10)
        }
        // Unknown variant — return None so the bad-bytes recovery advances
        // one byte at a time. Preserves the "don't silently swallow unknown
        // variants" invariant from the spec.
        _ => None,
    }
}

/// After the main scan completes, resolve V2/V3 channel_fields to channel
/// indices using the algorithm in spec/xrk_format.py:_resolve_c_variants,
/// then migrate buffered samples into gc_data[2] (same normalized layout
/// as V1 rows: tc(4) + data(N)).
fn resolve_c_variants(state: &mut ParserState) {
    if state.v2v3_by_cf.is_empty() {
        return;
    }

    // Phase 1 — partition observed V2/V3 channel_fields into pairs and orphans.
    let cf_set: std::collections::BTreeSet<u16> = state.v2v3_by_cf.keys().copied().collect();
    let mut pairs: Vec<(u16, u16)> = Vec::new();
    let mut orphans: Vec<u16> = Vec::new();
    let mut processed: std::collections::HashSet<u16> = std::collections::HashSet::new();
    for &cf in &cf_set {
        if processed.contains(&cf) {
            continue;
        }
        let low = cf & 0xF;
        if (low == 0x0 || low == 0x8) && cf_set.contains(&(cf | 0x4)) {
            let partner = cf | 0x4;
            pairs.push((cf, partner));
            processed.insert(cf);
            processed.insert(partner);
        } else if (low == 0x4 || low == 0xC) && cf_set.contains(&(cf & !0x4)) {
            // Partner already handled via base pass.
            continue;
        } else {
            orphans.push(cf);
            processed.insert(cf);
        }
    }

    // Phase 2 — identify CHS candidates. Expansion fp16 channels with
    // hw_id != 0; partition by sample period.
    let mut paired_candidates: Vec<((u32, u16), u16)> = Vec::new();
    let mut orphan_candidates: Vec<((u32, u16), u16)> = Vec::new();
    for (&ch_idx, ch_info) in &state.channels {
        let chs = &ch_info.chs;
        if chs.decoder_type != 20 || chs.source_type != 1 || chs.hardware_id == 0 {
            continue;
        }
        let mms = chs.mms();
        let key = (chs.hardware_ref, chs.source_channel_id);
        if mms <= 5 {
            paired_candidates.push((key, ch_idx));
        } else if 5 < mms && mms <= 15 {
            orphan_candidates.push((key, ch_idx));
        }
    }
    paired_candidates.sort();
    orphan_candidates.sort();

    // Phase 3 — build the channel_field → channel_index map.
    let mut expansion_map: HashMap<u16, u16> = HashMap::new();
    for ((base, plus4), (_, ch_idx)) in pairs.iter().zip(paired_candidates.iter()) {
        expansion_map.insert(*base, *ch_idx);
        expansion_map.insert(*plus4, *ch_idx);
    }
    for (cf, (_, ch_idx)) in orphans.iter().zip(orphan_candidates.iter()) {
        expansion_map.insert(*cf, *ch_idx);
    }

    // Phase 4 — per-channel sample collection with priority resolution.
    let mut per_channel: HashMap<u16, HashMap<i32, (Vec<u8>, u8)>> = HashMap::new();
    for (&cf, entries) in &state.v2v3_by_cf {
        let Some(&ch_idx) = expansion_map.get(&cf) else {
            continue;
        };
        let bucket = per_channel.entry(ch_idx).or_default();
        for (tc, bytes, prio, _) in entries {
            let slot = bucket.entry(*tc).or_insert_with(|| (Vec::new(), 0));
            if *prio > slot.1 {
                *slot = (bytes.clone(), *prio);
            }
        }
    }

    // Phase 5 — migrate into gc_data[2] in strictly increasing tc order.
    // Row layout matches V1: 4 bytes tc + N bytes data. add_helper stays
    // at size + 12 (set by register_chs).
    for (ch_idx, samples) in per_channel {
        let idx = ch_idx as usize;
        if idx >= state.gc_data[2].len() {
            continue;
        }
        let mut tcs: Vec<i32> = samples.keys().copied().collect();
        tcs.sort_unstable();
        let acc = &mut state.gc_data[2][idx];
        for tc in tcs {
            if tc <= acc.last_timecode {
                continue;
            }
            acc.last_timecode = tc;
            acc.data.extend_from_slice(&tc.to_le_bytes());
            acc.data.extend_from_slice(&samples[&tc].0);
        }
    }
}

/// Decode all channels from raw accumulated data.
fn decode_all_channels(
    gc_data: &[Vec<Accum>; 4],
    channels: &HashMap<u16, ChannelInfo>,
    groups: &HashMap<u16, GroupInfo>,
    time_offset: i64,
) -> Result<HashMap<u16, ChannelData>, Error> {
    let mut result = HashMap::new();

    for (&ch_idx, ch_info) in channels {
        let decoder_type = ch_info.chs.decoder_type;
        let ch_name = ch_info.chs.long_name();

        // Skip channels without a known decoder
        let sample_size = if crate::decoders::is_manual_decoder(&ch_name) {
            8 // Manual decoders use 8-byte samples (uint64)
        } else if let Some(size) = crate::decoders::sample_byte_size(decoder_type) {
            size as usize
        } else {
            continue;
        };

        // Skip filtered-out channels
        if ch_name == "StrtRec" || ch_name == "Master Clk" {
            continue;
        }

        let is_manual = crate::decoders::is_manual_decoder(&ch_name);

        if let Some(grp_idx) = ch_info.group_index {
            // Channel is part of a group — extract from G message data
            if let Some(grp_info) = groups.get(&grp_idx) {
                let g_idx = grp_idx as usize;
                if g_idx < gc_data[0].len() && !gc_data[0][g_idx].data.is_empty() {
                    let acc = &gc_data[0][g_idx];
                    let stride = acc.add_helper - 3; // subtract opcode overhead
                    if stride == 0 {
                        continue;
                    }
                    let n_rows = acc.data.len() / stride;
                    let grp_offset = ch_info.group_offset.unwrap_or(6);
                    // grp_offset is relative to the stored data (after stripping `(G` opcode):
                    // stored[0..4] = timecode, stored[4..6] = index, stored[6..] = channel data
                    // grp_offset=6 for the first channel = starts at stored byte 6
                    let data_offset_in_stored = grp_offset;

                    let mut timecodes = Vec::with_capacity(n_rows);
                    let mut values =
                        ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

                    for row in 0..n_rows {
                        let row_start = row * stride;
                        // Timecode at start of each row
                        if row_start + 4 > acc.data.len() {
                            break;
                        }
                        let tc = i32::from_le_bytes([
                            acc.data[row_start],
                            acc.data[row_start + 1],
                            acc.data[row_start + 2],
                            acc.data[row_start + 3],
                        ]);
                        timecodes.push(tc as i64 - time_offset);

                        let sample_start = row_start + data_offset_in_stored;
                        if sample_start + sample_size <= acc.data.len() {
                            let sample_data = &acc.data[sample_start..sample_start + sample_size];
                            if let Some(val) = if is_manual {
                                crate::decoders::decode_manual_gear(sample_data)
                            } else {
                                crate::decoders::decode_sample(decoder_type, sample_data)
                            } {
                                values.push(val);
                            } else {
                                values.push_default();
                            }
                        }
                    }

                    // Apply V conversion (mV -> V)
                    if ch_info.chs.units() == "V" {
                        values.apply_mv_to_v();
                    }

                    if !timecodes.is_empty() {
                        let _ = grp_info; // used for validation
                        result.insert(ch_idx, ChannelData { timecodes, values });
                    }
                }
            }
        } else {
            // Non-group channel: try S messages, then c messages, then M messages
            let ch_usize = ch_idx as usize;

            // Check for S/c + M conflict (E)
            let has_sc = (ch_usize < gc_data[1].len() && !gc_data[1][ch_usize].data.is_empty())
                || (ch_usize < gc_data[2].len() && !gc_data[2][ch_usize].data.is_empty());
            let has_m = ch_usize < gc_data[3].len() && !gc_data[3][ch_usize].timecodes.is_empty();
            if has_sc && has_m {
                return Err(Error::InvalidData(format!(
                    "Can't have both S/c and M records for channel {} (index={})",
                    ch_name, ch_usize
                )));
            }

            // Check S messages
            let (view_offset, stride_offset, cat_idx) =
                if ch_usize < gc_data[1].len() && !gc_data[1][ch_usize].data.is_empty() {
                    // S messages: stored = tc(4) + idx(2) + data(N), sample starts at offset 6
                    (6, 3, 1)
                } else if ch_usize < gc_data[2].len() && !gc_data[2][ch_usize].data.is_empty() {
                    // c messages: stored = tc(4) + data(N), sample starts at offset 4
                    (4, 8, 2)
                } else {
                    (0, 0, 255) // sentinel
                };

            if cat_idx < 4 {
                let acc = &gc_data[cat_idx][ch_usize];
                let stride = acc.add_helper - stride_offset;
                if stride == 0 {
                    continue;
                }
                let n_rows = acc.data.len() / stride;

                let mut timecodes = Vec::with_capacity(n_rows);
                let mut values =
                    ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

                for row in 0..n_rows {
                    let row_start = row * stride;
                    if row_start + 4 > acc.data.len() {
                        break;
                    }
                    let tc = i32::from_le_bytes([
                        acc.data[row_start],
                        acc.data[row_start + 1],
                        acc.data[row_start + 2],
                        acc.data[row_start + 3],
                    ]);
                    timecodes.push(tc as i64 - time_offset);

                    let sample_start = row_start + view_offset;
                    if sample_start + sample_size <= acc.data.len() {
                        let sample_data = &acc.data[sample_start..sample_start + sample_size];
                        if let Some(val) = if is_manual {
                            crate::decoders::decode_manual_gear(sample_data)
                        } else {
                            crate::decoders::decode_sample(decoder_type, sample_data)
                        } {
                            values.push(val);
                        } else {
                            values.push_default();
                        }
                    }
                }

                if ch_info.chs.units() == "V" {
                    values.apply_mv_to_v();
                }

                if !timecodes.is_empty() {
                    result.insert(ch_idx, ChannelData { timecodes, values });
                }
            } else {
                // M messages
                if ch_usize < gc_data[3].len() && !gc_data[3][ch_usize].timecodes.is_empty() {
                    let acc = &gc_data[3][ch_usize];
                    let n_rows = acc.timecodes.len();

                    let mut timecodes = Vec::with_capacity(n_rows);
                    let mut values =
                        ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

                    for (i, &tc) in acc.timecodes.iter().enumerate() {
                        timecodes.push(tc as i64 - time_offset);
                        let sample_start = i * sample_size;
                        if sample_start + sample_size <= acc.data.len() {
                            let sample_data = &acc.data[sample_start..sample_start + sample_size];
                            if let Some(val) = if is_manual {
                                crate::decoders::decode_manual_gear(sample_data)
                            } else {
                                crate::decoders::decode_sample(decoder_type, sample_data)
                            } {
                                values.push(val);
                            } else {
                                values.push_default();
                            }
                        }
                    }

                    if ch_info.chs.units() == "V" {
                        values.apply_mv_to_v();
                    }

                    if !timecodes.is_empty() {
                        result.insert(ch_idx, ChannelData { timecodes, values });
                    }
                }
            }
        }
    }

    Ok(result)
}

/// Raw parsed lap data: parallel vectors of (lap_num, start_time, end_time, lap_type).
/// Lap nums are normalized to 0-based indexing.
#[derive(Debug)]
struct RawLapData {
    lap_nums: Vec<i32>,
    start_times: Vec<i64>,
    end_times: Vec<i64>,
    lap_types: Vec<String>,
}

/// Parse LAP messages with segment filtering, dedup, gap-fill, 0-based normalization.
///
/// This is the shared core logic used by both `process_laps` (internal `LapInfo`)
/// and `get_processed_laps` (public `ProcessedLap`).
/// Matches aim_xrk.pyx:_get_laps (LAP message branch).
fn parse_lap_messages(
    header_messages: &HashMap<u32, Vec<HeaderMessage>>,
    time_offset: i64,
) -> Result<RawLapData, Error> {
    let mut lap_nums: Vec<i32> = Vec::new();
    let mut start_times: Vec<i64> = Vec::new();
    let mut end_times: Vec<i64> = Vec::new();
    let mut lap_types: Vec<String> = Vec::new();

    if let Some(lap_msgs) = header_messages.get(&tokens::lap()) {
        for m in lap_msgs {
            let Some(lap) = LapPayload::parse(&m.payload) else {
                continue;
            };

            let end_time = lap.end_time as i64 - time_offset;
            let duration = lap.duration as i64;
            let lap_num = lap.lap_num as i32;

            // Skip non-zero segments
            if lap.segment != 0 {
                continue;
            }

            // Compare with the last lap number we've seen.
            // Using .last() which returns Option — no unwrap needed.
            if let Some(&last_num) = lap_nums.last() {
                if last_num == lap_num {
                    // Duplicate — skip
                    continue;
                } else if last_num + 2 == lap_num {
                    // Gap of 1 — emit inferred lap
                    let inferred_start = end_times.last().copied().unwrap_or(0);
                    lap_nums.push(lap_num - 1);
                    start_times.push(inferred_start);
                    end_times.push(end_time - duration);
                    lap_types.push("full".to_string());
                } else if last_num + 1 != lap_num {
                    // Unexpected gap (> 1 or backwards) (B)
                    return Err(Error::InvalidData(format!(
                        "Lap gap from {} to {}",
                        last_num, lap_num
                    )));
                }
                // last_num + 1 == lap_num: sequential — accept (fall through)
            }
            // else: first lap — accept (fall through)

            lap_nums.push(lap_num);
            start_times.push(end_time - duration);
            end_times.push(end_time);
            lap_types.push("full".to_string());
        }
    }

    // Classify first lap as "out" if it starts at or before time 0
    if !lap_types.is_empty() && start_times[0] <= 0 {
        lap_types[0] = "out".to_string();
    }

    // Normalize to 0-based indexing
    if let Some(&min_lap) = lap_nums.iter().min() {
        for n in &mut lap_nums {
            *n -= min_lap;
        }
    }

    Ok(RawLapData {
        lap_nums,
        start_times,
        end_times,
        lap_types,
    })
}

/// Process LAP messages with segment filtering, dedup, gap-fill, 0-based normalization.
/// Returns internal `LapInfo` structs stored in `ParseResult`.
/// Errors from parse_lap_messages are logged and result in empty laps.
fn process_laps(
    header_messages: &HashMap<u32, Vec<HeaderMessage>>,
    time_offset: i64,
    diagnostics: &mut crate::diagnostics::Diagnostics,
) -> Vec<LapInfo> {
    let raw = match parse_lap_messages(header_messages, time_offset) {
        Ok(r) => r,
        Err(e) => {
            diagnostics.push(crate::diagnostics::Diagnostic::LapParsing { message: e.to_string() });
            return Vec::new();
        }
    };

    raw.lap_nums
        .iter()
        .enumerate()
        .map(|(i, &n)| LapInfo {
            segment: 0,
            lap_num: n as u16,
            duration: (raw.end_times[i] - raw.start_times[i]) as u32,
            end_time: raw.end_times[i] as u32,
        })
        .collect()
}

/// Processed lap info ready for Arrow conversion.
/// Uses i64 for times to match the PyArrow output.
#[derive(Debug, Clone)]
pub struct ProcessedLap {
    pub num: i32,
    pub start_time: i64,
    pub end_time: i64,
    pub lap_type: String,
}

/// Get processed laps from ParseResult (with proper start/end times).
/// Map channel index -> user-facing channel name.
///
/// A CHS table may define the same long_name at several channel indices,
/// each a genuinely distinct channel with its own source_channel_id and
/// data stream. Following the official AIM DLL's convention, the first
/// occurrence keeps the plain name and the k-th occurrence (k >= 2) is
/// exposed as `"<name> dup <k>"` (observed on the issue84 fixture:
/// `Right_Btn_4_led dup 2`). Numbering follows ascending channel index
/// over ALL CHS entries, whether or not they carry data, matching the
/// DLL. Mirrors `channel_display_names()` in spec/xrk_format.py and the
/// Cython backend's rename pass.
///
/// `reserved` holds names already claimed outside the CHS table, counted
/// as occurrence 1: callers pass the 12 synthesized GPS channel names
/// when the file carries GPS data, so a CHS channel colliding with e.g.
/// `GPS_InlineAcc` (observed on SFJ/86) becomes `"GPS_InlineAcc dup 2"`
/// while the synthesized channel keeps its canonical name.
pub fn channel_display_names(
    channels: &HashMap<u16, ChannelInfo>,
    reserved: &[&str],
) -> HashMap<u16, String> {
    let mut idxs: Vec<u16> = channels.keys().copied().collect();
    idxs.sort_unstable();
    let mut seen: HashMap<String, u32> = HashMap::new();
    for name in reserved {
        seen.insert((*name).to_string(), 1);
    }
    let mut out = HashMap::new();
    for idx in idxs {
        let base = channels[&idx].chs.long_name();
        let k = seen
            .entry(base.clone())
            .and_modify(|k| *k += 1)
            .or_insert(1);
        let name = if *k > 1 {
            format!("{} dup {}", base, k)
        } else {
            base
        };
        out.insert(idx, name);
    }
    out
}

pub fn get_processed_laps(result: &ParseResult) -> Result<Vec<ProcessedLap>, Error> {
    let raw = parse_lap_messages(&result.header_messages, result.time_offset)?;

    Ok(raw
        .lap_nums
        .iter()
        .enumerate()
        .map(|(i, &n)| ProcessedLap {
            num: n,
            start_time: raw.start_times[i],
            end_time: raw.end_times[i],
            lap_type: raw.lap_types[i].clone(),
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::messages::{tokdec, HeaderMessage};
    use std::collections::HashMap;

    // ── Header message frame builder ───────────────────────────────────

    /// Build a valid header message frame with correct checksum and framing.
    fn make_header_frame(token_str: &str, version: u8, payload: &[u8]) -> Vec<u8> {
        let wire_token = tokdec(token_str);
        // Pad 3-char tokens with space
        let wire_token = if token_str.len() == 3 {
            wire_token | (0x20 << 24)
        } else {
            wire_token
        };
        let payload_len = payload.len() as i32;
        let checksum: u16 = payload
            .iter()
            .fold(0u16, |acc, &b| acc.wrapping_add(b as u16));

        let mut frame = Vec::new();
        frame.push(0x3C); // '<'
        frame.push(0x68); // 'h'
        frame.extend_from_slice(&wire_token.to_le_bytes());
        frame.extend_from_slice(&payload_len.to_le_bytes());
        frame.push(version);
        frame.push(0x3E); // '>'
        frame.extend_from_slice(payload);
        frame.push(0x3C); // '<'
        frame.extend_from_slice(&wire_token.to_le_bytes());
        frame.extend_from_slice(&checksum.to_le_bytes());
        frame.push(0x3E); // '>'
        frame
    }

    // ── CHS payload builder ────────────────────────────────────────────

    /// Build a 112-byte CHS payload with the given properties.
    fn make_chs_bytes(
        index: u16,
        short_name: &str,
        long_name: &str,
        decoder_type: u8,
        data_size: u8,
        unit_type_byte: u8,
        sample_period_us: u32,
    ) -> Vec<u8> {
        let mut data = vec![0u8; 112];
        data[0..2].copy_from_slice(&index.to_le_bytes());
        data[12] = unit_type_byte;
        data[20] = decoder_type;
        // short_name at [24:32]
        for (i, b) in short_name.bytes().enumerate() {
            if i < 8 {
                data[24 + i] = b;
            }
        }
        // long_name at [32:56]
        for (i, b) in long_name.bytes().enumerate() {
            if i < 24 {
                data[32 + i] = b;
            }
        }
        data[64..68].copy_from_slice(&sample_period_us.to_le_bytes());
        data[72] = data_size;
        data
    }

    // ── LAP payload builder ────────────────────────────────────────────

    /// Build a 20-byte LAP payload.
    fn make_lap_payload(segment: u8, lap_num: u16, duration: u32, end_time: u32) -> Vec<u8> {
        let mut data = vec![0u8; 20];
        data[1] = segment;
        data[2..4].copy_from_slice(&lap_num.to_le_bytes());
        data[4..8].copy_from_slice(&duration.to_le_bytes());
        data[16..20].copy_from_slice(&end_time.to_le_bytes());
        data
    }

    // ── S message builder ──────────────────────────────────────────────

    /// Build an S data message: (S + timecode(4) + index(2) + data(N) + )
    fn make_s_message(timecode: i32, index: u16, sample_data: &[u8]) -> Vec<u8> {
        let mut msg = Vec::new();
        msg.push(b'(');
        msg.push(b'S');
        msg.extend_from_slice(&timecode.to_le_bytes());
        msg.extend_from_slice(&index.to_le_bytes());
        msg.extend_from_slice(sample_data);
        msg.push(b')');
        msg
    }

    // ── M message builder ──────────────────────────────────────────────

    /// Build an M data message: (M + timecode(4) + index(2) + count(2) + data(N*count) + )
    fn make_m_message(timecode: i32, index: u16, count: u16, samples: &[u8]) -> Vec<u8> {
        let mut msg = Vec::new();
        msg.push(b'(');
        msg.push(b'M');
        msg.extend_from_slice(&timecode.to_le_bytes());
        msg.extend_from_slice(&index.to_le_bytes());
        msg.extend_from_slice(&count.to_le_bytes());
        msg.extend_from_slice(samples);
        msg.push(b')');
        msg
    }

    // ── c message builder ──────────────────────────────────────────────

    /// Build a c (expansion) message:
    /// (c + unk1(1) + channel_field(2) + unk3(1) + unk4(1) + timecode(4) + data(N) + )
    fn make_c_message(index: u16, timecode: i32, sample_data: &[u8]) -> Vec<u8> {
        let channel_field = (index << 3) | 4; // lower 3 bits = 0b100
        let mut msg = Vec::new();
        msg.push(b'(');
        msg.push(b'c');
        msg.push(0); // unk1
        msg.extend_from_slice(&channel_field.to_le_bytes());
        msg.push(0x84); // unk3
        msg.push(6); // unk4
        msg.extend_from_slice(&timecode.to_le_bytes());
        msg.extend_from_slice(sample_data);
        msg.push(b')');
        msg
    }

    /// Build a c message with custom field values for validation testing.
    fn make_c_message_raw(
        unk1: u8,
        channel_field: u16,
        unk3: u8,
        unk4: u8,
        timecode: i32,
        sample_data: &[u8],
    ) -> Vec<u8> {
        let mut msg = Vec::new();
        msg.push(b'(');
        msg.push(b'c');
        msg.push(unk1);
        msg.extend_from_slice(&channel_field.to_le_bytes());
        msg.push(unk3);
        msg.push(unk4);
        msg.extend_from_slice(&timecode.to_le_bytes());
        msg.extend_from_slice(sample_data);
        msg.push(b')');
        msg
    }

    // ── GRP payload builder ────────────────────────────────────────────

    fn make_grp_payload(index: u16, channel_indices: &[u16]) -> Vec<u8> {
        let mut data = Vec::new();
        data.extend_from_slice(&index.to_le_bytes());
        data.extend_from_slice(&(channel_indices.len() as u16).to_le_bytes());
        for &ch in channel_indices {
            data.extend_from_slice(&ch.to_le_bytes());
        }
        data
    }

    // ── Build a complete XRK stream from components ────────────────────

    /// Build a minimal XRK stream: CHS header(s) + data messages.
    fn build_xrk_stream(headers: &[Vec<u8>], data_msgs: &[Vec<u8>]) -> Vec<u8> {
        let mut stream = Vec::new();
        for h in headers {
            stream.extend_from_slice(h);
        }
        for d in data_msgs {
            stream.extend_from_slice(d);
        }
        stream
    }

    // ═══════════════════════════════════════════════════════════════════
    // Tests
    // ═══════════════════════════════════════════════════════════════════

    // ── parse_xrk: basic tests ─────────────────────────────────────────

    #[test]
    fn test_parse_xrk_empty() {
        let result = parse_xrk(&[], None);
        assert!(result.is_ok());
        let r = result.unwrap();
        assert!(r.channels.is_empty());
        assert!(r.channel_data.is_empty());
    }

    #[test]
    fn test_parse_xrk_single_chs_no_data() {
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let frame = make_header_frame("CHS", 0, &chs_bytes);
        let result = parse_xrk(&frame, None).unwrap();
        assert_eq!(result.channels.len(), 1);
        assert!(result.channels.contains_key(&0));
        assert!(result.channel_data.is_empty());
    }

    #[test]
    fn test_parse_xrk_s_message_produces_channel_data() {
        // decoder_type=0 → i32, byte_size=4
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let sample = 5000i32.to_le_bytes();
        let s_msg = make_s_message(1000, 0, &sample);
        let stream = build_xrk_stream(&[chs_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(result.channel_data.contains_key(&0));
        let ch = &result.channel_data[&0];
        assert_eq!(ch.timecodes.len(), 1);
        match &ch.values {
            ChannelValues::Int32(v) => {
                assert_eq!(v.len(), 1);
                assert_eq!(v[0], 5000);
            }
            other => panic!("expected Int32, got {:?}", std::mem::discriminant(other)),
        }
    }

    #[test]
    fn test_parse_xrk_multiple_s_messages() {
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let s1 = make_s_message(1000, 0, &1000i32.to_le_bytes());
        let s2 = make_s_message(2000, 0, &2000i32.to_le_bytes());
        let s3 = make_s_message(3000, 0, &3000i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[s1, s2, s3]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        assert_eq!(ch.timecodes.len(), 3);
        match &ch.values {
            ChannelValues::Int32(v) => assert_eq!(v, &[1000, 2000, 3000]),
            _ => panic!("expected Int32"),
        }
    }

    #[test]
    fn test_parse_xrk_s_message_timecode_dedup() {
        // Two S messages with the same timecode: only the first should be accepted
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let s1 = make_s_message(1000, 0, &100i32.to_le_bytes());
        let s2 = make_s_message(1000, 0, &200i32.to_le_bytes()); // dupe timecode
        let s3 = make_s_message(2000, 0, &300i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[s1, s2, s3]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        assert_eq!(ch.timecodes.len(), 2);
    }

    #[test]
    fn test_parse_xrk_m_message_produces_channel_data() {
        // decoder_type=1 → u16, byte_size=2; mms from sample_period_us
        let chs_bytes = make_chs_bytes(0, "TPS", "TPS", 1, 2, 1, 10_000);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        // 3 samples of u16, each 2 bytes
        let mut samples = Vec::new();
        samples.extend_from_slice(&100u16.to_le_bytes());
        samples.extend_from_slice(&200u16.to_le_bytes());
        samples.extend_from_slice(&300u16.to_le_bytes());
        let m_msg = make_m_message(1000, 0, 3, &samples);
        let stream = build_xrk_stream(&[chs_frame], &[m_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        // 3 samples at mms=10: timecodes should be 1000, 1010, 1020 (relative)
        assert_eq!(ch.timecodes.len(), 3);
        match &ch.values {
            ChannelValues::UInt16(v) => assert_eq!(v, &[100, 200, 300]),
            _ => panic!("expected UInt16"),
        }
    }

    #[test]
    fn test_parse_xrk_c_message_produces_channel_data() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let c_msg = make_c_message(0, 1000, &500i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        assert_eq!(ch.timecodes.len(), 1);
        match &ch.values {
            ChannelValues::Int32(v) => assert_eq!(v[0], 500),
            _ => panic!("expected Int32"),
        }
    }

    #[test]
    fn test_parse_xrk_float32_decoder() {
        // decoder_type=6 → f32
        let chs_bytes = make_chs_bytes(0, "TEMP", "Temperature", 6, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let sample = 98.6f32.to_le_bytes();
        let s_msg = make_s_message(1000, 0, &sample);
        let stream = build_xrk_stream(&[chs_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        match &ch.values {
            ChannelValues::Float32(v) => assert!((v[0] - 98.6).abs() < 0.1),
            _ => panic!("expected Float32"),
        }
    }

    #[test]
    fn test_parse_xrk_multiple_channels() {
        let chs0 = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs1 = make_chs_bytes(1, "TPS", "Throttle", 0, 4, 1, 0);
        let f0 = make_header_frame("CHS", 0, &chs0);
        let f1 = make_header_frame("CHS", 0, &chs1);
        let s0 = make_s_message(1000, 0, &5000i32.to_le_bytes());
        let s1 = make_s_message(1000, 1, &50i32.to_le_bytes());
        let stream = build_xrk_stream(&[f0, f1], &[s0, s1]);
        let result = parse_xrk(&stream, None).unwrap();
        assert_eq!(result.channels.len(), 2);
        assert!(result.channel_data.contains_key(&0));
        assert!(result.channel_data.contains_key(&1));
    }

    // ── G (group) messages ─────────────────────────────────────────────

    #[test]
    fn test_parse_xrk_group_message() {
        // Create two channels in a group
        let chs0 = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs1 = make_chs_bytes(1, "TPS", "Throttle", 0, 4, 1, 0);
        let f0 = make_header_frame("CHS", 0, &chs0);
        let f1 = make_header_frame("CHS", 0, &chs1);
        let grp_payload = make_grp_payload(0, &[0, 1]);
        let f_grp = make_header_frame("GRP", 0, &grp_payload);

        // G message: (G + timecode(4) + index(2) + ch0_data(4) + ch1_data(4) + )
        let mut g_msg = Vec::new();
        g_msg.push(b'(');
        g_msg.push(b'G');
        g_msg.extend_from_slice(&1000i32.to_le_bytes()); // timecode
        g_msg.extend_from_slice(&0u16.to_le_bytes()); // group index
        g_msg.extend_from_slice(&5000i32.to_le_bytes()); // ch0 value
        g_msg.extend_from_slice(&50i32.to_le_bytes()); // ch1 value
        g_msg.push(b')');

        let stream = build_xrk_stream(&[f0, f1, f_grp], &[g_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(result.channel_data.contains_key(&0));
        assert!(result.channel_data.contains_key(&1));
        match &result.channel_data[&0].values {
            ChannelValues::Int32(v) => assert_eq!(v[0], 5000),
            _ => panic!("expected Int32 for RPM"),
        }
        match &result.channel_data[&1].values {
            ChannelValues::Int32(v) => assert_eq!(v[0], 50),
            _ => panic!("expected Int32 for TPS"),
        }
    }

    // ── c-message field validation (C) ─────────────────────────────────

    #[test]
    fn test_c_message_valid_fields_accepted() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let c_msg = make_c_message(0, 1000, &100i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(result.channel_data.contains_key(&0));
    }

    #[test]
    fn test_c_message_invalid_unk1_rejected() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let c_msg = make_c_message_raw(1, 4, 0x84, 6, 1000, &100i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        // Invalid c-message should be skipped, no channel data
        assert!(!result.channel_data.contains_key(&0));
    }

    #[test]
    fn test_c_message_invalid_channel_low_bits_rejected() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        // channel_field lower 3 bits = 5 instead of 4
        let c_msg = make_c_message_raw(0, 5, 0x84, 6, 1000, &100i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(!result.channel_data.contains_key(&0));
    }

    #[test]
    fn test_c_message_invalid_unk3_rejected() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let c_msg = make_c_message_raw(0, 4, 0x85, 6, 1000, &100i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(!result.channel_data.contains_key(&0));
    }

    #[test]
    fn test_c_message_invalid_unk4_rejected() {
        let chs_bytes = make_chs_bytes(0, "EGT", "Exhaust Gas", 0, 4, 17, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let c_msg = make_c_message_raw(0, 4, 0x84, 7, 1000, &100i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[c_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert!(!result.channel_data.contains_key(&0));
    }

    // ── LAP message tests (B) ──────────────────────────────────────────

    fn make_lap_header_messages(laps: &[(u8, u16, u32, u32)]) -> HashMap<u32, Vec<HeaderMessage>> {
        let mut map = HashMap::new();
        let lap_token = tokens::lap();
        let msgs: Vec<HeaderMessage> = laps
            .iter()
            .map(|&(segment, lap_num, duration, end_time)| {
                let payload = make_lap_payload(segment, lap_num, duration, end_time);
                HeaderMessage {
                    token: lap_token,
                    version: 0,
                    payload,
                }
            })
            .collect();
        map.insert(lap_token, msgs);
        map
    }

    #[test]
    fn test_lap_sequential_ok() {
        // Three sequential laps: 1, 2, 3
        let msgs = make_lap_header_messages(&[
            (0, 1, 60000, 60000),  // lap 1: 0-60s
            (0, 2, 55000, 115000), // lap 2: 60s-115s
            (0, 3, 58000, 173000), // lap 3: 115s-173s
        ]);
        let raw = parse_lap_messages(&msgs, 0).unwrap();
        assert_eq!(raw.lap_nums.len(), 3);
        // Should be 0-based: [0, 1, 2]
        assert_eq!(raw.lap_nums, vec![0, 1, 2]);
    }

    #[test]
    fn test_lap_gap_of_one_fills() {
        // Laps 1, 3 (gap of 1) — should infer lap 2
        let msgs = make_lap_header_messages(&[
            (0, 1, 60000, 60000),  // lap 1
            (0, 3, 55000, 170000), // lap 3 (gap from 1 to 3 = gap of 1)
        ]);
        let raw = parse_lap_messages(&msgs, 0).unwrap();
        assert_eq!(raw.lap_nums.len(), 3);
        assert_eq!(raw.lap_nums, vec![0, 1, 2]);
    }

    #[test]
    fn test_lap_gap_too_large_returns_error() {
        // Laps 1, 4 — gap of 2, should error
        let msgs = make_lap_header_messages(&[(0, 1, 60000, 60000), (0, 4, 55000, 230000)]);
        let result = parse_lap_messages(&msgs, 0);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("Lap gap"), "error was: {}", err);
    }

    #[test]
    fn test_lap_duplicate_skipped() {
        // Duplicate lap numbers are skipped
        let msgs = make_lap_header_messages(&[
            (0, 1, 60000, 60000),
            (0, 1, 60000, 60000), // duplicate
            (0, 2, 55000, 115000),
        ]);
        let raw = parse_lap_messages(&msgs, 0).unwrap();
        assert_eq!(raw.lap_nums.len(), 2);
    }

    #[test]
    fn test_lap_nonzero_segment_skipped() {
        // Non-zero segments are filtered out
        let msgs = make_lap_header_messages(&[
            (0, 1, 60000, 60000),
            (1, 1, 30000, 30000), // segment 1 — skipped
            (0, 2, 55000, 115000),
        ]);
        let raw = parse_lap_messages(&msgs, 0).unwrap();
        assert_eq!(raw.lap_nums.len(), 2);
    }

    #[test]
    fn test_lap_no_messages() {
        let msgs: HashMap<u32, Vec<HeaderMessage>> = HashMap::new();
        let raw = parse_lap_messages(&msgs, 0).unwrap();
        assert!(raw.lap_nums.is_empty());
    }

    #[test]
    fn test_lap_time_offset_applied() {
        let msgs = make_lap_header_messages(&[(0, 1, 60000, 160000)]);
        let raw = parse_lap_messages(&msgs, 100000).unwrap();
        // end_time = 160000 - 100000 = 60000
        assert_eq!(raw.end_times[0], 60000);
        // start_time = end_time - duration = 60000 - 60000 = 0
        assert_eq!(raw.start_times[0], 0);
    }

    // ── CHS duplicate name check (D) ───────────────────────────────────

    #[test]
    fn test_register_chs_duplicate_same_name_ok() {
        // Re-registering with the same name should not cause issues
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let f1 = make_header_frame("CHS", 0, &chs_bytes);
        let f2 = make_header_frame("CHS", 0, &chs_bytes); // duplicate, same name
        let s_msg = make_s_message(1000, 0, &5000i32.to_le_bytes());
        let stream = build_xrk_stream(&[f1, f2], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert_eq!(result.channels.len(), 1);
        assert!(result.channel_data.contains_key(&0));
    }

    #[test]
    fn test_register_chs_redefinition_last_name_wins() {
        // A later re-definition at the same index wins on names (matching
        // the AIM DLL, which exposes the renamed channel with the full
        // data stream); the data itself is unaffected.
        let chs1 = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs2 = make_chs_bytes(0, "SPD", "Speed", 0, 4, 16, 0);
        let f1 = make_header_frame("CHS", 0, &chs1);
        let f2 = make_header_frame("CHS", 0, &chs2);
        let s_msg = make_s_message(1000, 0, &5000i32.to_le_bytes());
        let stream = build_xrk_stream(&[f1, f2], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        assert_eq!(result.channels[&0].chs.long_name(), "Speed");
        assert!(result.channel_data.contains_key(&0));
    }

    // ── V-unit conversion ──────────────────────────────────────────────

    #[test]
    fn test_parse_xrk_v_unit_conversion() {
        // unit_type_byte=0x95 (0x80|21) → calibrated mV → V → divide by 1000
        // decoder_type=0 → i32 → Float64 for V conversion
        let chs_bytes = make_chs_bytes(0, "BATT", "Battery", 0, 4, 0x95, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let sample = 12500i32.to_le_bytes(); // 12500 mV = 12.5 V
        let s_msg = make_s_message(1000, 0, &sample);
        let stream = build_xrk_stream(&[chs_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        match &ch.values {
            ChannelValues::Float64(v) => {
                assert!((v[0] - 12.5).abs() < 0.001);
            }
            _ => panic!("expected Float64 for V-converted int channel"),
        }
    }

    // ── ChannelValues type selection ───────────────────────────────────

    #[test]
    fn test_channel_values_type_selection() {
        // Normal int32 decoder
        let v = ChannelValues::new(0, "rpm", false, 10);
        assert!(matches!(v, ChannelValues::Int32(_)));

        // uint16 decoder
        let v = ChannelValues::new(1, "%", false, 10);
        assert!(matches!(v, ChannelValues::UInt16(_)));

        // float32 decoder
        let v = ChannelValues::new(6, "C", false, 10);
        assert!(matches!(v, ChannelValues::Float32(_)));

        // uint8 decoder
        let v = ChannelValues::new(13, "", false, 10);
        assert!(matches!(v, ChannelValues::UInt8(_)));

        // Manual gear → UInt32
        let v = ChannelValues::new(0, "", true, 10);
        assert!(matches!(v, ChannelValues::UInt32(_)));

        // V unit + int decoder → Float64
        let v = ChannelValues::new(0, "V", false, 10);
        assert!(matches!(v, ChannelValues::Float64(_)));

        // V unit + float decoder → Float32
        let v = ChannelValues::new(6, "V", false, 10);
        assert!(matches!(v, ChannelValues::Float32(_)));
    }

    #[test]
    fn test_channel_values_push_and_mv_to_v() {
        let mut v = ChannelValues::Float64(Vec::new());
        v.push(crate::decoders::SampleValue::Int32(12500));
        v.apply_mv_to_v();
        match v {
            ChannelValues::Float64(vals) => assert!((vals[0] - 12.5).abs() < 0.001),
            _ => panic!("wrong variant"),
        }
    }

    // ── ProcessedLap integration ───────────────────────────────────────

    #[test]
    fn test_get_processed_laps_from_parse_result() {
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let lap_payload = make_lap_payload(0, 1, 60000, 60000);
        let lap_frame = make_header_frame("LAP", 0, &lap_payload);
        let s_msg = make_s_message(1000, 0, &5000i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame, lap_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let laps = get_processed_laps(&result).unwrap();
        assert_eq!(laps.len(), 1);
        assert_eq!(laps[0].num, 0);
    }

    // ── time_offset computation ────────────────────────────────────────

    #[test]
    fn test_time_offset_from_data_messages() {
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let s1 = make_s_message(5000, 0, &100i32.to_le_bytes());
        let s2 = make_s_message(10000, 0, &200i32.to_le_bytes());
        let stream = build_xrk_stream(&[chs_frame], &[s1, s2]);
        let result = parse_xrk(&stream, None).unwrap();
        // time_offset should be 5000 (minimum timecode)
        assert_eq!(result.time_offset, 5000);
        // channel timecodes should be relative: [0, 5000]
        let ch = &result.channel_data[&0];
        assert_eq!(ch.timecodes[0], 0);
        assert_eq!(ch.timecodes[1], 5000);
    }

    // ── Progress callback ──────────────────────────────────────────────

    #[test]
    fn test_parse_xrk_with_progress_callback() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let count = AtomicUsize::new(0);
        let cb = |_current: usize, _total: usize| {
            count.fetch_add(1, Ordering::Relaxed);
        };
        let chs_bytes = make_chs_bytes(0, "RPM", "Engine RPM", 0, 4, 15, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let stream = build_xrk_stream(&[chs_frame], &[]);
        let _ = parse_xrk(&stream, Some(&cb));
        // Progress callback may or may not fire depending on data size (8MB threshold)
        // Just verify it doesn't crash
    }

    // ── Header message parsing ─────────────────────────────────────────

    #[test]
    fn test_header_message_stored() {
        let lap_payload = make_lap_payload(0, 1, 60000, 60000);
        let lap_frame = make_header_frame("LAP", 0, &lap_payload);
        let result = parse_xrk(&lap_frame, None).unwrap();
        let lap_token = tokens::lap();
        assert!(result.header_messages.contains_key(&lap_token));
        assert_eq!(result.header_messages[&lap_token].len(), 1);
    }

    // ── M-message mms=0 warning (G) ───────────────────────────────────

    #[test]
    fn test_m_message_mms_zero_rejected() {
        // sample_period_us=0 → mms=0 → M messages should be skipped
        let chs_bytes = make_chs_bytes(0, "TPS", "Throttle", 1, 2, 1, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let mut samples = Vec::new();
        samples.extend_from_slice(&100u16.to_le_bytes());
        let m_msg = make_m_message(1000, 0, 1, &samples);
        let stream = build_xrk_stream(&[chs_frame], &[m_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        // M-message should be skipped since mms=0
        assert!(!result.channel_data.contains_key(&0));
    }

    // ── Decoder types ──────────────────────────────────────────────────

    #[test]
    fn test_parse_xrk_uint8_decoder() {
        // decoder_type=13 → u8, byte_size=1
        let chs_bytes = make_chs_bytes(0, "GEAR", "Gear", 13, 1, 31, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let s_msg = make_s_message(1000, 0, &[3]);
        let stream = build_xrk_stream(&[chs_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        match &ch.values {
            ChannelValues::UInt8(v) => assert_eq!(v[0], 3),
            _ => panic!("expected UInt8"),
        }
    }

    #[test]
    fn test_parse_xrk_int16_decoder() {
        // decoder_type=4 → i16, byte_size=2
        let chs_bytes = make_chs_bytes(0, "STEER", "Steering", 4, 2, 4, 0);
        let chs_frame = make_header_frame("CHS", 0, &chs_bytes);
        let sample = (-300i16).to_le_bytes();
        let s_msg = make_s_message(1000, 0, &sample);
        let stream = build_xrk_stream(&[chs_frame], &[s_msg]);
        let result = parse_xrk(&stream, None).unwrap();
        let ch = &result.channel_data[&0];
        match &ch.values {
            ChannelValues::Int16(v) => assert_eq!(v[0], -300),
            _ => panic!("expected Int16"),
        }
    }
}
