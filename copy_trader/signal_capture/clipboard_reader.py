"""
剪貼板採集服務 (跟 ScreenCaptureService 平行的訊號來源)

職責：
  1. 對每個設定的 LINE 視窗，呼叫平台層 copy_chat_tail() 拿到「最近 N 屏」純文字
  2. 用 LineTextParser 切成訊息列表
  3. 與上一輪的 seen_keys 做 diff，只回傳新增訊息
  4. 針對第一次啟動做 "baseline" 處理 — 把目前看到的全部訊息視為已讀，
     避免把幾小時前的舊報單當作新信號送出

觸發策略（避免每秒都搶焦點）：
  * 優先來源：LINE 視窗標題中的未讀數 `(N)` — 變動時強制觸發
  * 兜底：距上次成功複製 ≥ stale_seconds（預設 10s）
  * 首次：必定觸發，用於建立 baseline
  * 複製完成後再以「整塊剪貼板文字的 md5」做一次比對 — 與上次相同就直接跳過
    parser，避免浪費 CPU（更重要：避免把同一批訊息重覆餵下游 dedup）

下游（app.py）拿到新訊息後，仍然走既有的 RegexSignalParser → Vision fallback
→ TradeManager 流程；這層只負責「把 OCR 換成剪貼板」。
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional, Set, Tuple

from copy_trader.platform import ClipboardControl, ScreenCapture, WindowInfo

from .line_text_parser import LineMessage, LineTextParser, diff_new_messages

logger = logging.getLogger(__name__)

# 只在模組載入時嘗試 import 一次；非 Windows 直接標記 None，
# _peek_title() 就能無痛退回 enumerate_windows fallback，不會每秒 raise 一次。
try:
    import win32gui as _win32gui  # type: ignore[import-not-found]
except Exception:
    _win32gui = None

# 虛擬桌面 helper — 只在 Windows 平台有值
_get_vdm = None
if sys.platform == "win32":
    try:
        from copy_trader.platform.windows import get_virtual_desktop_manager as _get_vdm  # type: ignore
    except Exception:
        _get_vdm = None


# ----- 未讀數 regex -----
# LINE 視窗標題樣式（實際格式尚待 Windows 實機驗證；此處涵蓋常見變體）：
#   "黃金報單🈲言群"              → 無未讀
#   "黃金報單🈲言群 (3)"         → 3 則未讀（半形）
#   "(2) 黃金報單🈲言群"         → 放前面的半形
#   "黃金報單 [5]"                → 方括號
#   "黃金報單（3）"               → 全形括號
#   "黃金報單【8】"               → 全形方括號
_RE_UNREAD_TAIL = re.compile(r'[\(\[\uFF08\u3010](\d{1,3})[\)\]\uFF09\u3011]\s*$')
# 頭部：全形括號後可能沒空白（「（2）群」），半形後通常會有空白 — 兩種都接。
_RE_UNREAD_HEAD = re.compile(r'^[\(\[\uFF08\u3010](\d{1,3})[\)\]\uFF09\u3011]\s*')


def _extract_unread_count(title: str) -> Optional[int]:
    """從視窗標題裡抽出未讀數，找不到回傳 None。"""
    if not title:
        return None
    m = _RE_UNREAD_TAIL.search(title)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = _RE_UNREAD_HEAD.match(title)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


# -------------------- 設定與來源 --------------------

@dataclass
class ClipboardWindow:
    """要讀取的 LINE 視窗設定。"""
    name: str                    # 內部識別碼 (對應 CaptureWindow.name)
    window_name: str             # 用來比對的視窗標題 (子字串/不區分大小寫)
    display_name: str = ""       # UI 顯示用
    window_id: Optional[int] = None  # 已經找到的 hwnd，下次復用；找不到時重新搜
    screens: int = 2             # Shift+PgUp 次數
    copy_mode: str = "tail"      # "tail" = 底部 N 屏, "all" = Ctrl+A 全選複製

    @property
    def label(self) -> str:
        return self.display_name or self.window_name or self.name


# -------------------- 結果 --------------------

@dataclass
class ClipboardCapture:
    """單次對一個視窗剪貼板採集的結果。"""
    source_name: str             # ClipboardWindow.name
    display_name: str
    window_id: Optional[int]
    all_messages: List[LineMessage] = field(default_factory=list)
    new_messages: List[LineMessage] = field(default_factory=list)
    raw_text: str = ""
    elapsed_ms: float = 0.0
    ok: bool = False
    error: str = ""
    skipped: bool = False        # True = 沒搶焦點、沒複製（因觸發條件未滿足）
    skip_reason: str = ""        # e.g. "no_unread_and_not_stale"
    unread: Optional[int] = None # 觸發時讀到的未讀數
    title: str = ""              # 觸發時視窗標題


# -------------------- 服務 --------------------

class ClipboardReaderService:
    """
    每輪被呼叫時：對每個 ClipboardWindow 判斷是否需要複製，
    只在需要時才抢焦點 Ctrl+C。複製完再 diff 出新訊息。

    觸發條件（任一成立即觸發）：
      1. 未讀數與上次不同（標題裡有 `(N)` 格式）
      2. 距上次成功複製 ≥ stale_seconds
      3. 首次（尚未 baseline）

    複製回來的整塊文字會做 md5 比對：
      - 與上次相同 → 直接跳過 parser 與下游 dedup（省 CPU、避免重覆 log）
      - 不同 → 跑 parser + diff
    """

    SEEN_KEYS_MAX = 500          # 每個來源保留多少訊息 key 做去重
    WINDOW_ID_RETRY_INTERVAL = 30.0  # 找不到 hwnd 時多久重新搜一次
    DEFAULT_STALE_SECONDS = 10.0  # 兜底：距上次成功複製超過這麼久就強制複製一次

    def __init__(
        self,
        windows: List[ClipboardWindow],
        stale_seconds: float = DEFAULT_STALE_SECONDS,
    ):
        self.windows = list(windows)
        self.clipboard = ClipboardControl()
        self.screen = ScreenCapture()
        self.parser = LineTextParser()
        self.stale_seconds = max(1.0, float(stale_seconds))

        # 每個 source 維護一組「已看過的 key」，用 deque 做滑動視窗
        self._seen_keys: Dict[str, Deque[Tuple[str, str, str]]] = {
            w.name: deque(maxlen=self.SEEN_KEYS_MAX) for w in windows
        }
        self._seen_set: Dict[str, Set[Tuple[str, str, str]]] = {
            w.name: set() for w in windows
        }
        # Baseline：第一次讀到時把全部當已讀，避免歷史訊息觸發下單
        self._baselined: Set[str] = set()
        # 上次成功找到 hwnd 的時間（給 window_id 失效重試用）
        self._last_lookup_at: Dict[str, float] = {}

        # ----- 觸發狀態 -----
        # 上一輪讀到的未讀數（看標題 `(N)`）；None = 沒看到 (N) 或視窗不存在
        self._last_unread: Dict[str, Optional[int]] = {w.name: None for w in windows}
        # 上次「有效複製完成」的時間 — 成功取得非空文字才更新
        self._last_copy_at: Dict[str, float] = {w.name: 0.0 for w in windows}
        # 上次複製到的整塊文字 md5
        self._last_text_hash: Dict[str, str] = {w.name: "" for w in windows}
        # 全選複製模式會拿到完整聊天記錄；用「下游已確認處理」的最後 key
        # 作為切點，避免 seen deque 截斷後把很舊的訊息重新視為新訊息。
        self._last_marked_key: Dict[str, Optional[Tuple[str, str, str]]] = {
            w.name: None for w in windows
        }

    # -------- public --------

    def capture_all(self) -> List[ClipboardCapture]:
        """對所有設定視窗各做一次剪貼板讀取。"""
        out: List[ClipboardCapture] = []
        for w in self.windows:
            try:
                out.append(self._capture_one(w))
            except Exception as e:
                logger.exception(f"clipboard capture failed for {w.label}: {e}")
                out.append(ClipboardCapture(
                    source_name=w.name,
                    display_name=w.label,
                    window_id=w.window_id,
                    ok=False,
                    error=str(e),
                ))
        return out

    def mark_seen(self, source_name: str, messages: List[LineMessage]) -> None:
        """把訊息加入 seen 集合（下游處理完畢後呼叫，避免重覆下單）。"""
        seen_set = self._seen_set.get(source_name)
        seen_deque = self._seen_keys.get(source_name)
        if seen_set is None or seen_deque is None:
            return
        for m in messages:
            k = m.key
            if k in seen_set:
                continue
            if len(seen_deque) == seen_deque.maxlen:
                old = seen_deque.popleft()
                seen_set.discard(old)
            seen_deque.append(k)
            seen_set.add(k)
        if messages:
            self._last_marked_key[source_name] = messages[-1].key

    def force_retry(self, source_name: str) -> None:
        """
        讓下一輪即使剪貼板全文相同也重新 parse。

        下游若發布 Hub 或寫 MT5 失敗，訊息不能 mark_seen；若不清掉 hash，
        下一輪會因 identical text shortcut 直接跳過，導致無法重試。
        """
        if source_name in self._last_text_hash:
            self._last_text_hash[source_name] = ""
        if source_name in self._last_copy_at:
            self._last_copy_at[source_name] = 0.0

    # -------- window id resolution --------

    def _resolve_window_id(self, w: ClipboardWindow) -> Optional[int]:
        now = time.time()
        # 若已有 id，先驗證是否還存在
        if w.window_id:
            try:
                rect = self.screen.get_window_rect(w.window_id)
                if rect:
                    return w.window_id
            except Exception:
                pass
            w.window_id = None  # 失效了

        last = self._last_lookup_at.get(w.name, 0.0)
        if now - last < self.WINDOW_ID_RETRY_INTERVAL and w.window_id is None and last != 0.0:
            # 節流：避免每秒都對整個桌面 EnumWindows
            return None

        self._last_lookup_at[w.name] = now
        try:
            hits: List[WindowInfo] = self.screen.enumerate_windows(w.window_name)
        except Exception as e:
            logger.debug(f"enumerate_windows failed: {e}")
            return None

        if not hits:
            logger.debug(f"no window matched: {w.window_name!r}")
            return None

        # 與 screen_capture 一致：偏好標題最短（最貼近精確匹配）的那個
        hits.sort(key=lambda h: len(h.title))
        w.window_id = hits[0].window_id
        logger.info(f"resolved clipboard window {w.label!r} → hwnd={w.window_id} ({hits[0].title!r})")
        return w.window_id

    # -------- title-only probe (cheap, no focus steal) --------

    def _peek_title(self, hwnd: int) -> str:
        """讀視窗標題（不搶焦點、不截圖）。找不到回傳空字串。"""
        if _win32gui is not None:
            try:
                return _win32gui.GetWindowText(hwnd) or ""
            except Exception as e:
                logger.debug(f"GetWindowText failed for hwnd={hwnd}: {e}")
                return ""
        # 非 Windows 平台（macOS 開發測試用）— 回傳空字串即可。
        # 不呼叫 enumerate_windows("") — 那會枚舉整個桌面、非常昂貴。
        return ""

    def _should_copy(self, w: ClipboardWindow, hwnd: int) -> Tuple[bool, str, str, Optional[int]]:
        """
        判斷是否該對這個視窗執行 copy_chat_tail。
        回傳 (should_copy, reason, title, unread)。
        """
        title = self._peek_title(hwnd)
        unread = _extract_unread_count(title)
        now = time.time()
        last_copy = self._last_copy_at.get(w.name, 0.0)
        last_unread = self._last_unread.get(w.name, None)

        # 1. 首次 → 必定觸發（要建 baseline）
        #    注意：baseline 忽略虛擬桌面判斷，因為若歷史訊息沒被 mark_seen，
        #    使用者切到 LINE 桌面的第一瞬間會把全部歷史訊息當新訊號觸發下單。
        #    Baseline 只有在程式啟動時發生一次，接受短暫跨桌面 focus。
        if w.name not in self._baselined:
            return True, "first_baseline", title, unread

        # 0. 若 LINE 在另一個虛擬桌面 → 完全跳過，避免把使用者拉過去
        #    之後使用者切回 LINE 所在桌面時會自動恢復
        if _get_vdm is not None:
            try:
                if not _get_vdm().is_window_on_current_desktop(hwnd):
                    return False, "other_virtual_desktop", title, unread
            except Exception:
                pass

        # 2. 未讀數有變化 → 強觸發
        #    這裡要小心：LINE 自己聚焦的群 unread 會一直是 None，這時 fall through 到規則 3
        if unread is not None and unread != last_unread:
            return True, f"unread_changed({last_unread}→{unread})", title, unread

        # 3. 兜底：太久沒複製
        if now - last_copy >= self.stale_seconds:
            return True, f"stale>{self.stale_seconds:.0f}s", title, unread

        # 4. 其他 — 跳過
        return False, "no_change", title, unread

    # -------- core capture --------

    def _capture_one(self, w: ClipboardWindow) -> ClipboardCapture:
        t0 = time.time()
        cap = ClipboardCapture(
            source_name=w.name,
            display_name=w.label,
            window_id=w.window_id,
        )

        hwnd = self._resolve_window_id(w)
        if not hwnd:
            cap.error = "window_not_found"
            return cap
        cap.window_id = hwnd

        should, reason, title, unread = self._should_copy(w, hwnd)
        cap.title = title
        cap.unread = unread

        if not should:
            cap.skipped = True
            cap.skip_reason = reason
            cap.ok = True  # 非錯誤，只是無動作
            logger.debug(f"clipboard skip {w.label!r}: {reason} (title={title!r}, unread={unread})")
            return cap

        logger.debug(f"clipboard trigger {w.label!r}: {reason} (title={title!r}, unread={unread})")

        copy_mode = (w.copy_mode or "tail").strip().lower()
        try:
            if copy_mode == "all" and hasattr(self.clipboard, "copy_chat_all"):
                text = self.clipboard.copy_chat_all(hwnd)
            else:
                text = self.clipboard.copy_chat_tail(hwnd, screens=max(1, int(w.screens)))
        except Exception as e:
            cap.error = f"copy_failed:{e}"
            return cap

        cap.elapsed_ms = (time.time() - t0) * 1000.0
        cap.raw_text = text or ""

        if not text:
            cap.error = "empty_clipboard"
            logger.debug(f"clipboard empty for {w.label!r} after {cap.elapsed_ms:.0f}ms")
            return cap

        # 複製成功 — 記錄時間 & 更新未讀數快照
        now = time.time()
        self._last_copy_at[w.name] = now
        self._last_unread[w.name] = unread

        # 整塊文字 md5 比對：一樣就不用再跑 parser
        text_hash = _md5(text)
        prev_hash = self._last_text_hash.get(w.name, "")
        if text_hash == prev_hash:
            cap.ok = True
            cap.new_messages = []
            logger.debug(
                f"clipboard {w.label!r}: identical to last copy "
                f"(hash {text_hash[:8]}…, elapsed {cap.elapsed_ms:.0f}ms)"
            )
            return cap
        self._last_text_hash[w.name] = text_hash

        parsed = self.parser.parse(text)
        cap.all_messages = parsed.messages

        # 第一次讀到 → baseline：全部當已讀
        if w.name not in self._baselined:
            self.mark_seen(w.name, parsed.messages)
            self._baselined.add(w.name)
            logger.info(
                f"baseline {w.label!r}: {len(parsed.messages)} messages marked as seen "
                f"({len(parsed.non_system_messages)} non-system)"
            )
            cap.ok = True
            cap.new_messages = []
            return cap

        # 非首次：挑出新訊息
        if copy_mode == "all":
            last_key = self._last_marked_key.get(w.name)
            new_msgs = []
            found_tail = False
            if last_key is not None:
                for i, parsed_msg in enumerate(parsed.messages):
                    if parsed_msg.key == last_key:
                        found_tail = True
                        new_msgs = parsed.messages[i + 1:]
                        break
            if last_key is None or not found_tail:
                seen = self._seen_set[w.name]
                new_msgs = diff_new_messages(parsed.messages, seen)
        else:
            seen = self._seen_set[w.name]
            new_msgs = diff_new_messages(parsed.messages, seen)
        # 過濾掉系統訊息（加入聊天 / Auto-reply），這些不會是交易信號
        cap.new_messages = [m for m in new_msgs if not m.is_system]
        cap.ok = True

        if cap.new_messages:
            logger.info(
                f"clipboard {w.label!r}: {len(cap.new_messages)} new message(s) "
                f"via {reason} (elapsed {cap.elapsed_ms:.0f}ms)"
            )
        else:
            logger.debug(f"clipboard {w.label!r}: no new message (elapsed {cap.elapsed_ms:.0f}ms)")

        return cap


# -------------------- convenience --------------------

def make_windows_from_config(config_windows) -> List[ClipboardWindow]:
    """從 config.capture_windows 轉成 ClipboardWindow 清單。"""
    out: List[ClipboardWindow] = []
    for w in config_windows:
        out.append(ClipboardWindow(
            name=getattr(w, "name", "default"),
            window_name=getattr(w, "window_name", "") or "",
            display_name=getattr(w, "display_name", "") or getattr(w, "window_name", "") or "",
            window_id=getattr(w, "window_id", None),
            screens=2,
            copy_mode=getattr(w, "copy_mode", "tail"),
        ))
    return out
