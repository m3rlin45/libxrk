#!/bin/bash
#
# Cleanup script for DLL comparison tools
# Removes downloaded DLL and Wine Python setup
#
# Usage: ./cleanup_dll_comparison.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_DIR="$SCRIPT_DIR/.setup"

echo "=== AIM DLL Comparison Cleanup ==="
echo ""

# Remove setup directory
if [ -d "$SETUP_DIR" ]; then
    echo "Removing setup directory..."
    rm -rf "$SETUP_DIR"
    echo "  Removed $SETUP_DIR"
else
    echo "Setup directory not found (already clean)"
fi

# Remove DLL
DLL_PATH="$SCRIPT_DIR/MatLabXRK-2017-64-ReleaseU.dll"
if [ -f "$DLL_PATH" ]; then
    echo "Removing DLL..."
    rm -f "$DLL_PATH"
    echo "  Removed $DLL_PATH"
else
    echo "DLL not found (already clean)"
fi

# Also clean up old /tmp location if it exists
if [ -d "/tmp/libxrk-dll-setup" ]; then
    echo "Removing old /tmp setup..."
    rm -rf "/tmp/libxrk-dll-setup"
    echo "  Removed /tmp/libxrk-dll-setup"
fi

echo ""
echo "=== Cleanup Complete ==="
echo ""
