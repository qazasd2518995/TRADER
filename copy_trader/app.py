"""
Copy Trader - Main Application
Monitors screen for trading signals and executes them automatically.
"""
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Set
from pathlib import Path

# Ensure local imports work without shadowing stdlib modules like `platform`.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_this_dir)
while _this_dir in sys.path:
    sys.path.remove(_this_dir)
sys.path.append(_this_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

from config import Config, CaptureRegion, CaptureWindow, load_config, DATA_DIR, DEFAULT_SYMBOL
from signal_capture import (
    ScreenCaptureService,
    CaptureWindow as SCWindow,
    OCRService,
    ClipboardReaderService,
    ClipboardWindow,
    LineMessage,
)
from signal_parser import RegexSignalParser
from signal_parser.keyword_filter import is_potential_signal, extract_quick_info
from signal_parser.gemini_vision_parser import GeminiVisionParser
from signal_parser.groq_vision_parser import GroqVisionParser
from trade_manager import TradeManager, ManagedOrder, OrderStatus

# Configure logging — use DATA_DIR so logs persist in packaged mode
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(DATA_DIR / 'copy_trader.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


def _capture_window_label(window: CaptureWindow) -> str:
    return getattr(window, "display_name", "") or window.window_name


class CopyTrader:
    """Main copy trading application."""

    def __init__(self, config: Config, event_callback=None):
        """
        Initialize copy trader.

        Args:
            config: Application configuration
            event_callback: Optional callback(event_type, data_dict) for GUI integration
        """
        self.config = config
        self.event_callback = event_callback

        # Initialize components based on capture mode
        if config.capture_mode == "window" and config.capture_windows:
            # Convert config CaptureWindow to screen_capture CaptureWindow
            windows = [
                SCWindow(
                    window_id=getattr(w, "window_id", None),
                    window_name=w.window_name,
                    app_name=w.app_name,
                    name=w.name
                )
                for w in config.capture_windows
            ]
            self.capture_service = ScreenCaptureService(windows=windows)
            logger.info(f"Using window capture mode: {[_capture_window_label(w) for w in config.capture_windows]}")
        else:
            # Fallback to region-based capture
            from signal_capture.screen_capture import CaptureRegion as SCRegion
            regions = [
                SCRegion(x=r.x, y=r.y, width=r.width, height=r.height, name=r.name)
                for r in config.capture_regions
            ]
            self.capture_service = ScreenCaptureService(regions=regions)
            logger.info(f"Using region capture mode: {len(regions)} regions")
        self.ocr_service = OCRService()

        # === Clipboard reader (primary signal path) ===
        self.clipboard_service = None
        try:
            cb_windows = [
                ClipboardWindow(
                    name=w.name,
                    window_name=w.window_name,
                    display_name=_capture_window_label(w),
                    window_id=getattr(w, "window_id", None),
                    screens=int(getattr(config, "clipboard_screens", 2) or 2),
                    copy_mode=str(getattr(config, "clipboard_copy_mode", "tail") or "tail"),
                )
                for w in (config.capture_windows or [])
            ]
            if cb_windows:
                self.clipboard_service = ClipboardReaderService(
                    cb_windows,
                    stale_seconds=float(getattr(config, "clipboard_stale_seconds", 10.0) or 10.0),
                )
                logger.info(
                    f"Clipboard reader initialized for {len(cb_windows)} window(s), "
                    f"stale_seconds={self.clipboard_service.stale_seconds:.0f}"
                )
                logger.info(
                    "💡 想完全零打擾？把 LINE 拖到另一個虛擬桌面 (Win+Ctrl+D 建立)，"
                    "本程式偵測到 LINE 不在當前桌面時會自動跳過複製。"
                )
        except Exception as e:
            logger.warning(f"Clipboard reader init failed: {e}")

        # === 3-tier Vision fallback chain: Gemini → Groq → Regex ===
        self.gemini_parser = None
        self.groq_parser = None

        # Tier 1: Gemini (free 250 RPD, no image token cost)
        if config.gemini_api_key:
            try:
                self.gemini_parser = GeminiVisionParser(
                    api_key=config.gemini_api_key,
                    model=config.gemini_vision_model
                )
            except Exception as e:
                logger.warning(f"Gemini init failed: {e}")

        # Tier 2: Groq (free 500K TPD, but images eat tokens fast)
        if config.groq_api_key:
            try:
                self.groq_parser = GroqVisionParser(
                    api_key=config.groq_api_key,
                    model=config.groq_vision_model
                )
            except Exception as e:
                logger.warning(f"Groq Vision init failed: {e}")

        # Tier 3: Regex (always available, local, free)
        self._regex_parser = RegexSignalParser()
        self.signal_parser = self._regex_parser

        tiers = []
        if self.gemini_parser: tiers.append("Gemini")
        if self.groq_parser: tiers.append("Groq")
        tiers.append("Regex")
        logger.info(f"Parser chain: {' → '.join(tiers)}")
        self.trade_manager = TradeManager(
            mt5_files_dir=config.mt5_files_dir
        )
        self.trade_manager.default_lot_size = config.default_lot_size
        self.trade_manager.set_symbol_name(getattr(config, "symbol_name", DEFAULT_SYMBOL))
        self.trade_manager.partial_close_ratios = config.partial_close_ratios

        # Martingale settings
        self.trade_manager.use_martingale = config.use_martingale
        self.trade_manager.martingale_multiplier = config.martingale_multiplier
        self.trade_manager.martingale_max_level = config.martingale_max_level
        self.trade_manager.martingale_lots = getattr(config, 'martingale_lots', [])
        self.trade_manager.martingale_per_source = getattr(config, 'martingale_per_source', False)
        self.trade_manager.martingale_source_lots = getattr(config, 'martingale_source_lots', {})

        # Build mapping from internal window name -> display name
        self._window_display_names: Dict[str, str] = {}
        if config.capture_mode == "window" and config.capture_windows:
            for w in config.capture_windows:
                self._window_display_names[w.name] = _capture_window_label(w)

        # State
        self._running = False
        self._processed_hashes: Dict[str, float] = {}   # hash -> expiry timestamp
        self._recent_ocr_texts: list = []  # last N OCR texts for fuzzy dedup
        self._processed_signals: Set[str] = set()  # Dedup by parsed signal content
        self._daily_loss = 0.0
        self._daily_trades = 0
        self._incomplete_retry_counts: dict = {}  # text_hash -> retry count
        # Pending signal buffer: wait for multi-message signals to complete
        # Key: source_name, Value: {"signal": ParsedSignal, "time": float, "direction": str}
        self._pending_signals: dict = {}
        self._signals_file = DATA_DIR / "copy_trader_signals.json"
        self._last_cleanup_time = 0.0
        self._cancel_processed: set = set()
        # Vision dedup: prevent sending the same incomplete signal to Vision repeatedly
        # Key: (source, direction, tp_tuple) -> expiry timestamp
        self._vision_sent_cache: Dict[str, float] = {}
        self._stale_capture_logged_at: Dict[str, float] = {}

        # Stats
        self._api_calls_today = 0
        self._signals_filtered = 0

        # Load persisted signals and existing MT5 orders for dedup
        self._load_persisted_signals()
        self._load_existing_mt5_orders()

        logger.info("CopyTrader initialized")

        # Create trade journal on startup
        windows_str = ", ".join([_capture_window_label(w) for w in config.capture_windows]) or "無"
        self._write_trade_journal(
            "SYSTEM_START",
            details=f"監控群組=[{windows_str}] | 馬丁={'各群獨立' if config.martingale_per_source else '全域共用'} | 自動下單={config.auto_execute}",
        )
        self._emit_event("initialized", {})
        tiers_log = []
        if self.gemini_parser: tiers_log.append("Gemini")
        if self.groq_parser: tiers_log.append("Groq")
        tiers_log.append("Regex")
        logger.info(f"  Parser: {' → '.join(tiers_log)}")
        logger.info(f"  Auto execute: {config.auto_execute}")
        logger.info(f"  Default lot size: {config.default_lot_size}")
        logger.info(f"  Capture interval: {config.capture_interval}s")
        logger.info(f"  OCR confirmation: {config.ocr_confirm_count}x with {config.ocr_confirm_delay}s delay (confirmation uses cropped 50%)")
        logger.info(f"  Martingale: {'ON' if config.use_martingale else 'OFF'} (x{config.martingale_multiplier}, max level {config.martingale_max_level})")

    async def start(self):
        """Start the copy trader."""
        logger.info("Starting Copy Trader...")

        # Verify MT5 connection
        if not self._verify_mt5_connection():
            logger.error("MT5 connection failed. Make sure MT5 and the bridge EA are running.")
            return

        # Start trade manager
        self.trade_manager.start()

        # Setup signal handlers
        self._setup_signal_handlers()

        # Main loop with adaptive frequency
        self._running = True
        self._idle_cycles = 0
        self._startup_baseline = True  # First cycle = baseline only, don't trade
        logger.info("Copy Trader running. Press Ctrl+C to stop.")

        # Build baseline — skip old signals already on screen so they don't trigger new orders.
        # Clipboard path does its own baseline inside ClipboardReaderService (first _capture_one).
        if self._use_clipboard_path():
            logger.info("Clipboard path: baseline will be built on first capture cycle")
        else:
            logger.info("Building OCR baseline from current screen (skipping existing signals)...")
            try:
                frames = self.capture_service.capture_all_regions(deduplicate=False)
                for frame in frames:
                    text = self.ocr_service.extract_newest_bubble_text(frame.image_path)
                    if text and len(text.strip()) >= 10:
                        text_hash = self._compute_text_hash(text)
                        self._add_hash_with_ttl(text_hash, self.config.signal_dedup_minutes * 60)
                        self._recent_ocr_texts.append(text)
                        # Also try to parse and mark as processed
                        signal = self.signal_parser.parse_latest(text)
                        if signal.is_valid and signal.stop_loss and signal.take_profit:
                            key = str(self._signal_key(signal))
                            self._processed_signals.add(key)
                            logger.info(f"Baseline: marked as seen: {signal}")
                    Path(frame.image_path).unlink(missing_ok=True)
                logger.info(f"Baseline complete: {len(self._processed_signals)} signals, {len(self._processed_hashes)} hashes")
            except Exception as e:
                logger.warning(f"Baseline scan failed: {e}")

        while self._running:
            try:
                if self._use_clipboard_path():
                    had_frames = await self._process_clipboard_cycle()
                else:
                    had_frames = await self._process_cycle()
                self._periodic_cleanup()

                # Adaptive frequency: slow down when idle, speed up on activity
                if had_frames:
                    self._idle_cycles = 0
                    interval = self.config.capture_interval  # normal: 1s
                else:
                    self._idle_cycles += 1
                    if self._idle_cycles > 10:
                        interval = min(3.0, self.config.capture_interval * 2)  # idle: slow to 2-3s
                    else:
                        interval = self.config.capture_interval

                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                self._write_trade_journal(
                    "SYSTEM_ERROR",
                    details=f"主迴圈錯誤: {str(e)[:200]}",
                )
                await asyncio.sleep(5)

        logger.info("Copy Trader stopped")
        self._write_trade_journal("SYSTEM_STOP", details="系統正常停止")

    def stop(self):
        """Stop the copy trader."""
        logger.info("Stopping Copy Trader...")
        self._running = False
        self.trade_manager.stop()

    def _setup_signal_handlers(self):
        """Setup OS signal handlers for graceful shutdown."""
        def handle_signal(sig, frame):
            logger.info(f"Received signal {sig}")
            self.stop()

        signal.signal(signal.SIGINT, handle_signal)
        # SIGTERM is not supported on Windows
        if sys.platform != 'win32':
            signal.signal(signal.SIGTERM, handle_signal)

    def _verify_mt5_connection(self) -> bool:
        """Verify MT5 bridge is running."""
        symbol_name = getattr(self.config, "symbol_name", DEFAULT_SYMBOL)
        candidate_files = [
            Path(self.config.mt5_files_dir) / f"{symbol_name}_price.json",
            Path(self.config.mt5_files_dir) / f"{DEFAULT_SYMBOL}_price.json",
        ]
        price_file = next((path for path in candidate_files if path.exists()), None)

        if not price_file:
            logger.warning(f"Price file not found: {[str(p) for p in candidate_files]}")
            return False

        # Check if file was updated recently
        age = time.time() - price_file.stat().st_mtime
        if age > 120:
            logger.warning(f"Price file is stale ({age:.0f}s old) - market may be closed")

        logger.info("MT5 connection verified")
        return True

    # ------------------------------------------------------------------
    # Clipboard-based signal path (primary)
    # ------------------------------------------------------------------

    def _use_clipboard_path(self) -> bool:
        """是否使用剪貼板作為主要訊號來源。"""
        src = getattr(self.config, "signal_source", "clipboard")
        return src == "clipboard" and self.clipboard_service is not None

    @staticmethod
    def _merge_pending_signal(prev, new):
        """
        把 new 的已知欄位填入 prev，回傳合併後的 ParsedSignal。
        規則：prev 已有的欄位優先保留，只有 prev 為空時才用 new 的值。
        例外：take_profit 若 prev 空或 new 比較完整，整個覆蓋。
        """
        # 直接在 prev 物件上修改（ParsedSignal 是 dataclass，mutable）
        if new is None:
            return prev
        if not prev.direction and new.direction:
            prev.direction = new.direction
        if not prev.entry_price and new.entry_price:
            prev.entry_price = new.entry_price
        if not prev.stop_loss and new.stop_loss:
            prev.stop_loss = new.stop_loss
        # TP：prev 空 → 用 new；prev 有但 new 較多 → 取 new；否則保留
        prev_tp = list(prev.take_profit or [])
        new_tp = list(new.take_profit or [])
        if not prev_tp and new_tp:
            prev.take_profit = new_tp
        elif new_tp and len(new_tp) > len(prev_tp):
            prev.take_profit = new_tp
        # market order / lot 維持 prev
        if not getattr(prev, "is_market_order", False) and getattr(new, "is_market_order", False):
            prev.is_market_order = True
        if not prev.lot_size and new.lot_size:
            prev.lot_size = new.lot_size
        # merge 後等同於完整解析成功
        prev.is_valid = bool(prev.direction)
        return prev

    async def _process_clipboard_cycle(self) -> bool:
        """
        剪貼板主通道：
          1. 對每個 LINE 視窗做一次 copy_chat_tail
          2. 切出訊息列表 → diff 出新訊息
          3. 逐則跑：取消/SL-hit 關鍵字 → 既有 regex → (未完整時 Vision) → 下單
          4. mark_seen 避免重覆下單
        """
        self._update_daily_loss()
        if self._daily_loss >= self.config.max_daily_loss:
            logger.warning(f"Daily loss limit reached (${self._daily_loss:.2f})")
            return False

        # 清掉逾時 pending
        now = time.time()
        expired = [k for k, v in self._pending_signals.items() if now - v["time"] > 120]
        for k in expired:
            sig = self._pending_signals[k]["signal"]
            self._log_signal_skip(
                "等待逾時（120秒未收齊數值）",
                signal=sig, source=k,
                details=f"已有: direction={sig.direction}, entry={sig.entry_price}, SL={sig.stop_loss}, TP={sig.take_profit}"
            )
            self._pending_signals.pop(k)

        captures = self.clipboard_service.capture_all()
        had_any_new = False

        for cap in captures:
            if not cap.ok or not cap.new_messages:
                continue

            # 按時間順序處理（parser 已依照原文順序）
            for msg in cap.new_messages:
                try:
                    result = await self._process_clipboard_message(msg, cap)
                except Exception as e:
                    # 只把「parser/邏輯層」的例外視為壞訊息 mark_seen；
                    # 如果是 MT5 相關例外（通訊/檔案系統），要保留訊號讓下一輪重試。
                    err_str = str(e)
                    mt5_related = any(
                        kw in err_str
                        for kw in ("MT5", "mt5", "bridge", "command", "price_file", "timeout", "not connected")
                    )
                    if mt5_related:
                        logger.error(f"clipboard message MT5-related error, will retry: {e}")
                        if hasattr(self.clipboard_service, "force_retry"):
                            self.clipboard_service.force_retry(cap.source_name)
                        # 不 mark_seen — 讓下一輪重試
                        continue
                    logger.exception(f"clipboard message handler failed: {e}")
                    self.clipboard_service.mark_seen(cap.source_name, [msg])
                    continue

                # result == "pending" 代表目前缺 SL/TP 等資訊，等待後續訊息補齊 —
                # 此時不 mark seen，讓下一輪若剪貼板內容有變（例如對方編輯了訊息）
                # 還能重新評估。配合 app._pending_signals 的 120 秒 timeout 兜底。
                if result == "pending":
                    continue
                self.clipboard_service.mark_seen(cap.source_name, [msg])
                had_any_new = True

        return had_any_new

    async def _process_clipboard_message(self, msg: "LineMessage", cap) -> str:
        """
        處理單一則新 LINE 訊息（從剪貼板來）。

        Returns 一個字串表示結果，caller 用這個決定是否 mark_seen：
          "done"     — 已下單 / 已確定不是信號 / 撤單處理完 → 可以 mark_seen
          "pending"  — 缺 SL/TP 等，等後續訊息補齊 → 不要 mark_seen
        """
        source_name = cap.source_name or "default"
        source_display = cap.display_name or source_name
        body = (msg.body or "").strip()

        if not body:
            return "done"

        # 1. 取消 / SL-hit 關鍵字 — 即使是短訊息也要處理
        if self._check_cancel_keywords(body, source_name):
            return "done"
        if self._check_sl_hit(body, source_name):
            return "done"

        # 2. 長度過濾（解析交易信號需要至少 10 字；短訊息上面已處理完）
        if len(body) < 10:
            return "done"

        # 3. 內容關鍵字過濾
        has_pending = source_name in self._pending_signals
        is_signal, filter_reason = is_potential_signal(body)
        if not is_signal and not has_pending:
            self._signals_filtered += 1
            logger.debug(f"clipboard filtered ({source_display}): {filter_reason}")
            return "done"

        t_signal_start = time.time()
        ts_str = msg.timestamp.isoformat() if msg.timestamp else msg.time_str
        logger.info(f"📨 Clipboard new msg [{source_display} {ts_str} {msg.sender}]: {body[:80]!r}")

        # 4. 多信號偵測（一則 body 裡可能同時有 BUY+Sell）
        all_regex_signals = self.signal_parser.parse_all_latest(body)
        extra_signals = []
        if len(all_regex_signals) > 1:
            complete_extras = []
            for sig in all_regex_signals:
                if sig.is_valid and sig.stop_loss and sig.take_profit and len(sig.take_profit) > 0:
                    sk = str(self._signal_key(sig))
                    if sk not in self._processed_signals:
                        complete_extras.append(sig)
            if len(complete_extras) > 1:
                logger.info(f"Multi-signal: {len(complete_extras)} signals in one message")
                extra_signals = complete_extras[1:]

        # 5. Regex 主解析
        regex_signal = self.signal_parser.parse_latest(body)
        if not regex_signal.is_valid and not regex_signal.direction and not has_pending:
            return "done"

        regex_complete = (
            regex_signal.is_valid
            and regex_signal.stop_loss
            and regex_signal.take_profit
            and len(regex_signal.take_profit) > 0
        )

        if regex_complete:
            signal = regex_signal
            logger.info(f"Regex 完整解析 (clipboard): {signal}")
            pre_key = str(self._signal_key(signal))
            if pre_key in self._processed_signals:
                logger.debug(f"重複信號，跳過: {signal}")
                return "done"
        else:
            # Regex 不完整 — 在 clipboard 模式下不跑 Vision（沒有截圖），
            # 但保留 pending 跨訊息合併邏輯。
            have = []
            if regex_signal.direction: have.append(f"方向={regex_signal.direction}")
            if regex_signal.entry_price: have.append(f"入場={regex_signal.entry_price}")
            if regex_signal.stop_loss: have.append(f"SL={regex_signal.stop_loss}")
            if regex_signal.take_profit: have.append(f"TP={regex_signal.take_profit}")
            logger.info(f"Regex 不完整 (clipboard): {', '.join(have) or '無'}")
            signal = regex_signal

        # --- 若有 pending，把新訊息的欄位合併進去（pending 為底，新值填空）---
        # 這一步必須在完整性檢查之前，否則第二則補單訊息會被誤判為不完整而退出。
        prev_pending = self._pending_signals.get(source_name) if has_pending else None
        if prev_pending is not None:
            signal = self._merge_pending_signal(prev_pending["signal"], signal)

        if not signal.is_valid:
            return "done"

        # 6. 完整性檢查 + pending buffer（跨訊息合併）
        missing = []
        if not signal.entry_price and not getattr(signal, 'is_market_order', False):
            missing.append("入場價")
        if not signal.stop_loss:
            missing.append("止損")
        if not signal.take_profit or len(signal.take_profit) == 0 or signal.take_profit[0] is None:
            missing.append("止盈")

        if missing:
            if prev_pending is not None:
                elapsed = time.time() - prev_pending["time"]
                if elapsed > 120:
                    self._log_signal_skip(
                        "等待逾時（120秒未收齊數值）",
                        signal=prev_pending["signal"], source=source_display,
                        details=f"仍缺少: {', '.join(missing)} | 已等待 {elapsed:.0f}秒"
                    )
                    self._pending_signals.pop(source_name, None)
                    return "done"
            first_time = prev_pending is None
            self._pending_signals[source_name] = {
                "signal": signal,
                "time": prev_pending["time"] if prev_pending else time.time(),
                "direction": signal.direction,
                "last_hash": "",
            }
            if first_time:
                have_parts = []
                if signal.direction: have_parts.append(f"方向={signal.direction}")
                if signal.entry_price: have_parts.append(f"入場={signal.entry_price}")
                if signal.stop_loss: have_parts.append(f"止損={signal.stop_loss}")
                if signal.take_profit: have_parts.append(f"止盈={signal.take_profit}")
                logger.warning(f"⏳ 缺少 {', '.join(missing)} — 等待後續訊息 (已有: {', '.join(have_parts)})")
                self._write_trade_journal(
                    "PENDING_SIGNAL", signal=signal, source=source_display,
                    details=f"缺少: {', '.join(missing)} | 已有: {', '.join(have_parts)}",
                    ocr_text=body,
                )
            return "pending"

        # 7. 信號完整 — 清理 pending
        if source_name in self._pending_signals:
            pending_time = self._pending_signals[source_name]["time"]
            wait_secs = time.time() - pending_time
            logger.info(f"✅ 跨訊息信號收齊！等待了 {wait_secs:.1f}秒 | {signal}")
            self._write_trade_journal(
                "MULTI_MSG_COMPLETE", signal=signal, source=source_display,
                details=f"等待了 {wait_secs:.1f}秒",
                ocr_text=body,
            )
            self._pending_signals.pop(source_name, None)

        # 8. 最終 dedup
        signal_dedup_key = str(self._signal_key(signal))
        if signal_dedup_key in self._processed_signals:
            logger.info(f"重複信號（相同數值），跳過: {signal}")
            return "done"

        if signal.confidence < 0.95:
            signal.confidence = 0.95

        if not self._validate_signal(signal):
            return "done"

        self._processed_signals.add(signal_dedup_key)
        self._save_persisted_signals()

        self._emit_event("signal_detected", {
            "direction": signal.direction,
            "entry": signal.entry_price,
            "sl": signal.stop_loss,
            "tp": signal.take_profit,
        })

        lot = self.trade_manager.get_martingale_lot_size(source_display)
        mg_level = self.trade_manager.current_martingale_level
        if self.trade_manager.martingale_per_source:
            src_state = self.trade_manager._source_martingale.get(source_display, {})
            mg_level = src_state.get("level", 0)
        self._write_trade_journal(
            "ORDER_SUBMITTED", signal=signal, source=source_display,
            details=(
                f"method=clipboard+regex | 手數={lot} | 馬丁層級={mg_level}"
                f" | signal_key={self._signal_key(signal)}"
                f" | sender={msg.sender} | msg_time={ts_str}"
            ),
            ocr_text=body,
        )

        signal_id = self.trade_manager.submit_signal(
            signal,
            auto_execute=self.config.auto_execute,
            cancel_after_seconds=self.config.cancel_pending_after_seconds,
            cancel_if_price_beyond=self.config.cancel_if_price_beyond_percent,
            source_window=source_display,
        )
        elapsed = time.time() - t_signal_start
        logger.info(f"✅ Clipboard signal submitted: {signal_id} (pipeline {elapsed:.2f}s)")
        self._daily_trades += 1
        self._emit_event("trade_submitted", {"signal_id": signal_id})

        # 9. 處理 extras（同一 body 裡的第二筆信號）
        for extra_sig in extra_signals:
            extra_key = str(self._signal_key(extra_sig))
            if extra_key in self._processed_signals:
                continue
            if not self._validate_signal(extra_sig):
                continue
            self._processed_signals.add(extra_key)
            self._save_persisted_signals()
            self._write_trade_journal(
                "ORDER_SUBMITTED", signal=extra_sig, source=source_display,
                details=(
                    f"method=clipboard+regex | multi_signal=True"
                    f" | signal_key={self._signal_key(extra_sig)}"
                ),
                ocr_text=body,
            )
            extra_id = self.trade_manager.submit_signal(
                extra_sig,
                auto_execute=self.config.auto_execute,
                cancel_after_seconds=self.config.cancel_pending_after_seconds,
                cancel_if_price_beyond=self.config.cancel_if_price_beyond_percent,
                source_window=source_display,
            )
            logger.info(f"✅ Clipboard extra signal submitted: {extra_id}")
            self._daily_trades += 1
            self._emit_event("trade_submitted", {"signal_id": extra_id})

        return "done"

    async def _process_cycle(self) -> bool:
        """Single processing cycle. Returns True if frames were processed."""
        # Update and check daily loss limit from closed trades
        self._update_daily_loss()
        if self._daily_loss >= self.config.max_daily_loss:
            logger.warning(f"Daily loss limit reached (${self._daily_loss:.2f})")
            return False

        # Capture screen regions
        frames = self.capture_service.capture_all_regions(deduplicate=True)

        if not frames:
            return False

        # Clean up expired pending signals (>120s)
        now = time.time()
        expired = [k for k, v in self._pending_signals.items() if now - v["time"] > 120]
        for k in expired:
            sig = self._pending_signals[k]["signal"]
            self._log_signal_skip(
                "等待逾時（120秒未收齊數值）",
                signal=sig, source=k,
                details=f"已有: direction={sig.direction}, entry={sig.entry_price}, SL={sig.stop_loss}, TP={sig.take_profit}"
            )
            self._pending_signals.pop(k)

        for frame in frames:
            source_display = self._window_display_names.get(frame.source_name or "", frame.source_name or "")

            # --- BUBBLE-AWARE OCR: detect newest chat bubbles, OCR only those ---
            t_ocr = time.time()
            text = self.ocr_service.extract_newest_bubble_text(frame.image_path)
            ocr_ms = (time.time() - t_ocr) * 1000
            logger.debug(f"OCR took {ocr_ms:.0f}ms")

            if not text:
                continue

            raw_text = text

            stale_reason = self._detect_stale_chat_capture(raw_text)
            if stale_reason:
                recovered = await self._recover_latest_chat_capture(
                    frame.source_name or "",
                    source_display,
                    raw_text,
                    stale_reason,
                )
                Path(frame.image_path).unlink(missing_ok=True)
                if not recovered:
                    continue
                frame, raw_text, text = recovered
            else:
                text = self._sanitize_chat_text(raw_text)

            # Check for cancel/withdraw and SL-hit keywords BEFORE length filter.
            # Short messages like "撤" (1 char) or "SL" (2 chars) must be caught
            # even when the OCR text is very short.
            if self._check_cancel_keywords(raw_text, frame.source_name):
                Path(frame.image_path).unlink(missing_ok=True)
                continue

            if self._check_sl_hit(raw_text, frame.source_name):
                Path(frame.image_path).unlink(missing_ok=True)
                continue

            # Now apply length filter for signal parsing (signals need at least 10 chars)
            if len(text.strip()) < 10:
                Path(frame.image_path).unlink(missing_ok=True)
                continue

            # Check for duplicate (exact hash + fuzzy text comparison)
            text_hash = self._compute_text_hash(text)
            if self._is_hash_active(text_hash):
                logger.debug("Duplicate (exact hash), skipping")
                continue

            # Fuzzy text dedup: catch OCR variations of the same content
            is_fuzzy_dup = False
            for prev_text in self._recent_ocr_texts:
                if self._is_text_similar(text, prev_text):
                    is_fuzzy_dup = True
                    break
            if is_fuzzy_dup:
                logger.debug("Duplicate (fuzzy match), skipping")
                self._add_hash_with_ttl(text_hash, 30)
                continue
            # Keep rolling window of recent texts (max 10)
            self._recent_ocr_texts.append(text)
            if len(self._recent_ocr_texts) > 10:
                self._recent_ocr_texts.pop(0)

            # MT5 TRADE HISTORY FILTER — skip completed trade screenshots
            if self._is_mt5_trade_history(text):
                logger.info("MT5 trade history screenshot detected, skipping")
                self._add_hash_with_ttl(text_hash, 120)
                continue

            # KEYWORD FILTER - quick local check
            # Bypass filter if we have a pending incomplete signal for this source
            source_name = frame.source_name or "default"
            has_pending = source_name in self._pending_signals
            is_signal, filter_reason = is_potential_signal(text)
            if not is_signal and not has_pending:
                self._signals_filtered += 1
                logger.debug(f"Filtered out (not a signal): {filter_reason}")
                self._add_hash_with_ttl(text_hash, 60)
                continue
            if has_pending and not is_signal:
                filter_reason = "pending signal waiting for more values"

            t_signal_start = time.time()
            logger.info(f"Potential signal detected: {filter_reason}")

            # === MULTI-SIGNAL CHECK — detect if OCR contains 2+ signals ===
            all_regex_signals = self.signal_parser.parse_all_latest(text)
            extra_signals = []
            if len(all_regex_signals) > 1:
                # Multiple signals found (e.g. 乘 posting Sell+BUY at same time)
                # Check which are complete and non-duplicate
                complete_extras = []
                for sig in all_regex_signals:
                    if sig.is_valid and sig.stop_loss and sig.take_profit and len(sig.take_profit) > 0:
                        sk = str(self._signal_key(sig))
                        if sk not in self._processed_signals:
                            complete_extras.append(sig)
                if len(complete_extras) > 1:
                    logger.info(f"Multi-signal: found {len(complete_extras)} complete signals in one capture")
                    # Process extras after the primary signal
                    extra_signals = complete_extras[1:]  # first one goes through normal flow

            # === REGEX FIRST — always use regex parse_latest (timestamp-based) ===
            regex_signal = self.signal_parser.parse_latest(text)

            # 1) Regex found nothing at all AND no pending → skip
            if not regex_signal.is_valid and not regex_signal.direction and not has_pending:
                logger.debug(f"Regex: not a signal, skipping")
                self._add_hash_with_ttl(text_hash, 60)
                continue

            # 2) Regex got complete signal (direction + SL + TP) → use directly, no Vision needed
            regex_complete = (
                regex_signal.is_valid
                and regex_signal.stop_loss
                and regex_signal.take_profit
                and len(regex_signal.take_profit) > 0
            )

            if regex_complete:
                signal = regex_signal
                logger.info(f"Regex 完整解析: {signal}")

                # Check duplicate
                pre_key = str(self._signal_key(signal))
                if pre_key in self._processed_signals:
                    logger.debug(f"重複信號（已處理），跳過: {signal}")
                    self._add_hash_with_ttl(text_hash, 60)
                    continue
            else:
                # 3) Regex incomplete — send to Vision for help
                # Extract what regex got for logging
                have = []
                if regex_signal.direction: have.append(f"方向={regex_signal.direction}")
                if regex_signal.entry_price: have.append(f"入場={regex_signal.entry_price}")
                if regex_signal.stop_loss: have.append(f"SL={regex_signal.stop_loss}")
                if regex_signal.take_profit: have.append(f"TP={regex_signal.take_profit}")

                # --- Vision dedup: don't send the same incomplete signal to Vision repeatedly ---
                # Use parsed signal content (not OCR text hash) as key, because OCR text
                # varies slightly between captures even when the actual signal is the same.
                source_name = frame.source_name or "default"
                tp_tuple = tuple(regex_signal.take_profit) if regex_signal.take_profit else ()
                vision_key = f"{source_name}:{regex_signal.direction}:{regex_signal.entry_price}:{regex_signal.stop_loss}:{tp_tuple}"
                vision_expiry = self._vision_sent_cache.get(vision_key, 0)

                if time.time() < vision_expiry:
                    # Already sent this incomplete signal to Vision recently — skip
                    logger.debug(f"Vision dedup: 同一不完整信號已送過，跳過 ({', '.join(have)})")
                    self._add_hash_with_ttl(text_hash, 60)
                    continue

                logger.info(f"Regex 不完整 ({', '.join(have) or '無'}), 送 Vision 補充...")

                # Cache this signal for 120 seconds to prevent repeated Vision calls
                self._vision_sent_cache[vision_key] = time.time() + 120

                signal = None
                self._api_calls_today += 1

                # Tier 1: Gemini (run in executor to avoid blocking event loop)
                loop = asyncio.get_event_loop()
                if self.gemini_parser and self.gemini_parser.is_available:
                    logger.info("Sending screenshot to Gemini...")
                    signal = await loop.run_in_executor(
                        None, self.gemini_parser.parse_image, frame.image_path)
                    if signal.is_valid:
                        logger.info(f"Gemini result: {signal}")
                    elif signal.error:
                        logger.warning(f"Gemini failed: {signal.error}, trying Groq...")
                        signal = None
                    else:
                        signal = None

                # Tier 2: Groq (run in executor)
                if signal is None and self.groq_parser and self.groq_parser.is_available:
                    logger.info("Sending screenshot to Groq Vision...")
                    signal = await loop.run_in_executor(
                        None, self.groq_parser.parse_image, frame.image_path)
                    if signal.is_valid:
                        logger.info(f"Groq result: {signal}")
                    elif signal.error:
                        logger.warning(f"Groq failed: {signal.error}, falling back to regex...")
                        signal = None
                    else:
                        signal = None

                # Tier 3: use whatever regex got
                if signal is None:
                    signal = regex_signal

            logger.debug(f"API calls today: {self._api_calls_today}")

            if not signal.is_valid:
                if signal.error:
                    self._log_signal_skip(
                        "Vision 解析失敗",
                        source=source_display,
                        details=f"錯誤: {signal.error}"
                    )
                else:
                    logger.debug(f"非交易信號，忽略")
                self._add_hash_with_ttl(text_hash, 60)
                continue

            logger.info(f"Valid signal detected: {signal}")

            # Early dedup check (exact match)
            early_key = str(self._signal_key(signal))
            if early_key in self._processed_signals:
                logger.debug(f"重複信號（已處理），跳過: {signal}")
                self._add_hash_with_ttl(text_hash, 60)
                continue

            # Must have ALL 3 values: entry price, SL, TP
            missing = []
            if not signal.entry_price and not getattr(signal, 'is_market_order', False):
                missing.append("入場價")
            if not signal.stop_loss:
                missing.append("止損")
            if not signal.take_profit or len(signal.take_profit) == 0 or signal.take_profit[0] is None:
                missing.append("止盈")

            if missing:
                source = frame.source_name or "default"
                pending = self._pending_signals.get(source)

                if pending:
                    # Already waiting — check timeout (120s)
                    elapsed = time.time() - pending["time"]
                    if elapsed > 120:
                        self._log_signal_skip(
                            "等待逾時（120秒未收齊數值）",
                            signal=pending["signal"], source=source_display,
                            details=f"仍缺少: {', '.join(missing)} | 已等待 {elapsed:.0f}秒"
                        )
                        self._pending_signals.pop(source, None)
                        self._add_hash_with_ttl(text_hash, 180)
                        continue

                # Store/update as pending — wait for next message to complete it
                prev = self._pending_signals.get(source)
                first_time = prev is None
                self._pending_signals[source] = {
                    "signal": signal,
                    "time": prev["time"] if prev else time.time(),
                    "direction": signal.direction,
                    "last_hash": text_hash,
                }

                if first_time:
                    # First detection — log and use short TTL for quick re-check
                    have_parts = []
                    if signal.direction:
                        have_parts.append(f"方向={signal.direction}")
                    if signal.entry_price:
                        have_parts.append(f"入場={signal.entry_price}")
                    if signal.stop_loss:
                        have_parts.append(f"止損={signal.stop_loss}")
                    if signal.take_profit:
                        have_parts.append(f"止盈={signal.take_profit}")
                    logger.warning(
                        f"⏳ 缺少 {', '.join(missing)} — 等待後續訊息... "
                        f"(已有: {', '.join(have_parts)})"
                    )
                    self._write_trade_journal(
                        "PENDING_SIGNAL", signal=signal, source=source_display,
                        details=f"缺少: {', '.join(missing)} | 已有: {', '.join(have_parts)}",
                        ocr_text=raw_text,
                    )
                    self._add_hash_with_ttl(text_hash, 15)
                else:
                    # Same signal still incomplete — long TTL to avoid re-sending Vision
                    # Will re-trigger when screen actually changes (new text_hash)
                    self._add_hash_with_ttl(text_hash, 120)
                continue

            # Signal is complete — clear any pending buffer for this source
            source = frame.source_name or "default"
            if source in self._pending_signals:
                pending_time = self._pending_signals[source]["time"]
                wait_secs = time.time() - pending_time
                logger.info(f"✅ 跨訊息信號收齊！等待了 {wait_secs:.1f}秒 | {signal}")
                self._write_trade_journal(
                    "MULTI_MSG_COMPLETE", signal=signal, source=source_display,
                    details=f"等待了 {wait_secs:.1f}秒",
                    ocr_text=raw_text,
                )
                self._pending_signals.pop(source, None)

            # === CONFIRMATION: second Vision call on fresh capture ===
            confirmed = await self._confirm_signal_vision(signal, frame)
            if not confirmed:
                self._log_signal_skip(
                    "二次確認失敗（重抓後關鍵數值差異過大）",
                    signal=signal, source=source_display,
                )
                self._add_hash_with_ttl(text_hash, 120)
                Path(frame.image_path).unlink(missing_ok=True)
                continue

            logger.info("Signal CONFIRMED")
            self._incomplete_retry_counts.pop(text_hash, None)

            if signal.confidence < 0.95:
                signal.confidence = 0.95

            # Signal-level dedup: exact match on direction+entry+SL+TP1
            signal_key = self._signal_key(signal)
            signal_dedup_key = f"{signal_key}"
            if signal_dedup_key in self._processed_signals:
                logger.info(f"重複信號（相同數值），跳過: {signal}")
                self._add_hash_with_ttl(text_hash, self.config.signal_dedup_minutes * 60)
                continue

            # Safety checks for auto mode
            if not self._validate_signal(signal):
                continue

            # Mark as processed
            self._add_hash_with_ttl(text_hash, self.config.signal_dedup_minutes * 60)
            self._processed_signals.add(signal_dedup_key)
            self._save_persisted_signals()  # Persist to disk for restart survival

            self._emit_event("signal_detected", {
                "direction": signal.direction,
                "entry": signal.entry_price,
                "sl": signal.stop_loss,
                "tp": signal.take_profit,
            })

            # Submit to trade manager (include source window name)
            source_window = self._window_display_names.get(frame.source_name, frame.source_name)

            # Journal: record everything for post-day audit
            lot = self.trade_manager.get_martingale_lot_size(source_window)
            mg_level = self.trade_manager.current_martingale_level
            if self.trade_manager.martingale_per_source:
                src_state = self.trade_manager._source_martingale.get(source_window, {})
                mg_level = src_state.get("level", 0)
            self._write_trade_journal(
                "ORDER_SUBMITTED", signal=signal, source=source_window,
                details=(
                    f"method={getattr(signal, 'parse_method', 'regex')}"
                    f" | 手數={lot} | 馬丁層級={mg_level}"
                    f" | signal_key={self._signal_key(signal)}"
                ),
                ocr_text=raw_text,
            )

            signal_id = self.trade_manager.submit_signal(
                signal,
                auto_execute=self.config.auto_execute,
                cancel_after_seconds=self.config.cancel_pending_after_seconds,
                cancel_if_price_beyond=self.config.cancel_if_price_beyond_percent,
                source_window=source_window
            )

            elapsed = time.time() - t_signal_start
            logger.info(f"Signal submitted: {signal_id} (pipeline took {elapsed:.1f}s)")
            self._daily_trades += 1

            self._emit_event("trade_submitted", {"signal_id": signal_id})

            # === PROCESS EXTRA SIGNALS (from multi-signal detection) ===
            for extra_sig in extra_signals:
                extra_key = str(self._signal_key(extra_sig))
                if extra_key in self._processed_signals:
                    continue
                # Skip vision confirmation for extras — they came from same OCR capture
                if not self._validate_signal(extra_sig):
                    continue
                self._processed_signals.add(extra_key)
                self._save_persisted_signals()
                lot_e = self.trade_manager.get_martingale_lot_size(source_window)
                mg_level_e = self.trade_manager.current_martingale_level
                if self.trade_manager.martingale_per_source:
                    src_state_e = self.trade_manager._source_martingale.get(source_window, {})
                    mg_level_e = src_state_e.get("level", 0)
                self._write_trade_journal(
                    "ORDER_SUBMITTED", signal=extra_sig, source=source_window,
                    details=(
                        f"method={getattr(extra_sig, 'parse_method', 'regex')}"
                        f" | 手數={lot_e} | 馬丁層級={mg_level_e}"
                        f" | signal_key={self._signal_key(extra_sig)}"
                        f" | multi_signal=True"
                    ),
                    ocr_text=raw_text,
                )
                extra_id = self.trade_manager.submit_signal(
                    extra_sig,
                    auto_execute=self.config.auto_execute,
                    cancel_after_seconds=self.config.cancel_pending_after_seconds,
                    cancel_if_price_beyond=self.config.cancel_if_price_beyond_percent,
                    source_window=source_window
                )
                logger.info(f"Extra signal submitted: {extra_id}")
                self._daily_trades += 1
                self._emit_event("trade_submitted", {"signal_id": extra_id})

            # Clean up screenshot
            Path(frame.image_path).unlink(missing_ok=True)

        return True

    async def _confirm_signal_vision(self, first_signal, first_frame) -> bool:
        """
        Confirm signal by re-capturing and re-parsing.
        Uses Vision if available, otherwise falls back to OCR+regex.
        """
        confirm_count = self.config.ocr_confirm_count
        confirm_delay = self.config.ocr_confirm_delay

        if confirm_count <= 1:
            return True

        first_key = self._signal_key(first_signal)
        logger.info(f"Confirming signal: {first_key} ({confirm_count - 1} more reads)")

        matches = 0
        for i in range(confirm_count - 1):
            await asyncio.sleep(confirm_delay)

            # Re-capture the SAME window
            source = first_frame.source_name
            if source:
                frame = self.capture_service.capture_single_window(source)
            else:
                frames = self.capture_service.capture_all_regions(deduplicate=False)
                frame = frames[0] if frames else None
            if not frame:
                logger.warning(f"Confirm {i+1}: capture failed")
                continue

            # Confirm with bubble OCR + regex (same method as primary parse)
            retry_signal = None
            raw_text = self.ocr_service.extract_newest_bubble_text(frame.image_path)
            source_display = self._window_display_names.get(first_frame.source_name or "", first_frame.source_name or "")

            if raw_text:
                stale_reason = self._detect_stale_chat_capture(raw_text)
                if stale_reason:
                    recovered = await self._recover_latest_chat_capture(
                        source or "",
                        source_display,
                        raw_text,
                        stale_reason,
                    )
                    Path(frame.image_path).unlink(missing_ok=True)
                    if not recovered:
                        logger.warning(f"Confirm {i+1}: chat still stale after recovery")
                        continue
                    frame, raw_text, text = recovered
                else:
                    text = self._sanitize_chat_text(raw_text)
            else:
                text = ""

            if text and len(text.strip()) >= 10:
                is_sig, _ = is_potential_signal(text)
                if is_sig:
                    retry_signal = self.signal_parser.parse_latest(text)

            Path(frame.image_path).unlink(missing_ok=True)

            if not retry_signal or not retry_signal.is_valid:
                logger.warning(f"Confirm {i+1}: parse failed")
                continue

            # Check completeness
            if not retry_signal.stop_loss or not retry_signal.take_profit:
                logger.warning(f"Confirm {i+1}: incomplete signal")
                continue

            retry_key = self._signal_key(retry_signal)
            logger.info(f"Confirm {i+1}: {retry_key}")

            if retry_key == first_key:
                matches += 1
                logger.info(f"  -> MATCH ({matches}/{confirm_count-1})")
            elif self._signals_match_for_confirmation(first_signal, retry_signal):
                matches += 1
                logger.info(
                    f"  -> RELAXED MATCH ({matches}/{confirm_count-1}) "
                    f"(expected {first_key}, got {retry_key})"
                )
            else:
                logger.warning(f"  -> MISMATCH (expected {first_key}, got {retry_key})")
                # Journal: record mismatch details for debugging
                self._write_trade_journal(
                    "CONFIRM_MISMATCH", source=source_display,
                    details=f"第一次={first_key} | 確認={retry_key} | 確認信號={retry_signal}",
                    ocr_text=text if text else "",
                )

        required = confirm_count - 1
        if matches >= required:
            return True
        else:
            logger.warning(f"Only {matches}/{required} confirmations matched")
            return False

    @staticmethod
    def _prices_close(price1, price2, tolerance: float) -> bool:
        """Compare two parsed prices with a small OCR-safe tolerance."""
        if price1 is None or price2 is None:
            return False
        return abs(float(price1) - float(price2)) <= tolerance

    def _take_profit_overlap(self, first_tps, retry_tps, tolerance: float = 2.0) -> bool:
        """True if the two TP lists share at least one close-enough level."""
        if not first_tps or not retry_tps:
            return False

        for first_tp in first_tps:
            for retry_tp in retry_tps:
                if self._prices_close(first_tp, retry_tp, tolerance):
                    return True
        return False

    def _signals_match_for_confirmation(self, first_signal, retry_signal) -> bool:
        """Allow small OCR/parser drift when confirming the same signal."""
        if not first_signal or not retry_signal:
            return False

        if first_signal.direction != retry_signal.direction:
            return False

        if not self._prices_close(first_signal.stop_loss, retry_signal.stop_loss, 1.5):
            return False

        first_entry = first_signal.entry_price
        retry_entry = retry_signal.entry_price
        if first_entry is not None and retry_entry is not None:
            if not self._prices_close(first_entry, retry_entry, 5.0):
                return False

        if not self._take_profit_overlap(first_signal.take_profit, retry_signal.take_profit):
            return False

        return True

    # Cancel keywords that trigger deletion of pending orders
    # Multi-char keywords first (more specific), single-char last (prone to false positives)
    CANCEL_KEYWORDS = ['撤單', '撤单', '刪單', '删单', '測單', '测单', '測掉', '撤掉', '撒掉', '取消', '撤', '撒']

    # Phrases that CONTAIN cancel keywords but are NOT cancel commands.
    # "直到取消" = MT5 GTC order expiry type, not a cancel instruction.
    # "就取消/會取消/就撤單" = discussing future actions, not cancelling now.
    CANCEL_EXCLUSION_PHRASES = [
        '直到取消', '到取消',           # MT5 GTC
        '就取消', '會取消', '再取消',    # future tense: "I will cancel"
        '就撤單', '會撤單', '可能撤單',  # future tense: "I will withdraw"
        '就撤单', '會撤单',
        '我會撤', '我就撤',             # "I will withdraw"
    ]

    # "SL hit" keywords — signal provider says their SL was triggered
    # This means the signal is no longer valid, cancel any pending order from this source
    SL_HIT_KEYWORDS = ['SL', 'sl', '觸損', '止損了', '損了']

    # Phrases that CONTAIN SL keywords but are NOT SL-hit notifications.
    # "SL抓到15" = adjusting SL to 15 points, not SL triggered.
    SL_NOT_HIT_PHRASES = ['SL抓', 'sl抓', 'SL調', 'SL移', 'SL上移', 'SL設', 'SL到', 'sl調', 'sl移']

    # Blocks containing these patterns are likely UI elements (title bar, tabs, ads),
    # not actual chat messages. Skip them to avoid false cancel triggers.
    UI_NOISE_PATTERNS = [
        'Leverage Empire',
        '禁言群',
        '黃金報單',
        '限定優惠',
        '最低一件',
        '立即下載',
    ]

    CHAT_RECENCY_SECONDS = 5 * 60
    CHAT_STALE_LOG_INTERVAL = 60
    CHAT_BACKLOG_PATTERNS = [
        r'以下為尚未[^\s|]{0,6}[訊讯]息',
        r'\d+\+\s*[则則]\s*[訊讯]息',
        r'加入聊天',
        r'Auto-?reply',
        r'進[來来]的朋友',
        r'記得到記事本看開單策略',
        r'還有其他報單群可以加入',
        r'查看討論串[內内]的訊息',
        r'傳送至Keep筆記',
        r'另存新檔',
        r'儲存\|另存新檔',
    ]
    CHAT_CLEANUP_PATTERNS = [
        (r'以下為尚未[^\s|]{0,6}[訊讯]息', ' |MSG| '),
        (r'\d+\+\s*[则則]\s*[訊讯]息', ' '),
        (r'[@●•]\s*\d+', ' '),
        (r'加入聊天', ' '),
        (r'Auto-?reply', ' '),
        (r'進[來来]的朋友', ' '),
        (r'記得到記事本看開單策略', ' '),
        (r'還有其他報單群可以加入', ' '),
        (r'查看討論串[內内]的訊息', ' '),
        (r'傳送至Keep筆記', ' '),
        (r'另存新檔', ' '),
        (r'\b儲存\b', ' '),
    ]
    CHAT_TIME_RE = re.compile(
        r'(?<!\d)(?:(?P<meridiem>[上下]?\s*午)\s*)?(?P<hour>\d{1,2})\s*[:：]\s*(?P<minute>\d{2})(?!\s*[:：]\s*\d{2})'
    )

    def _sanitize_chat_text(self, text: str) -> str:
        """Remove LINE UI noise so the parser sees mostly chat content."""
        cleaned = text or ""
        for pattern, replacement in self.CHAT_CLEANUP_PATTERNS:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'(?:\s*\|MSG\|\s*)+', ' |MSG| ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()
        cleaned = re.sub(r'^(?:\|MSG\|\s*)+', '', cleaned)
        cleaned = re.sub(r'(?:\s*\|MSG\|)+$', '', cleaned)
        return cleaned.strip()

    def _extract_latest_visible_chat_time(self, text: str) -> datetime | None:
        """Infer the newest visible LINE message timestamp from OCR text."""
        now = datetime.now()
        best_candidate = None
        best_age = None

        for match in self.CHAT_TIME_RE.finditer(text or ""):
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            meridiem = (match.group("meridiem") or "").replace(" ", "")

            hour_candidates = []
            if meridiem.startswith("下"):
                hour_candidates.append((hour % 12) + 12)
            elif meridiem.startswith("上"):
                hour_candidates.append(hour % 12)
            else:
                hour_candidates.append(hour)
                if hour < 12:
                    hour_candidates.append(hour + 12)

            for candidate_hour in hour_candidates:
                candidate = now.replace(
                    hour=candidate_hour,
                    minute=minute,
                    second=0,
                    microsecond=0,
                )
                if candidate > now + timedelta(minutes=5):
                    candidate -= timedelta(days=1)
                age = (now - candidate).total_seconds()
                if age < 0:
                    continue
                if best_age is None or age < best_age:
                    best_age = age
                    best_candidate = candidate

        return best_candidate

    def _detect_stale_chat_capture(self, raw_text: str) -> str:
        """Return a reason when the OCR frame is likely not showing the newest chat."""
        latest_visible_time = self._extract_latest_visible_chat_time(raw_text)
        if latest_visible_time is not None:
            age_seconds = (datetime.now() - latest_visible_time).total_seconds()
            if age_seconds > self.CHAT_RECENCY_SECONDS:
                age_minutes = max(1, int(age_seconds // 60))
                return (
                    f"最新可見聊天時間 {latest_visible_time.strftime('%H:%M')} "
                    f"已過 {age_minutes} 分鐘，疑似仍停留在舊訊息"
                )
            return ""

        if any(re.search(pattern, raw_text, re.IGNORECASE) for pattern in self.CHAT_BACKLOG_PATTERNS):
            return "畫面含未讀/加入聊天/分享面板等雜訊，疑似未定位到最新訊息"

        return ""

    def _handle_stale_chat_capture(self, source_name: str, source_display: str, raw_text: str, reason: str):
        """Force the chat window back to bottom and log the issue with rate limiting."""
        logger.warning(f"聊天室未對齊最新訊息 [{source_display or source_name or 'default'}]: {reason}")
        if source_name:
            try:
                self.capture_service.scroll_window_to_bottom(source_name, force=True)
            except Exception as e:
                logger.debug(f"Failed to recenter chat window '{source_name}': {e}")

        now = time.time()
        last_logged = self._stale_capture_logged_at.get(source_name or "", 0)
        if now - last_logged >= self.CHAT_STALE_LOG_INTERVAL:
            self._stale_capture_logged_at[source_name or ""] = now
            self._write_trade_journal(
                "CHAT_NOT_READY",
                source=source_display,
                details=reason,
                ocr_text=raw_text,
            )

    async def _recover_latest_chat_capture(self, source_name: str, source_display: str, raw_text: str, reason: str):
        """
        Try to actively re-align the chat to the newest message and recapture it.

        Returns:
            tuple(frame, fresh_raw_text, fresh_sanitized_text) if recovery succeeds, else None
        """
        if not source_name:
            self._handle_stale_chat_capture(source_name, source_display, raw_text, reason)
            return None

        self._handle_stale_chat_capture(source_name, source_display, raw_text, reason)

        for attempt in range(1, 4):
            try:
                self.capture_service.scroll_window_to_bottom(source_name, force=True)
            except Exception as e:
                logger.debug(f"Failed to force-scroll '{source_name}' on attempt {attempt}: {e}")

            await asyncio.sleep(0.35)
            fresh_frame = self.capture_service.capture_single_window(source_name)
            if fresh_frame is None:
                continue

            fresh_raw_text = self.ocr_service.extract_newest_bubble_text(fresh_frame.image_path)
            if not fresh_raw_text:
                Path(fresh_frame.image_path).unlink(missing_ok=True)
                continue

            fresh_reason = self._detect_stale_chat_capture(fresh_raw_text)
            if fresh_reason:
                logger.debug(
                    f"聊天室校正後仍非最新 [{source_display or source_name}] "
                    f"(attempt {attempt}/3): {fresh_reason}"
                )
                Path(fresh_frame.image_path).unlink(missing_ok=True)
                continue

            fresh_text = self._sanitize_chat_text(fresh_raw_text)
            if len(fresh_text.strip()) < 10:
                Path(fresh_frame.image_path).unlink(missing_ok=True)
                continue

            logger.info(
                f"聊天室已重新對齊最新訊息 [{source_display or source_name}] "
                f"(attempt {attempt}/3)"
            )
            self._write_trade_journal(
                "CHAT_RECOVERED",
                source=source_display,
                details=f"聊天室重新對齊成功 | attempt={attempt}/3 | 原因={reason}",
                ocr_text=fresh_raw_text,
            )
            return fresh_frame, fresh_raw_text, fresh_text

        return None

    def _check_cancel_keywords(self, text: str, source_name: str = "") -> bool:
        """
        Check if recent messages contain cancel keywords.
        If found, cancel pending orders from the SAME source.
        Returns True if cancel was triggered (caller should skip signal parsing).

        Checks the last 3 blocks (not just the latest) because:
        - LINE reply/quote messages may split across timestamp blocks
        - OCR may produce extra blocks from UI elements
        """
        # Only check RECENT message blocks (not older ones)
        # This prevents old cancel messages from blocking newer signals
        blocks = self._regex_parser._split_by_timestamps(text)

        # Only check the LAST non-trivial block (newest message)
        # This prevents old cancel messages from blocking newer signals after them
        recent_blocks = []
        for block in reversed(blocks):
            block_text = block.strip()
            if len(block_text) < 1:
                continue
            if any(noise in block_text for noise in self.UI_NOISE_PATTERNS):
                continue
            recent_blocks.append(block_text)
            if len(recent_blocks) >= 1:  # Only the latest block
                break

        if not recent_blocks:
            return False

        # Check if any recent block contains cancel keywords
        found_keyword = None
        matched_block = ""
        for block_text in recent_blocks:
            for kw in self.CANCEL_KEYWORDS:
                if kw in block_text:
                    found_keyword = kw
                    matched_block = block_text
                    break
            if found_keyword:
                break

        if not found_keyword:
            return False

        # Check if the cancel keyword is part of an exclusion phrase (e.g. "直到取消" from MT5 UI)
        for excl in self.CANCEL_EXCLUSION_PHRASES:
            if found_keyword in excl and excl in matched_block:
                logger.debug(f"Cancel keyword '{found_keyword}' ignored — part of exclusion phrase '{excl}'")
                return False

        # Extra validation for single-char keywords (high false positive risk):
        # The keyword must appear in the LAST 20 chars of the block (newest message).
        # This handles cases where "撤" is at the end of a longer OCR block.
        if len(found_keyword) == 1:
            tail = matched_block[-20:] if len(matched_block) > 20 else matched_block
            if found_keyword not in tail:
                logger.debug(f"Cancel keyword '{found_keyword}' ignored — not in tail of block")
                return False

        # Dedup: don't process the same cancel text twice
        cancel_hash = hashlib.md5(matched_block.encode('utf-8')).hexdigest()[:12]
        if cancel_hash in self._cancel_processed:
            return False
        self._cancel_processed.add(cancel_hash)

        source_display = self._window_display_names.get(source_name, source_name)
        logger.warning(f"撤單關鍵字 '{found_keyword}' [{source_display}]: {matched_block[:80]}")

        # 1) Delete pending orders from this source only
        pending = self.trade_manager._get_pending_orders()
        cancelled = 0
        for order in pending:
            ticket = order.get('ticket')
            if not ticket:
                continue
            # Check if this order came from the same source
            order_source = self.trade_manager._signal_sources.get(str(ticket), "")
            # Fallback: look up source from ManagedOrders by ticket
            if not order_source:
                for mo in self.trade_manager.orders.values():
                    if mo.ticket == ticket and mo.source_window:
                        order_source = mo.source_window
                        break
            # Source isolation: only delete if same source, or if source unknown and
            # there's only one monitored group (backward compat for single-group setups)
            if source_display and order_source:
                if order_source != source_display:
                    logger.debug(f"撤單跳過: ticket={ticket} 來源={order_source} != {source_display}")
                    continue  # Different source, skip
            elif source_display and not order_source:
                # Source unknown — don't delete to prevent cross-group interference
                logger.warning(f"撤單跳過: ticket={ticket} 來源未知，不刪除以避免跨群誤刪")
                continue
            self.trade_manager._delete_pending_order(ticket)
            logger.info(f"撤單: 刪除掛單 ticket={ticket} price={order.get('price')} (來源: {order_source or '未知'})")
            cancelled += 1

        # Note: 已成交的持倉不平倉，撤單只影響尚未成交的掛單

        logger.warning(f"撤單結果 [{source_display}]: 刪除 {cancelled} 筆掛單")
        self._write_trade_journal(
            "CANCEL_TRIGGERED", source=source_display,
            details=f"關鍵字='{found_keyword}' | 刪除 {cancelled} 筆掛單 | 原文: {matched_block[:100]}",
        )
        return True

    def _check_sl_hit(self, text: str, source_name: str = "") -> bool:
        """
        Check if the latest message indicates the signal provider's SL was hit.
        Short messages like "SL" or "sl" mean the signal is expired.
        Cancel pending orders from this source.
        """
        blocks = self._regex_parser._split_by_timestamps(text)
        latest_block = ""
        for block in reversed(blocks):
            bt = block.strip()
            if len(bt) >= 1 and not any(n in bt for n in self.UI_NOISE_PATTERNS):
                latest_block = bt
                break

        if not latest_block:
            return False

        # Check if the block ENDS with an SL-hit keyword
        # Take the last 10 chars — SL hit messages are at the very end
        import re
        tail = latest_block[-10:].strip() if len(latest_block) > 10 else latest_block.strip()

        # If tail contains a price after SL (like "Sl 4540"), it's a signal definition, not SL-hit
        if re.search(r'(?:sl)\s*[：:=]?\s*\d{3,5}', tail, re.IGNORECASE):
            return False

        # If tail contains SL adjustment phrases (not SL triggered), skip
        for excl in self.SL_NOT_HIT_PHRASES:
            if excl.lower() in tail.lower():
                logger.debug(f"SL keyword ignored — matches exclusion '{excl}' in: {tail}")
                return False

        for kw in self.SL_HIT_KEYWORDS:
            # Case-insensitive match on the tail
            if kw.lower() in tail.lower():

                source_display = self._window_display_names.get(source_name, source_name)

                # Dedup
                sl_hash = hashlib.md5(f"sl_hit_{latest_block}".encode()).hexdigest()[:12]
                if sl_hash in self._cancel_processed:
                    return False
                self._cancel_processed.add(sl_hash)

                logger.warning(f"信號觸損通知 '{kw}' [{source_display}]: {latest_block}")

                # Cancel pending orders from this source only
                pending = self.trade_manager._get_pending_orders()
                cancelled = 0
                for order in pending:
                    ticket = order.get('ticket')
                    if not ticket:
                        continue
                    order_source = self.trade_manager._signal_sources.get(str(ticket), "")
                    if not order_source:
                        for mo in self.trade_manager.orders.values():
                            if mo.ticket == ticket and mo.source_window:
                                order_source = mo.source_window
                                break
                    if source_display and order_source:
                        if order_source != source_display:
                            continue  # Different source, skip
                    elif source_display and not order_source:
                        continue  # Source unknown, don't delete
                    self.trade_manager._delete_pending_order(ticket)
                    cancelled += 1

                self._write_trade_journal(
                    "SL_HIT_DETECTED", source=source_display,
                    details=f"關鍵字='{kw}' | 刪除 {cancelled} 筆掛單 | 原文: {latest_block}",
                )
                return True

        return False

    def _is_mt5_trade_history(self, text: str) -> bool:
        """
        Detect if OCR text is from an MT5 trade completion/history screenshot.

        These screenshots show completed trades (TP HIT, SL, etc.) and should NOT
        be parsed as new trading signals. They contain distinctive patterns like
        price arrows (4449.83→4438.20), MT5 timestamps, [tp]/[sl] result indicators.

        Returns True if 2+ indicators are found.
        """
        import re
        indicators = 0

        # Price movement arrow: 4449.83→4438.20
        if re.search(r'\d{4,5}(?:\.\d+)?\s*[→⟶]\s*\d{4,5}(?:\.\d+)?', text):
            indicators += 1

        # MT5 timestamp: 2026.03.26 11:03:03 or 2026.03.2611:03:03
        if re.search(r'\d{4}\.\d{2}\.\d{2}\s*\d{2}:\d{2}:\d{2}', text):
            indicators += 1

        # Result indicator: [tp] or [sl] or ,tp] or ,sl]
        if re.search(r'[,\[]\s*(?:tp|sl)\s*\]', text, re.IGNORECASE):
            indicators += 1

        # Ticket ID: ID followed by 6+ digit number
        if re.search(r'\bID\s*\d{6,}', text):
            indicators += 1

        # "placed" keyword (MT5 order placement confirmation)
        if re.search(r'\bplaced\b', text, re.IGNORECASE):
            indicators += 1

        # "手續費" or "手绿费" (commission field in trade history)
        if '手續費' in text or '手绿费' in text or '手续费' in text:
            indicators += 1

        if indicators >= 2:
            logger.debug(f"MT5 trade history detected ({indicators} indicators), skipping signal parse")
            return True
        return False

    def _signal_key(self, signal) -> tuple:
        """Exact key: direction + entry + SL. All from the SAME parser result."""
        entry = round(signal.entry_price, 1) if signal.entry_price else None
        sl = round(signal.stop_loss, 1) if signal.stop_loss else None
        tp1 = None
        if signal.take_profit and len(signal.take_profit) > 0:
            tp1 = round(signal.take_profit[0], 1)
        return (signal.direction, entry, sl, tp1)

    def _load_persisted_signals(self):
        """Load previously processed signals from disk to survive restarts."""
        try:
            if self._signals_file.exists():
                with open(self._signals_file, 'r') as f:
                    data = json.load(f)
                signals = data.get('signals', [])
                expiry = data.get('expiry', 0)
                if time.time() < expiry:
                    for s in signals:
                        self._processed_signals.add(s)
                    logger.info(f"Loaded {len(signals)} persisted signals for dedup")
                else:
                    logger.info("Persisted signals expired, starting fresh")
                    self._signals_file.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to load persisted signals: {e}")

    def _save_persisted_signals(self):
        """Save processed signals to disk."""
        try:
            data = {
                'signals': list(self._processed_signals),
                'expiry': time.time() + 86400,  # 24 hours (not signal_dedup_minutes which is only 10min)
                'updated': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            with open(self._signals_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save persisted signals: {e}")

    def _load_existing_mt5_orders(self):
        """
        Read MT5 pending orders and open positions on startup.
        Pre-populate dedup to avoid re-submitting the same signals after restart.
        """
        orders_file = Path(self.config.mt5_files_dir) / "orders.json"
        positions_file = Path(self.config.mt5_files_dir) / "positions.json"
        count = 0

        # Check pending orders — restore them for tracking (auto-cancel, source mapping)
        pending_restored = 0
        try:
            if orders_file.exists():
                with open(orders_file, 'r') as f:
                    data = json.load(f)
                for order in data.get('orders', []):
                    if order.get('magic') == 999999:  # Our orders only
                        direction = 'sell' if order.get('type', 0) in [3, 5] else 'buy'
                        entry = round(order.get('price', 0), 1)
                        sl = round(order.get('sl', 0), 1) or None
                        tp = round(order.get('tp', 0), 1) or None
                        key = str((direction, entry, sl, tp))
                        self._processed_signals.add(key)
                        count += 1

                        # Create ManagedOrder so pending order is tracked for auto-cancel
                        ticket = order.get('ticket')
                        if ticket:
                            from signal_parser import ParsedSignal
                            signal_id = f"restored_{ticket}"
                            # Look up source from persisted signal_sources
                            source = self.trade_manager._signal_sources.get(
                                str(ticket), self.trade_manager._signal_sources.get(signal_id, "")
                            )
                            dummy_signal = ParsedSignal(
                                is_valid=True,
                                symbol=order.get('symbol', 'XAUUSD'),
                                direction=direction,
                                entry_price=order.get('price'),
                                stop_loss=sl,
                                take_profit=[tp] if tp else [],
                                lot_size=order.get('volume_current'),
                                confidence=1.0,
                                raw_text="restored_from_mt5",
                                raw_text_summary="restored_from_mt5",
                            )
                            managed = ManagedOrder(
                                signal_id=signal_id,
                                signal=dummy_signal,
                                status=OrderStatus.SENT,
                                ticket=ticket,
                                remaining_volume=order.get('volume_current', 0),
                                source_window=source,
                                cancel_after_seconds=self.config.cancel_pending_after_seconds,
                            )
                            self.trade_manager.orders[signal_id] = managed
                            # Ensure source mapping exists for cancel-by-source
                            if source:
                                self.trade_manager._signal_sources[str(ticket)] = source
                            pending_restored += 1
                            logger.info(
                                f"Restored pending order: {signal_id} "
                                f"ticket={ticket} {direction} @ {entry} source={source or '未知'}"
                            )
        except Exception as e:
            logger.warning(f"Failed to read MT5 orders for dedup: {e}")
        if pending_restored > 0:
            logger.info(f"Tracking {pending_restored} restored pending orders")

        # Check open positions — also register them in trade_manager for close detection
        tracked_count = 0
        try:
            if positions_file.exists():
                with open(positions_file, 'r') as f:
                    data = json.load(f)
                for pos in data.get('positions', []):
                    if pos.get('magic') == 999999:
                        ptype = pos.get('type', '')
                        direction = 'buy' if ptype in (0, 'buy') else 'sell'
                        entry = round(pos.get('price_open', 0), 1)
                        sl = round(pos.get('sl', 0), 1) or None
                        tp = round(pos.get('tp', 0), 1) or None
                        key = str((direction, entry, sl, tp))
                        self._processed_signals.add(key)
                        count += 1

                        # Create a ManagedOrder so _check_closed_positions can track it
                        ticket = pos.get('ticket')
                        if ticket:
                            from signal_parser import ParsedSignal
                            signal_id = f"restored_{ticket}"
                            # Look up source from persisted signal_sources
                            source = self.trade_manager._signal_sources.get(
                                str(ticket), self.trade_manager._signal_sources.get(signal_id, "")
                            )
                            dummy_signal = ParsedSignal(
                                is_valid=True,
                                symbol=pos.get('symbol', 'XAUUSD'),
                                direction=direction,
                                entry_price=pos.get('price_open'),
                                stop_loss=sl,
                                take_profit=[tp] if tp else [],
                                lot_size=pos.get('volume'),
                                confidence=1.0,
                                raw_text="restored_from_mt5",
                                raw_text_summary="restored_from_mt5",
                            )
                            managed = ManagedOrder(
                                signal_id=signal_id,
                                signal=dummy_signal,
                                status=OrderStatus.FILLED,
                                ticket=ticket,
                                entry_price=pos.get('price_open'),
                                entry_time=time.time(),
                                remaining_volume=pos.get('volume', 0),
                                last_known_profit=pos.get('profit', 0),
                                source_window=source,
                            )
                            self.trade_manager.orders[signal_id] = managed
                            tracked_count += 1
                            logger.info(
                                f"Restored position for tracking: {signal_id} "
                                f"ticket={ticket} {direction} profit={pos.get('profit', 0)} source={source or '未知'}"
                            )
        except Exception as e:
            logger.warning(f"Failed to read MT5 positions for dedup: {e}")

        if count > 0:
            logger.info(f"Pre-loaded {count} existing MT5 orders/positions for dedup")
        if tracked_count > 0:
            logger.info(f"Tracking {tracked_count} existing positions for martingale close detection")

    def _validate_signal(self, signal) -> bool:
        """Validate signal before execution."""
        # Check confidence threshold
        if signal.confidence < self.config.min_confidence:
            self._log_signal_skip(
                "信心度不足",
                signal=signal,
                details=f"信心度 {signal.confidence:.0%} < 門檻 {self.config.min_confidence:.0%}"
            )
            return False

        # Check max open positions
        active_orders = [
            o for o in self.trade_manager.get_all_orders()
            if o.status.value in ['pending', 'sent', 'filled', 'partial']
        ]
        if len(active_orders) >= self.config.max_open_positions:
            self._log_signal_skip(
                "已達最大持倉數",
                signal=signal,
                details=f"目前 {len(active_orders)} 筆，上限 {self.config.max_open_positions} 筆"
            )
            return False

        # Check price deviation (if entry price specified)
        if signal.entry_price:
            current_price = self.trade_manager._get_current_price()
            if current_price:
                deviation = abs(signal.entry_price - current_price) / current_price
                if deviation > self.config.max_price_deviation:
                    self._log_signal_skip(
                        "入場價偏離過大",
                        signal=signal,
                        details=f"偏離 {deviation:.1%} > 上限 {self.config.max_price_deviation:.1%} "
                                f"(入場={signal.entry_price}, 現價={current_price})"
                    )
                    return False

        # Check SL makes sense for the direction
        if signal.entry_price and signal.stop_loss:
            if signal.direction == "buy" and signal.stop_loss >= signal.entry_price:
                self._log_signal_skip(
                    "止損方向錯誤（BUY 的 SL 應低於入場價）",
                    signal=signal,
                    details=f"入場={signal.entry_price}, SL={signal.stop_loss}"
                )
                return False
            if signal.direction == "sell" and signal.stop_loss <= signal.entry_price:
                self._log_signal_skip(
                    "止損方向錯誤（SELL 的 SL 應高於入場價）",
                    signal=signal,
                    details=f"入場={signal.entry_price}, SL={signal.stop_loss}"
                )
                return False

        # Check SL is not too close to entry (min 3 points)
        if signal.entry_price and signal.stop_loss:
            sl_distance = abs(signal.entry_price - signal.stop_loss)
            if sl_distance < 3:
                self._log_signal_skip(
                    "止損距離太近（<3 點，可能解析錯誤）",
                    signal=signal,
                    details=f"入場={signal.entry_price}, SL={signal.stop_loss}, 距離={sl_distance:.1f}"
                )
                return False

        # Check SL ≠ TP (nonsensical)
        if signal.stop_loss and signal.take_profit:
            if signal.stop_loss in signal.take_profit:
                self._log_signal_skip(
                    "止損等於止盈（解析錯誤）",
                    signal=signal,
                    details=f"SL={signal.stop_loss} 出現在 TP={signal.take_profit} 中"
                )
                return False

        return True

    def _update_daily_loss(self):
        """Update daily loss from MT5 closed trades file."""
        try:
            from datetime import datetime, timedelta
            closed_file = Path(self.config.mt5_files_dir) / "closed_trades.json"
            if not closed_file.exists():
                return
            with open(closed_file, 'r') as f:
                data = json.load(f)

            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            total_loss = 0.0
            for trade in data.get("trades", []):
                try:
                    close_ts = int(trade.get("close_timestamp", 0))
                    if close_ts <= 0:
                        continue
                    close_time = datetime.fromtimestamp(close_ts)
                    if close_time >= today_start:
                        profit = float(trade.get("profit", 0))
                        if profit < 0:
                            total_loss += abs(profit)
                except (TypeError, ValueError):
                    continue
            self._daily_loss = total_loss
        except Exception:
            pass  # Don't crash the main loop over stats

    # ── Trade Journal: persistent record of every trading decision ──

    def _write_trade_journal(self, action: str, signal=None, source: str = "",
                              details: str = "", ocr_text: str = ""):
        """
        Write a trade decision record to a persistent text file.
        This survives app restarts and provides a complete audit trail.

        Actions: SIGNAL_DETECTED, ORDER_SUBMITTED, ORDER_SKIPPED, CANCEL_TRIGGERED
        """
        try:
            from datetime import datetime
            journal_file = DATA_DIR / "trade_journal.txt"

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"\n{'='*60}"]
            lines.append(f"[{timestamp}] {action}")
            if source:
                lines.append(f"  來源: {source}")
            if signal:
                lines.append(f"  信號: {signal}")
                if hasattr(signal, 'entry_price'):
                    lines.append(f"  入場: {signal.entry_price}")
                    lines.append(f"  止損: {signal.stop_loss}")
                    lines.append(f"  止盈: {signal.take_profit}")
                    lines.append(f"  方向: {signal.direction}")
                    lines.append(f"  方法: {getattr(signal, 'parse_method', 'regex')}")
            if details:
                lines.append(f"  詳情: {details}")
            if ocr_text:
                # Save first 300 chars of OCR text for debugging
                lines.append(f"  OCR原文: {ocr_text[:300]}")

            with open(journal_file, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')

        except Exception as e:
            logger.debug(f"Failed to write trade journal: {e}")

    def _log_signal_skip(self, reason: str, signal=None, source: str = "", details: str = ""):
        """Log a structured signal skip event for debugging.

        Emits both a WARNING log and a 'signal_skipped' event to the frontend.
        """
        parts = [f"⚠ 信號跳過"]
        if source:
            parts.append(f"[{source}]")
        parts.append(f"原因: {reason}")
        if signal:
            parts.append(f"| {signal}")
        if details:
            parts.append(f"| {details}")
        msg = " ".join(parts)
        logger.warning(msg)
        self._emit_event("signal_skipped", {
            "reason": reason,
            "signal": str(signal) if signal else "",
            "source": source,
            "details": details,
        })
        # Also write to trade journal
        self._write_trade_journal("ORDER_SKIPPED", signal=signal, source=source, details=f"{reason} | {details}")

    def _normalize_ocr_text(self, text: str) -> str:
        """Normalize OCR text for dedup — strip noise, whitespace, common phrases."""
        import re
        normalized = text.strip().lower()
        # Remove all whitespace variations
        normalized = re.sub(r'\s+', '', normalized)
        # Remove common noise phrases
        for word in ['純粹', '個人', '投資', '分享', '僅供參考', '不構成', '計畫', '建議']:
            normalized = normalized.replace(word, '')
        # Remove emoji and special chars
        normalized = re.sub(r'[✨🫧◐‿◑﻿]+', '', normalized)
        return normalized

    def _compute_text_hash(self, text: str) -> str:
        """Compute hash of normalized text for deduplication."""
        normalized = self._normalize_ocr_text(text)
        return hashlib.md5(normalized.encode()).hexdigest()[:16]

    def _is_text_similar(self, text1: str, text2: str, threshold: float = 0.85) -> bool:
        """Fuzzy text comparison using sequence matcher (no extra deps)."""
        from difflib import SequenceMatcher
        n1 = self._normalize_ocr_text(text1)
        n2 = self._normalize_ocr_text(text2)
        if not n1 or not n2:
            return False
        return SequenceMatcher(None, n1, n2).ratio() >= threshold

    def _add_hash_with_ttl(self, hash_value: str, ttl_seconds: float):
        """Add a hash with an expiry timestamp (no asyncio task needed)."""
        self._processed_hashes[hash_value] = time.time() + ttl_seconds

    def _is_hash_active(self, hash_value: str) -> bool:
        """Check if a hash is still within its TTL."""
        expiry = self._processed_hashes.get(hash_value)
        if expiry is None:
            return False
        if time.time() >= expiry:
            del self._processed_hashes[hash_value]
            return False
        return True

    def _emit_event(self, event_type: str, data: dict = None):
        """Emit event to GUI callback if available."""
        if self.event_callback:
            try:
                self.event_callback(event_type, data or {})
            except Exception:
                pass

    def _periodic_cleanup(self):
        """Periodically clean up expired hashes, cancel sets, and temp files."""
        now = time.time()
        if now - self._last_cleanup_time < 60:  # Run at most once per minute
            return

        # Heartbeat log every 30 minutes for monitoring gaps
        if not hasattr(self, '_last_heartbeat'):
            self._last_heartbeat = now
        if now - self._last_heartbeat >= 1800:  # 30 min
            self._last_heartbeat = now
            self._write_trade_journal(
                "HEARTBEAT",
                details=f"系統運行中 | API呼叫={self._api_calls_today} | 日交易={self._daily_trades} | 日虧損=${self._daily_loss:.2f}",
            )
        self._last_cleanup_time = now

        # Clean expired hashes
        expired = [k for k, exp in self._processed_hashes.items() if now >= exp]
        for k in expired:
            del self._processed_hashes[k]
        if expired:
            logger.debug(f"Cleaned {len(expired)} expired hashes")

        # Clean cancel processed set (keep max 200 entries, remove oldest half)
        if len(self._cancel_processed) > 200:
            # Keep the newer half (set is unordered, but this prevents unbounded growth)
            keep = list(self._cancel_processed)[-100:]
            self._cancel_processed = set(keep)
            logger.debug("Trimmed cancel_processed set to 100 entries")

        # Clean incomplete retry counts (remove stale entries)
        stale_retries = [k for k, v in self._incomplete_retry_counts.items() if v > 20]
        for k in stale_retries:
            del self._incomplete_retry_counts[k]

        # Clean expired vision dedup cache
        expired_vision = [k for k, exp in self._vision_sent_cache.items() if now >= exp]
        for k in expired_vision:
            del self._vision_sent_cache[k]

        # Clean processed signals set (keep max 200)
        if len(self._processed_signals) > 200:
            # Remove oldest half to prevent unbounded growth
            keep = list(self._processed_signals)[-100:]
            self._processed_signals = set(keep)
            logger.debug("Trimmed _processed_signals to 100 entries")

        # Clean up temp screenshot files (only meaningful in OCR path)
        if not self._use_clipboard_path():
            self.capture_service.cleanup_old_files(max_age_seconds=300)


async def main():
    """Main entry point."""
    print("=" * 60)
    print("       Copy Trader - Automatic Signal Execution")
    print("=" * 60)

    # Load configuration
    config = load_config()

    # Check if capture is configured
    if config.capture_mode == "window":
        if not config.capture_windows:
            print("\nERROR: No capture windows configured")
            print("Configure capture_windows in config.py")
            sys.exit(1)
        # Verify windows exist
        from signal_capture.screen_capture import list_app_windows, get_window_id_by_name
        for cw in config.capture_windows:
            hwnd = get_window_id_by_name(cw.window_name, cw.app_name)
            matching = hwnd is not None
            if not matching:
                try:
                    print(f"\nWARNING: Window '{cw.window_name}' not found")
                except UnicodeEncodeError:
                    print(f"\nWARNING: Window not found (name contains special characters)")
    else:
        if not config.capture_regions or config.capture_regions[0].width == 0:
            print("\n" + "=" * 60)
            print("First-time setup: Configure screen capture region")
            print("=" * 60)
            print("\nYou need to specify which area of your screen to monitor.")
            print("Open your chat application (WeChat/LINE) and position the window.")
            print("\nThen run the region selector:")
            print("  python -m signal_capture.screen_capture")
            print("\nOr manually configure in config.py")
            sys.exit(1)

    # Create and start
    trader = CopyTrader(config)

    try:
        await trader.start()
    except KeyboardInterrupt:
        trader.stop()


if __name__ == "__main__":
    asyncio.run(main())
