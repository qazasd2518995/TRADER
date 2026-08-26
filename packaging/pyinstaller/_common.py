"""Shared PyInstaller configuration for the two Web control-panel roles."""

from pathlib import Path


_CORE = [
    "copy_trader.config",
    "copy_trader.central.web_launcher",
    "copy_trader.central.webui",
    "copy_trader.central.stats",
    "copy_trader.central.market",
    "copy_trader.central.membership",
    "copy_trader.central.member_admin",
    "copy_trader.central.hub_server",
]

_CLIENT = [
    "copy_trader.central.mt5_client_agent",
    "copy_trader.trade_manager.manager",
    "copy_trader.signal_parser.regex_parser",
]

_CENTRAL = [
    "copy_trader.central.signal_collector",
    "copy_trader.signal_parser.regex_parser",
    "copy_trader.signal_parser.strict_parser",   # signal_collector 在頂部就 import
    "copy_trader.line_db.discovery",
    "copy_trader.line_db.factory",
    "copy_trader.line_db.identity",
    "copy_trader.line_db.keys",
    "copy_trader.line_db.ledger",                 # collector 用它記帳(收回/撤單對帳)
    "copy_trader.line_db.models",
    "copy_trader.line_db.source",
    "copy_trader.line_db.sqlite_provider",
    "copy_trader.line_db.windows_credentials",
    "apsw",
]

_EXCLUDES = [
    "PySide6", "PyQt5", "PyQt6", "tkinter",
    "PIL", "cv2", "numpy", "onnxruntime", "rapidocr", "pytesseract",
    "groq", "anthropic", "google.genai", "openai",
    "scipy", "matplotlib", "pandas", "pytest", "IPython", "jupyter",
]


def excludes(role: str) -> list[str]:
    values = list(_EXCLUDES)
    if role == "client":
        values.extend(["apsw", "copy_trader.line_db"])
    return values


def hidden(role: str, _platform: str) -> list[str]:
    modules = _CORE + (_CLIENT if role == "client" else _CENTRAL)
    return list(dict.fromkeys(modules))


def datas(root: Path, role: str) -> list[tuple[str, str]]:
    if role != "client":
        return []
    return [(str(root / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"), "mt5_ea")]


def collect_apsw():
    """Collect SQLite3MC's Python extension and adjacent native assets."""
    from PyInstaller.utils.hooks import collect_all

    return collect_all("apsw")
