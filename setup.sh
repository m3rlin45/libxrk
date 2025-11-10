#!/bin/bash
# Setup script for libxrk project

set -e

echo "Installing dependencies with Poetry..."
poetry install

echo ""
echo "Building Cython extension..."
poetry build

echo ""
echo "Setup complete! The Cython extension has been compiled."
echo ""
echo "To use the library in development mode, run:"
echo "  poetry shell"
echo "  python -c 'from libxrk.data import AIMXRK; print(\"Import successful!\")'"
