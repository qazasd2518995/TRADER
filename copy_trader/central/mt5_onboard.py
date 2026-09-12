"""會員端第一次啟動時，把 MT5 整套設定好並跑起來。

要取代的是安裝說明裡最長也最容易失敗的那一段：找 MT5 資料夾 → 複製 .mq5 →
F4 開 MetaEditor → F7 編譯 → 開 XAUUSD 圖表 → 把 EA 拖上去 → 勾允許演算法
交易。六個步驟，每一步都有人卡住。

這裡用的全是 MetaQuotes 官方支援的機制，不是 UI 自動化：

  config\\common.ini  [Experts] Enabled=1        → 演算法交易總開關
  terminal64.exe /config:x.ini
                     [Common]  Login/Password/Server → 自動登入
                     [StartUp] Expert/Symbol/Period  → 自動開圖並掛 EA

2026-09-11 在一台複製出來的 Exness 模擬環境實測：冷啟動到橋接檔產出約 60 秒，
全程零點擊，連登入視窗都沒有跳。

三個一定要記住的細節
  1. **/portable 不能省。** 少了它 MT5 會改用 %APPDATA%\\MetaQuotes 當資料夾，
     EA 就把橋接檔寫到那邊去 —— 行程活著、圖表正常，但會員端讀的是安裝目錄
     底下的 MQL5\\Files，永遠等不到更新，看起來就像 EA 壞了。
  2. **設定檔裡有明文密碼，用完立刻刪。** 它只是拿來讓 MT5 完成第一次登入；
     之後靠 MT5 自己的 KeepPrivate 記住。密碼不進 settings.json、不上傳 Hub。
  3. **已經在跑就不要再啟動一次。** MT5 對同一個可攜目錄不接受第二個實例，
     而且兩個 EA 同時讀 commands.json 會把同一張單下兩次。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EA_NAME = "MT5_File_Bridge_Enhanced"
EA_FILE = f"{EA_NAME}.ex5"

# 找不到 MT5 時要給會員的去處。給官方頁面而不是直連的 .exe：直連網址會變，
# 而「從我們的程式自動抓一個執行檔下來靜默執行」本身就不是該做的事。
MT5_DOWNLOAD_PAGE = "https://www.exness.com/metatrader-5/"

# 橋接檔比這個新就當作 MT5 已經在正常運作，不要再啟動一次。
FRESH_SECONDS = 90.0

# 冷啟動要下載商品清單、登入、初始化 EA。實測約 60 秒，留一倍餘裕。
LAUNCH_TIMEOUT = 150.0
POLL_INTERVAL = 2.0


@dataclass
class OnboardResult:
    ok: bool
    reason: str = ""
    files_dir: str = ""

    def __bool__(self) -> bool:
        return self.ok


def bundled_ea() -> Optional[Path]:
    """打包進安裝檔的那份 .ex5。找不到回 None。

    帶 .ex5（已編譯）而不是 .mq5 正是重點 —— 會員不用開 MetaEditor 編譯。
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(Path(meipass) / "mt5_ea")
    roots.append(Path(__file__).resolve().parents[2] / "mt5_ea")   # 原始碼樹
    for root in roots:
        candidate = root / EA_FILE
        if candidate.is_file():
            return candidate
    return None


def find_terminal(configured: str = "") -> Optional[Path]:
    """找 terminal64.exe。configured 可以是安裝目錄或執行檔本身。"""
    if configured:
        path = Path(configured.strip().strip('"'))
        if path.is_file() and path.name.lower() == "terminal64.exe":
            return path
        if (path / "terminal64.exe").is_file():
            return path / "terminal64.exe"
        return None
    for base in _default_install_dirs():
        candidate = base / "terminal64.exe"
        if candidate.is_file():
            return candidate
    return None


def _default_install_dirs() -> list[Path]:
    """可能裝著 MT5 的資料夾，由最可能到最不可能。

    **不能只找「MetaTrader 5」這個名字。** 券商版會裝成自己的名字 ——
    Exness 的是「MetaTrader 5 EXNESS」，別家又不一樣，而且同一台可能同時
    裝了好幾個。用 glob 把 MetaTrader 5* 都撈出來，交給呼叫端挑第一個裝得
    起來的。會員自己在設定裡指定路徑時完全不會走到這裡。
    """
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "黃金跟單MT5")     # 我們自己裝的位置優先
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        try:
            out.extend(sorted(p for p in Path(base).glob("MetaTrader 5*")
                              if p.is_dir()))
        except OSError:
            continue
    return out


def files_dir(terminal: Path) -> Path:
    return terminal.parent / "MQL5" / "Files"


def bridge_is_fresh(terminal: Path, within: float = FRESH_SECONDS) -> bool:
    """EA 現在是不是正在寫檔。這是「已經好了」的唯一可靠證據。

    用 account_info.json 而不是 symbol_info.json：後者只在 EA 初始化時寫一次，
    拿它判斷「現在還活著嗎」永遠會是否定的（EA 跑一小時後那個檔就一小時沒動
    過了）。account_info.json 每兩秒重寫一次，才反映得出當下狀態。
    """
    probe = files_dir(terminal) / "account_info.json"
    try:
        return (time.time() - probe.stat().st_mtime) <= within
    except OSError:
        return False


def install_ea(terminal: Path) -> bool:
    """把 .ex5 放進 MQL5\\Experts。已經是同一份就不動它。"""
    source = bundled_ea()
    if source is None:
        logger.warning("安裝檔裡找不到 %s，無法自動安裝 EA", EA_FILE)
        return False
    target_dir = terminal.parent / "MQL5" / "Experts"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / EA_FILE
        if target.is_file() and target.stat().st_size == source.stat().st_size:
            return True
        shutil.copy2(source, target)
        logger.info("EA 已安裝：%s", target)
        return True
    except OSError as exc:
        logger.warning("EA 安裝失敗：%s", exc)
        return False


def enable_algo_trading(terminal: Path) -> None:
    """把 config\\common.ini 的 [Experts] Enabled 設成 1。

    這就是工具列上那顆「演算法交易」按鈕。它是純文字 INI、跟著可攜目錄走，
    所以可以在 MT5 啟動之前先寫好 —— 會員不用自己去點那顆按鈕，而那顆沒開
    的話 EA 掛上去也是哭臉、一張單都下不出去。
    """
    path = terminal.parent / "config" / "common.ini"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() \
            if path.is_file() else []
    except OSError as exc:
        logger.warning("讀不到 common.ini：%s", exc)
        return

    wanted = {"Enabled": "1", "Account": "1", "Profile": "1"}
    out: list[str] = []
    in_experts = False
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_experts:                       # 離開 [Experts] 前補齊沒出現的鍵
                for key, value in wanted.items():
                    if key not in seen:
                        out.append(f"{key}={value}")
                seen.clear()
            in_experts = (stripped.lower() == "[experts]")
        elif in_experts and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                seen.add(key)
                out.append(f"{key}={wanted[key]}")
                continue
        out.append(line)
    if in_experts:
        for key, value in wanted.items():
            if key not in seen:
                out.append(f"{key}={value}")
    elif "[Experts]" not in "\n".join(out):
        out.extend(["[Experts]"] + [f"{k}={v}" for k, v in wanted.items()])

    try:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("寫不了 common.ini：%s", exc)


def _write_startup_ini(terminal: Path, login: str, password: str,
                       server: str, symbol: str, period: str) -> Path:
    """產生一次性的啟動設定檔。**裡面有明文密碼，呼叫端負責刪掉。**"""
    path = terminal.parent / "onboard.ini"
    body = (
        "[Common]\n"
        f"Login={login}\n"
        f"Password={password}\n"
        f"Server={server}\n"
        "KeepPrivate=1\n"          # 讓 MT5 自己記住，之後就不需要這個檔了
        "\n[Experts]\n"
        "Enabled=1\nAccount=1\nProfile=1\n"
        "\n[StartUp]\n"
        f"Expert={EA_NAME}\n"
        f"Symbol={symbol}\n"
        f"Period={period}\n"
    )
    # MT5 讀這個檔用的是系統碼頁，寫 ASCII 最保險（帳號、伺服器、商品代號
    # 都是 ASCII；密碼若含非 ASCII 字元，那本來就不是 MT5 支援的密碼）。
    path.write_text(body, encoding="ascii", errors="replace")
    return path


def onboard(*, mt5_path: str, login: str, password: str, server: str,
            symbol: str = "XAUUSD247m", period: str = "M5",
            timeout: float = LAUNCH_TIMEOUT) -> OnboardResult:
    """裝好 EA、開好演算法交易、登入並掛上圖表，等到橋接檔真的出現為止。

    symbol 預設是 Exness 的 24/7 黃金。**券商沒有這個代號的話，MT5 會開出一張
    沒有資料的圖表** —— 2026-09-12 的 MT5-7 就是這樣：它在 MetaQuotes-Demo 上，
    沒有 XAUUSD247m，於是 EA 掛在一張死圖上。

    EA v4.41 起這件事不再是致命的（ResolveTradeSymbol 會自己去商品清單裡找
    黃金，找不到就每秒重試而不是 INIT_FAILED），但圖表對了還是比較好 ——
    所以這裡先確認代號存在，不存在就退回帳戶裡真的有的那個黃金代號。
    """
    login = (login or "").strip()
    server = (server or "").strip()
    if not login.isdigit():
        return OnboardResult(False, "MT5 帳號應該是一串數字")
    if not server:
        return OnboardResult(False, "請填 MT5 伺服器名稱（例如 Exness-MT5Real43）")
    if not (password or "").strip():
        return OnboardResult(False, "請填 MT5 密碼")

    terminal = find_terminal(mt5_path)
    if terminal is None:
        # 刻意不自動下載安裝檔。那需要從網路抓一個執行檔然後靜默跑起來，
        # 而券商的下載網址沒有可驗證的固定位置 —— 網址失效或被換掉，等於在
        # 會員的電腦上靜默執行不明程式。開戶本來就要去券商網站，順手裝 MT5
        # 是同一趟，而且那一步只是下一步下一步，沒有任何要設定的東西。
        return OnboardResult(
            False,
            f"這台電腦上找不到 MT5。請先到 {MT5_DOWNLOAD_PAGE} 下載安裝，"
            "裝好之後回來再按一次這個按鈕就會自動接手。"
            "（如果你裝在非預設位置，把資料夾填進「MT5 安裝資料夾」）",
        )

    if bridge_is_fresh(terminal):
        # 已經在跑而且 EA 正常。再啟動一次只會多一個實例、多一份下單風險。
        return OnboardResult(True, "MT5 已經在運作", str(files_dir(terminal)))

    if not install_ea(terminal):
        return OnboardResult(False, f"無法把 {EA_FILE} 放進 MQL5\\Experts")
    enable_algo_trading(terminal)

    ini = _write_startup_ini(terminal, login, password, server, symbol, period)
    started = time.time()
    try:
        subprocess.Popen(
            [str(terminal), "/portable", f"/config:{ini}"],
            cwd=str(terminal.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        _shred(ini)
        return OnboardResult(False, f"啟動 MT5 失敗：{exc}")

    probe = files_dir(terminal) / "symbol_info.json"
    deadline = started + max(30.0, timeout)
    try:
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                if probe.stat().st_mtime >= started:
                    return OnboardResult(True, "MT5 已就緒", str(files_dir(terminal)))
            except OSError:
                continue
    finally:
        # 不管成功失敗都要刪 —— 這個檔案裡有明文密碼。
        _shred(ini)

    return OnboardResult(
        False,
        f"MT5 啟動了但 {timeout:.0f} 秒內沒有產出資料。"
        "常見原因：帳號密碼或伺服器名稱不對、這個帳戶不能交易黃金。",
    )


def _shred(path: Path) -> None:
    """刪掉含明文密碼的暫存檔；刪不掉至少先把內容蓋掉。"""
    try:
        path.write_text("", encoding="ascii")
    except OSError:
        pass
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("刪不掉 %s（裡面有明文密碼）：%s", path, exc)
