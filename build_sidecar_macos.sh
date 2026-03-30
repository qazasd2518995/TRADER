#!/bin/bash
set -e
echo "=== Installing macOS Python dependencies ==="
pip install -r sidecar_requirements_macos.txt
echo "=== Building sidecar with PyInstaller ==="
pyinstaller --noconfirm copy-trader-sidecar-macos.spec
echo "=== Copying sidecar to Tauri binaries ==="
mkdir -p src-tauri/binaries
cp dist/copy-trader-sidecar src-tauri/binaries/copy-trader-sidecar-aarch64-apple-darwin
echo "=== Done! ==="
