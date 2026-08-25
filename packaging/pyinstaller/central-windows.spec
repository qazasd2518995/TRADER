# -*- mode: python ; coding: utf-8 -*-
"""訊號中心 · Windows

建置：
  pyinstaller --noconfirm packaging/pyinstaller/central-windows.spec

模組清單一律從 _common.py 拿——四份 spec 各自維護的下場是會漂移，
先前 central-windows.spec 的 hiddenimports 就整個是空的。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
sys.path.insert(0, str(Path(SPECPATH)))
from _common import hidden, datas, EXCLUDES          # noqa: E402

a = Analysis(
    [str(ROOT / "copy_trader/central/central_signal_center_web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas(ROOT),
    hiddenimports=hidden("central", "windows"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="黃金訊號中心",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / "src-tauri/icons/icon.ico"),
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="黃金訊號中心",
)
