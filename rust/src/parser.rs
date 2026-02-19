//! Streaming state machine: scan for opcodes, accumulate channel data.
//!
//! Equivalent to `_decode_sequence()` in aim_xrk.pyx.
//! This is the core parsing engine that processes the XRK binary stream.

use std::collections::HashMap;

use crate::messages::{self, HeaderMessage, Payload, tokens};
use crate::payloads::chs::ChsPayload;
use crate::payloads::grp::GrpPayload;
use crate::payloads::lap::LapPayload;

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
pub fn parse_xrk(data: &[u8], progress: Option<&dyn Fn(usize, usize)>) -> ParseResult {
    let mut state = ParserState::new();

    // Pre-scan: learn channel structure from headers and count data sizes.
    // This allows pre-allocating accumulator Vecs to eliminate realloc churn
    // during the main scan (~22% of pure Rust time was in Vec::grow_amortized).
    let hints = prescan(data, &mut state);
    state.apply_prescan_hints(&hints);

    scan_stream(data, &mut state, progress);
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
    last_time: Option<i64>,
    /// ENF sub-messages: each entry is the sub-parsed messages from one ENF message
    enf_sub_messages: Vec<HashMap<u32, Vec<HeaderMessage>>>,
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
            last_time: None,
            enf_sub_messages: Vec::new(),
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
        self.channels.entry(chs.index).or_insert_with(|| ChannelInfo {
            chs: chs.clone(),
            group_index: None,
            group_offset: None,
        });
    }

    fn register_grp(&mut self, grp: &GrpPayload) {
        let idx = grp.index as usize;

        // Compute group data size from member channels
        let grp_size: usize = grp.channel_indices.iter()
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

        self.groups.insert(grp.index, GroupInfo { grp: grp.clone() });
    }

    fn finalize(self) -> ParseResult {
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
                    if stride == 0 { continue; }
                    // First timecode
                    if acc.data.len() >= 4 {
                        let tc = i32::from_le_bytes([acc.data[0], acc.data[1], acc.data[2], acc.data[3]]);
                        tc_min_candidates.push(tc as i64);
                    }
                    // Last timecode
                    let n_rows = acc.data.len() / stride;
                    if n_rows > 0 {
                        let last_offset = (n_rows - 1) * stride;
                        if last_offset + 4 <= acc.data.len() {
                            let tc = i32::from_le_bytes([
                                acc.data[last_offset], acc.data[last_offset + 1],
                                acc.data[last_offset + 2], acc.data[last_offset + 3],
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
                tc_max_candidates.push(*acc.timecodes.last().unwrap() as i64);
            }
        }

        // GPS messages: 56-byte records, first 4 bytes = int32 timecode
        if self.gps_data.len() >= 56 {
            let tc = i32::from_le_bytes([
                self.gps_data[0], self.gps_data[1], self.gps_data[2], self.gps_data[3],
            ]);
            tc_min_candidates.push(tc as i64);
            let last_offset = self.gps_data.len() - 56;
            let tc = i32::from_le_bytes([
                self.gps_data[last_offset], self.gps_data[last_offset + 1],
                self.gps_data[last_offset + 2], self.gps_data[last_offset + 3],
            ]);
            tc_max_candidates.push(tc as i64);
        }

        let time_offset = tc_min_candidates.into_iter().min().unwrap_or(0);
        let last_time = tc_max_candidates.into_iter().max().unwrap_or(0);

        // Now decode channels
        let channel_data = decode_all_channels(&self.gc_data, &self.channels, &self.groups, time_offset);

        // Extract and process lap info from LAP messages
        let laps = process_laps(&self.header_messages, time_offset);

        ParseResult {
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
        }
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
                            if total_size > 3 && pos + total_size <= len && data[pos + total_size - 1] == b')' {
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
                            if total_size > 3 && pos + total_size <= len && data[pos + total_size - 1] == b')' {
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
                    if pos + 12 <= len {
                        let channel_field = u16::from_le_bytes([data[pos + 3], data[pos + 4]]);
                        let index = (channel_field >> 3) as usize;
                        if index < state.gc_data[2].len() {
                            let total_size = state.gc_data[2][index].add_helper;
                            if total_size > 8 && pos + total_size <= len && data[pos + total_size - 1] == b')' {
                                hints.add_data_bytes(2, index, total_size - 8);
                                pos += total_size;
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
        for (_, ch_info) in &sub_state.channels {
            if !state.channels.contains_key(&ch_info.chs.index) {
                state.register_chs(&ch_info.chs);
            }
        }
        for (_, grp_info) in &sub_state.groups {
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

/// Scan the byte stream and populate the parser state.
fn scan_stream(data: &[u8], state: &mut ParserState, progress: Option<&dyn Fn(usize, usize)>) {
    let len = data.len();
    let mut pos: usize = 0;
    let progress_interval: usize = 8_000_000;
    let mut next_progress: usize = progress_interval;

    while pos < len {
        if pos + 3 > len {
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
                        pos += consumed;
                        continue;
                    }
                }
                b'S' => {
                    // S message: single channel sample
                    if let Some(consumed) = try_parse_s_message(data, pos, state) {
                        pos += consumed;
                        continue;
                    }
                }
                b'M' => {
                    // M message: multi-sample burst
                    if let Some(consumed) = try_parse_m_message(data, pos, state) {
                        pos += consumed;
                        continue;
                    }
                }
                b'c' => {
                    // c message: expansion device channel
                    if let Some(consumed) = try_parse_c_message(data, pos, state) {
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

            match HeaderMessage::parse(data, pos) {
                Ok((msg, consumed)) => {
                    handle_header_message(&msg, state);
                    pos += consumed;
                    continue;
                }
                Err(_) => {}
            }
        }

        // No match — skip byte
        pos += 1;
    }
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

        // Merge CHS and GRP from sub-parse
        for (idx, ch_info) in &sub_state.channels {
            if !state.channels.contains_key(idx) {
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
                state.header_messages.entry(tokens::idn()).or_default().push(synthetic_msg);
            }
            // Also store under original token for iSLV extraction
            let _ = idn; // used above
        }
        _ => {}
    }

    // Store message for metadata extraction
    state.header_messages.entry(token).or_default().push(msg.clone());
}

/// Try to parse a G (group) message at the given position.
/// Returns bytes consumed on success.
fn try_parse_g_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    // (G + timecode(4) + index(2) + data(N) + )
    if pos + 9 > data.len() { return None; }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;

    if index >= state.gc_data[0].len() { return None; }

    let total_size = state.gc_data[0][index].add_helper;
    if pos + total_size > data.len() { return None; }
    if data[pos + total_size - 1] != b')' { return None; }

    let acc = &mut state.gc_data[0][index];
    if timecode > acc.last_timecode {
        acc.last_timecode = timecode;
        // Append timecode + data (skip opcode 2 bytes, include tc+idx+data)
        acc.data.extend_from_slice(&data[pos + 2..pos + total_size - 1]);
    }

    Some(total_size)
}

/// Try to parse an S (single sample) message.
fn try_parse_s_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 9 > data.len() { return None; }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;

    if index >= state.gc_data[1].len() { return None; }

    let total_size = state.gc_data[1][index].add_helper;
    if pos + total_size > data.len() { return None; }
    if data[pos + total_size - 1] != b')' { return None; }

    let acc = &mut state.gc_data[1][index];
    if timecode > acc.last_timecode {
        acc.last_timecode = timecode;
        acc.data.extend_from_slice(&data[pos + 2..pos + total_size - 1]);
    }

    Some(total_size)
}

/// Try to parse an M (multi-sample) message.
fn try_parse_m_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 11 > data.len() { return None; }

    let timecode = i32::from_le_bytes([data[pos + 2], data[pos + 3], data[pos + 4], data[pos + 5]]);
    let index = u16::from_le_bytes([data[pos + 6], data[pos + 7]]) as usize;
    let count = u16::from_le_bytes([data[pos + 8], data[pos + 9]]) as usize;

    if index >= state.gc_data[3].len() { return None; }

    let sample_size = state.gc_data[3][index].add_helper;
    let mms = state.gc_data[3][index].mms;
    if mms == 0 { return None; }

    let total_size = 10 + sample_size * count + 1; // header + data + ')'
    if pos + total_size > data.len() { return None; }
    if data[pos + total_size - 1] != b')' { return None; }

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
fn try_parse_c_message(data: &[u8], pos: usize, state: &mut ParserState) -> Option<usize> {
    if pos + 12 > data.len() { return None; }

    // c message header: (c + unk1(1) + channel_field(2) + unk3(1) + unk4(1) + timecode(4)
    let channel_field = u16::from_le_bytes([data[pos + 3], data[pos + 4]]);
    let timecode = i32::from_le_bytes([data[pos + 7], data[pos + 8], data[pos + 9], data[pos + 10]]);
    let index = (channel_field >> 3) as usize;

    if index >= state.gc_data[2].len() { return None; }

    let total_size = state.gc_data[2][index].add_helper;
    if pos + total_size > data.len() { return None; }
    if data[pos + total_size - 1] != b')' { return None; }

    let acc = &mut state.gc_data[2][index];
    if timecode > acc.last_timecode {
        acc.last_timecode = timecode;
        // For c messages, we store timecode(4) + data (skip the c-specific header)
        acc.data.extend_from_slice(&data[pos + 7..pos + total_size - 1]);
    }

    Some(total_size)
}

/// Decode all channels from raw accumulated data.
fn decode_all_channels(
    gc_data: &[Vec<Accum>; 4],
    channels: &HashMap<u16, ChannelInfo>,
    groups: &HashMap<u16, GroupInfo>,
    time_offset: i64,
) -> HashMap<u16, ChannelData> {
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
                    if stride == 0 { continue; }
                    let n_rows = acc.data.len() / stride;
                    let grp_offset = ch_info.group_offset.unwrap_or(6);
                    // grp_offset is relative to the stored data (after stripping `(G` opcode):
                    // stored[0..4] = timecode, stored[4..6] = index, stored[6..] = channel data
                    // grp_offset=6 for the first channel = starts at stored byte 6
                    let data_offset_in_stored = grp_offset;

                    let mut timecodes = Vec::with_capacity(n_rows);
                    let mut values = ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

                    for row in 0..n_rows {
                        let row_start = row * stride;
                        // Timecode at start of each row
                        if row_start + 4 > acc.data.len() { break; }
                        let tc = i32::from_le_bytes([
                            acc.data[row_start], acc.data[row_start + 1],
                            acc.data[row_start + 2], acc.data[row_start + 3],
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

            // Check S messages
            let (view_offset, stride_offset, cat_idx) = if ch_usize < gc_data[1].len() && !gc_data[1][ch_usize].data.is_empty() {
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
                if stride == 0 { continue; }
                let n_rows = acc.data.len() / stride;

                let mut timecodes = Vec::with_capacity(n_rows);
                let mut values = ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

                for row in 0..n_rows {
                    let row_start = row * stride;
                    if row_start + 4 > acc.data.len() { break; }
                    let tc = i32::from_le_bytes([
                        acc.data[row_start], acc.data[row_start + 1],
                        acc.data[row_start + 2], acc.data[row_start + 3],
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
                    let mut values = ChannelValues::new(decoder_type, ch_info.chs.units(), is_manual, n_rows);

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

    result
}

/// Process LAP messages with segment filtering, dedup, gap-fill, 0-based normalization.
/// Matches aim_xrk.pyx:_get_laps (LAP message branch).
fn process_laps(header_messages: &HashMap<u32, Vec<HeaderMessage>>, time_offset: i64) -> Vec<LapInfo> {
    use std::io::Cursor;
    use binrw::BinRead;

    let mut lap_nums: Vec<i32> = Vec::new();
    let mut start_times: Vec<i64> = Vec::new();
    let mut end_times: Vec<i64> = Vec::new();

    if let Some(lap_msgs) = header_messages.get(&tokens::lap()) {
        for m in lap_msgs {
            if m.payload.len() < 20 { continue; }
            let Ok(lap) = LapPayload::read(&mut Cursor::new(&m.payload)) else { continue };

            let end_time = lap.end_time as i64 - time_offset;
            let duration = lap.duration as i64;
            let lap_num = lap.lap_num as i32;

            // Skip non-zero segments
            if lap.segment != 0 {
                continue;
            }

            if lap_nums.is_empty() {
                // First lap — accept
            } else if *lap_nums.last().unwrap() == lap_num {
                // Duplicate — skip
                continue;
            } else if *lap_nums.last().unwrap() + 1 == lap_num {
                // Sequential — accept
            } else if *lap_nums.last().unwrap() + 2 == lap_num {
                // Gap of 1 — emit inferred lap
                lap_nums.push(lap_num - 1);
                start_times.push(*end_times.last().unwrap());
                end_times.push(end_time - duration);
            }
            // else: unexpected gap, skip silently (Cython asserts but we don't)

            lap_nums.push(lap_num);
            start_times.push(end_time - duration);
            end_times.push(end_time);
        }
    }

    // Normalize to 0-based indexing
    if !lap_nums.is_empty() {
        let min_lap = *lap_nums.iter().min().unwrap();
        for n in &mut lap_nums {
            *n -= min_lap;
        }
    }

    lap_nums.iter().enumerate().map(|(i, &n)| LapInfo {
        segment: 0,
        lap_num: n as u16,
        duration: (end_times[i] - start_times[i]) as u32,
        end_time: end_times[i] as u32,
    }).collect()
}

/// Processed lap info ready for Arrow conversion.
/// Uses i64 for times to match the PyArrow output.
#[derive(Debug, Clone)]
pub struct ProcessedLap {
    pub num: i32,
    pub start_time: i64,
    pub end_time: i64,
}

/// Get processed laps from ParseResult (with proper start/end times).
pub fn get_processed_laps(result: &ParseResult) -> Vec<ProcessedLap> {
    use std::io::Cursor;
    use binrw::BinRead;

    let mut lap_nums: Vec<i32> = Vec::new();
    let mut start_times: Vec<i64> = Vec::new();
    let mut end_times: Vec<i64> = Vec::new();

    if let Some(lap_msgs) = result.header_messages.get(&tokens::lap()) {
        for m in lap_msgs {
            if m.payload.len() < 20 { continue; }
            let Ok(lap) = LapPayload::read(&mut Cursor::new(&m.payload)) else { continue };

            let end_time = lap.end_time as i64 - result.time_offset;
            let duration = lap.duration as i64;
            let lap_num = lap.lap_num as i32;

            if lap.segment != 0 { continue; }

            if lap_nums.is_empty() {
                // accept
            } else if *lap_nums.last().unwrap() == lap_num {
                continue;
            } else if *lap_nums.last().unwrap() + 1 == lap_num {
                // accept
            } else if *lap_nums.last().unwrap() + 2 == lap_num {
                lap_nums.push(lap_num - 1);
                start_times.push(*end_times.last().unwrap());
                end_times.push(end_time - duration);
            }

            lap_nums.push(lap_num);
            start_times.push(end_time - duration);
            end_times.push(end_time);
        }
    }

    // Normalize to 0-based
    if !lap_nums.is_empty() {
        let min_lap = *lap_nums.iter().min().unwrap();
        for n in &mut lap_nums {
            *n -= min_lap;
        }
    }

    lap_nums.iter().enumerate().map(|(i, &n)| ProcessedLap {
        num: n,
        start_time: start_times[i],
        end_time: end_times[i],
    }).collect()
}
