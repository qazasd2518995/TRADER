# -*- mode: python ; coding: utf-8 -*-
"""會員端 · macOS

建置：
  pyinstaller --noconfirm packaging/pyinstaller/client-macos.spec

模組清單一律從 _common.py 拿——四份 spec 各自維護的下場是會漂移，
先前 central-windows.spec 的 hiddenimports 就整個是空的。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
sys.path.insert(0, str(Path(SPECPATH)))
from _common import hidden, datas, excludes          # noqa: E402

a = Analysis(
    [str(ROOT / "copy_trader/central/client_agent_web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas(ROOT, "client"),
    hiddenimports=hidden("client", "macos"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes("client"),
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="黃金跟單會員端",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="黃金跟單會員端",
)

app = BUNDLE(
    coll,
    name="黃金跟單會員端.app",
    icon=str(ROOT / "packaging/assets/icon.icns"),
    bundle_identifier="com.goldtrader.member",
    info_plist={
        # 控制台是網頁介面，不需要 Dock 圖示以外的東西；
        # 但要標明支援 Retina，不然文字在高解析螢幕上會糊。
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
        "CFBundleShortVersionString": "1.0.1",
        "CFBundleVersion": "1.0.1",
    },
)
