"""
背景截圖 + OCR 採集服務 (ClipboardReaderService 的替換件)

背景
----
LINE Desktop 於 2026-06 前後更新後，開始擋掉「合成的鍵盤/滑鼠輸入」，
導致靠 Ctrl+A / Ctrl+C 的剪貼板採集整條失效 (empty_clipboard)。

本服務改用 **PrintWindow 背景截圖 + OCR**：
  * 不靠鍵盤/滑鼠 → LINE 擋不了
  * 不用搶前景 → 不打擾使用者、背景視窗也能拍
  * 只讀「目前可見」的聊天 → 天生不會被歷史洪水淹沒

流程 (每輪)
----------
  1. 解析 LINE 視窗 hwnd (節流重搜)
  2. ScreenCapture.capture_window(hwnd) → PIL Image (PrintWindow)
  3. 存暫存 PNG → OCRService.extract_text → 文字
  4. RegexSignalParser.parse_all_latest → 完整訊號
  5. 每個訊號轉成「正規化文字」當去重鍵 (對 OCR 數字雜訊穩定)
  6. 首輪 baseline：目前可見訊號全部視為已讀，不發布
  7. 之後：訊號需在計時窗內出現 N 次 (ocr_confirm_count) 才發布 — 擋單次 OCR 誤讀
  8. 已發布/已 baseline 的訊號整個 session 不再重發 — 徹底防重覆/洪水

介面與 ClipboardReaderService 一致 (capture_all / mark_seen / force_retry)，
CentralSignalCollector 依 config.signal_source 選用，其餘流程完全不變。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Set, Tuple

from copy_trader.platform import ScreenCapture, WindowInfo
from copy_trader.signal_parser.regex_parser import ParsedSignal, RegexSignalParser

from .clipboard_reader import ClipboardWindow, pick_chat_window  # 重用視窗設定型別 / 挑窗邏輯
from .line_text_parser import LineMessage

logger = logging.getLogger(__name__)

# 視窗尺寸控制用 (SetWindowPos)；非 Windows 直接 None，_ensure_tall 會略過。
try:
    import win32con as _win32con  # type: ignore[import-not-found]
    import win32gui as _win32gui  # type: ignore[import-not-found]
except Exception:
    _win32con = None
    _win32gui = None


# -------------------- 訊號 → 正規化文字 --------------------

def _fmt_price(value) -> str:
    """把價格格式化成穩定字串 (4180 / 4180.5)，供去重鍵與 parser 重解析用。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if f == int(f):
        return str(int(f))
    return ("%.2f" % f).rstrip("0").rstrip(".")


def signal_to_canonical_text(sig: ParsedSignal) -> str:
    """
    由「已解析的訊號」反向組出乾淨的單一訊號文字。

    目的有二：
      1. 當作去重鍵 — 由 direction/entry/sl/tp 決定，對 OCR 的其它雜訊免疫。
      2. 當作下游 _process_message 的 body — 保證只含「這一筆」訊號，
         不夾帶畫面上其它舊訊號 (避免舊單被一起重發)。

    格式刻意貼合 RegexSignalParser 認得的樣式 (實測可 round-trip)。
    """
    dir_word = "Buy" if sig.direction == "buy" else "Sell"
    lines = ["乘XAUUSD 黃金", ""]
    if sig.is_market_order or sig.entry_price is None:
        lines.append("%s 市價" % dir_word)
    else:
        lines.append("%s ：%s" % (dir_word, _fmt_price(sig.entry_price)))
    if sig.stop_loss is not None:
        lines.append("止損：%s" % _fmt_price(sig.stop_loss))
    for tp in (sig.take_profit or []):
        if tp is not None:
            lines.append("止盈：%s" % _fmt_price(tp))
    return "\n".join(lines)


def _is_complete(sig: ParsedSignal) -> bool:
    has_entry = sig.entry_price is not None or bool(sig.is_market_order)
    return bool(sig.is_valid and sig.direction and has_entry and sig.stop_loss and sig.take_profit)


# 群組撤單關鍵字 (實測自報單群：取消 / 撤掉 / 空單先撤掉 / 都撤單 …)
_CANCEL_KWS = ("取消", "撤單", "撤掉", "撤回", "先撤", "都撤", "撤了", "撤吧", "刪單", "砍單", "撤")
CANCEL_PREFIX = "__CANCEL__"


def detect_cancel_after_signal(text: str, last_sig: ParsedSignal) -> Optional[str]:
    """
    最後一個訊號『之後』的文字若出現撤單關鍵字 → 回傳方向提示
    ('sell'/'buy'/'')；否則 None。

    只看「最後一個訊號之後」是為了把撤單綁定到它要撤的那一單：新訊號一出現，
    舊撤單字就落在新訊號上方、不再觸發，避免舊撤單誤撤到新掛單。
    """
    anchor = 0
    for tp in (last_sig.take_profit or []):
        s = _fmt_price(tp)
        if s:
            i = text.rfind(s)
            if i >= 0:
                anchor = max(anchor, i + len(s))
    tail = text[anchor:] if anchor else text
    if any(kw in tail for kw in _CANCEL_KWS):
        if "空" in tail:
            return "sell"
        if "多" in tail:
            return "buy"
        return ""
    return None


# -------------------- 結果 --------------------

@dataclass
class OcrCapture:
    """單次對一個視窗截圖+OCR 的結果 (欄位對齊 ClipboardCapture 的下游用法)。"""
    source_name: str
    display_name: str
    window_id: Optional[int] = None
    new_messages: List[LineMessage] = field(default_factory=list)
    raw_text: str = ""
    elapsed_ms: float = 0.0
    ok: bool = False
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


# -------------------- 服務 --------------------

class WindowOcrReaderService:
    """
    每輪對每個視窗做「PrintWindow 截圖 → OCR → 抽訊號 → diff」。

    去重完全以「正規化訊號文字」為鍵，對 OCR 數字以外的雜訊免疫；
    整個 session 已發布過的訊號不再重發 (優先防洪水/重覆，寧可漏掉極罕見的
    同參數重貼)。
    """

    SEEN_MAX = 1000
    WINDOW_ID_RETRY_INTERVAL = 30.0
    DEFAULT_MIN_INTERVAL = 1.5  # 兩次 OCR 最小間隔 (秒)，避免無謂吃 CPU

    # 「同一個訊號要被讀到 confirm_count 次才發布」的計時窗 (秒)。
    #
    # 原本的規則是「連續 N 輪」——只要中間有一輪沒讀到就整個歸零。實測 2026-08-10：
    # yuyu 的 Buy 4335 在 19:44:16 就被完整且正確地讀出來了，卻拖到 20:17:55 才發布，
    # 中間卡了 34 分鐘。原因是廣告輪播/畫面重繪讓訊號時有時無，每漏讀一輪就：
    #   (1) 計數歸零，(2) _confirm 變空 → 影像差異守衛重新生效 → 整輪跳過 OCR，
    # 於是卡在「跳過→跳過→…」直到畫面變動夠大才脫困。
    #
    # 改成計時窗後，漏讀不再清空計數，只有超過這個時間才過期。防瞬間誤讀的效果仍在：
    # 誤讀的鍵跟正確的鍵是不同字串，各自獨立計數，誤讀仍需在窗內再出現一次才會發布
    # (實測那次誤讀 4346 與正確的 4345 相隔 71 分鐘，遠超出窗口，不會互相成全)。
    CONFIRM_WINDOW_SEC = 120.0

    # 讀到「像訊號但不完整」之後，維持高敏感度重讀多久 (秒)。
    # 訊息通常在幾秒內就會被渲染完整；給 10 分鐘是留給廣告輪播/延遲載入的餘裕，
    # 同時避免畫面上長期存在一則永遠讀不全的東西 (例如別人貼的 MT5 持倉截圖)
    # 害這個來源永遠不進入省電的跳過模式。
    PARTIAL_RETRY_SEC = 600.0

    def __init__(
        self,
        windows: List[ClipboardWindow],
        confirm_count: int = 2,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        ocr_service=None,
        tmp_dir: Optional[str] = None,
        resize_tall: bool = True,
    ):
        self.windows = list(windows)
        self.screen = ScreenCapture()
        self.parser = RegexSignalParser()
        self.confirm_count = max(1, int(confirm_count or 1))
        self.min_interval = max(0.3, float(min_interval))
        # 啟動時把 LINE 視窗拉高 → 一次看到更多訊息, baseline 涵蓋更多歷史訊號,
        # 也更能涵蓋到最新那則 (LINE 擋合成捲動, 但 SetWindowPos 擋不掉)
        self.resize_tall = bool(resize_tall)
        self._resized: Set[str] = set()

        # OCR 服務延遲載入 (第一次用時載模型 ~2s)
        self._ocr = ocr_service
        self._ocr_init_failed = False

        # 暫存 PNG 目錄
        if tmp_dir:
            self._tmp_dir = tmp_dir
        else:
            try:
                from copy_trader.config import DATA_DIR
                self._tmp_dir = str(DATA_DIR)
            except Exception:
                self._tmp_dir = "."

        # 每個來源：已看過(baseline+已發布)的正規化訊號文字
        self._seen_keys: Dict[str, Deque[str]] = {w.name: deque(maxlen=self.SEEN_MAX) for w in windows}
        self._seen_set: Dict[str, Set[str]] = {w.name: set() for w in windows}
        self._seen_ts: Dict[str, Dict[str, float]] = {w.name: {} for w in windows}
        # 尚未確認的新訊號 → (在計時窗內被讀到幾次, 第一次讀到的時間)
        self._confirm: Dict[str, Dict[str, Tuple[int, float]]] = {w.name: {} for w in windows}
        # 最後一次讀到「像訊號但不完整」的時間 → 這段期間提高畫面變動敏感度重讀
        self._partial_pending: Dict[str, float] = {w.name: 0.0 for w in windows}
        # baseline 狀態
        self._baselined: Set[str] = set()
        # 已警告過「白名單在 OCR 管道無效」的來源 (只吼一次, 不洗版)
        self._warned_sender_filter: Set[str] = set()
        # 節流
        self._last_lookup_at: Dict[str, float] = {}
        self._last_ocr_at: Dict[str, float] = {w.name: 0.0 for w in windows}
        # 上次「中下聊天區」縮圖 — 幾乎相同(容忍微動畫/廣告)就跳過 OCR
        self._last_thumb: Dict[str, object] = {w.name: None for w in windows}
        # 已經為「被去重擋掉」印過 log 的鍵 — 每把鍵只提醒一次，不洗版
        self._suppress_logged: Dict[str, Set[str]] = {w.name: set() for w in windows}

        # 跨重啟去重：把已發布/baseline 的訊號鍵存檔，重啟後載回。
        # 防「重啟後舊訊號(捲回畫面/重新 baseline)被當新單重發 → 會員重複下單」。
        #
        # 保留期 = 「同一組數字在這段時間內重複出現就視為同一單」。設 1 天：提供者
        # 的區間單常常隔幾天回到同一個價位 (乘的 Buy 4050/4040/4065 就跨了 7/31 和
        # 8/03)，保留 2 天會把隔日的真新單當成舊單吃掉。
        # 這個值必須搭配 _expire_seen() 才有意義 — 見該函式的說明。
        self._seen_retention_sec = 24 * 3600  # 保留 1 天
        self._seen_path: Optional[Path] = None
        try:
            from copy_trader.config import DATA_DIR
            self._seen_path = Path(DATA_DIR) / "central_ocr_seen.json"
        except Exception:
            self._seen_path = None
        self._load_seen()

    # -------- OCR 延遲載入 --------

    def _get_ocr(self):
        if self._ocr is None and not self._ocr_init_failed:
            try:
                from .ocr import OCRService
                self._ocr = OCRService()
                logger.info("WindowOcrReader: OCR 引擎就緒 (%s)", getattr(self._ocr, "engine", "?"))
            except Exception as e:
                self._ocr_init_failed = True
                logger.error("WindowOcrReader: OCR 初始化失敗: %s", e)
        return self._ocr

    # -------- public --------

    def capture_all(self) -> List[OcrCapture]:
        out: List[OcrCapture] = []
        for w in self.windows:
            try:
                out.append(self._capture_one(w))
            except Exception as e:
                logger.exception("window OCR capture failed for %s: %s", w.label, e)
                out.append(OcrCapture(source_name=w.name, display_name=w.label, ok=False, error=str(e)))
        return out

    def mark_seen(self, source_name: str, messages: List[LineMessage]) -> None:
        seen_set = self._seen_set.get(source_name)
        seen_deque = self._seen_keys.get(source_name)
        confirm = self._confirm.get(source_name)
        ts_map = self._seen_ts.setdefault(source_name, {})
        if seen_set is None or seen_deque is None:
            return
        now = time.time()
        changed = False
        for m in messages:
            k = m.body  # 正規化訊號文字即為鍵
            if confirm is not None:
                confirm.pop(k, None)
            ts_map[k] = now  # 更新時間戳(即使已 seen) 以延長保留
            if k in seen_set:
                continue
            if len(seen_deque) == seen_deque.maxlen:
                old = seen_deque.popleft()
                seen_set.discard(old)
            seen_deque.append(k)
            seen_set.add(k)
            changed = True
        if changed or messages:
            self._save_seen()

    def _expire_seen(self, source_name: str) -> int:
        """把超過保留期的鍵，從「執行期」的去重集合裡淘汰掉。回傳淘汰筆數。

        為什麼需要這個
        --------------
        保留期原本只在 _load_seen / _save_seen 套用，執行期的 _seen_set 沒有任何
        時間淘汰 — mark_seen 只有在 deque 塞滿 SEEN_MAX 筆時才會擠掉最舊的。所以
        程式只要不重啟，seen_set 就一直長大，幾天前發過的訊號永遠被視為「已讀」。

        2026-08-03 的漏單就是這樣來的：乘在 7/31 17:26 發過
        `Buy 4050 / 止損 4040 / 止盈 4065`，8/03 17:20 又發了一模一樣的數字，
        正規化後的鍵一個字元都不差；而服務已經連續跑了 98 小時，那把鍵還在記憶體裡
        → _capture_one 直接 `continue`，連 confirm 都不累加，完全沒有 log，
        訊號就這樣無聲消失。存檔裡其實早就沒有那把鍵了（存檔有套保留期）——
        檔案是乾淨的，髒的是記憶體。
        """
        ts_map = self._seen_ts.get(source_name)
        seen_set = self._seen_set.get(source_name)
        seen_deque = self._seen_keys.get(source_name)
        if not ts_map or seen_set is None or seen_deque is None:
            return 0
        now = time.time()
        stale = {k for k, ts in ts_map.items() if now - ts > self._seen_retention_sec}
        if not stale:
            return 0
        for k in stale:
            ts_map.pop(k, None)
            seen_set.discard(k)
            self._suppress_logged.get(source_name, set()).discard(k)
        # deque 也要清，否則過期的鍵會一直佔著 SEEN_MAX 的名額
        remaining = [k for k in seen_deque if k not in stale]
        seen_deque.clear()
        seen_deque.extend(remaining)
        logger.info(
            "去重淘汰 %r: %d 筆超過 %.0f 小時的舊鍵已釋放 (同數值的新單可以重新發布)",
            source_name, len(stale), self._seen_retention_sec / 3600.0,
        )
        return len(stale)

    def _load_seen(self) -> None:
        """重啟時載回保留期內已發布/baseline 的訊號鍵，避免重複下單。"""
        if not self._seen_path or not self._seen_path.exists():
            return
        try:
            data = json.loads(self._seen_path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = time.time()
        loaded = 0
        for name in self._seen_set:
            for key, ts in (data.get(name) or {}).items():
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    continue
                if now - ts > self._seen_retention_sec:
                    continue
                self._seen_ts[name][key] = ts
                if key not in self._seen_set[name]:
                    self._seen_keys[name].append(key)
                    self._seen_set[name].add(key)
                    loaded += 1
        if loaded:
            logger.info("OCR 去重：從存檔載回 %d 筆近期已發布訊號 (重啟後不重發、會員不重複下單)", loaded)

    def _save_seen(self) -> None:
        if not self._seen_path:
            return
        try:
            now = time.time()
            data = {
                name: {k: ts for k, ts in d.items() if now - ts <= self._seen_retention_sec}
                for name, d in self._seen_ts.items()
            }
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._seen_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._seen_path)
        except Exception as e:
            logger.debug("save seen failed: %s", e)

    def force_retry(self, source_name: str) -> None:
        # 下游發布失敗且未 mark_seen 時，訊號仍不在 _seen → 下一輪自然重發，
        # 這裡把影像簽章清掉，確保即使畫面沒變也會重跑 OCR 重試。
        if source_name in self._last_thumb:
            self._last_thumb[source_name] = None
        if source_name in self._last_ocr_at:
            self._last_ocr_at[source_name] = 0.0

    # -------- window id --------

    def _resolve_window_id(self, w: ClipboardWindow) -> Optional[int]:
        now = time.time()
        if w.window_id:
            try:
                if self.screen.get_window_rect(w.window_id):
                    return w.window_id
            except Exception:
                pass
            w.window_id = None

        last = self._last_lookup_at.get(w.name, 0.0)
        if w.window_id is None and last != 0.0 and now - last < self.WINDOW_ID_RETRY_INTERVAL:
            return None
        self._last_lookup_at[w.name] = now
        try:
            hits: List[WindowInfo] = self.screen.enumerate_windows(w.window_name)
        except Exception as e:
            logger.debug("enumerate_windows failed: %s", e)
            return None
        if not hits:
            logger.debug("no window matched: %r", w.window_name)
            return None
        best = pick_chat_window(hits)
        if best is None:
            return None
        w.window_id = best.window_id
        logger.info(
            "resolved OCR window %r → hwnd=%s (%r, %s)",
            w.label, w.window_id, best.title, best.owner_name or "?",
        )
        return w.window_id

    def _ensure_tall(self, hwnd: int, w: ClipboardWindow) -> None:
        """把 LINE 視窗移到螢幕頂端並拉高, 讓一次可見的訊息量最大化。

        LINE 擋掉合成捲動, 但 SetWindowPos 是視窗管理 API 擋不掉。視窗越高,
        一次看到的訊息越多, baseline 涵蓋越完整, 也越能涵蓋到最新那則。
        """
        if _win32gui is None or _win32con is None:
            return
        try:
            import ctypes
            L, T, R, B = _win32gui.GetWindowRect(hwnd)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            target_h = max(600, screen_h - 80)
            cur_w = R - L
            new_w = max(cur_w, 400)
            if (B - T) >= target_h - 120 and cur_w >= 400:
                return  # 已經夠高夠寬, 不動使用者視窗
            _win32gui.SetWindowPos(
                hwnd, 0, L, 0, new_w, target_h,
                _win32con.SWP_NOZORDER | _win32con.SWP_NOACTIVATE,
            )
            time.sleep(0.6)  # 等 LINE 重新排版
            logger.info("已將 LINE 視窗 %r 拉高至 %dpx (顯示更多訊息)", w.label, target_h)
        except Exception as e:
            logger.debug("resize window failed: %s", e)

    # 中下聊天區平均像素差 < 此值 → 視為畫面沒變 (容忍微動畫)
    _DIFF_THRESHOLD = 2.5
    # 有殘缺訊號待補讀時改用這個 — 幾乎任何重繪都會觸發重讀 (見 PARTIAL_RETRY_SEC)
    _PARTIAL_DIFF_THRESHOLD = 0.3

    def _region_thumb(self, img):
        """裁掉上方標題/廣告區, 只取『中下聊天區』縮成小灰階圖做差異比對。"""
        try:
            w, h = img.size
            crop = img.crop((0, int(h * 0.34), w, int(h * 0.93)))
            return crop.convert("L").resize((64, 64))
        except Exception:
            return None

    @staticmethod
    def _thumbs_similar(a, b, threshold: float) -> bool:
        if a is None or b is None:
            return False
        try:
            from PIL import ImageChops, ImageStat
            return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] < threshold
        except Exception:
            return False

    # -------- core --------

    def _capture_one(self, w: ClipboardWindow) -> OcrCapture:
        t0 = time.time()
        cap = OcrCapture(source_name=w.name, display_name=w.label, window_id=w.window_id)

        # 發送者白名單 + OCR = 認不出誰發的 → 整個來源跳過, 不亂跟
        # (改用模板指紋 required_patterns 的視窗不受此限 — 見 _sender_filter_unsupported)
        if self._sender_filter_unsupported(w):
            cap.ok = True
            cap.skipped = True
            cap.skip_reason = "sender_filter_needs_clipboard"
            return cap

        # 節流：距上次 OCR 未達 min_interval 就跳過 (baseline 除外)
        if w.name in self._baselined and (time.time() - self._last_ocr_at.get(w.name, 0.0)) < self.min_interval:
            cap.ok = True
            cap.skipped = True
            cap.skip_reason = "min_interval"
            return cap

        hwnd = self._resolve_window_id(w)
        if not hwnd:
            cap.error = "window_not_found"
            return cap
        cap.window_id = hwnd

        # 首次見到此視窗 → 拉高 (在 baseline 之前, 讓 baseline 涵蓋更多可見訊號)
        if self.resize_tall and w.name not in self._resized:
            self._ensure_tall(hwnd, w)
            self._resized.add(w.name)

        img = self.screen.capture_window(hwnd)
        if img is None:
            cap.error = "capture_failed"
            return cap

        # 影像差異比對：中下聊天區幾乎沒變 → 跳過 OCR (baseline 仍需跑一次)
        # 守衛：若有「待確認」訊號 (confirm 計數中) 絕不可跳過，否則新訊號被讀到一次後
        # 畫面靜止→跳過OCR→永遠湊不到 confirm_count 次數。這也是 confirm≥2 能擋掉
        # 「瞬間誤讀」的關鍵：多次 OCR 讀到一致才發，瞬間讀錯的那次會被下一次否決。
        #
        # 另一個門檻：上一輪若讀到「像訊號但不完整」(例如止損沒讀到)，代表畫面上很可能
        # 就有一則還沒讀全的報單。實測 2026-08-10：14:17:35 讀到 SELL 4350 但 SL 是 None，
        # 因為不完整所以 _confirm 是空的、畫面又靜止 → 之後每一輪都跳過 OCR，直到 15:32
        # 有新訊息進來畫面才變 —— 那則訊號整整躺了 75 分鐘沒被重讀。所以這種時候要把
        # 敏感度拉高，任何細微重繪 (字體重繪/廣告輪播/滾動一兩像素) 都要能觸發重讀。
        thumb = self._region_thumb(img)
        partial_since = self._partial_pending.get(w.name, 0.0)
        retrying_partial = (time.time() - partial_since) <= self.PARTIAL_RETRY_SEC
        threshold = self._PARTIAL_DIFF_THRESHOLD if retrying_partial else self._DIFF_THRESHOLD
        if (w.name in self._baselined
                and not self._confirm.get(w.name)
                and self._thumbs_similar(thumb, self._last_thumb.get(w.name), threshold)):
            cap.ok = True
            cap.skipped = True
            cap.skip_reason = "no_visual_change"
            self._last_ocr_at[w.name] = time.time()
            return cap
        self._last_thumb[w.name] = thumb

        ocr = self._get_ocr()
        if ocr is None:
            cap.error = "ocr_unavailable"
            return cap

        img_path = "%s/_ocr_%s.png" % (self._tmp_dir.rstrip("/\\"), w.name)
        try:
            img.save(img_path)
        except Exception as e:
            cap.error = "save_failed:%s" % e
            return cap

        text = ocr.extract_text(img_path) or ""
        self._last_ocr_at[w.name] = time.time()
        cap.raw_text = text
        cap.elapsed_ms = (time.time() - t0) * 1000.0
        if not text.strip():
            cap.error = "empty_ocr"
            return cap

        # 抽出目前可見的完整訊號 → 正規化文字集合
        current: List[str] = []
        seen_local: Set[str] = set()
        last_complete: Optional[ParsedSignal] = None
        saw_partial = False
        for s in self._parse_signals(w, text):
            if not _is_complete(s):
                # 有方向就代表「看起來是報單、只是還沒讀全」——記下來讓下一輪用高敏感度
                # 重讀 (見 PARTIAL_RETRY_SEC)。沒方向的就只是聊天裡的雜數字，不算。
                if s.direction:
                    saw_partial = True
                continue
            last_complete = s
            key = signal_to_canonical_text(s)
            if key in seen_local:
                continue
            seen_local.add(key)
            current.append(key)

        # 撤單偵測：最後一個訊號之後若有撤單字 → 加一個 cancel key，綁定該訊號。
        # 與訊號共用 baseline / confirm≥2 / 去重 / 存檔，所以：啟動前既有的撤單不觸發、
        # 需連續讀到才發、同一撤單不重發。
        if last_complete is not None:
            dir_hint = detect_cancel_after_signal(text, last_complete)
            if dir_hint is not None:
                cancel_key = "%s:%s:%s" % (CANCEL_PREFIX, dir_hint or "any", signal_to_canonical_text(last_complete))
                if cancel_key not in seen_local:
                    seen_local.add(cancel_key)
                    current.append(cancel_key)

        # 有殘缺訊號 → 開啟高敏感度重讀；這一輪把它讀全了就關掉，回到省電模式。
        if saw_partial:
            if not self._partial_pending.get(w.name):
                logger.info(
                    "%s: 讀到不完整的報單 (缺欄位) → 提高畫面變動敏感度重讀, 最多 %.0f 分鐘",
                    w.label, self.PARTIAL_RETRY_SEC / 60.0,
                )
            self._partial_pending[w.name] = time.time()
        elif self._partial_pending.get(w.name):
            self._partial_pending[w.name] = 0.0

        # 比對之前先淘汰過期的鍵 — 否則長時間不重啟時保留期形同虛設 (見 _expire_seen)
        self._expire_seen(w.name)
        seen_set = self._seen_set[w.name]

        # 首輪 baseline：目前可見訊號全部視為已讀，不發布
        # (跨重啟去重已從存檔載回近 2 天的鍵，這裡再補上目前可見的)
        if w.name not in self._baselined:
            self.mark_seen(w.name, [self._make_msg(w, key) for key in current])
            self._baselined.add(w.name)
            cap.ok = True
            logger.info("baseline(OCR) %r: %d 則可見訊號設為已讀", w.label, len(current))
            return cap

        # 非首輪：在計時窗內被讀到 confirm_count 次才發布 (見 CONFIRM_WINDOW_SEC)。
        # 注意這裡是「就地更新」而不是每輪重建 — 漏讀一輪不可以把計數清掉。
        now = time.time()
        confirm = self._confirm[w.name]
        for k in [k for k, (_, first) in confirm.items() if now - first > self.CONFIRM_WINDOW_SEC]:
            confirm.pop(k, None)
        emitted: List[LineMessage] = []
        for key in current:
            if key in seen_set:
                # 每把鍵只講一次 — 「訊號被去重擋掉」原本完全無聲, 出事時 log 上
                # 只看得到 Parsed signal 然後什麼都沒有, 極難查 (見 _expire_seen)。
                logged = self._suppress_logged.setdefault(w.name, set())
                if key not in logged:
                    logged.add(key)
                    age = time.time() - self._seen_ts.get(w.name, {}).get(key, time.time())
                    logger.info(
                        "%s: 訊號與 %.1f 小時前已發布的完全相同 → 去重跳過: %s",
                        w.label, age / 3600.0, key.replace("\n", " ")[:70],
                    )
                continue
            c, first = confirm.get(key, (0, now))
            c += 1
            confirm[key] = (c, first)
            if c >= self.confirm_count:
                emitted.append(self._make_msg(w, key))
                confirm.pop(key, None)   # 發布後就不必再留著佔住守衛

        cap.new_messages = emitted
        cap.ok = True
        if emitted:
            logger.info("OCR %r: %d 則新訊號確認發布 (elapsed %.0fms)", w.label, len(emitted), cap.elapsed_ms)
        return cap

    def _parse_signals(self, w: ClipboardWindow, text: str) -> List[ParsedSignal]:
        """從 OCR 全文抽訊號；有設模板指紋的視窗則「逐訊息區塊」比對後才抽。

        不能直接拿整頁 OCR 文字比對指紋 —— 畫面上同時有提供者的單和群友的單時,
        整頁必然含指紋, 於是別人的單也一起被收進來。所以先切成訊息區塊
        (依 LINE 時間戳/日期/氣泡邊界), 只解析「自己就帶指紋」的那幾塊。
        """
        if not getattr(w, "required_patterns", None):
            return self.parser.parse_all_latest(text)

        out: List[ParsedSignal] = []
        for block in self.parser.split_message_blocks(text):
            if not w.matches_template(block):
                continue
            out.extend(self.parser.parse_all_latest(block))
        if not out:
            logger.debug("OCR %r: 本輪沒有區塊符合模板指紋 %s", w.label, w.required_patterns)
        return out

    def _sender_filter_unsupported(self, w: ClipboardWindow) -> bool:
        """OCR 認不出發送者 → 有設白名單的視窗一律跳過 (寧可不跟, 不可跟錯人)。

        例外：改用「模板指紋」(required_patterns) 的視窗不受影響 — 那是比對內容,
        OCR 讀得到內容, 所以在 OCR 管道也能正確只跟指定的提供者。
        """
        if not getattr(w, "allowed_senders", None):
            return False
        if getattr(w, "required_patterns", None):
            return False
        if w.name not in self._warned_sender_filter:
            self._warned_sender_filter.add(w.name)
            logger.warning(
                "%r 設了發送者白名單 %s, 但 OCR 管道讀不出發送者 → 這個來源整個跳過。"
                "請改用 required_patterns (模板指紋), 或把 signal_source 設成 clipboard。",
                w.label, w.allowed_senders,
            )
        return True

    def _make_msg(self, w: ClipboardWindow, canonical: str) -> LineMessage:
        return LineMessage(
            index=0,
            time_str="",
            timestamp=None,      # OCR 無可靠訊息時間；staleness 由 baseline + 會員端 captured_at 把關
            sender=w.label,
            body=canonical,
        )


# -------------------- convenience --------------------

def make_ocr_windows_from_config(config_windows) -> List[ClipboardWindow]:
    out: List[ClipboardWindow] = []
    for w in config_windows:
        out.append(ClipboardWindow(
            name=getattr(w, "name", "default"),
            window_name=getattr(w, "window_name", "") or "",
            display_name=getattr(w, "display_name", "") or getattr(w, "window_name", "") or "",
            window_id=getattr(w, "window_id", None),
            allowed_senders=list(getattr(w, "allowed_senders", []) or []),
            required_patterns=list(getattr(w, "required_patterns", []) or []),
        ))
    return out
