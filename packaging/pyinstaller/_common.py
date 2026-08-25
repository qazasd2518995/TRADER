"""四份 .spec 共用的清單。

分開寫的後果已經發生過一次：central-windows.spec 的 hiddenimports 和 datas
是空的，打出來的訊號中心不會夾帶 EA 檔案。這個檔就是為了讓那種漂移不再發生
—— 四份 spec 一律從這裡拿清單，改一次四邊同步。

為什麼需要 hiddenimports：PyInstaller 靠靜態分析找相依，但這個專案有大量
「在函式裡才 import」的寫法（延遲載入避免啟動變慢、或是平台專屬模組），
那種 import 分析器不一定抓得到。抓不到的下場是打包當下沒有錯誤、使用者
按下按鈕才 ModuleNotFoundError。所以下面這份清單是用 AST 掃出來的實際
函式內 import，不是憑印象列的。
"""

from pathlib import Path

# ── 兩個角色都會用到 ────────────────────────────────────────────────
_CORE = [
    "copy_trader.config",
    "copy_trader.platform",
    "copy_trader.central.web_launcher",
    "copy_trader.central.webui",
    "copy_trader.central.stats",
    "copy_trader.central.market",      # K 線與報價（web_launcher 在 do_GET 裡才 import）
    "copy_trader.central.membership",
    "copy_trader.central.member_admin",
    "copy_trader.central.hub_server",
]

# ── 會員端：跟單、下單、讀 MT5 ──────────────────────────────────────
_CLIENT = [
    "copy_trader.central.mt5_client_agent",
    "copy_trader.trade_manager.manager",
    "copy_trader.signal_parser.regex_parser",
    "copy_trader.signal_parser.groq_parser",
    "copy_trader.mt5_reader",
]

# ── 訊號中心：擷取 LINE 視窗、解析、發布 ────────────────────────────
_CENTRAL = [
    "copy_trader.central.signal_collector",
    "copy_trader.signal_capture.clipboard_reader",
    "copy_trader.signal_capture.line_text_parser",
    "copy_trader.signal_capture.window_ocr_reader",
    "copy_trader.signal_capture.screen_capture",
    "copy_trader.signal_capture.ocr",
    "copy_trader.signal_parser.keyword_filter",
    "copy_trader.signal_parser.regex_parser",
]

# ── 平台專屬 ────────────────────────────────────────────────────────
_WINDOWS = [
    "copy_trader.platform.windows",
    "win32api", "win32con", "win32gui", "win32clipboard", "pywintypes",
]
_MACOS = [
    "copy_trader.platform.macos",
    "Quartz", "AppKit",
]

_PIL = ["PIL", "PIL.Image"]

# ── 排除：開發或訓練時才用的重量級套件，夾進去會讓安裝檔多好幾百 MB ──
#
# 兩個角色不能共用同一份排除清單。OCR 那一組（rapidocr / onnxruntime / cv2
# / numpy）對會員端確實是死重量 —— 他不擷取畫面，只讀 MT5 寫出來的檔；但對
# 訊號中心那就是核心：整條擷取管線是 PrintWindow + OCR，剪貼簿那條在
# 2026-07-30 已經整個移除。把它排掉會打出一個「開得起來、但永遠讀不到訊號」
# 的訊號中心。
#
# 實測差別：訊號中心含 OCR 是 278 MB，排掉之後 34 MB —— 少了 244 MB 卻不會
# 有任何建置錯誤，要等使用者按下開始、log 噴 RapidOCR 載入失敗才會發現。
_SHARED_EXCLUDES = [
    "PySide6", "PyQt5", "PyQt6",
    "scipy", "matplotlib", "pandas",
    "pytest", "IPython", "jupyter",
    "groq", "anthropic", "google.genai", "openai",
    "tkinter",
]

# 只有會員端排得掉的 OCR 堆疊
_OCR_STACK = ["rapidocr", "onnxruntime", "cv2", "numpy", "pywt", "PyWavelets"]


def excludes(role: str) -> list:
    """依角色決定排除清單。role: "client" | "central"。"""
    if role == "central":
        return list(_SHARED_EXCLUDES)      # 訊號中心要 OCR，一個都不能排
    return _SHARED_EXCLUDES + _OCR_STACK


# 舊名保留，讓還沒改到的 spec 不會直接壞掉；它一律回會員端那份，
# 訊號中心的 spec 必須改用 excludes("central")。
EXCLUDES = excludes("client")


def hidden(role: str, platform: str) -> list:
    """組出某個角色 × 某個平台要明講的模組清單。

    role: "client" | "central"
    platform: "windows" | "macos"
    """
    mods = list(_CORE)
    mods += _CLIENT if role == "client" else _CENTRAL
    mods += _WINDOWS if platform == "windows" else _MACOS
    mods += _PIL
    # 去重但保持順序，方便 diff 時看得懂
    seen, out = set(), []
    for m in mods:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def datas(root: Path) -> list:
    """要夾帶進安裝檔的資料檔。

    EA 原始碼一定要夾帶：會員裝完之後要把它複製到 MT5 的 Experts 目錄再編譯，
    沒有這個檔他就得另外跟你要，等於安裝流程斷一截。
    """
    return [
        (str(root / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"), "mt5_ea"),
    ]


# OCR 執行期需要整包收進來的套件。
#
# 這些光靠 hiddenimports 是不夠的：真正佔體積也真正不可或缺的是**資料檔**
# —— PP-OCRv6 的 .onnx 模型、opencv 與 onnxruntime 的原生 DLL。PyInstaller
# 的靜態分析看不到那些（模型是執行期用路徑組出來讀的），所以要用 collect_all
# 把每個套件的 .py + 資料檔 + 二進位整包抓進去。
#
# 2026-08-25 這段在 spec 合併時整個掉了，打出來的訊號中心 217 MB、能開、
# 但 RapidOCR 載入就失敗。少了它跟少了 hiddenimports 一樣是靜默失敗。
_OCR_COLLECT = ("rapidocr", "onnxruntime", "cv2", "shapely", "omegaconf", "pyclipper")


def collect_ocr():
    """回傳 (datas, binaries, hiddenimports) 三個要併進 Analysis 的清單。

    只有訊號中心需要呼叫。會員端不擷取畫面，收這些只是讓安裝檔多 244 MB。
    """
    from PyInstaller.utils.hooks import collect_all

    d, b, h = [], [], []
    for pkg in _OCR_COLLECT:
        _d, _b, _h = collect_all(pkg)
        d += _d
        b += _b
        h += _h
    return d, b, h
