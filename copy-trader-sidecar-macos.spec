# -*- mode: python ; coding: utf-8 -*-
"""
黃金跟單系統 — Sidecar PyInstaller 打包配置 (macOS)
執行方式: pyinstaller --noconfirm copy-trader-sidecar-macos.spec
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# RapidOCR 模型檔案（ONNX models + config）
rapid_datas = []
rapid_hidden = []
try:
    rapid_datas = collect_data_files('rapidocr', include_py_files=False)
    rapid_hidden = [
        'rapidocr',
        'rapidocr.main',
        'rapidocr.cal_rec_boxes',
        'rapidocr.cal_rec_boxes.main',
        'rapidocr.ch_ppocr_cls',
        'rapidocr.ch_ppocr_cls.main',
        'rapidocr.ch_ppocr_cls.utils',
        'rapidocr.ch_ppocr_det',
        'rapidocr.ch_ppocr_det.main',
        'rapidocr.ch_ppocr_det.utils',
        'rapidocr.ch_ppocr_rec',
        'rapidocr.ch_ppocr_rec.main',
        'rapidocr.ch_ppocr_rec.typings',
        'rapidocr.ch_ppocr_rec.utils',
        'rapidocr.inference_engine',
        'rapidocr.inference_engine.base',
        'rapidocr.inference_engine.onnxruntime',
        'rapidocr.inference_engine.onnxruntime.main',
        'rapidocr.inference_engine.onnxruntime.provider_config',
        'rapidocr.utils',
        'rapidocr.utils.download_file',
        'rapidocr.utils.load_image',
        'rapidocr.utils.log',
        'rapidocr.utils.output',
        'rapidocr.utils.parse_parameters',
        'rapidocr.utils.process_img',
        'rapidocr.utils.to_json',
        'rapidocr.utils.to_markdown',
        'rapidocr.utils.typings',
        'rapidocr.utils.utils',
        'rapidocr.utils.vis_res',
    ]
except Exception:
    print("Warning: RapidOCR not found, skipping...")

a = Analysis(
    ['copy_trader/sidecar_main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('mt5_ea/MT5_File_Bridge_Enhanced.mq5', 'mt5_ea'),
    ] + rapid_datas,
    hiddenimports=[
        # pyobjc — macOS screen capture
        'objc', 'Quartz', 'AppKit', 'Vision', 'Foundation', 'CoreFoundation',
        # PIL — image processing
        'PIL', 'PIL.Image',
        'imagehash',
        # OCR
        'rapidocr', 'onnxruntime',
        # LLM parsers (groq + its dependencies)
        'groq', 'httpx', 'httpx._transports', 'httpx._transports.default',
        'httpcore', 'httpcore._async', 'httpcore._sync',
        'anyio', 'anyio._backends', 'anyio._backends._asyncio',
        'sniffio', 'distro', 'h11',
        # Auth
        'boto3', 'botocore', 'bcrypt', 'copy_trader.auth_handler',
        # copy_trader 內部模組
        'copy_trader.config', 'copy_trader.app', 'copy_trader.mt5_reader',
        'copy_trader.platform', 'copy_trader.platform.base', 'copy_trader.platform.macos',
        'copy_trader.signal_capture', 'copy_trader.signal_capture.screen_capture', 'copy_trader.signal_capture.ocr',
        'copy_trader.signal_capture.bubble_detector',
        'copy_trader.signal_parser', 'copy_trader.signal_parser.keyword_filter', 'copy_trader.signal_parser.groq_parser',
        'copy_trader.signal_parser.groq_vision_parser', 'copy_trader.signal_parser.gemini_vision_parser',
        # google-genai SDK
        'google.genai', 'google.genai.types',
        'copy_trader.signal_parser.parser', 'copy_trader.signal_parser.prompts', 'copy_trader.signal_parser.regex_parser',
        'copy_trader.trade_manager', 'copy_trader.trade_manager.manager',
    ] + rapid_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # PySide6 / Qt — sidecar 不需要 GUI
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PySide6.QtSvg', 'PySide6.QtQuick', 'PySide6.QtQml',
        'qasync',
        # Python 標準庫不需要的
        'tkinter', 'unittest', 'xmlrpc', 'pydoc',
        # 科學計算/開發工具
        'matplotlib', 'scipy', 'jupyter', 'notebook', 'IPython', 'pytest',
        # Windows-only — not applicable on macOS
        'winsdk',
        'win32gui', 'win32ui', 'win32con', 'win32api',
        # OCR 不需要的
        'paddleocr', 'paddle', 'paddlepaddle', 'rapidocr_onnxruntime',
        # 不需要的 transitive deps
        'pywt', 'PyWavelets',
    ],
    cipher=block_cipher,
)

# Remove unnecessary large binaries
_exclude_bins = [
    # PySide6/Qt (should already be excluded, belt-and-suspenders)
    'qt6quick', 'qt6qml', 'qt6pdf', 'qt6opengl',
    'qt6virtualkeyboard', 'qt6qmlmodels', 'qt6network',
    # OpenCV unused
    'opencv_videoio_ffmpeg',
    # PIL - AVIF codec not needed
    '_avif',
]
a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in _exclude_bins)]

# Remove unnecessary data files
_exclude_datas = [
    'pywt',
]
a.datas = [d for d in a.datas if not any(x in d[0] for x in _exclude_datas)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='copy-trader-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Not recommended on macOS arm64
    console=True,  # sidecar 需要 console 才能 stdin/stdout (headless JSON-RPC)
    onefile=True,
)
