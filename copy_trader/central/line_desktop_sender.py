"""用登入中的 LINE 桌面版，把通知發到自己的社群。

為什麼不是官方帳號 Bot
  目標是 LINE **社群(OpenChat)**。Messaging API 的 push 只收 user/group/room
  三種 ID，社群不在裡面 —— Bot 結構上就到不了這群人。就算到得了，免費額度
  是「按送達人數」扣的 200 則/月（見 hub_server._line_error_zh），會員一多
  就更不可能。

為什麼不是直接寫 LINE 的資料庫
  .edb 是「送出之後」才寫進去的鏡子：沒有待送佇列，訊息 id 也是伺服器發的
  snowflake。寫一列進去等於零封包出去，只是自己騙自己。

所以走 UI 自動化，但只用最輕的那一段
  LINE 26.4 的輸入框(AutoSuggestTextArea)有**可寫的** UIA ValuePattern，
  文字是程式化寫進去的 —— 不碰剪貼簿（避開剪貼簿汙染那類坑），也不逐字
  模擬鍵盤（打到一半被搶焦點就會把字打進別的視窗，這台還開著六個 MT5）。
  只有最後送出那一下需要焦點：SetFocus + Enter，前後約 0.5 秒，然後把焦點
  還回去。

送出之後一定要回執
  UI 自動化最可怕的不是失敗，是「以為送出去了其實沒有」。所以每一則都回頭
  查 LINE 資料庫，確認那則訊息真的以自己的身分落地了。DB 沒有 = 沒送到，
  由呼叫端決定重試或改走官方帳號補發。
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 聊天室拉成獨立視窗時，視窗類別固定是這個，標題就是聊天室名稱。
# 只比對標題會中 LineMediaPlayer 的同名視窗陷阱 —— 那個是別的行程，
# 所以視窗類別跟行程名兩個都要對。
CHAT_WINDOW_CLASS = "ChatWindow"
LINE_PROCESS = "line.exe"
INPUT_CLASS = "AutoSuggestTextArea"     # UIA 樹裡唯一的 EditControl

SEND_MIN_INTERVAL = 2.0                 # 兩則之間至少隔這麼久，免得被判洗版
RECEIPT_TIMEOUT = 20.0                  # 等 DB 回執最多這麼久
RECEIPT_POLL = 1.0
UIA_MAX_DEPTH = 14                      # 輸入框在第 9 層，留點餘裕


@dataclass
class SendResult:
    ok: bool
    reason: str = ""
    message_id: str = ""
    rowid: int = 0

    def __bool__(self) -> bool:
        return self.ok


def _process_name(pid: int) -> str:
    """行程執行檔名（小寫）。查不到回空字串 —— 查不到就當作不是 LINE。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def default_database_path() -> Optional[str]:
    """訊號中心設定裡指定的 .edb。找不到回 None（讓 provider 去自動搜尋）。

    不能靠自動搜尋：這台機器的 db 目錄裡有六個帳號 × album_/keep_/chatStats_
    的舊檔，discovery 會直接以「候選太多」失敗。而訊號中心早就在設定裡指好了
    唯一那個 —— 跟著它走，讀取端跟發送端才不會各讀各的檔。
    開發時 DATA_DIR 是原始碼目錄、打包後才是 APPDATA，兩個都找。
    """
    from copy_trader.config import DATA_DIR

    candidates = [Path(DATA_DIR) / "central_web_launcher_settings.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "黃金跟單系統" / "central_web_launcher_settings.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        value = str((data or {}).get("line_database_path") or "").strip()
        if value:
            return value
    return None


def workstation_locked() -> bool:
    """螢幕鎖住了嗎。鎖住的話 SetFocus/Enter 一定送不出去，要先知道。"""
    user32 = ctypes.windll.user32
    DESKTOP_SWITCHDESKTOP = 0x0100
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False


class LineDesktopSender:
    """把文字送進指定的 LINE 聊天視窗，並用 LINE 資料庫確認送達。

    執行緒安全：內部有鎖，同一時間只會有一則在送。UIA 是 COM，跨執行緒要各自
    初始化，所以送出那段自己包了 thread initializer。
    """

    def __init__(
        self,
        window_title: str,
        database_path: str | Path | None = None,
        keychain_service: str = "line-db-research",
        min_interval: float = SEND_MIN_INTERVAL,
    ):
        self.window_title = window_title
        self.database_path = database_path or default_database_path()
        self.keychain_service = keychain_service
        self.min_interval = float(min_interval)
        self._lock = threading.Lock()
        self._last_sent_at = 0.0
        self._provider = None
        self._chat: Optional[Tuple[str, str, str]] = None   # (chat_id, my_mid, kind)

    # ---------- LINE 資料庫（回執用） ----------

    def _connection(self):
        """讀取用連線。斷掉就重開 —— LINE 換帳號時 .edb 會整個換檔。"""
        from copy_trader.line_db.keys import default_key_provider
        from copy_trader.line_db.sqlite_provider import SQLiteLineDatabaseProvider

        if self._provider is None:
            self._provider = SQLiteLineDatabaseProvider(
                self.database_path, default_key_provider(self.keychain_service)
            )
        try:
            connection = self._provider.connect()
            connection.execute("SELECT 1").fetchone()
            return connection
        except Exception:
            self._provider = None
            self._chat = None
            raise

    def resolve_chat(self) -> Tuple[str, str, str]:
        """視窗標題 -> (chat_id, 我在這個聊天室的發訊者 ID, 類型)。

        社群跟群組的 _from 不是同一種 ID：社群是 squareMemberMid(p…)，群組是
        自己的 mid(u…)。回執比對要用對的那個，否則永遠對不上。
        """
        if self._chat is not None:
            return self._chat
        connection = self._connection()
        rows = connection.execute(
            'SELECT c."_squareChatMid", s."_myMemberId" FROM "_squareChat" AS c '
            'JOIN "_square" AS s ON s."_mid"=c."_squareMid" WHERE c."_name"=?',
            (self.window_title,),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError(f"LINE 社群聊天室名稱不唯一：{self.window_title}")
        if rows:
            self._chat = (str(rows[0][0]), str(rows[0][1] or ""), "openchat")
            return self._chat

        rows = connection.execute(
            'SELECT "_chatMid" FROM "_groupChat" WHERE "_chatName"=?',
            (self.window_title,),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError(f"LINE 群組名稱不唯一：{self.window_title}")
        if rows:
            me = connection.execute('SELECT "_mid" FROM "_profile"').fetchone()
            self._chat = (str(rows[0][0]), str(me[0] if me else ""), "group")
            return self._chat

        raise RuntimeError(f"LINE 資料庫裡找不到這個聊天室：{self.window_title}")

    def _max_rowid(self, chat_id: str) -> int:
        row = self._connection().execute(
            'SELECT COALESCE(max(rowid),0) FROM "_message" WHERE "_chatId"=?', (chat_id,)
        ).fetchone()
        return int(row[0] or 0)

    def _await_receipt(self, chat_id: str, my_mid: str, text: str,
                       after_rowid: int) -> Optional[Tuple[int, str]]:
        """等這則訊息以自己的身分落地。回 (rowid, message_id)，逾時回 None。"""
        deadline = time.time() + RECEIPT_TIMEOUT
        while time.time() < deadline:
            rows = self._connection().execute(
                'SELECT rowid, "_id", "_from", "_text" FROM "_message" '
                'WHERE "_chatId"=? AND rowid>? ORDER BY rowid',
                (chat_id, after_rowid),
            ).fetchall()
            for rowid, message_id, sender, body in rows:
                if sender == my_mid and (body or "") == text:
                    return int(rowid), str(message_id or "")
            time.sleep(RECEIPT_POLL)
        return None

    # ---------- 視窗 ----------

    def _find_window(self, auto):
        for window in auto.GetRootControl().GetChildren():
            try:
                if window.ClassName != CHAT_WINDOW_CLASS or window.Name != self.window_title:
                    continue
                if _process_name(window.ProcessId) != LINE_PROCESS:
                    continue        # 同名視窗陷阱：標題一樣但不是 LINE 本身
                return window
            except Exception:       # noqa: BLE001 — 列舉途中視窗可能剛好被關掉
                continue
        return None

    def _find_input(self, control, depth: int = 0):
        if depth > UIA_MAX_DEPTH:
            return None
        for child in control.GetChildren():
            try:
                if child.ControlTypeName == "EditControl" and child.ClassName == INPUT_CLASS:
                    return child
            except Exception:       # noqa: BLE001
                continue
            found = self._find_input(child, depth + 1)
            if found is not None:
                return found
        return None

    # ---------- 啟動自檢 ----------

    def preflight(self) -> SendResult:
        """啟動時就把這條路走一遍，問題要講在第一筆訊號之前。

        送出是背景 thread 裡的旁路，壞掉只會留一行 warning —— 沒有這個自檢，
        「打包漏了 uiautomation」「聊天視窗沒開」都要等到第一筆真訊號要發時
        才發現，而那時候已經漏掉一則了。

        uiautomation 匯入不了會直接 raise：那是**永遠不會好**的（打包沒帶到
        comtypes 的 UIA 包裝），呼叫端應該整個停用而不是留著假裝可用。
        其餘的（視窗沒開、聊天室改名）等一下可能就好了，回 SendResult 讓
        呼叫端警告但保留。
        """
        import uiautomation as auto            # 失敗就讓它往上拋

        try:
            _chat_id, my_mid, kind = self.resolve_chat()
        except Exception as exc:               # noqa: BLE001
            return SendResult(False, f"LINE 資料庫查不到這個聊天室：{exc}")
        if not my_mid:
            return SendResult(False, "查不到自己在這個聊天室的身分，送達回執會永遠對不上")
        try:
            with auto.UIAutomationInitializerInThread(debug=False):
                auto.SetGlobalSearchTimeout(3)
                window = self._find_window(auto)
                if window is None:
                    return SendResult(False, "聊天視窗目前沒開（要把該聊天室拉成獨立視窗）")
                if self._find_input(window) is None:
                    return SendResult(False, "找得到聊天視窗，但找不到輸入框")
        except Exception as exc:               # noqa: BLE001
            return SendResult(False, f"檢查聊天視窗失敗：{exc}")
        return SendResult(True, f"就緒（{kind}）")

    # ---------- 送出 ----------

    def send(self, text: str, *, verify_receipt: bool = True) -> SendResult:
        text = (text or "").strip()
        if not text:
            return SendResult(False, "空訊息")
        with self._lock:
            return self._send_locked(text, verify_receipt)

    def _send_locked(self, text: str, verify_receipt: bool) -> SendResult:
        import uiautomation as auto

        wait = self.min_interval - (time.time() - self._last_sent_at)
        if wait > 0:
            time.sleep(wait)
        if workstation_locked():
            return SendResult(False, "螢幕已鎖定，UI 自動化送不出訊息")

        chat_id = my_mid = ""
        baseline = 0
        if verify_receipt:
            try:
                chat_id, my_mid, _kind = self.resolve_chat()
                baseline = self._max_rowid(chat_id)
            except Exception as exc:                    # noqa: BLE001
                # 這裡失敗有兩種：資料庫讀不到，或聊天室被改名了。訊息要能分辨，
                # 否則改名會被當成資料庫壞掉，往完全錯的方向查。
                return SendResult(False, f"送達確認的前置查詢失敗：{exc}")

        user32 = ctypes.windll.user32
        with auto.UIAutomationInitializerInThread(debug=False):
            auto.SetGlobalSearchTimeout(3)
            window = self._find_window(auto)
            if window is None:
                return SendResult(False, f"找不到聊天視窗：{self.window_title}（是不是被關掉了）")
            box = self._find_input(window)
            if box is None:
                return SendResult(False, "找不到訊息輸入框（LINE 改版了？）")

            value = box.GetValuePattern()
            existing = value.Value or ""
            if existing:
                # 你正在打字。蓋掉別人打到一半的字，比晚一點發嚴重得多。
                return SendResult(False, f"輸入框有未送出的草稿，先不動它：{existing[:20]!r}")

            value.SetValue(text)
            time.sleep(0.4)
            staged = box.GetValuePattern().Value or ""
            if staged != text:
                box.GetValuePattern().SetValue("")
                return SendResult(False, f"輸入框內容與預期不符，已清空並中止：{staged[:40]!r}")

            previous = user32.GetForegroundWindow()
            box.SetFocus()
            time.sleep(0.2)
            box.SendKey(auto.Keys.VK_RETURN, waitTime=0.3)
            try:
                if previous and previous != window.NativeWindowHandle:
                    user32.SetForegroundWindow(previous)
            except Exception:                           # noqa: BLE001
                pass

            leftover = box.GetValuePattern().Value or ""
            if leftover:
                # Enter 沒被吃掉。留著會在下一則的草稿檢查擋住自己，先清掉。
                box.GetValuePattern().SetValue("")
                return SendResult(False, "Enter 沒有送出訊息，輸入框仍有內容")

        self._last_sent_at = time.time()
        if not verify_receipt:
            return SendResult(True, "已送出（未驗證）")

        receipt = self._await_receipt(chat_id, my_mid, text, baseline)
        if receipt is None:
            return SendResult(False, f"送出後 {RECEIPT_TIMEOUT:.0f} 秒內資料庫沒有這則訊息")
        rowid, message_id = receipt
        logger.info("LINE 桌面發送成功：%s rowid=%s id=%s",
                    self.window_title, rowid, message_id)
        return SendResult(True, "已送出並確認", message_id=message_id, rowid=rowid)

    def send_with_retry(self, text: str, attempts: int = 2) -> SendResult:
        """重試。草稿佔用跟螢幕鎖定屬於「等一下也不會好」，不浪費次數重試。"""
        result = SendResult(False, "沒有嘗試")
        for attempt in range(1, max(1, attempts) + 1):
            result = self.send(text)
            if result.ok or "草稿" in result.reason or "螢幕已鎖定" in result.reason:
                return result
            logger.warning("LINE 桌面發送第 %s 次失敗：%s", attempt, result.reason)
            time.sleep(1.5)
        return result


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="用 LINE 桌面版發一則訊息")
    parser.add_argument("--title", required=True, help="聊天視窗標題（＝聊天室名稱）")
    parser.add_argument("--text", required=True, help="訊息內容，\\n 換行")
    parser.add_argument("--db", default=None, help="LINE .edb 路徑，預設自動尋找")
    parser.add_argument("--service", default="line-db-research")
    parser.add_argument("--dry-run", action="store_true",
                        help="只寫進輸入框後清掉，不送出")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sender = LineDesktopSender(args.title, args.db, args.service)
    text = args.text.replace("\\n", "\n")

    if args.dry_run:
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread(debug=False):
            auto.SetGlobalSearchTimeout(3)
            window = sender._find_window(auto)
            if window is None:
                print("找不到聊天視窗:", args.title)
                return 1
            box = sender._find_input(window)
            box.GetValuePattern().SetValue(text)
            time.sleep(0.4)
            print("staged:", repr(box.GetValuePattern().Value))
            box.GetValuePattern().SetValue("")
            print("已清空，未送出")
        return 0

    result = sender.send_with_retry(text)
    print(("✅ " if result.ok else "❌ ") + result.reason,
          f"rowid={result.rowid} id={result.message_id}" if result.ok else "")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
