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
  7. 之後：訊號需連續出現 N 次 (ocr_confirm_count) 才發布 — 擋單次 OCR 誤讀
  8. 已發布/已 baseline 的訊號整個 session 不再重發 — 徹底防重覆/洪水

介面與 ClipboardReaderService 一致 (capture_all / mark_seen / force_retry)，
CentralSignalCollector 依 config.signal_source 選用，其餘流程完全不變。
"""
from __future__ import annotations

import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set

from copy_trader.platform import ScreenCapture, WindowInfo
from copy_trader.signal_parser.regex_parser import ParsedSignal, RegexSignalParser

from .clipboard_reader import ClipboardWindow  # 重用視窗設定型別
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
        # 尚未確認的新訊號 → 連續出現次數
        self._confirm: Dict[str, Dict[str, int]] = {w.name: {} for w in windows}
        # baseline 狀態
        self._baselined: Set[str] = set()
        # 節流
        self._last_lookup_at: Dict[str, float] = {}
        self._last_ocr_at: Dict[str, float] = {w.name: 0.0 for w in windows}
        # 上次「中下聊天區」縮圖 — 幾乎相同(容忍微動畫/廣告)就跳過 OCR
        self._last_thumb: Dict[str, object] = {w.name: None for w in windows}

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
        if seen_set is None or seen_deque is None:
            return
        for m in messages:
            k = m.body  # 正規化訊號文字即為鍵
            if confirm is not None:
                confirm.pop(k, None)
            if k in seen_set:
                continue
            if len(seen_deque) == seen_deque.maxlen:
                old = seen_deque.popleft()
                seen_set.discard(old)
            seen_deque.append(k)
            seen_set.add(k)

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
        hits.sort(key=lambda h: len(h.title))
        w.window_id = hits[0].window_id
        logger.info("resolved OCR window %r → hwnd=%s (%r)", w.label, w.window_id, hits[0].title)
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
        thumb = self._region_thumb(img)
        if w.name in self._baselined and self._thumbs_similar(thumb, self._last_thumb.get(w.name), self._DIFF_THRESHOLD):
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
        for s in self.parser.parse_all_latest(text):
            if not _is_complete(s):
                continue
            key = signal_to_canonical_text(s)
            if key in seen_local:
                continue
            seen_local.add(key)
            current.append(key)

        seen_set = self._seen_set[w.name]

        # 首輪 baseline：目前可見訊號全部視為已讀，不發布
        if w.name not in self._baselined:
            for key in current:
                self.mark_seen(w.name, [self._make_msg(w, key)])
            self._baselined.add(w.name)
            cap.ok = True
            logger.info("baseline(OCR) %r: %d 則可見訊號設為已讀", w.label, len(current))
            return cap

        # 非首輪：連續確認後才發布
        confirm = self._confirm[w.name]
        new_confirm: Dict[str, int] = {}
        emitted: List[LineMessage] = []
        for key in current:
            if key in seen_set:
                continue
            c = confirm.get(key, 0) + 1
            new_confirm[key] = c
            if c >= self.confirm_count:
                emitted.append(self._make_msg(w, key))
        self._confirm[w.name] = new_confirm

        cap.new_messages = emitted
        cap.ok = True
        if emitted:
            logger.info("OCR %r: %d 則新訊號確認發布 (elapsed %.0fms)", w.label, len(emitted), cap.elapsed_ms)
        return cap

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
        ))
    return out
