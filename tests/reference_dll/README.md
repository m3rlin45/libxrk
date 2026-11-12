# AIM Official DLL Reference Wrapper

Tools to compare libxrk lap times against the official AIM MatLabXRK DLL.

**Note:** The DLL is proprietary AIM software and is NOT included in this repository.

## Quick Start

### 1. Install Wine (if not already installed)

```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y wine64

# Fedora
sudo dnf install wine

# macOS (not recommended - use a Linux VM)
brew install --cask wine-stable
```

### 2. Run the Setup Script

```bash
cd tests/reference_dll
./setup_dll_comparison.sh
```

This script will:
- Verify Wine is installed
- Download the DLL from https://github.com/laz-/xrk
- Set up Windows Python (embeddable) for Wine
- Test that everything works

### 3. Run the Comparison

```bash
# From project root
poetry run python tests/reference_dll/full_comparison.py
```

## Manual Setup

If you prefer to set things up manually:

### Download the DLL

Option A - Clone the xrk repo:
```bash
git clone --depth 1 https://github.com/laz-/xrk.git /tmp/xrk
cp /tmp/xrk/MatLabXRK-2017-64-ReleaseU.dll tests/reference_dll/
```

Option B - Download from AIM (if available):
- Documentation: https://www.aim-sportline.com/download/software/doc/how-to-access-xrk-files-data-without-aim-software_101.pdf

### Setup Windows Python for Wine

```bash
cd /tmp
curl -L -o python-3.12.0-embed-amd64.zip \
  "https://www.python.org/ftp/python/3.12.0/python-3.12.0-embed-amd64.zip"
mkdir -p python-embed
python3 -c "import zipfile; zipfile.ZipFile('python-3.12.0-embed-amd64.zip').extractall('python-embed')"
```

### Run Comparison Manually

```bash
WINEDEBUG=-all wine /tmp/python-embed/python.exe \
  Z:/path/to/libxrk/tests/reference_dll/wine_compare.py \
  Z:/tmp/dll-scratch/test.xrk
```

Give the DLL a copy of the file under an ASCII path in a scratch directory
(here `cp tests/test_data/aim_official/test.xrk /tmp/dll-scratch/`), not the
original:

- The DLL cannot open a non-ASCII path, such as a RaceStudio export with a
  Japanese session name. Its `open_file` takes an ANSI string, and Wine mangles
  non-ASCII command-line arguments outside a UTF-8 locale.
- The DLL writes next to its input: an `.xrz` gets a decompressed sibling
  `.xrk`, and `<name>.NNN.tmp` scratch files are left behind.

The spec tests do this for every DLL call (`_run_dll_script` in
`spec/tests/conftest.py`). The comparison scripts in this directory pass the
path they are given straight to Wine.

## Files

| File | Description |
|------|-------------|
| `setup_dll_comparison.sh` | Automated setup script |
| `wine_compare.py` | Standalone script for Wine Python (no dependencies) |
| `full_comparison.py` | Compares DLL vs libxrk for all test files |
| `compare_laps.py` | Detailed comparison with formatted output |
| `print_laps.py` | Print libxrk lap times (no Wine/DLL needed) |
| `aim_dll_wrapper.py` | Python ctypes wrapper (reference only) |
| `COMPARISON_REPORT.md` | Latest comparison results |

## Troubleshooting

### "wine: failed to open" error
The Wine Python setup may be missing. Re-run the setup script.

### "Failed to open file", or a result with no channels
The DLL could not open the path. Check that it exists and is ASCII; see "Run
Comparison Manually" above.

### DLL not loading
Make sure you're using 64-bit Wine (`wine64` or `wine` on 64-bit systems).

### Permission denied
Make the setup script executable: `chmod +x setup_dll_comparison.sh`
