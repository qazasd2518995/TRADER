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


def detect_mt5_symbol(mt5_files_dir: str) -> str:
    """Infer the broker-specific gold symbol from bridge files."""
    directory = Path(mt5_files_dir or "")
    if not directory.is_dir():
        return DEFAULT_SYMBOL
    symbol = str(_read_json_dict(directory / "symbol_info.json").get("symbol") or "").strip()
    if _is_valid_symbol_name(symbol):
        return symbol
    try:
        price_files = sorted(directory.glob("*_price.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        price_files = []
    for path in price_files:
        symbol = str(_read_json_dict(path).get("symbol") or "").strip()
        if _is_valid_symbol_name(symbol):
            return symbol
        inferred = path.name.removesuffix("_price.json")
        if _is_valid_symbol_name(inferred):
            return inferred
    return DEFAULT_SYMBOL


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
        detected = detect_mt5_symbol(self.mt5_files_dir)
        if configured and (Path(self.mt5_files_dir) / f"{configured}_price.json").exists():
            return configured
        if configured and configured != detected:
            logger.info("Resolved MT5 symbol from %s to %s based on broker files", configured, detected)
        return detected or configured or DEFAULT_SYMBOL


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
