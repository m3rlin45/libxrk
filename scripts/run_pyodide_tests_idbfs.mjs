/**
 * Test libxrk in Pyodide with IDBFS to simulate JupyterLite's IndexedDB storage.
 * 
 * JupyterLite uses IDBFS (IndexedDB Filesystem) which doesn't support mmap.
 * This test demonstrates the failure and tests potential fixes.
 * 
 * Usage: node scripts/run_pyodide_tests_idbfs.mjs [--dist-dir=./dist]
 */

import { loadPyodide } from "pyodide";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const pyodideTestsDir = path.join(__dirname, "pyodide_tests");

function findWheel(distDir) {
  if (!fs.existsSync(distDir)) {
    throw new Error(`Dist directory not found: ${distDir}`);
  }
  const wheels = fs.readdirSync(distDir).filter((f) => f.endsWith(".whl"));
  const pyodideWheel = wheels.find((w) => w.includes("pyodide"));
  if (pyodideWheel) return path.join(distDir, pyodideWheel);
  const emscriptenWheel = wheels.find((w) => w.includes("emscripten"));
  if (emscriptenWheel) return path.join(distDir, emscriptenWheel);
  throw new Error(`No Pyodide/Emscripten wheel found in ${distDir}`);
}

function parseArgs() {
  const args = { distDir: path.join(projectRoot, "dist") };
  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--dist-dir=")) args.distDir = arg.split("=")[1];
  }
  return args;
}

async function main() {
  const args = parseArgs();

  console.log("Loading Pyodide...");
  const pyodide = await loadPyodide();

  console.log("Loading packages...");
  await pyodide.loadPackage(["numpy", "pyarrow", "micropip"]);

  const wheelPath = findWheel(args.distDir);
  console.log(`Installing wheel: ${wheelPath}`);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`file://${path.resolve(wheelPath)}`);

  // Read test file as bytes (like pyfetch would do in browser)
  const testFile = path.join(projectRoot, "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk");
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

  const testResult = await pyodide.runPythonAsync(`
import sys
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
