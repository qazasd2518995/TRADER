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
    # MT5 一鍵設定。web_launcher 是在函式裡才 import（設定過程壞掉不能讓整個
    # 會員端起不來），這種延遲 import 不保證被靜態分析抓到 —— 漏了的話按鈕
    # 會回「No module named ...」，而那要等到會員真的按下去才發現。
    "copy_trader.central.mt5_onboard",
]

_CENTRAL = [
    "copy_trader.central.signal_collector",
    "copy_trader.central.ultra_strategy",
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
    # 執行設定影子對照。web_launcher 是在函式裡才 import（壞掉也不能影響
    # 訊號），這種延遲 import 不保證被靜態分析抓到，明確列出來。
    "copy_trader.central.bar_store",
    "copy_trader.central.exec_shadow",
    "copy_trader.backtest",
    "copy_trader.backtest.engine",
    "apsw",
]

# LINE 桌面版社群播報。只有 Windows 有 —— 它驅動的是 Windows 版 LINE client
# 的 UI Automation 介面，macOS 版沒有對應的東西，混進共用清單會弄壞 mac 建置。
# web_launcher 一樣是在函式裡才 import，所以要明確列出來。
_CENTRAL_WINDOWS = [
    "copy_trader.central.line_desktop_sender",
    "uiautomation",
]

# 管理端：只跟雲端 Hub 講話，會員資料全在那邊。沒有 LINE、沒有 MT5，
# 也沒有任何發布路徑 —— 它連 signal_collector 都不打包，結構上就發不了訊號。
_ADMIN: list[str] = []

_EXCLUDES = [
    "PySide6", "PyQt5", "PyQt6", "tkinter",
    "PIL", "cv2", "numpy", "onnxruntime", "rapidocr", "pytesseract",
    "groq", "anthropic", "google.genai", "openai",
    "scipy", "matplotlib", "pandas", "pytest", "IPython", "jupyter",
]


def excludes(role: str) -> list[str]:
    values = list(_EXCLUDES)
    if role != "central":
        # 社群播報只屬於訊號端。會員端與管理端沒有 LINE 資料庫可以對回執，
        # 打包進去只是多一條能被誤觸的發文路徑。
        values.extend(["uiautomation", "comtypes",
                       "copy_trader.central.line_desktop_sender"])
    if role == "client":
        values.extend(["apsw", "copy_trader.line_db"])
    if role == "admin":
        # 把訊號擷取與發布整段排除。這是安全設計不是瘦身：管理端跟訊號端
        # 連同一個 Hub，只要打包進去就有機會被啟動，兩台同時發單 = 會員
        # 重複下單。少一份程式碼就少一種出錯方式。
        values.extend(["apsw", "copy_trader.line_db",
                       "copy_trader.central.signal_collector",
                       "copy_trader.central.ultra_strategy",
                       "copy_trader.central.exec_shadow",
                       "copy_trader.central.bar_store",
                       "copy_trader.backtest", "copy_trader.strategy"])
    return values


def hidden(role: str, platform: str) -> list[str]:
    modules = _CORE + {"client": _CLIENT, "admin": _ADMIN}.get(role, _CENTRAL)
    if role == "central" and platform == "windows":
        modules = modules + _CENTRAL_WINDOWS
    return list(dict.fromkeys(modules))


def datas(root: Path, role: str) -> list[tuple[str, str]]:
    if role != "client":
        return []
    # **.ex5 是主角**：帶了它，會員就不用開 MetaEditor 按 F7 編譯 —— 那是整份
    # 安裝說明裡最容易失敗的一步(要找對資料夾、要看懂 0 errors)。.ex5 是跨機器
    # 的 bytecode，直接複製進 MQL5\Experts 就能掛。
    # .mq5 原始碼仍然一起帶：會員或客服想確認「這支 EA 到底做了什麼」時看得到，
    # 而且真的遇到 build 不相容時還能自己重編一次。
    compiled = root / "mt5_ea" / "MT5_File_Bridge_Enhanced.ex5"
    source = root / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"
    # 改了 .mq5 卻忘了重編，安裝檔就會帶著舊邏輯出貨 —— 而且完全看不出來，
    # 會員那邊只會表現成「某些情況下行為跟說明不一樣」。寧可在這裡擋下建置。
    # 重編：<任一台 MT5>\metaeditor64.exe /compile:"<.mq5 絕對路徑>"
    if compiled.is_file() and source.is_file() and \
            compiled.stat().st_mtime < source.stat().st_mtime:
        raise SystemExit(
            f"{compiled.name} 比 {source.name} 舊 —— 請先用 metaeditor64.exe "
            "重新編譯 EA，否則安裝檔會帶著舊版的下單邏輯出貨。"
        )
    return [
        (str(compiled), "mt5_ea"),
        (str(source), "mt5_ea"),
    ]


def collect_apsw():
    """Collect SQLite3MC's Python extension and adjacent native assets."""
    from PyInstaller.utils.hooks import collect_all

    return collect_all("apsw")


def collect_uia():
    """comtypes 對 UIAutomation 的包裝是**執行時產生**的，凍結之後產不出來。

    uiautomation 初始化時會呼叫 comtypes.client.GetModule("UIAutomationCore.dll")，
    它預設把產生的 Python 包裝寫進 comtypes/gen —— 在 exe 裡那是唯讀的。把開發機
    上已經產好的 comtypes.gen.*（UIAutomationClient 與它依賴的那幾支）一起打包，
    GetModule 就會直接沿用現成的，不會嘗試重新產生。

    漏掉的話不會爆在建置階段，而是打包後的訊號中心一啟用社群播報就 import 失敗 ——
    而那條路徑是「壞掉也不能影響訊號」，例外被吞成一行 warning，等於靜靜不發。
    """
    from PyInstaller.utils.hooks import collect_all, collect_submodules

    datas_, binaries_, hidden_ = collect_all("comtypes")
    return datas_, binaries_, hidden_ + collect_submodules("uiautomation")
