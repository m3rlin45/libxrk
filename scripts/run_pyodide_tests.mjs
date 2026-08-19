/**
 * Run libxrk tests in Pyodide (WebAssembly) environment.
 *
 * Usage: node scripts/run_pyodide_tests.mjs [--dist-dir=./dist] [--backend=rust]
 */

import { loadPyodide } from "pyodide";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");
const pyodideTestsDir = path.join(__dirname, "pyodide_tests");

// Wheel ABI tag for the supported runtime (PEP 783, Python 3.14).
const ABI_TAG = "pyemscripten_2026";

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
 */
function findWheel(distDir) {
  if (!fs.existsSync(distDir)) {
    throw new Error(`Dist directory not found: ${distDir}`);
  }

  const wheels = fs.readdirSync(distDir).filter((f) => f.endsWith(".whl"));
  if (wheels.length === 0) {
    throw new Error(`No wheel files found in ${distDir}`);
  }

  const matchingWheel = wheels.find((w) => w.includes(ABI_TAG));
  if (matchingWheel) {
    return path.join(distDir, matchingWheel);
  }

  // Fallback: try any emscripten wheel
  const emscriptenWheel = wheels.find((w) => w.includes("emscripten"));
  if (emscriptenWheel) {
    console.log(`Warning: No wheel found with ABI tag ${ABI_TAG}, using ${emscriptenWheel}`);
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
    backend: "",
  };

  for (const arg of process.argv.slice(2)) {
    if (arg.startsWith("--dist-dir=")) {
      args.distDir = arg.split("=")[1];
    } else if (arg.startsWith("--backend=")) {
      args.backend = arg.split("=")[1];
    }
  }

  return args;
}

// Test classes to run. Each gets a FRESH Pyodide instance: the wasm32 heap
// only grows, and repeatedly parsing the large fixtures fragments it into the
// 4GB address-space limit if all classes share one interpreter.
const TEST_CLASSES = ["Test86XRK", "TestSFJXRK", "TestChannelMerge"];

/** Run one test class in its own Pyodide instance. Returns its exit code. */
async function runClass(args, className) {
  const pyodide = await loadPyodide();
  await pyodide.loadPackage(["numpy", "pyarrow", "micropip"]);

  const wheelPath = findWheel(args.distDir);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(`file://${path.resolve(wheelPath)}`);
  await micropip.install("parameterized");

  copyDirToFs(pyodide, path.join(projectRoot, "tests"), "/tests");
  try {
    pyodide.FS.mkdir("/pyodide_tests");
  } catch {
    // already exists
  }
  const testRunnerPath = path.join(pyodideTestsDir, "run_unit_tests.py");
  pyodide.FS.writeFile("/pyodide_tests/run_unit_tests.py", fs.readFileSync(testRunnerPath, "utf-8"));

  const backendSetup = args.backend
    ? `import os; os.environ['LIBXRK_BACKEND'] = '${args.backend}'\n`
    : "";

  return await pyodide.runPythonAsync(`
${backendSetup}import sys
sys.path.insert(0, '/pyodide_tests')
from run_unit_tests import run_tests
run_tests(only=${JSON.stringify(className)})
`);
}

async function main() {
  const args = parseArgs();
  console.log(`Pyodide${args.backend ? ` (backend: ${args.backend})` : ""}`);

  let failed = 0;
  for (const className of TEST_CLASSES) {
    console.log(`\n=== ${className} (fresh interpreter) ===`);
    const rc = await runClass(args, className);
    if (rc !== 0) failed++;
  }

  console.log(`\nTest result: ${failed === 0 ? "PASSED" : "FAILED"}`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
