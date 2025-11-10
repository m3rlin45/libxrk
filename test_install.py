#!/usr/bin/env python3
"""
Test script to verify libxrk installation and functionality.
"""


def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")

    from libxrk.data import AIMXRK, aim_track

    print("✓ Successfully imported AIMXRK and aim_track")

    from libxrk.data import base, gps

    print("✓ Successfully imported base and gps modules")

    # Test base classes
    print(f"✓ Channel class available: {base.Channel}")
    print(f"✓ Lap class available: {base.Lap}")
    print(f"✓ LogFile class available: {base.LogFile}")

    print("\n✅ All imports successful!")
    print("\nYou can now use libxrk to read AIM XRK/XRZ files:")
    print("  from libxrk.data import AIMXRK")
    print("  log = AIMXRK('your_file.xrk', progress=None)")
    print("  channels = log.channels")
    print("  laps = log.laps")


if __name__ == "__main__":
    test_imports()
