#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "============================================"
echo "  Build one-click macOS central signal app"
echo "============================================"

if python3 -m PyInstaller --version >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import apsw
PY
then
  echo "Using existing Python build dependencies."
else
  echo "Creating local build virtual environment..."
  python3 -m venv .venv-one-click-macos
  . .venv-one-click-macos/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r one_click_requirements_macos.txt
fi

PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/pyinstaller_config}" python3 -m PyInstaller --noconfirm packaging/pyinstaller/central-macos.spec

DMG_DIR="dist/installers"
APP_PATH="dist/黃金訊號中心.app"
DMG_PATH="${DMG_DIR}/黃金訊號中心.dmg"

mkdir -p "${DMG_DIR}"
if [ -d "${APP_PATH}" ] && command -v hdiutil >/dev/null 2>&1; then
  rm -f "${DMG_PATH}"
  hdiutil create -volname "黃金訊號中心" -srcfolder "${APP_PATH}" -ov -format UDZO "${DMG_PATH}"
  echo "DMG: ${DMG_PATH}"
else
  echo "App bundle: ${APP_PATH}"
fi

echo "Done."
