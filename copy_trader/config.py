"""
Copy Trader Configuration (Windows Version)
Supports JSON persistence for GUI settings.
"""
import os
import sys
import json
import glob
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


def _get_data_dir() -> Path:
    """資料目錄：config.json、signals、logs 都存這裡。"""
    try:
        from copy_trader.platform import PlatformConfig
        return PlatformConfig().get_app_data_path()
    except ImportError:
        if getattr(sys, 'frozen', False):
            if sys.platform == "darwin":
                return Path.home() / "Library" / "Application Support" / "黃金跟單系統"
            return Path(os.environ.get('APPDATA', '~')) / '黃金跟單系統'
        return Path(__file__).parent.parent


DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 設定檔路徑
CONFIG_FILE = DATA_DIR / "config.json"


@dataclass
class CaptureRegion:
    """Screen capture region definition (coordinate-based)."""
    x: int
    y: int
    width: int
    height: int
    name: str = "default"


@dataclass
class CaptureWindow:
    """Window capture definition (works in background)."""
    window_name: str          # Part of window title to match
    app_name: str = "LINE"    # Application name
    name: str = "default"     # Identifier for this capture source


@dataclass
class Config:
    """Copy Trader configuration."""

    # Signal Source
    signal_source: str = "screen_ocr"

    # Screen Capture Settings
    capture_mode: str = "window"  # "region" or "window"
    capture_regions: List[CaptureRegion] = field(default_factory=list)
    capture_windows: List[CaptureWindow] = field(default_factory=list)
    capture_interval: float = 1.0

    # Parser Settings (hardcoded — Gemini > Groq > Regex fallback chain)
    parser_mode: str = "regex"
    gemini_api_key: str = ""  # Google AI Studio key — set in __post_init__
    gemini_vision_model: str = "gemini-2.5-flash-lite"
    groq_api_key: str = "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"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"

    # Trading Settings
    auto_execute: bool = True
    default_lot_size: float = 0.01
    symbol_name: str = "XAUUSD.s"
    max_open_positions: int = 10

    # Cancellation Rules
    cancel_pending_after_seconds: int = 7200
    cancel_if_price_beyond_percent: float = 1.0

    # Multiple TP Settings
    partial_close_ratios: List[float] = field(default_factory=lambda: [0.5, 0.3, 0.2])

    # Martingale Settings
    use_martingale: bool = True
    martingale_multiplier: float = 2.0
    martingale_max_level: int = 4
    martingale_lots: List[float] = field(default_factory=list)  # 全域自訂每層手數
    martingale_per_source: bool = False  # True=每群各自馬丁, False=全域共用
    martingale_source_lots: dict = field(default_factory=dict)  # 各群自訂手數 {"群名": [0.01, 0.02, ...]}

    # OCR Confirmation Settings
    ocr_confirm_count: int = 2       # Number of OCR reads to confirm a signal (2 = 1 initial + 1 confirmation)
    ocr_confirm_delay: float = 1.0   # Seconds between each confirmation OCR

    # Safety Settings
    min_confidence: float = 0.9
    max_price_deviation: float = 0.01
    signal_dedup_minutes: int = 10
    max_daily_loss: float = 500.0

    # MT5 Bridge Settings (auto detect)
    mt5_files_dir: str = ""

    # Logging
    log_level: str = "INFO"
    log_file: str = "copy_trader.log"

    # Hardcoded API keys — always use these regardless of config.json
    _GROQ_API_KEY = "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"
    _GEMINI_API_KEY = "AIzaSyDmCbE-8vQwzFKHu6wZ7J1uD5MImUbS0jM"

    def __post_init__(self):
        self.groq_api_key = self._GROQ_API_KEY
        if self._GEMINI_API_KEY:
            self.gemini_api_key = self._GEMINI_API_KEY

        # Auto-detect MT5 Files directory
        if not os.path.exists(self.mt5_files_dir):
            self.mt5_files_dir = self._find_mt5_files_dir()

        # Default capture windows
        if self.capture_mode == "window":
            if not self.capture_windows:
                self.capture_windows = [
                    CaptureWindow(
                        window_name="黃金報單🈲言群",
                        app_name="LINE",
                        name="gold_signal_1"
                    ),
                    CaptureWindow(
                        window_name="鄭",
                        app_name="LINE",
                        name="gold_signal_2"
                    ),
                ]
        else:
            if not self.capture_regions:
                self.capture_regions = [
                    CaptureRegion(x=696, y=99, width=375, height=566, name="line_gold_signal")
                ]

    def _find_mt5_files_dir(self) -> str:
        """Auto-detect MT5 Files directory using platform layer."""
        try:
            from copy_trader.platform import PlatformConfig
            path = PlatformConfig().get_mt5_files_path()
            if path and path.is_dir():
                return str(path)
        except ImportError:
            pass

        # Fallback: platform-specific hardcoded paths
        if sys.platform == "darwin":
            mac_path = Path.home() / "Library" / "Application Support" / \
                "net.metaquotes.wine.metatrader5" / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files"
            return str(mac_path)
        else:
            search_paths = [
                r"C:\Program Files\MetaTrader 5\MQL5\Files",
                r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files",
            ]
            for p in search_paths:
                if os.path.isdir(p):
                    return p
            return r"C:\Program Files\MetaTrader 5\MQL5\Files"


def save_config(config: Config, path: Path = CONFIG_FILE):
    """Save config to JSON file."""
    data = {
        "signal_source": config.signal_source,
        "capture_mode": config.capture_mode,
        "capture_windows": [
            {"window_name": w.window_name, "app_name": w.app_name, "name": w.name}
            for w in config.capture_windows
        ],
        "capture_regions": [
            {"x": r.x, "y": r.y, "width": r.width, "height": r.height, "name": r.name}
            for r in config.capture_regions
        ],
        "capture_interval": config.capture_interval,
        "auto_execute": config.auto_execute,
        "default_lot_size": config.default_lot_size,
        "symbol_name": config.symbol_name,
        "max_open_positions": config.max_open_positions,
        "cancel_pending_after_seconds": config.cancel_pending_after_seconds,
        "cancel_if_price_beyond_percent": config.cancel_if_price_beyond_percent,
        "partial_close_ratios": config.partial_close_ratios,
        "use_martingale": config.use_martingale,
        "martingale_multiplier": config.martingale_multiplier,
        "martingale_max_level": config.martingale_max_level,
        "martingale_lots": config.martingale_lots,
        "martingale_per_source": config.martingale_per_source,
        "martingale_source_lots": config.martingale_source_lots,
        "ocr_confirm_count": config.ocr_confirm_count,
        "ocr_confirm_delay": config.ocr_confirm_delay,
        "min_confidence": config.min_confidence,
        "max_price_deviation": config.max_price_deviation,
        "signal_dedup_minutes": config.signal_dedup_minutes,
        "max_daily_loss": config.max_daily_loss,
        "mt5_files_dir": config.mt5_files_dir,
        "log_level": config.log_level,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_config(path: Path = CONFIG_FILE) -> Config:
    """Load configuration from JSON file, falling back to defaults."""
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 分離特殊欄位
            windows_data = data.pop('capture_windows', [])
            regions_data = data.pop('capture_regions', [])

            # 過濾有效的 Config 欄位
            valid_fields = {f.name for f in Config.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_fields}

            config = Config(**filtered)

            # 還原擷取視窗
            if windows_data:
                config.capture_windows = [
                    CaptureWindow(**w) for w in windows_data
                ]
            # 還原擷取區域
            if regions_data:
                config.capture_regions = [
                    CaptureRegion(**r) for r in regions_data
                ]

            return config

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to load config.json: {e}")

    return Config()
