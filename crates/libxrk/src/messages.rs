//! Header message framing: parse `<h` ... `>` wrapper with token, checksum, payload.
//!
//! Reference: aim_xrk.pyx:213-226, spec/xrk_format.py:430-455.
//!
//! Wire format:
//!   `<h`          opcode (2 bytes: 0x3C 0x68)
//!   token         uint32 LE (3-char tokens have trailing space 0x20)
//!   payload_len   int32 LE
//!   version       uint8
//!   `>`           close bracket (0x3E)
//!   \[payload\]     payload_len bytes
//!   `<`           footer open (0x3C)
//!   token         uint32 LE (must match header)
//!   checksum      uint16 LE (sum of payload bytes, truncated to u16)
//!   `>`           footer close (0x3E)

use std::io::Cursor;

use binrw::BinRead;

use crate::payloads;

/// Encode a token string to a uint32 LE integer.
/// Matches aim_xrk.pyx:179-181.
pub fn tokdec(s: &str) -> u32 {
    let mut result: u32 = 0;
    for (i, b) in s.bytes().enumerate() {
        result |= (b as u32) << (i * 8);
    }
    result
}

/// Decode a uint32 LE integer to a token string.
/// Matches aim_xrk.pyx:183-188.
pub fn tokenc(mut i: u32) -> String {
    let mut s = String::new();
    while i != 0 {
        s.push((i & 0xFF) as u8 as char);
        i >>= 8;
    }
    s
}

/// Strip trailing space (0x20) padding from 3-char tokens.
pub fn strip_token_padding(wire_token: u32) -> u32 {
    if (wire_token >> 24) == 0x20 {
        wire_token - (0x20 << 24)
    } else {
        wire_token
    }
}

/// Decode a null-terminated ASCII string from bytes.
pub fn nullterm_string(data: &[u8]) -> String {
    let end = data.iter().position(|&b| b == 0).unwrap_or(data.len());
    String::from_utf8_lossy(&data[..end]).into_owned()
}

/// Parsed header message with validated framing.
#[derive(Debug, Clone)]
pub struct HeaderMessage {
    pub token: u32,
    pub version: u8,
    pub payload: Vec<u8>,
}

/// Error during header message parsing.
#[derive(Debug)]
pub enum HeaderError {
    TooShort,
    BadOpcode,
    BadHeaderClose,
    PayloadTooLong,
    BadFooterOpen,
    TokenMismatch,
    ChecksumMismatch,
    BadFooterClose,
}

impl HeaderMessage {
    /// Parse a header message from `data` at the given offset.
    /// Returns (HeaderMessage, bytes_consumed) on success.
    pub fn parse(data: &[u8], offset: usize) -> Result<(Self, usize), HeaderError> {
        let remaining = data.len() - offset;
        if remaining < 20 {
            // Minimum: 2(op) + 4(tok) + 4(len) + 1(ver) + 1(>) + 0(payload) + 1(<) + 4(tok) + 2(sum) + 1(>)
            return Err(HeaderError::TooShort);
        }

        let d = &data[offset..];

        // Opcode: '<h' = 0x3C 0x68
        if d[0] != 0x3C || d[1] != 0x68 {
            return Err(HeaderError::BadOpcode);
        }

        let wire_token = u32::from_le_bytes([d[2], d[3], d[4], d[5]]);
        let payload_len = i32::from_le_bytes([d[6], d[7], d[8], d[9]]) as usize;
        let version = d[10];

        if d[11] != 0x3E {
            return Err(HeaderError::BadHeaderClose);
        }

        let payload_start = 12;
        let payload_end = payload_start + payload_len;

        if payload_end + 8 > remaining {
            return Err(HeaderError::PayloadTooLong);
        }

        // Payload
        let payload = &d[payload_start..payload_end];

        // Checksum: sum of payload bytes truncated to u16 (wrapping is intentional)
        let checksum: u16 = payload
            .iter()
            .fold(0u16, |acc, &b| acc.wrapping_add(b as u16));

        // Footer
        let ftr = &d[payload_end..];
        if ftr[0] != 0x3C {
            return Err(HeaderError::BadFooterOpen);
        }
        let footer_token = u32::from_le_bytes([ftr[1], ftr[2], ftr[3], ftr[4]]);
        let footer_checksum = u16::from_le_bytes([ftr[5], ftr[6]]);
        if ftr[7] != 0x3E {
            return Err(HeaderError::BadFooterClose);
        }

        if footer_token != wire_token {
            return Err(HeaderError::TokenMismatch);
        }
        if footer_checksum != checksum {
            return Err(HeaderError::ChecksumMismatch);
        }

        let token = strip_token_padding(wire_token);
        let total_consumed = payload_end + 8;

        Ok((
            HeaderMessage {
                token,
                version,
                payload: payload.to_vec(),
            },
            total_consumed,
        ))
    }
}

/// Dispatched payload types from header messages.
#[derive(Debug, Clone)]
pub enum Payload {
    Chs(payloads::chs::ChsPayload),
    Grp(payloads::grp::GrpPayload),
    Gps(Vec<u8>),  // Raw 56-byte GPS payload, accumulated separately
    Gnfi(Vec<u8>), // Raw 32-byte GNFI payload, accumulated separately
    Lap(payloads::lap::LapPayload),
    Idn(payloads::idn::IdnPayload),
    Trk(payloads::trk::TrkPayload),
    Gpsr(payloads::gpsr::GpsrPayload),
    Cal(payloads::cal::CalPayload),
    Odo(payloads::odo::OdoPayload),
    EmbeddedIdn(payloads::idn::IdnPayload), // iSLV/SRC
    Racm(payloads::racm::RacmPayload),
    Vet(payloads::vet::VetPayload),
    Cde(Vec<u8>),
    StringMsg(String),
    Cnf(Vec<HeaderMessage>), // Recursive
    Enf(Vec<HeaderMessage>), // Recursive
    Unknown(Vec<u8>),
}

/// Known token constants.
pub mod tokens {
    use super::tokdec;

    pub fn gps() -> u32 {
        tokdec("GPS")
    }
    pub fn gps1() -> u32 {
        tokdec("GPS1")
    }
    pub fn gnfi() -> u32 {
        tokdec("GNFI")
    }
    pub fn chs() -> u32 {
        tokdec("CHS")
    }
    pub fn grp() -> u32 {
        tokdec("GRP")
    }
    pub fn lap() -> u32 {
        tokdec("LAP")
    }
    pub fn idn() -> u32 {
        tokdec("idn")
    }
    pub fn trk() -> u32 {
        tokdec("TRK")
    }
    pub fn gpsr() -> u32 {
        tokdec("GPSR")
    }
    pub fn cal() -> u32 {
        tokdec("CAL")
    }
    pub fn odo() -> u32 {
        tokdec("ODO")
    }
    pub fn cnf() -> u32 {
        tokdec("CNF")
    }
    pub fn enf() -> u32 {
        tokdec("ENF")
    }
    pub fn islv() -> u32 {
        tokdec("iSLV")
    }
    pub fn src() -> u32 {
        tokdec("SRC")
    }
    pub fn racm() -> u32 {
        tokdec("RACM")
    }
    pub fn vet() -> u32 {
        tokdec("VET")
    }
    pub fn cde() -> u32 {
        tokdec("CDE")
    }

    // String message tokens
    pub fn rcr() -> u32 {
        tokdec("RCR")
    }
    pub fn veh() -> u32 {
        tokdec("VEH")
    }
    pub fn cmp() -> u32 {
        tokdec("CMP")
    }
    pub fn vty() -> u32 {
        tokdec("VTY")
    }
    pub fn ndv() -> u32 {
        tokdec("NDV")
    }
    pub fn tmd() -> u32 {
        tokdec("TMD")
    }
    pub fn tmt() -> u32 {
        tokdec("TMT")
    }
    pub fn dbun() -> u32 {
        tokdec("DBUN")
    }
    pub fn dbut() -> u32 {
        tokdec("DBUT")
    }
    pub fn dver() -> u32 {
        tokdec("DVER")
    }
    pub fn manl() -> u32 {
        tokdec("MANL")
    }
    pub fn modl() -> u32 {
        tokdec("MODL")
    }
    pub fn mani() -> u32 {
        tokdec("MANI")
    }
    pub fn modi() -> u32 {
        tokdec("MODI")
    }
    pub fn hwnf() -> u32 {
        tokdec("HWNF")
    }
    pub fn pdlt() -> u32 {
        tokdec("PDLT")
    }
    pub fn nte() -> u32 {
        tokdec("NTE")
    }
    pub fn plm() -> u32 {
        tokdec("+LM")
    }
    pub fn man() -> u32 {
        tokdec("MAN")
    }
    pub fn mod_() -> u32 {
        tokdec("MOD")
    }
}

/// Check if a token is a string message type.
fn is_string_token(token: u32) -> bool {
    token == tokens::rcr()
        || token == tokens::veh()
        || token == tokens::cmp()
        || token == tokens::vty()
        || token == tokens::ndv()
        || token == tokens::tmd()
        || token == tokens::tmt()
        || token == tokens::dbun()
        || token == tokens::dbut()
        || token == tokens::dver()
        || token == tokens::manl()
        || token == tokens::modl()
        || token == tokens::mani()
        || token == tokens::modi()
        || token == tokens::hwnf()
        || token == tokens::pdlt()
        || token == tokens::nte()
        || token == tokens::plm()
        || token == tokens::man()
        || token == tokens::mod_()
}

/// Dispatch a header message payload to the appropriate type.
pub fn dispatch_payload(msg: &HeaderMessage) -> Payload {
    let token = msg.token;
    let data = &msg.payload;

    if token == tokens::gps() || token == tokens::gps1() {
        return Payload::Gps(data.clone());
    }
    if token == tokens::gnfi() {
        return Payload::Gnfi(data.clone());
    }

    if token == tokens::chs() {
        if data.len() >= 112 {
            if let Ok(chs) = payloads::chs::ChsPayload::read(&mut Cursor::new(data)) {
                return Payload::Chs(chs);
            }
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::grp() {
        if data.len() >= 4 {
            if let Ok(grp) = payloads::grp::GrpPayload::parse(data) {
                return Payload::Grp(grp);
            }
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::lap() {
        if data.len() >= 20 {
            if let Ok(lap) = payloads::lap::LapPayload::read(&mut Cursor::new(data)) {
                return Payload::Lap(lap);
            }
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::idn() {
        if data.len() >= 10 {
            return Payload::Idn(payloads::idn::IdnPayload::parse(data));
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::trk() {
        if data.len() >= 44 {
            return Payload::Trk(payloads::trk::TrkPayload::parse(data));
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::gpsr() {
        if data.len() >= 36 {
            return Payload::Gpsr(payloads::gpsr::GpsrPayload::parse(data));
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::cal() {
        if data.len() >= 40 {
            return Payload::Cal(payloads::cal::CalPayload::parse(data));
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::odo() {
        return Payload::Odo(payloads::odo::OdoPayload::parse(data));
    }

    if token == tokens::islv() {
        if data.len() >= 16 && data.starts_with(b"idn") {
            let idn_data = &data[6..];
            if idn_data.len() >= 10 {
                return Payload::EmbeddedIdn(payloads::idn::IdnPayload::parse(idn_data));
            }
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::src() {
        if data.len() >= 62 && data.starts_with(b"idn") {
            let idn_data = &data[6..];
            if idn_data.len() >= 10 {
                return Payload::EmbeddedIdn(payloads::idn::IdnPayload::parse(idn_data));
            }
        }
        return Payload::Unknown(data.clone());
    }

    if token == tokens::racm() {
        return Payload::Racm(payloads::racm::RacmPayload::parse(data));
    }

    if token == tokens::vet() {
        return Payload::Vet(payloads::vet::VetPayload::parse(data));
    }

    if token == tokens::cde() {
        return Payload::Cde(data.clone());
    }

    if is_string_token(token) {
        return Payload::StringMsg(nullterm_string(data));
    }

    // CNF/ENF are handled at the parser level (recursive), not here
    if token == tokens::cnf() || token == tokens::enf() {
        return Payload::Unknown(data.clone());
    }

    Payload::Unknown(data.clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tokdec_tokenc_roundtrip() {
        for s in [
            "GPS", "CHS", "GRP", "LAP", "idn", "GNFI", "CNF", "ENF", "TRK",
        ] {
            let encoded = tokdec(s);
            let decoded = tokenc(encoded);
            assert_eq!(s, decoded, "roundtrip failed for {s}");
        }
    }

    #[test]
    fn test_strip_token_padding() {
        // 3-char token "GPS" gets padded to "GPS " (4th byte = 0x20)
        let padded = tokdec("GPS") | (0x20 << 24);
        assert_eq!(strip_token_padding(padded), tokdec("GPS"));

        // 4-char token "GNFI" should not be stripped
        let four_char = tokdec("GNFI");
        assert_eq!(strip_token_padding(four_char), four_char);
    }

    #[test]
    fn test_nullterm_string() {
        assert_eq!(nullterm_string(b"hello\0world"), "hello");
        assert_eq!(nullterm_string(b"noterm"), "noterm");
        assert_eq!(nullterm_string(b"\0"), "");
    }
}
