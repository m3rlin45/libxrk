/**
 * Run libxrk tests in Pyodide (WebAssembly) environment simulating JupyterLite.
 * 
 * This test simulates how JupyterLite loads files - by reading file bytes
 * via fetch/pyfetch (which returns bytes, not a file with a file descriptor),
 * then writing to a special filesystem that doesn't support mmap.
 * 
 * In JupyterLite, files from URLs or uploads don't have real file descriptors,
 * so mmap() fails with "OSError: [Errno 43] No such device".
 * 
 * Usage: node scripts/run_pyodide_tests_nodefs.mjs [--dist-dir=./dist]
 */

import { loadPyodide } from "pyodide";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");

/**
 * Find the Pyodide-compatible wheel file in the dist directory.
 */
function findWheel(distDir) {
  if (!fs.existsSync(distDir)) {
    throw new Error(`Dist directory not found: ${distDir}`);
  }

  const wheels = fs.readdirSync(distDir).filter((f) => f.endsWith(".whl"));
  if (wheels.length === 0) {
    throw new Error(`No wheel files found in ${distDir}`);
  }

  // Prefer pyodide wheel, then emscripten wheel
  const pyodideWheel = wheels.find((w) => w.includes("pyodide"));
  if (pyodideWheel) {
    return path.join(distDir, pyodideWheel);
  }

  const emscriptenWheel = wheels.find((w) => w.includes("emscripten"));
  if (emscriptenWheel) {
    return path.join(distDir, emscriptenWheel);
  }

  throw new Error(`No Pyodide/Emscripten wheel found in ${distDir}. Found: ${wheels.join(", ")}`);
}

/**
 * Parse command line arguments.
 */
function parseArgs() {
  const args = {
    distDir: path.join(projectRoot, "dist"),
  };

  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--dist-dir=")) {
      args.distDir = arg.split("=")[1];
    }
  }

  return args;
}

async function main() {
  const args = parseArgs();

  console.log("Loading Pyodide...");
  const pyodide = await loadPyodide();

  console.log("Loading packages...");
  await pyodide.loadPackage(["numpy", "pyarrow", "micropip"]);

  // Find and install the wheel
  const wheelPath = findWheel(args.distDir);
  console.log(`Installing wheel: ${wheelPath}`);

  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`file://${path.resolve(wheelPath)}`);

  // Read the test file as bytes (simulating fetch in browser)
  const testFile = path.join(projectRoot, "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk");
  const fileBuffer = fs.readFileSync(testFile);
  // Convert Node.js Buffer to Uint8Array (which Pyodide understands)
  const fileBytes = new Uint8Array(fileBuffer);
  console.log(`Read test file as bytes: ${fileBytes.length} bytes`);

  // Pass the file bytes to Python
  pyodide.globals.set("js_file_bytes", fileBytes);

  console.log("\nRunning test simulating JupyterLite file loading...\n");
  console.log("In JupyterLite, files loaded via pyfetch or from URLs don't have");
  console.log("real file descriptors, so mmap() fails.\n");

  const testResult = await pyodide.runPythonAsync(`
import sys
import os
import io
import mmap

# Simulate how JupyterLite loads a file:
# 1. Fetch file as bytes (done in JS, passed to Python)
# 2. Either use BytesIO directly, or write to a temp file

# First, let's demonstrate the issue with BytesIO (no file descriptor)
print("Test 1: Using BytesIO directly (no file descriptor)")
print("-" * 50)

file_bytes = bytes(js_file_bytes.to_py())
print(f"Received {len(file_bytes)} bytes from JavaScript")

# BytesIO doesn't have a file descriptor, so mmap fails
bio = io.BytesIO(file_bytes)
try:
    # This will fail because BytesIO has no fileno()
    fd = bio.fileno()
    print(f"Got file descriptor: {fd}")
except io.UnsupportedOperation as e:
    print(f"BytesIO.fileno() failed: {e}")
    print("This is expected - BytesIO has no real file descriptor")

print()
print("Test 2: Writing to MEMFS temp file and using mmap")
print("-" * 50)

# Write bytes to a temp file in MEMFS (Pyodide's in-memory filesystem)
temp_path = "/tmp/test_file.xrk"
with open(temp_path, 'wb') as f:
    f.write(file_bytes)
print(f"Wrote {len(file_bytes)} bytes to {temp_path}")

# Now try to mmap it - this works in MEMFS but not in real browser scenarios
# where files come from IndexedDB or other browser storage
with open(temp_path, 'rb') as f:
    try:
        fd = f.fileno()
        print(f"Got file descriptor: {fd}")
        m = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
        print(f"mmap succeeded! Got {len(m)} bytes")
        m.close()
    except OSError as e:
        print(f"mmap failed: {e}")

print()
print("Test 3: Testing aim_xrk with a file loaded as bytes")
print("-" * 50)
print("This simulates the JupyterLite scenario where files are")
print("loaded via pyfetch and written to MEMFS")
print()

from libxrk import aim_xrk

try:
    log = aim_xrk(temp_path, progress=None)
    print(f"SUCCESS: Loaded {len(log.channels)} channels")
    result = 0
except OSError as e:
    print(f"FAILED with OSError: {e}")
    result = 1
except Exception as e:
    print(f"FAILED with {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    result = 1

# Clean up
os.remove(temp_path)

print()
print("=" * 50)
print("CONCLUSION:")
print("The current implementation works with MEMFS temp files,")
print("but there's a better approach: support loading from bytes")
print("directly without requiring mmap at all.")
print()
print("A bytes-based API would be more JupyterLite-friendly:")
print("  aim_xrk_from_bytes(file_bytes)")
print("=" * 50)

result
`);

  console.log(`\nTest result: ${testResult === 0 ? "PASSED" : "FAILED"}`);
  process.exit(testResult);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
