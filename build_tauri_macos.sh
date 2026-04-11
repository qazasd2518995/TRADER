#!/bin/bash
set -e
echo "=== NOTE: Local macOS build only ==="
echo "This script is for local testing."
echo "Unsigned or ad-hoc signed DMGs can show '已損毀' on other Macs."
echo "Use ./build_release_macos.sh with Developer ID signing for external distribution."
echo "=== Step 1: Build Python sidecar ==="
bash build_sidecar_macos.sh
echo "=== Step 2: Install frontend dependencies ==="
npm install
echo "=== Step 3: Build Tauri app ==="
npm run tauri build
echo "=== Build complete! ==="
echo "Output: src-tauri/target/release/bundle/dmg/"
