"""Small, web-only runtime configuration for the MT5 client."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


logger = logging.getLogger(__name__)
DEFAULT_SYMBOL = "XAUUSD"


def _instance_name() -> str:
    """Return a filesystem-safe name for side-by-side client instances."""
    raw = (os.environ.get("COPY_TRADER_INSTANCE") or "").strip()
    if not raw:
        argv = sys.argv[1:]
        for index, argument in enumerate(argv):
            if argument == "--instance" and index + 1 < len(argv):
                raw = argv[index + 1].strip()
                break
            if argument.startswith("--instance="):
                raw = argument.split("=", 1)[1].strip()
                break
    if not raw:
        return ""
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", raw)[:32].strip("_")
    return safe or "h" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def _get_data_dir() -> Path:
    """Return the writable application data directory without GUI dependencies."""
    if not getattr(sys, "frozen", False):
        base = Path(__file__).resolve().parent.parent
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "黃金跟單系統"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / "黃金跟單系統" if appdata else Path.home() / "AppData" / "Roaming" / "黃金跟單系統"
    else:
        data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(data_home) / "gold-copy-trader" if data_home else Path.home() / ".local" / "share" / "gold-copy-trader"
    instance = _instance_name()
    return base / f"instance_{instance}" if instance else base


DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"


def _is_valid_symbol_name(value: object) -> bool:
    symbol = value.strip() if isinstance(value, str) else ""
    return bool(symbol) and all(character.isalnum() or character in "._-" for character in symbol)


def _read_json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return {}


def _price_is_live(data: dict) -> bool:
    """Bridge 價格檔有實際報價(bid/ask 不為 0)才算數——避開換代號後殘留、bid=0 的舊檔。"""
    try:
        return float(data.get("bid") or 0) > 0 or float(data.get("ask") or 0) > 0
    except (TypeError, ValueError):
        return False


def detect_mt5_symbol(mt5_files_dir: str) -> str:
    """從 bridge 檔案推斷這家券商的黃金代號; 查不到回空字串(讓上層保留設定值)。

    第一來源是 symbol_info.json 的 symbol —— 那是「當前掛著的 EA」寫的,最可信,
    換券商/換代號都會跟著變。退而求其次才掃 *_price.json,且優先取「有實際報價」
    的最新檔,免得抓到換代號後沒人再更新、bid=0 的殘檔。
    """
    directory = Path(mt5_files_dir or "")
    if not directory.is_dir():
        return ""
    symbol = str(_read_json_dict(directory / "symbol_info.json").get("symbol") or "").strip()
    if _is_valid_symbol_name(symbol):
        return symbol
    try:
        price_files = sorted(directory.glob("*_price.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        price_files = []
    # 第一輪只認有實際報價的檔; 都沒有再放寬(EA 剛啟動、市場休市等情況)。
    for require_live in (True, False):
        for path in price_files:
            data = _read_json_dict(path)
            if require_live and not _price_is_live(data):
                continue
            symbol = str(data.get("symbol") or "").strip()
            if _is_valid_symbol_name(symbol):
                return symbol
            inferred = path.name.removesuffix("_price.json")
            if _is_valid_symbol_name(inferred):
                return inferred
    return ""


def _find_mt5_files_dir() -> str:
    if sys.platform == "darwin":
        return str(
            Path.home()
            / "Library"
            / "Application Support"
            / "net.metaquotes.wine.metatrader5"
            / "drive_c"
            / "Program Files"
            / "MetaTrader 5"
            / "MQL5"
            / "Files"
        )
    if sys.platform == "win32":
        candidates: list[Path] = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.extend((Path(appdata) / "MetaQuotes" / "Terminal").glob("*/MQL5/Files"))
        candidates.extend(
            [
                Path(r"C:\Program Files\MetaTrader 5\MQL5\Files"),
                Path(r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files"),
            ]
        )
        existing = [path for path in candidates if path.is_dir()]
        if existing:
            existing.sort(
                key=lambda path: max((child.stat().st_mtime for child in path.glob("*.json")), default=path.stat().st_mtime),
                reverse=True,
            )
            return str(existing[0])
        return str(candidates[-2])
    return str(Path.home() / "MetaTrader 5" / "MQL5" / "Files")


@dataclass
class Config:
    """Settings used by the web MT5 execution client."""

    auto_execute: bool = True
    default_lot_size: float = 0.01
    symbol_name: str = DEFAULT_SYMBOL
    partial_close_ratios: list[float] = field(default_factory=lambda: [0.5, 0.3, 0.2])
    use_martingale: bool = True
    martingale_multiplier: float = 2.0
    martingale_max_level: int = 5
    martingale_lots: list[float] = field(default_factory=list)
    martingale_per_source: bool = False
    martingale_source_lots: dict[str, list[float]] = field(default_factory=dict)
    mt5_files_dir: str = ""
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not self.mt5_files_dir or not Path(self.mt5_files_dir).is_dir():
            self.mt5_files_dir = _find_mt5_files_dir()
        self.symbol_name = self._resolve_symbol_name(self.symbol_name)

    def _resolve_symbol_name(self, configured_symbol: str) -> str:
        configured = (configured_symbol or "").strip()
        detected = detect_mt5_symbol(self.mt5_files_dir)   # "" = 券商檔案裡查不到
        # 券商檔案(symbol_info.json / 有效報價檔)是當前掛著的 EA 寫的,最可信。
        # 查得到具體代號就以它為準 —— 換券商、換代號都跟得上,也不會被設定檔殘留的
        # 舊代號 + 舊價格檔卡死(先前 XAUUSD.s 跨券商沿用、每單被拒的那個雷)。
        if _is_valid_symbol_name(detected):
            if configured and configured != detected:
                logger.info("依券商檔案把 MT5 代號從 %s 更新為 %s", configured, detected)
            return detected
        # 券商檔案還沒出現(EA 未啟動/空目錄)時,保留使用者設定,別蓋成預設。
        return configured or DEFAULT_SYMBOL


def save_config(config: Config, path: Path = CONFIG_FILE) -> None:
    """Persist only current web-client fields; legacy OCR keys are discarded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")


def load_config(path: Path = CONFIG_FILE) -> Config:
    """Load current fields while safely ignoring keys from older desktop builds."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            allowed = {item.name for item in fields(Config)}
            return Config(**{key: item for key, item in value.items() if key in allowed})
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Failed to load config.json: %s", exc)
    return Config()
