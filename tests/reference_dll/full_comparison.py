#!/usr/bin/env python
"""
Full comparison of DLL vs libxrk lap times.
Run from project root.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from libxrk import aim_xrk


def get_dll_laps(xrk_path: Path) -> list[tuple[int, int, int, int]]:
    """Get lap times from DLL via Wine subprocess."""
    wine_script = Path(__file__).parent / "wine_compare.py"
    wine_path = f"Z:{xrk_path.absolute()}"

    # Wine Python installed by setup_dll_comparison.sh
    setup_dir = Path(__file__).parent / ".setup"
    wine_python = str(setup_dir / "python-embed" / "python.exe")

    result = subprocess.run(
        [
            "/usr/bin/wine",
            wine_python,
            f"Z:{wine_script.absolute()}",
            wine_path,
        ],
        capture_output=True,
        text=True,
        env={"WINEDEBUG": "-all"},
    )

    laps = []
    for line in result.stdout.split("\n"):
        line = line.strip()
        if line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 4:
                lap_num = int(parts[0])
                start_ms = int(parts[1])
                duration_ms = int(parts[2])
                end_ms = int(parts[3])
                laps.append((lap_num, start_ms, end_ms, duration_ms))

    return laps


def get_libxrk_laps(xrk_path: Path) -> list[tuple[int, int, int, int]]:
    """Get lap times from libxrk."""
    log = aim_xrk(str(xrk_path))
    laps = []

    lap_nums = log.laps.column("num").to_pylist()
    start_times = log.laps.column("start_time").to_pylist()
    end_times = log.laps.column("end_time").to_pylist()

    for lap_num, start, end in zip(lap_nums, start_times, end_times):
        laps.append((lap_num, start, end, end - start))

    return laps


def compare_file(xrk_path: Path):
    """Compare a single file."""
    print(f"\n{'=' * 80}")
    print(f"File: {xrk_path}")
    print("=" * 80)

    dll_laps = get_dll_laps(xrk_path)
    libxrk_laps = get_libxrk_laps(xrk_path)

    print(f"\nDLL: {len(dll_laps)} laps, libxrk: {len(libxrk_laps)} laps")

    if len(dll_laps) != len(libxrk_laps):
        print("*** LAP COUNT MISMATCH ***")

    # Header
    print(
        f"\n{'Lap':>4}  {'DLL Start':>10}  {'lib Start':>10}  {'Δ Start':>8}  "
        f"{'DLL Dur':>10}  {'lib Dur':>10}  {'Δ Dur':>8}"
    )
    print("-" * 80)

    # Compare lap by lap (aligned by position, not lap number)
    max_laps = max(len(dll_laps), len(libxrk_laps))

    for i in range(max_laps):
        dll = dll_laps[i] if i < len(dll_laps) else None
        lib = libxrk_laps[i] if i < len(libxrk_laps) else None

        if dll and lib:
            start_diff = lib[1] - dll[1]
            dur_diff = lib[3] - dll[3]
            print(
                f"{i:>4}  {dll[1]:>10}  {lib[1]:>10}  {start_diff:>+8}  "
                f"{dll[3]:>10}  {lib[3]:>10}  {dur_diff:>+8}"
            )
        elif dll:
            print(
                f"{i:>4}  {dll[1]:>10}  {'(none)':>10}  {'---':>8}  {dll[3]:>10}  {'(none)':>10}  {'---':>8}"
            )
        elif lib:
            print(
                f"{i:>4}  {'(none)':>10}  {lib[1]:>10}  {'---':>8}  {'(none)':>10}  {lib[3]:>10}  {'---':>8}"
            )


def main():
    test_files = [
        Path("tests/test_data/aim_official/test.xrk"),
        Path("tests/test_data/SFJ/CMD_SFJ_Fuji GP Sh_Generic testing_a_0033.xrk"),
        Path("tests/test_data/86/CMD_Inferno 86_Fuji GP Sh_Generic testing_a_2248.xrk"),
    ]

    for f in test_files:
        if f.exists():
            compare_file(f)


if __name__ == "__main__":
    main()
