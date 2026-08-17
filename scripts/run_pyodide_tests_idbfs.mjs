/**
 * Test libxrk in Pyodide with IDBFS to simulate JupyterLite's IndexedDB storage.
 *
 * JupyterLite uses IDBFS (IndexedDB Filesystem) which doesn't support mmap.
 * This test demonstrates the failure and tests potential fixes.
 *
 * Usage: node scripts/run_pyodide_tests_idbfs.mjs [--dist-dir=./dist] [--pyodide-version=0.29]
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const pyodideTestsDir = path.join(__dirname, "pyodide_tests");

// Pyodide runtime version -> wheel ABI tag. One entry per supported runtime.
const ABI_TAG = {
  "0.29": "pyodide_2025",     // pre-PEP-783 tag, GitHub Releases only
  "314": "pyemscripten_2026", // PEP 783 tag (Python 3.14), published to PyPI
};

// "0.29.3" -> "0.29", "314.0.4" -> "314". Keys ABI_TAG and the npm package dir.
function runtimeSeries(version) {
  return version.startsWith("314") ? "314" : version.substring(0, 4);
}

/**
 * Find the Pyodide-compatible wheel file in the dist directory.
 * @param {string} distDir - Directory containing wheel files
 * @param {string} pyodideVersion - Pyodide version (e.g., "0.29")
 */
function findWheel(distDir, pyodideVersion) {
  if (!fs.existsSync(distDir)) {
    throw new Error(`Dist directory not found: ${distDir}`);
  }

  const wheels = fs.readdirSync(distDir).filter((f) => f.endsWith(".whl"));
  if (wheels.length === 0) {
    throw new Error(`No wheel files found in ${distDir}`);
  }

  // Determine ABI tag based on version
  const abiTag = ABI_TAG[runtimeSeries(pyodideVersion)] ?? "pyodide_2025";

  // Find wheel matching the ABI tag
  const matchingWheel = wheels.find((w) => w.includes(abiTag));
  if (matchingWheel) {
    return path.join(distDir, matchingWheel);
  }

  // Fallback: try any pyodide wheel
  const pyodideWheel = wheels.find((w) => w.includes("pyodide"));
  if (pyodideWheel) {
    console.log(`Warning: No wheel found with ABI tag ${abiTag}, using ${pyodideWheel}`);
    return path.join(distDir, pyodideWheel);
  }

  // Fallback: try emscripten wheel
  const emscriptenWheel = wheels.find((w) => w.includes("emscripten"));
  if (emscriptenWheel) {
    console.log(`Warning: No Pyodide wheel found, using ${emscriptenWheel}`);
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
    pyodideVersion: "0.29",
    backend: "",
  };

  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--dist-dir=")) {
      args.distDir = arg.split("=")[1];
    } else if (arg.startsWith("--pyodide-version=")) {
      args.pyodideVersion = arg.split("=")[1];
    } else if (arg.startsWith("--backend=")) {
      args.backend = arg.split("=")[1];
    }
  }

  return args;
}

/**
 * Dynamically load the appropriate Pyodide module based on version.
 * @param {string} version - Pyodide version (e.g., "0.29")
 */
async function loadPyodideModule(version) {
  const mod = await import(`pyodide-${runtimeSeries(version)}`);
  return mod.loadPyodide;
}

async function main() {
  const args = parseArgs();

  console.log(`Loading Pyodide ${args.pyodideVersion}...`);
  const loadPyodide = await loadPyodideModule(args.pyodideVersion);
  const pyodide = await loadPyodide();

  console.log("Loading packages...");
  await pyodide.loadPackage(["numpy", "pyarrow", "micropip"]);

  const wheelPath = findWheel(args.distDir, args.pyodideVersion);
  console.log(`Installing wheel: ${wheelPath}`);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`file://${path.resolve(wheelPath)}`);

  // Read test file as bytes (like pyfetch would do in browser)
  const testFile = path.join(
    projectRoot,
    "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"
  );
  const fileBytes = new Uint8Array(fs.readFileSync(testFile));
  console.log(`Read test file as bytes: ${fileBytes.length} bytes`);
  pyodide.globals.set("js_file_bytes", fileBytes);

  // Create pyodide_tests directory
  try {
    pyodide.FS.mkdir("/pyodide_tests");
  } catch (e) {
    // Directory might already exist
  }

  console.log("\n" + "=".repeat(60));
  console.log("Testing JupyterLite-like scenarios where mmap may not work");
  console.log("=".repeat(60) + "\n");

  // Read and execute Python test runner
  const testRunnerPath = path.join(pyodideTestsDir, "test_bytes_input.py");
  const testRunnerCode = fs.readFileSync(testRunnerPath, "utf-8");
  pyodide.FS.writeFile("/pyodide_tests/test_bytes_input.py", testRunnerCode);

  const backendSetup = args.backend
    ? `import os; os.environ['LIBXRK_BACKEND'] = '${args.backend}'\n`
    : "";

  const testResult = await pyodide.runPythonAsync(`
${backendSetup}import sys
sys.path.insert(0, '/pyodide_tests')
from test_bytes_input import run_bytes_input_tests
run_bytes_input_tests(js_file_bytes)
`);

  console.log(`\nOverall result: ${testResult === 0 ? "ALL PASSED" : "SOME FAILED (expected before fix)"}`);
  process.exit(testResult);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
