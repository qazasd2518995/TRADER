# -*- mode: python ; coding: utf-8 -*-
"""
Build:
  pyinstaller --noconfirm packaging/pyinstaller/central-windows.spec
"""
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]

a = Analysis(
    [str(ROOT / "copy_trader/central/central_signal_center_web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "mt5_ea/MT5_File_Bridge_Enhanced.mq5"), "mt5_ea"),
        (str(ROOT / "install_cloudflared_windows.bat"), "."),
    ],
    hiddenimports=[
        "copy_trader.central.hub_server",
        "copy_trader.central.signal_collector",
        "copy_trader.signal_capture.clipboard_reader",
        "copy_trader.signal_capture.line_text_parser",
        "copy_trader.signal_parser.keyword_filter",
        "copy_trader.signal_parser.regex_parser",
        "copy_trader.platform.windows",
        "win32api",
        "win32clipboard",
        "win32con",
        "win32gui",
        "win32ui",
        "PIL",
        "PIL.Image",
        "PIL.ImageGrab",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "numpy",
        "scipy",
        "matplotlib",
        "pytest",
        "rapidocr",
        "onnxruntime",
        "groq",
        "anthropic",
        "google.genai",
        "cv2",
        "pywt",
        "PyWavelets",
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
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="黃金訊號中心",
)
