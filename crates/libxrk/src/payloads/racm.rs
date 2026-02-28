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
            let flag = data[0];
            if flag != 0 {
                eprintln!(
                    "libxrk: unexpected RACM flag {} (expected 0). \
                     Please report at https://github.com/m3rlin45/libxrk/issues",
                    flag
                );
            }
            RacmPayload::Flag(flag)
        } else {
            RacmPayload::Flag(0)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_racm_mode_string() {
        let data = b"speed\0";
        let racm = RacmPayload::parse(data);
        match racm {
            RacmPayload::Mode(s) => assert_eq!(s, "speed"),
            _ => panic!("expected Mode variant"),
        }
    }

    #[test]
    fn test_racm_mode_performance() {
        let data = b"performance\0";
        let racm = RacmPayload::parse(data);
        match racm {
            RacmPayload::Mode(s) => assert_eq!(s, "performance"),
            _ => panic!("expected Mode variant"),
        }
    }

    #[test]
    fn test_racm_flag_zero() {
        let data = [0u8];
        let racm = RacmPayload::parse(&data);
        match racm {
            RacmPayload::Flag(0) => {}
            _ => panic!("expected Flag(0)"),
        }
    }

    #[test]
    fn test_racm_empty() {
        let data: [u8; 0] = [];
        let racm = RacmPayload::parse(&data);
        match racm {
            RacmPayload::Flag(0) => {}
            _ => panic!("expected Flag(0) for empty input"),
        }
    }
}
