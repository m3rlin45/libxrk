//! RACM — Race Mode (string or flag byte).
//!
//! Reference: aim_xrk.pyx:571-583.

use crate::messages::nullterm_string;

/// RACM payload — race mode setting.
#[derive(Debug, Clone)]
pub enum RacmPayload {
    /// String mode: "speed" or "performance"
    Mode(String),
    /// Single-byte flag (expected 0)
    Flag(u8),
}

impl RacmPayload {
    /// Parse a RACM payload from raw bytes.
    pub fn parse(data: &[u8]) -> Self {
        if data.len() > 1 {
            RacmPayload::Mode(nullterm_string(data))
        } else if !data.is_empty() {
            RacmPayload::Flag(data[0])
        } else {
            RacmPayload::Flag(0)
        }
    }
}
