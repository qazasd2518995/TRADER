# -*- mode: python ; coding: utf-8 -*-
"""訊號中心 · macOS

建置：
  pyinstaller --noconfirm packaging/pyinstaller/central-macos.spec

模組清單一律從 _common.py 拿——四份 spec 各自維護的下場是會漂移，
先前 central-windows.spec 的 hiddenimports 就整個是空的。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
sys.path.insert(0, str(Path(SPECPATH)))
from _common import hidden, datas, excludes, collect_apsw   # noqa: E402

_apsw_datas, _apsw_bins, _apsw_hidden = collect_apsw()

a = Analysis(
    [str(ROOT / "copy_trader/central/central_signal_center_web.py")],
    pathex=[str(ROOT)],
    binaries=_apsw_bins,
    datas=datas(ROOT, "central") + _apsw_datas,
    hiddenimports=hidden("central", "macos") + _apsw_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes("central"),
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

app = BUNDLE(
    coll,
    name="黃金訊號中心.app",
    icon=str(ROOT / "packaging/assets/icon.icns"),
    bundle_identifier="com.goldtrader.central",
    info_plist={
        # 控制台是網頁介面，不需要 Dock 圖示以外的東西；
        # 但要標明支援 Retina，不然文字在高解析螢幕上會糊。
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "1.2.3",
    },
)
