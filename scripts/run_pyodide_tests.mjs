/**
 * Run libxrk tests in Pyodide (WebAssembly) environment.
 *
 * Usage: node scripts/run_pyodide_tests.mjs [--dist-dir=./dist] [--pyodide-version=0.27]
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const pyodideTestsDir = path.join(__dirname, "pyodide_tests");

/**
 * Recursively copy a directory to the Pyodide virtual filesystem.
 */
function copyDirToFs(pyodide, srcDir, dstDir) {
  if (!fs.existsSync(srcDir)) {
    console.log(`Warning: Source directory not found: ${srcDir}`);
    return;
  }

  try {
    pyodide.FS.mkdir(dstDir);
  } catch (e) {
    // Directory might already exist
  }

  const entries = fs.readdirSync(srcDir, { withFileTypes: true });
  for (const entry of entries) {
    const srcPath = path.join(srcDir, entry.name);
    const dstPath = `${dstDir}/${entry.name}`;

    if (entry.isDirectory()) {
      copyDirToFs(pyodide, srcPath, dstPath);
    } else if (entry.isFile()) {
      const data = fs.readFileSync(srcPath);
      pyodide.FS.writeFile(dstPath, data);
    }
  }
}

/**
 * Find the Pyodide-compatible wheel file in the dist directory.
 * @param {string} distDir - Directory containing wheel files
 * @param {string} pyodideVersion - Pyodide version (e.g., "0.27" or "0.29")
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
  const abiYear = pyodideVersion.startsWith("0.27") ? "2024" : "2025";
  const abiTag = `pyodide_${abiYear}`;

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
    pyodideVersion: "0.27",
  };

  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--dist-dir=")) {
      args.distDir = arg.split("=")[1];
    } else if (arg.startsWith("--pyodide-version=")) {
      args.pyodideVersion = arg.split("=")[1];
    }
  }

  return args;
}

/**
 * Dynamically load the appropriate Pyodide module based on version.
 * @param {string} version - Pyodide version (e.g., "0.27" or "0.29")
 */
async function loadPyodideModule(version) {
  const majorMinor = version.substring(0, 4); // "0.27" or "0.29"
  const mod = await import(`pyodide-${majorMinor}`);
  return mod.loadPyodide;
}

async function main() {
  const args = parseArgs();

  console.log(`Loading Pyodide ${args.pyodideVersion}...`);
  const loadPyodide = await loadPyodideModule(args.pyodideVersion);
  const pyodide = await loadPyodide();

  console.log("Loading packages...");
  await pyodide.loadPackage(["numpy", "pyarrow", "micropip"]);

  // Find and install the wheel
  const wheelPath = findWheel(args.distDir, args.pyodideVersion);
  console.log(`Installing wheel: ${wheelPath}`);

  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`file://${path.resolve(wheelPath)}`);

  // Install parameterized for test parameterization
  console.log("Installing parameterized...");
  await micropip.install("parameterized");

  // Copy test directory to Pyodide filesystem
  const testsDir = path.join(projectRoot, "tests");
  console.log(`Copying test files from ${testsDir}...`);
  copyDirToFs(pyodide, testsDir, "/tests");

  // Create pyodide_tests directory and copy Python test runner
  try {
    pyodide.FS.mkdir("/pyodide_tests");
  } catch (e) {
    // Directory might already exist
  }

  console.log("\nRunning tests...\n");

  // Read and execute Python test runner
  const testRunnerPath = path.join(pyodideTestsDir, "run_unit_tests.py");
  const testRunnerCode = fs.readFileSync(testRunnerPath, "utf-8");
  pyodide.FS.writeFile("/pyodide_tests/run_unit_tests.py", testRunnerCode);

  const testResult = await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, '/pyodide_tests')
from run_unit_tests import run_tests
run_tests()
`);

  console.log(`\nTest result: ${testResult === 0 ? "PASSED" : "FAILED"}`);
  process.exit(testResult);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
