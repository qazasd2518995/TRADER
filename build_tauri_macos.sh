#!/bin/bash
set -e
echo "=== Step 1: Build Python sidecar ==="
bash build_sidecar_macos.sh
echo "=== Step 2: Install frontend dependencies ==="
npm install
echo "=== Step 3: Build Tauri app ==="
npm run tauri build
echo "=== Build complete! ==="
echo "Output: src-tauri/target/release/bundle/dmg/"
