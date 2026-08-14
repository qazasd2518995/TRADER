"""
Copy Trader Configuration (Windows Version)
Supports JSON persistence for GUI settings.
"""
import os
import re
import sys
import json
import glob
import hashlib
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path


def _instance_name() -> str:
    """多開用的實例名稱，取自環境變數 COPY_TRADER_INSTANCE。留空 = 不分流。

    同一台電腦要跑兩個會員跟單帳號時，兩份程式若共用資料目錄，會共用
    central_client_state.json 裡的 last_seq 游標 —— 那是「已處理到第幾號訊號」，
    共用的結果不是「兩個帳號都跟單」，而是每個訊號只被先輪詢到的那一份吃掉、
    另一份直接跳過，訊號被拆成兩半，而且完全無聲。settings / port 檔同樣會互相
    覆蓋。設了實例名稱就把整個資料目錄分流，上述檔案連同 config.json、log 全部
    自動獨立。

    留空時回傳 ""，路徑與加這個功能之前完全相同 —— 既有安裝不受影響。
    """
    raw = (os.environ.get("COPY_TRADER_INSTANCE") or "").strip()
    if not raw:
        # 環境變數沒設就直接看命令列。這裡刻意不靠進入點先把 argv 轉成環境變數 ——
        # copy_trader/__init__.py 本身就 `from .config import ...`，只要有人先 import
        # 了 copy_trader 底下任何東西，DATA_DIR 就已經算完，之後再設環境變數已經
        # 來不及。sys.argv 在任何 import 之前就存在，讀它才不受載入順序影響。
        argv = sys.argv[1:]
        for i, arg in enumerate(argv):
            if arg == "--instance" and i + 1 < len(argv):
                raw = argv[i + 1].strip()
                break
            if arg.startswith("--instance="):
                raw = arg.split("=", 1)[1].strip()
                break
    if not raw:
        return ""
    # 只留檔名安全的字元，擋掉 ".." 或路徑分隔字元跳出資料目錄
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", raw)[:32].strip("_")
    if not safe:
        # 純中文/符號的名稱 (例如「帳號B」) 會被清成空字串。若就這樣回 ""，分流會
        # 靜靜失效、兩個實例共用同一份 state — 正是這個功能要防的事。改用雜湊，
        # 寧可名字醜也不要無聲失效。
        safe = "h" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return safe


def _get_data_dir() -> Path:
    """資料目錄：config.json、signals、logs 都存這裡。"""
    base = None
    try:
        from copy_trader.platform import PlatformConfig
        base = PlatformConfig().get_app_data_path()
    except ImportError:
        if getattr(sys, 'frozen', False):
            if sys.platform == "darwin":
                base = Path.home() / "Library" / "Application Support" / "黃金跟單系統"
            else:
                base = Path(os.environ.get('APPDATA', '~')) / '黃金跟單系統'
        else:
            base = Path(__file__).parent.parent

    name = _instance_name()
    return base / f"instance_{name}" if name else base


DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 設定檔路徑
CONFIG_FILE = DATA_DIR / "config.json"
DEFAULT_SYMBOL = "XAUUSD"

logger = logging.getLogger(__name__)


def _is_valid_symbol_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    symbol = value.strip()
    if not symbol:
        return False
    return all(ch.isalnum() or ch in "._-" for ch in symbol)


def _read_json_dict(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return {}


def detect_mt5_symbol(mt5_files_dir: str) -> str:
    """Infer the broker's gold symbol from MT5 bridge files."""
    mt5_dir = Path(mt5_files_dir or "")
    if not mt5_dir.is_dir():
        return DEFAULT_SYMBOL

    symbol_info = _read_json_dict(mt5_dir / "symbol_info.json")
    symbol = str(symbol_info.get("symbol", "")).strip()
    if _is_valid_symbol_name(symbol):
        return symbol

    try:
        price_files = sorted(
            mt5_dir.glob("*_price.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        price_files = []

    for path in price_files:
        price_data = _read_json_dict(path)
        symbol = str(price_data.get("symbol", "")).strip()
        if _is_valid_symbol_name(symbol):
            return symbol

        inferred = path.name.removesuffix("_price.json")
        if _is_valid_symbol_name(inferred):
            return inferred

    return DEFAULT_SYMBOL


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
    window_name: str                # Raw window title / matching keyword
    app_name: str = "LINE"          # Application name
    name: str = "default"           # Identifier for this capture source
    window_id: Optional[int] = None # Stable while the source app window stays alive
    display_name: str = ""          # UI label shown to users
    # 只跟這些人的單 (LINE 暱稱「子字串」比對, 不分大小寫)。空 = 不過濾, 全群都跟。
    # 只有剪貼簿管道支援 (window_ocr 讀不出發送者 → 該視窗會被整個跳過)。
    # 缺點：對方改暱稱就整組靜悄悄失效 —— 多人報單群優先用下面的 required_patterns。
    allowed_senders: List[str] = field(default_factory=list)
    # 模板指紋：訊息必須含「全部」這些片段才當訊號 (忽略空白/不分大小寫)。空 = 不比對。
    # 認的是提供者的固定簽名檔 (例「個人建議不構成投資計畫」), 比暱稱穩:
    #   1. 對方改暱稱不會失效
    #   2. 內容型過濾 → OCR 管道也適用 (OCR 讀不出發送者, 但讀得到內容)
    #   3. 擋得掉群友「轉貼別人的單」「自己報單」—— 那些沒有簽名檔
    # 代價：提供者哪天忘了附簽名檔, 那一單會漏掉 (寧可漏, 不可跟錯人)。
    required_patterns: List[str] = field(default_factory=list)


@dataclass
class Config:
    """Copy Trader configuration."""

    # Signal Source —
    #   "window_ocr" (PrintWindow 背景截圖 + OCR)：主要管道。
    #   "clipboard"  (LINE 全選複製)：LINE 2026-06 更新後擋掉合成鍵鼠，已失效。
    #   "screen_ocr" (舊的螢幕區域 OCR 方案)。
    # 預設 window_ocr：剪貼簿法在新版 LINE 上拿不到任何文字 (empty_clipboard)。
    signal_source: str = "window_ocr"

    # Screen Capture Settings
    capture_mode: str = "window"  # "region" or "window"
    capture_regions: List[CaptureRegion] = field(default_factory=list)
    capture_windows: List[CaptureWindow] = field(default_factory=list)
    capture_interval: float = 1.0

    # Clipboard Capture Settings
    clipboard_screens: int = 2   # Shift+PgUp 次數 — 要讀幾屏
    clipboard_copy_mode: str = "tail"  # "tail" = bottom N pages, "all" = Ctrl+A/Cmd+A copy
    clipboard_min_interval: float = 0.7  # 兩次剪貼板採集之間最小間隔（秒）
    clipboard_stale_seconds: float = 10.0  # 未讀數沒變化時，最久幾秒做一次兜底複製

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
    symbol_name: str = DEFAULT_SYMBOL
    max_open_positions: int = 10

    # 撤單策略統一成一條：只留「掛單逾時未成交就自動刪單」，其餘全部關閉。
    # 訊息撤單與同方向改單防呆都「不分來源」—— 同時跟兩個以上報單群時，
    # A 群的訊息會撤掉 B 群的掛單（見 cancel_latest_pending /
    # cancel_pending_same_direction），所以兩個都關掉。
    cancel_pending_after_seconds: int = 10800    # 3 小時未成交 → 自動刪單；0=不因逾時刪
    cancel_if_price_beyond_percent: float = 0.0  # 0=關閉價格偏離自動撤單
    supersede_same_direction_minutes: int = 0    # 0=關閉同方向改單防呆 (不分來源)
    # 同一來源在這幾分鐘內又發新單 → 撤掉該來源之前還沒成交的掛單，只跟最新那筆。
    # 這條「分來源」，所以跟多群也安全，預設開著。
    #
    # 提供者會用「收回訊息再重發」修正報單：2026-08-14 yuyu 在 23 秒內發三次、
    # 收回前兩次 (止損 4359 → 4369 → 4364)，我們 3 秒的擷取延遲讓每個中途版本都
    # 在被收回前就發布了，會員端因此對同一則報單掛了三張、曝險三倍且止損各不相同。
    # 只撤未成交的掛單，已進場的部位不動。
    supersede_same_source_minutes: int = 3
    follow_group_cancel: bool = False            # False=不跟群組的「取消/撤」訊息

    # Multiple TP Settings
    partial_close_ratios: List[float] = field(default_factory=lambda: [0.5, 0.3, 0.2])

    # Martingale Settings
    use_martingale: bool = True
    martingale_multiplier: float = 2.0
    martingale_max_level: int = 5  # 關卡數: 5關 => 最大 base×2^4 = 16x
    martingale_lots: List[float] = field(default_factory=list)  # 全域自訂每層手數
    martingale_per_source: bool = False  # True=每群各自馬丁, False=全域共用
    martingale_source_lots: dict = field(default_factory=dict)  # 各群自訂手數 {"群名": [0.01, 0.02, ...]}

    # OCR Confirmation Settings
    # 需連續 N 次 OCR 讀到「完全一致」的訊號才發布，用來擋掉「瞬間誤讀」
    # (訊號剛出現/捲動時 OCR 偶爾讀錯數字)。搭配 window_ocr_reader 的
    # 「待確認時不跳過 OCR」守衛才能正常累積次數。2 = 讀到2次一致才發。
    ocr_confirm_count: int = 2
    ocr_confirm_delay: float = 1.0   # Seconds between each confirmation OCR

    # Safety Settings
    min_confidence: float = 0.9
    max_price_deviation: float = 0.01
    signal_dedup_minutes: int = 10
    signal_max_age_minutes: int = 10   # 訊號時效: 訊息時間超過這麼久(分)就不發布/不下單; 0=不限
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
        self.symbol_name = self._resolve_symbol_name(self.symbol_name)

        # Default capture windows
        if self.capture_mode == "window":
            if not self.capture_windows:
                self.capture_windows = [
                    CaptureWindow(
                        window_name="黃金報單🈲言群",
                        app_name="LINE",
                        name="gold_signal_1",
                        display_name="黃金報單🈲言群"
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

    def _resolve_symbol_name(self, configured_symbol: str) -> str:
        configured = (configured_symbol or "").strip()
        detected = detect_mt5_symbol(self.mt5_files_dir)

        if configured:
            configured_price_file = Path(self.mt5_files_dir) / f"{configured}_price.json"
            if configured_price_file.exists():
                return configured

        if configured and configured != detected:
            logger.info(
                "Resolved MT5 symbol from %s to %s based on broker files",
                configured,
                detected,
            )

        return detected or configured or DEFAULT_SYMBOL


def save_config(config: Config, path: Path = CONFIG_FILE):
    """Save config to JSON file."""
    data = {
        "signal_source": config.signal_source,
        "capture_mode": config.capture_mode,
        "capture_windows": [
            {
                "window_name": w.window_name,
                "app_name": w.app_name,
                "name": w.name,
                "window_id": w.window_id,
                "display_name": w.display_name,
                "allowed_senders": list(w.allowed_senders or []),
                "required_patterns": list(w.required_patterns or []),
            }
            for w in config.capture_windows
        ],
        "capture_regions": [
            {"x": r.x, "y": r.y, "width": r.width, "height": r.height, "name": r.name}
            for r in config.capture_regions
        ],
        "capture_interval": config.capture_interval,
        "clipboard_screens": config.clipboard_screens,
        "clipboard_copy_mode": config.clipboard_copy_mode,
        "clipboard_min_interval": config.clipboard_min_interval,
        "clipboard_stale_seconds": config.clipboard_stale_seconds,
        "auto_execute": config.auto_execute,
        "default_lot_size": config.default_lot_size,
        "symbol_name": config.symbol_name,
        "max_open_positions": config.max_open_positions,
        "cancel_pending_after_seconds": config.cancel_pending_after_seconds,
        "cancel_if_price_beyond_percent": config.cancel_if_price_beyond_percent,
        "supersede_same_direction_minutes": config.supersede_same_direction_minutes,
        "supersede_same_source_minutes": config.supersede_same_source_minutes,
        "follow_group_cancel": config.follow_group_cancel,
        "signal_max_age_minutes": config.signal_max_age_minutes,
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
        "signal_max_age_minutes": config.signal_max_age_minutes,
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
                    CaptureWindow(
                        window_name=w.get("window_name", ""),
                        app_name=w.get("app_name", "LINE"),
                        name=w.get("name", "default"),
                        window_id=w.get("window_id"),
                        display_name=w.get("display_name", w.get("window_name", "")),
                        allowed_senders=[
                            str(s).strip()
                            for s in (w.get("allowed_senders") or [])
                            if str(s).strip()
                        ],
                        required_patterns=[
                            str(p).strip()
                            for p in (w.get("required_patterns") or [])
                            if str(p).strip()
                        ],
                    )
                    for w in windows_data
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
