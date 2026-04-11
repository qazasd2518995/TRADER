#!/bin/bash
set -euo pipefail

APP_NAME="黃金跟單系統"
VERSION="${VERSION:-$(sed -n 's/  "version": "\(.*\)",/\1/p' src-tauri/tauri.conf.json | head -n 1)}"
APP_PATH="src-tauri/target/release/bundle/macos/${APP_NAME}.app"
DMG_DIR="src-tauri/target/release/bundle/dmg"
DMG_PATH="${DMG_DIR}/${APP_NAME}_${VERSION}_aarch64.dmg"
BUNDLE_DMG_SCRIPT="${DMG_DIR}/bundle_dmg.sh"
EMBEDDED_SIDECAR="${APP_PATH}/Contents/Resources/binaries/copy-trader-sidecar-aarch64-apple-darwin"

if [ -z "${DEVELOPER_ID_APPLICATION:-}" ]; then
  echo "ERROR: DEVELOPER_ID_APPLICATION is required for external macOS distribution."
  echo "Example: export DEVELOPER_ID_APPLICATION='Developer ID Application: Your Name (TEAMID)'"
  exit 1
fi

if ! security find-identity -v -p codesigning | grep -Fq "${DEVELOPER_ID_APPLICATION}"; then
  echo "ERROR: Signing identity not found in keychain: ${DEVELOPER_ID_APPLICATION}"
  exit 1
fi

echo "=== Step 1: Build Python sidecar ==="
bash build_sidecar_macos.sh

echo "=== Step 2: Install frontend dependencies ==="
npm install

echo "=== Step 3: Build app bundle ==="
npm run tauri build -- --bundles app

if [ ! -d "${APP_PATH}" ]; then
  echo "ERROR: App bundle not found: ${APP_PATH}"
  exit 1
fi

if [ ! -f "${BUNDLE_DMG_SCRIPT}" ]; then
  echo "ERROR: DMG bundler not found: ${BUNDLE_DMG_SCRIPT}"
  echo "Run a local macOS build once so Tauri generates bundle_dmg.sh."
  exit 1
fi

if [ -f "${EMBEDDED_SIDECAR}" ]; then
  echo "=== Step 4: Sign embedded sidecar ==="
  codesign --force --options runtime --sign "${DEVELOPER_ID_APPLICATION}" "${EMBEDDED_SIDECAR}"
fi

echo "=== Step 5: Sign app bundle ==="
codesign --force --deep --options runtime --sign "${DEVELOPER_ID_APPLICATION}" "${APP_PATH}"
codesign --verify --verbose=4 --deep "${APP_PATH}"

echo "=== Step 6: Create DMG ==="
rm -f "${DMG_PATH}"
"${BUNDLE_DMG_SCRIPT}" --skip-jenkins "${DMG_PATH}" "${APP_PATH}"

echo "=== Step 7: Sign DMG ==="
codesign --force --sign "${DEVELOPER_ID_APPLICATION}" "${DMG_PATH}"
codesign --verify --verbose=4 "${DMG_PATH}"

if [ -n "${NOTARYTOOL_PROFILE:-}" ]; then
  echo "=== Step 8: Notarize DMG ==="
  xcrun notarytool submit "${DMG_PATH}" --keychain-profile "${NOTARYTOOL_PROFILE}" --wait
  xcrun stapler staple "${DMG_PATH}"
  xcrun stapler validate "${DMG_PATH}"
else
  echo "WARNING: NOTARYTOOL_PROFILE is not set."
  echo "WARNING: The DMG is signed but not notarized."
  echo "WARNING: Other Macs may still show '已損毀' or '無法驗證開發者'."
fi

echo "=== Release complete! ==="
echo "Output: ${DMG_PATH}"
