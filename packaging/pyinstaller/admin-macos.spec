# -*- mode: python ; coding: utf-8 -*-
"""管理端 · macOS

建置：
  pyinstaller --noconfirm packaging/pyinstaller/admin-macos.spec

模組清單一律從 _common.py 拿——四份 spec 各自維護的下場是會漂移，
先前 central-windows.spec 的 hiddenimports 就整個是空的。

跟訊號中心的差別：不打包 apsw、不打包 LINE 擷取與超高頻策略。管理端跟訊號端
連的是同一個 Hub，只要那些程式碼在裡面就有機會被啟動，兩台同時發單就等於
會員重複下單 —— 少一份程式碼就少一種出錯方式。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
sys.path.insert(0, str(Path(SPECPATH)))
from _common import hidden, datas, excludes          # noqa: E402

a = Analysis(
    [str(ROOT / "copy_trader/central/admin_web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas(ROOT, "admin"),
    hiddenimports=hidden("admin", "macos"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes("admin"),
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="黃金管理端",
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
    name="黃金管理端",
)

app = BUNDLE(
    coll,
    name="黃金管理端.app",
    icon=str(ROOT / "packaging/assets/icon.icns"),
    bundle_identifier="com.goldtrader.admin",
    info_plist={
        # 控制台是網頁介面，不需要 Dock 圖示以外的東西；
        # 但要標明支援 Retina，不然文字在高解析螢幕上會糊。
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
        "CFBundleShortVersionString": "1.2.4",
        "CFBundleVersion": "1.2.4",
    },
)
