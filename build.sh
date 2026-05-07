#!/bin/bash
set -e

echo "============================================"
echo " Building .NET SDK Test Runner Executable"
echo "============================================"
echo

cd "$(dirname "$0")"

# Check that frontend is pre-built
if [ ! -f "backend/static/index.html" ]; then
    echo "ERROR: Frontend not built."
    echo "Run 'npm run build' in frontend/ first (requires Node.js once)."
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is required to build. Install from https://python.org"
    exit 1
fi

echo "[1/3] Installing backend dependencies..."
python3 -m pip install -r backend/requirements.txt

echo
echo "[2/3] Installing PyInstaller..."
python3 -m pip install pyinstaller

echo
echo "[3/3] Bundling into standalone executable..."
python3 -m PyInstaller build.spec --distpath dist --workpath build_temp --clean --noconfirm

# Cleanup
rm -rf build_temp

echo
echo "============================================"
echo " Build complete!"
echo " Output: dist/dotnet-test-runner"
echo "============================================"
echo
echo "Copy dist/dotnet-test-runner to your target VM and run it."
echo "The browser will open automatically."
