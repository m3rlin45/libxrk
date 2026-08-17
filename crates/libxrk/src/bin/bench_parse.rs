//! Standalone benchmark for Rust parser (no PyO3/Arrow).
//!
//! Usage: `cargo run --profile profiling --bin bench_parse -p libxrk -- <xrk_file> [iterations]`
//! Profiling: `samply record -- cargo run --profile profiling --bin bench_parse -p libxrk -- <xrk_file> 50`

use std::time::Instant;

use libxrk::gps;
use libxrk::parser;

fn decompress_if_zlib(data: Vec<u8>) -> Vec<u8> {
    if data.len() < 2 {
        return data;
    }
    if data[0] == 0x78 && matches!(data[1], 0x01 | 0x9C | 0xDA) {
        use std::io::Read;
        let mut decoder = flate2::read::ZlibDecoder::new(&data[..]);
        let mut decompressed = Vec::new();
        match decoder.read_to_end(&mut decompressed) {
            Ok(_) => decompressed,
            Err(_) => {
                if decompressed.is_empty() {
                    data
                } else {
                    decompressed
                }
            }
        }
    } else {
        data
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: {} <xrk_file> [iterations]", args[0]);
        std::process::exit(1);
    }

    let fname = &args[1];
    let iterations: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(20);

    let raw_data = std::fs::read(fname).expect("Failed to read file");
    let data = decompress_if_zlib(raw_data);
    let file_size_mb = data.len() as f64 / (1024.0 * 1024.0);

    eprintln!(
        "File: {} ({:.1} MB), {} iterations",
        fname, file_size_mb, iterations
    );

    // Warmup
    for _ in 0..2 {
        let result = parser::parse_xrk(&data, None).expect("parse failed");
        let _ = gps::decode_gps(&result.gps_data, result.time_offset);
        std::hint::black_box(&result);
    }

    let mut times = Vec::with_capacity(iterations);
    for _ in 0..iterations {
        let t0 = Instant::now();
        let result = parser::parse_xrk(&data, None).expect("parse failed");
        let _ = gps::decode_gps(&result.gps_data, result.time_offset);
        std::hint::black_box(&result);
        times.push(t0.elapsed().as_secs_f64() * 1000.0);
    }

    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let median = times[times.len() / 2];
    let min = times[0];
    let max = times[times.len() - 1];
    let mean: f64 = times.iter().sum::<f64>() / times.len() as f64;

    eprintln!(
        "Median: {:.1} ms  Mean: {:.1} ms  Min: {:.1} ms  Max: {:.1} ms",
        median, mean, min, max
    );
}
