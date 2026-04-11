#!/bin/bash
set -euo pipefail

APP_NAME="黃金跟單系統"
VERSION="${VERSION:-$(sed -n 's/  "version": "\(.*\)",/\1/p' src-tauri/tauri.conf.json | head -n 1)}"
APP_PATH="src-tauri/target/release/bundle/macos/${APP_NAME}.app"
DMG_DIR="src-tauri/target/release/bundle/dmg"
DMG_PATH="${DMG_DIR}/${APP_NAME}_${VERSION}_aarch64.dmg"
BUNDLE_DMG_SCRIPT="${DMG_DIR}/bundle_dmg.sh"
ICON_PATH="${DMG_DIR}/icon.icns"

if [ ! -d "${APP_PATH}" ]; then
  echo "ERROR: App bundle not found: ${APP_PATH}"
  echo "Run the app build first."
  exit 1
fi

if [ ! -f "${BUNDLE_DMG_SCRIPT}" ]; then
  echo "ERROR: DMG bundler not found: ${BUNDLE_DMG_SCRIPT}"
  echo "Run a local macOS build once so Tauri generates bundle_dmg.sh."
  exit 1
fi

echo "=== Re-sign app bundle (ad-hoc, local testing only) ==="
codesign --force --deep --sign - "${APP_PATH}"
codesign --verify --verbose=4 --deep "${APP_PATH}"

rm -f "${DMG_PATH}"

"${BUNDLE_DMG_SCRIPT}" \
  --skip-jenkins \
  --volname "${APP_NAME}" \
  --volicon "${ICON_PATH}" \
  --app-drop-link 380 170 \
  "${DMG_PATH}" \
  "${APP_PATH}"

echo "=== DMG rebuilt ==="
echo "Output: ${DMG_PATH}"
