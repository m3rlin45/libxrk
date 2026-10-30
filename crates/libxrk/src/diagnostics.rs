//! What the parser noticed while reading a file.
//!
//! The parser is fault-tolerant by design: a corrupt or truncated file still
//! yields whatever could be recovered. Until now it reported what it had to
//! skip by writing to stderr, which makes the information unusable to a
//! caller: a library embedded in an app, a notebook, or a WebAssembly module
//! has nowhere to put those lines, and cannot act on them either.
//!
//! Diagnostics are therefore collected and returned with the file. Nothing is
//! printed.
//!
//! ## Volume
//!
//! These events are not rare. A healthy 5 MB MyChron 6 log produces 56 480
//! "bad bytes" events and 30 058 channels with no usable sample interval —
//! 86 540 lines, 9.4 MB of text, for one file. Keeping every one of them would
//! trade a stderr flood for a memory flood, so the collection is capped and
//! counts what it drops. The totals stay exact.

use std::fmt;

/// Something the parser had to work around.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Diagnostic {
    /// A run of bytes matched no known message; the parser resynchronised past
    /// them. `offset` is where the run starts in the decompressed stream.
    BadBytes { offset: u64, len: usize },
    /// A channel declared a unit code the decoder does not know. The channel
    /// is decoded, its values are fine, it just comes out without a unit.
    UnknownUnit { code: u8, channel: String },
    /// Padding bytes that are always zero in known files were not zero.
    /// Harmless in itself, but it means this file uses the format in a way we
    /// have not seen — worth reporting upstream.
    UnexpectedChsPadding { channel: String, details: String },
    /// No sample interval could be derived for a channel's data block, so the
    /// block was skipped.
    NoSampleInterval { channel: String },
    /// The LAP messages could not be parsed; lap boundaries fall back to GPS
    /// detection.
    LapParsing { message: String },
}

impl fmt::Display for Diagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Diagnostic::BadBytes { offset, len } => {
                write!(f, "{len} unrecognised byte(s) at 0x{offset:x}, skipped")
            }
            Diagnostic::UnknownUnit { code, channel } => {
                write!(f, "unknown unit code {code} for channel {channel}")
            }
            Diagnostic::UnexpectedChsPadding { channel, details } => write!(
                f,
                "CHS padding non-zero for channel {channel}: {details}. \
                 Please report at https://github.com/m3rlin45/libxrk/issues with your XRK file."
            ),
            Diagnostic::NoSampleInterval { channel } => {
                write!(f, "no sample interval understood for channel {channel}")
            }
            Diagnostic::LapParsing { message } => write!(f, "lap parsing error: {message}"),
        }
    }
}

/// How many individual diagnostics are kept before only counting them.
///
/// Enough to see the shape of a damaged file, small enough that a file made of
/// noise cannot make the parser allocate without bound.
pub const MAX_KEPT: usize = 256;

/// Everything the parser noticed, with exact totals.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Diagnostics {
    /// The first [`MAX_KEPT`] diagnostics, in the order they occurred.
    pub kept: Vec<Diagnostic>,
    /// How many diagnostics were not kept. `kept.len() + dropped` is the true
    /// total.
    pub dropped: usize,
    /// Total bytes the parser had to skip, across every `BadBytes` event —
    /// counted in full, whether or not the event was kept.
    ///
    /// Exact, and identical between the two backends: both report 1 731 043
    /// bytes on the 5.2 MB MyChron 6 sample used to test this change.
    ///
    /// It is **not** a corruption threshold, and should not be read as one. A
    /// perfectly healthy log can legitimately skip a third of its bytes — that
    /// MyChron 6 file skips 33.1 % — while another logger skips none at all
    /// (`test.xrk` and `badGPSdata.xrk`: 0 %). What carries meaning is a change
    /// on the *same* file: flipping one byte at offset 1000 of that sample
    /// takes it from 33.1 % to 67.3 %, and from 29 channels to 12.
    pub bad_bytes: usize,
}

impl Diagnostics {
    pub fn push(&mut self, d: Diagnostic) {
        if let Diagnostic::BadBytes { len, .. } = &d {
            self.bad_bytes += len;
        }
        if self.kept.len() < MAX_KEPT {
            self.kept.push(d);
        } else {
            self.dropped += 1;
        }
    }

    /// Total number of diagnostics, kept or not.
    pub fn total(&self) -> usize {
        self.kept.len() + self.dropped
    }

    pub fn is_empty(&self) -> bool {
        self.total() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn totals_stay_exact_past_the_cap() {
        let mut d = Diagnostics::default();
        for i in 0..MAX_KEPT + 50 {
            d.push(Diagnostic::BadBytes { offset: i as u64, len: 3 });
        }
        assert_eq!(d.kept.len(), MAX_KEPT);
        assert_eq!(d.dropped, 50);
        assert_eq!(d.total(), MAX_KEPT + 50);
        assert_eq!(d.bad_bytes, 3 * (MAX_KEPT + 50), "the byte total ignores the cap");
    }

    #[test]
    fn a_clean_parse_says_nothing() {
        let d = Diagnostics::default();
        assert!(d.is_empty());
        assert_eq!(d.bad_bytes, 0);
    }

    #[test]
    fn messages_name_the_channel_and_the_offset() {
        assert_eq!(
            Diagnostic::BadBytes { offset: 0x4fd914, len: 217 }.to_string(),
            "217 unrecognised byte(s) at 0x4fd914, skipped"
        );
        assert_eq!(
            Diagnostic::NoSampleInterval { channel: "RPM".into() }.to_string(),
            "no sample interval understood for channel RPM"
        );
    }
}
