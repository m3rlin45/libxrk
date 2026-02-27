fn main() {
    // On macOS, Python C API symbols are resolved at runtime when Python loads
    // the extension. The linker must allow undefined symbols for this to work.
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os == "macos" {
        println!("cargo:rustc-cdylib-link-arg=-undefined");
        println!("cargo:rustc-cdylib-link-arg=dynamic_lookup");
    }
}
