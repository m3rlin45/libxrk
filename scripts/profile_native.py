#!/usr/bin/env python3
"""Script for native profiling of Cython code with py-spy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import os

os.chdir(Path(__file__).parent.parent)

from libxrk import aim_xrk

# Profile the large file parsing multiple times
TEST_FILE = "tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"

print(f"Profiling {TEST_FILE}")
print("Running 5 iterations...")

for i in range(5):
    log = aim_xrk(TEST_FILE)
    print(f"  Iteration {i+1}: {len(log.channels)} channels loaded")

print("Done")
