# -*- mode: python ; coding: utf-8 -*-
"""
Build:
  pyinstaller --noconfirm packaging/pyinstaller/client-windows.spec
"""
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]

a = Analysis(
    [str(ROOT / "copy_trader/central/client_agent_web.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "mt5_ea/MT5_File_Bridge_Enhanced.mq5"), "mt5_ea"),
    ],
    hiddenimports=[
        "copy_trader.central.mt5_client_agent",
        "copy_trader.trade_manager.manager",
        "copy_trader.signal_parser.regex_parser",
        "copy_trader.platform.windows",
        "win32api",
        "win32con",
        "win32gui",
        "PIL",
        "PIL.Image",
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
    name="黃金跟單會員端",
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
    name="黃金跟單會員端",
)
