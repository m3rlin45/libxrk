//! Diagnostic: parse an .xrk and report the GPS timeline health.
//! Usage: cargo run -p libxrk --release --example dump_gps -- <file.xrk>

use libxrk::{decompress_if_zlib, read_xrk};

fn hav(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6_371_000.0_f64;
    let (dlat, dlon) = ((lat2 - lat1).to_radians(), (lon2 - lon1).to_radians());
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    r * 2.0 * a.sqrt().atan2((1.0 - a).sqrt())
}

fn main() {
    let path = std::env::args().nth(1).expect("usage: dump_gps <file.xrk>");
    let bytes = std::fs::read(&path).expect("read file");
    let bytes = decompress_if_zlib(&bytes);
    let f = read_xrk(&bytes).expect("parse");
    let g = f.gps.expect("no GPS in file");

    let n = g.timecodes.len();
    let dur = (g.timecodes[n - 1] - g.timecodes[0]) as f64 / 1000.0;
    let mut non_mono = 0usize;
    let mut dups = 0usize;
    let mut max_gap = 0i64;
    for w in g.timecodes.windows(2) {
        let d = w[1] - w[0];
        if d < 0 {
            non_mono += 1;
        } else if d == 0 {
            dups += 1;
        }
        if d > max_gap {
            max_gap = d;
        }
    }

    let (mut min_lat, mut max_lat) = (f64::MAX, f64::MIN);
    let (mut min_lon, mut max_lon) = (f64::MAX, f64::MIN);
    for i in 0..n {
        min_lat = min_lat.min(g.latitude[i]);
        max_lat = max_lat.max(g.latitude[i]);
        min_lon = min_lon.min(g.longitude[i]);
        max_lon = max_lon.max(g.longitude[i]);
    }

    let mut dist = 0.0f64;
    let mut max_step = 0.0f64;
    let mut max_implied = 0.0f64;
    for i in 1..n {
        let d = hav(
            g.latitude[i - 1],
            g.longitude[i - 1],
            g.latitude[i],
            g.longitude[i],
        );
        dist += d;
        max_step = max_step.max(d);
        let dt = (g.timecodes[i] - g.timecodes[i - 1]) as f64 / 1000.0;
        if dt > 0.0 && dt < 10.0 {
            max_implied = max_implied.max(d / dt);
        }
    }
    let max_speed = g.speed.iter().cloned().fold(0.0f64, f64::max);

    println!("samples:        {n}");
    println!("duration:       {:.1}s", dur);
    println!("non-monotonic:  {non_mono}");
    println!("duplicate t:    {dups}");
    println!("max gap:        {max_gap}ms");
    println!(
        "lat range:      {:.4}..{:.4}  ({:.2} km)",
        min_lat,
        max_lat,
        (max_lat - min_lat) * 111.195
    );
    println!("lon range:      {:.4}..{:.4}", min_lon, max_lon);
    println!("polyline dist:  {:.2} mi", dist / 1609.34);
    println!("max step:       {:.1} m", max_step);
    println!("max implied v:  {:.1} mph", max_implied * 2.23694);
    println!("max GPS speed:  {:.1} mph", max_speed * 2.23694);
    println!("laps reported:  {}", f.laps.len());
    for l in f.laps.iter().take(12) {
        println!(
            "  lap {}: {:.3}s -> {:.3}s ({:.3}s)",
            l.num,
            l.start_time as f64 / 1000.0,
            l.end_time as f64 / 1000.0,
            (l.end_time - l.start_time) as f64 / 1000.0
        );
    }
}
