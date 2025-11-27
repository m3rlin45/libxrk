/**
 * Run libxrk tests in Pyodide (WebAssembly) environment.
 * 
 * Usage: node scripts/run_pyodide_tests.mjs [--dist-dir=./dist]
 */

import { loadPyodide } from "pyodide";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, "..");

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

  // Copy test directory to Pyodide filesystem
  const testsDir = path.join(projectRoot, "tests");
  console.log(`Copying test files from ${testsDir}...`);
  copyDirToFs(pyodide, testsDir, "/tests");

  console.log("\nRunning tests...\n");
  const testResult = await pyodide.runPythonAsync(`
import sys
import os

# Change to tests directory so relative paths work
os.chdir('/tests')
sys.path.insert(0, '/tests')

import unittest

# Import the existing test modules
from test_86_xrk import Test86XRK
from test_sfj_xrk import TestSFJXRK
from test_get_channels_as_table import TestChannelMerge

# Run the tests
loader = unittest.TestLoader()
suite = unittest.TestSuite()

# Add all test classes
suite.addTests(loader.loadTestsFromTestCase(Test86XRK))
suite.addTests(loader.loadTestsFromTestCase(TestSFJXRK))
suite.addTests(loader.loadTestsFromTestCase(TestChannelMerge))

runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)

# Print summary
print(f"\\n{'='*60}")
print(f"Tests run: {result.testsRun}")
print(f"Failures: {len(result.failures)}")
print(f"Errors: {len(result.errors)}")
print(f"Skipped: {len(result.skipped)}")
print(f"{'='*60}")

# Return exit code
0 if result.wasSuccessful() else 1
`);

  console.log(`\nTest result: ${testResult === 0 ? "PASSED" : "FAILED"}`);
  process.exit(testResult);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
