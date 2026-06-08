# xrk-wasm

A tiny [`wasm-bindgen`](https://github.com/rustwasm/wasm-bindgen) wrapper that
exposes libxrk's **pure-Rust** AiM `.xrk`/`.xrz` parser to the browser as a
standalone WebAssembly module — **no Pyodide, no Python runtime**. The whole
module is ~200 KB raw (well under 100 KB gzipped), so a web app can parse AiM
telemetry files entirely client-side.

This is an *additional* build target. It does not change or replace the Python
package or the two Python backends (Cython + PyO3); it just reuses the same
`crates/libxrk` core a second way. The Pyodide wheels (`just pyodide-build`)
remain the way to run the full Python API in the browser — `xrk-wasm` is the
lightweight option when you only need to parse a file into plain channel arrays.

## How it works

The wrapper depends on `crates/libxrk` by path (`default-features = false`, so
Arrow is left out of the wasm) and calls its public API — `read_xrk`,
`decompress_if_zlib`, `parser::ChannelValues`, plus the `XrkFile` /
`GpsDecodeResult` / `Metadata` / `ProcessedLap` types. Because it's a path
dependency, the wasm always tracks the parser in this repo; there is no revision
to pin or bump.

GPS-derived channels (latitude, longitude, speed, lateral/inline acceleration,
yaw rate, …) are merged into the channel list at their native GPS-fix timebase,
mirroring what libxrk's Python layer does, so the JS side can resample them onto
whatever timebase it wants.

## Building

```bash
just wasm-build              # from the repo root
# or directly:
scripts/build-xrk-wasm.sh
```

Requirements (the script bootstraps the wasm-bindgen CLI for you):

- `rustup` with the `wasm32-unknown-unknown` target
- `wasm-bindgen` 0.2.122 (auto-downloaded as a prebuilt binary if missing)
- `wasm-opt` from [binaryen](https://github.com/WebAssembly/binaryen) (optional;
  shrinks the module further)

Artifacts land in `crates/xrk-wasm/pkg/` (git-ignored):

| File | Purpose |
|------|---------|
| `xrk_wasm.js` | ES module loader / glue (`--target web`) |
| `xrk_wasm.d.ts` | TypeScript types |
| `xrk_wasm_bg.wasm` | the compiled parser |
| `xrk_wasm_bg.wasm.d.ts` | wasm import/export types |
| `THIRD-PARTY-NOTICES.txt` | bundled license notices |

This crate is a standalone Cargo workspace (it has its own `[workspace]` table
and is listed under `[workspace].exclude` in the root `Cargo.toml`), so it keeps
its size-optimized release profile and is never touched by `just check`, `cargo
test`, or the Python/Rust CI at the repo root.

## Usage (JavaScript / TypeScript)

```js
import init, { parse_xrk } from "./pkg/xrk_wasm.js";

await init();                                  // load the wasm module once

const buf = await file.arrayBuffer();          // file: a File / Blob
const parsed = parse_xrk(new Uint8Array(buf)); // throws a string on bad input

// parsed = {
//   channels: [{ name, units, interpolate, timecodes: number[], values: number[] }],
//   laps:     [{ num, start, end }],          // milliseconds
//   metadata: { Driver, Vehicle, Venue, ... } // plain object, present keys only
// }
```

`parse_xrk` is synchronous and CPU-bound, so for large files run it inside a Web
Worker to keep the UI responsive. Timecodes are in milliseconds. Each channel
arrives at its native sample rate; `interpolate` is `true` for continuous
signals (linear interpolation on resample) and `false` for discrete/state
signals (forward-fill).

## License

MIT — same as libxrk. See the repository `LICENSE` and the generated
`pkg/THIRD-PARTY-NOTICES.txt` for the full set of bundled-crate notices.
