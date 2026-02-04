#!/bin/bash
#
# Setup script for DLL comparison tools
# Downloads the proprietary AIM DLL and sets up Wine Python environment
#
# Usage: ./setup_dll_comparison.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_DIR="$SCRIPT_DIR/.setup"

echo "=== AIM DLL Comparison Setup ==="
echo ""

# Check for Wine
if ! command -v wine &> /dev/null; then
    echo "Wine is not installed."
    echo ""
    echo "To install Wine on Ubuntu/Debian:"
    echo "  sudo apt-get update && sudo apt-get install -y wine64"
    echo ""
    echo "To install Wine on Fedora:"
    echo "  sudo dnf install wine"
    echo ""
    echo "To install Wine on macOS (not recommended - use a VM):"
    echo "  brew install --cask wine-stable"
    echo ""
    exit 1
fi

echo "[1/3] Wine found: $(wine --version 2>&1 | head -1)"

# Create setup directory
mkdir -p "$SETUP_DIR"
cd "$SETUP_DIR"

# Download DLL from laz-/xrk repo
echo ""
echo "[2/3] Downloading AIM DLL..."
if [ -f "$SCRIPT_DIR/MatLabXRK-2017-64-ReleaseU.dll" ]; then
    echo "  DLL already exists, skipping download"
else
    if [ -d "xrk" ]; then
        rm -rf xrk
    fi
    git clone --depth 1 https://github.com/laz-/xrk.git xrk
    cp xrk/MatLabXRK-2017-64-ReleaseU.dll "$SCRIPT_DIR/"
    echo "  DLL downloaded to $SCRIPT_DIR/"
fi

# Download and extract Windows Python (embeddable)
echo ""
echo "[3/3] Setting up Wine Python..."
PYTHON_DIR="$SETUP_DIR/python-embed"
if [ -f "$PYTHON_DIR/python.exe" ]; then
    echo "  Wine Python already exists"
else
    PYTHON_ZIP="python-3.12.0-embed-amd64.zip"
    if [ ! -f "$PYTHON_ZIP" ]; then
        echo "  Downloading Windows Python..."
        curl -L -o "$PYTHON_ZIP" "https://www.python.org/ftp/python/3.12.0/$PYTHON_ZIP"
    fi
    echo "  Extracting..."
    rm -rf "$PYTHON_DIR"
    mkdir -p "$PYTHON_DIR"
    python3 -c "import zipfile; zipfile.ZipFile('$PYTHON_ZIP').extractall('$PYTHON_DIR')"
fi

# Test Wine Python
echo ""
echo "Testing Wine Python..."
if WINEDEBUG=-all wine "$PYTHON_DIR/python.exe" -c "print('Wine Python OK')" 2>&1 | grep -q "OK"; then
    echo "  Wine Python works!"
else
    echo "  Warning: Wine Python test failed. You may need to configure Wine."
    echo "  Try running: winecfg"
fi

# Verify DLL can be loaded
echo ""
echo "Testing DLL loading..."
DLL_PATH="$SCRIPT_DIR/MatLabXRK-2017-64-ReleaseU.dll"
if WINEDEBUG=-all wine "$PYTHON_DIR/python.exe" -c "
import ctypes
dll = ctypes.CDLL('Z:${DLL_PATH}')
print('DLL loaded OK')
" 2>&1 | grep -q "OK"; then
    echo "  DLL loads successfully!"
else
    echo "  Warning: DLL loading test failed."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To run the comparison:"
echo "  poetry run python tests/reference_dll/full_comparison.py"
echo ""
echo "To clean up:"
echo "  ./tests/reference_dll/cleanup_dll_comparison.sh"
echo ""
