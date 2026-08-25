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
from _common import hidden, datas, excludes, collect_ocr   # noqa: E402

# OCR 的模型與原生 DLL 靠靜態分析找不到（模型是執行期用路徑組出來讀的），
# 一定要 collect_all 整包抓。少了這段一樣打得出來、一樣沒有警告，只是
# RapidOCR 在會員按下開始時才載入失敗。
_ocr_datas, _ocr_bins, _ocr_hidden = collect_ocr()

a = Analysis(
    [str(ROOT / "copy_trader/central/central_signal_center_web.py")],
    pathex=[str(ROOT)],
    binaries=_ocr_bins,
    datas=datas(ROOT) + _ocr_datas,
    hiddenimports=hidden("central", "windows") + _ocr_hidden,
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
