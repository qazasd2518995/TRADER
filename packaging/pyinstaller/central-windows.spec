# -*- mode: python ; coding: utf-8 -*-
"""
Build:
  pyinstaller --noconfirm packaging/pyinstaller/central-windows.spec

中央訊號中心 (黃金訊號中心)：擷取 LINE 訊號 → 發布到 Hub。
LINE 更新後擋掉合成鍵鼠，剪貼簿失效，改用 PrintWindow 背景截圖 + OCR，
因此本包需要打包 rapidocr / onnxruntime / opencv / numpy 及 OCR 模型。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parents[1]

# --- 基本資料檔 ---
datas = [
    (str(ROOT / "mt5_ea/MT5_File_Bridge_Enhanced.mq5"), "mt5_ea"),
    (str(ROOT / "install_cloudflared_windows.bat"), "."),
]
binaries = []
hiddenimports = [
    "copy_trader.central.hub_server",
    "copy_trader.central.signal_collector",
    "copy_trader.signal_capture.clipboard_reader",
    "copy_trader.signal_capture.window_ocr_reader",
    "copy_trader.signal_capture.ocr",
    "copy_trader.signal_capture.bubble_detector",
    "copy_trader.signal_capture.line_text_parser",
    "copy_trader.signal_parser.keyword_filter",
    "copy_trader.signal_parser.regex_parser",
    "copy_trader.platform.windows",
    "win32api",
    "win32clipboard",
    "win32con",
    "win32gui",
    "win32ui",
    "numpy",
    "yaml",
    "colorlog",
    "six",
    "tqdm",
    "requests",
    "PIL",
    "PIL.Image",
    "PIL.ImageGrab",
]

# collect_all 抓每個套件的 .py + 資料檔 + 原生二進位 (OCR 模型 / DLL)
for _pkg in ("rapidocr", "onnxruntime", "cv2", "shapely", "omegaconf", "pyclipper"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [str(ROOT / "copy_trader/central/central_signal_center_web.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "scipy",
        "matplotlib",
        "pytest",
        "groq",
        "anthropic",
        "google.genai",
        "pywt",
        "PyWavelets",
        "paddleocr",
        "paddle",
        "torch",
    ],
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
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="黃金訊號中心",
)
