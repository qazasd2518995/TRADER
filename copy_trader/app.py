"""
Copy Trader - Main Application
Monitors screen for trading signals and executes them automatically.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
import hashlib
from typing import Dict, Set
from pathlib import Path

# Ensure imports work regardless of how the script is launched
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config, CaptureRegion, CaptureWindow, load_config, DATA_DIR
from signal_capture import ScreenCaptureService, CaptureWindow as SCWindow, OCRService
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
                    window_name=w.window_name,
                    app_name=w.app_name,
                    name=w.name
                )
                for w in config.capture_windows
            ]
            self.capture_service = ScreenCaptureService(windows=windows)
            logger.info(f"Using window capture mode: {[w.window_name for w in config.capture_windows]}")
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
        self.trade_manager.set_symbol_name(getattr(config, "symbol_name", "XAUUSD.s"))
        self.trade_manager.partial_close_ratios = config.partial_close_ratios

        # Martingale settings
        self.trade_manager.use_martingale = config.use_martingale
        self.trade_manager.martingale_multiplier = config.martingale_multiplier
        self.trade_manager.martingale_max_level = config.martingale_max_level
        self.trade_manager.martingale_lots = getattr(config, 'martingale_lots', [])

        # Build mapping from internal window name -> display name
        self._window_display_names: Dict[str, str] = {}
        if config.capture_mode == "window" and config.capture_windows:
            for w in config.capture_windows:
                self._window_display_names[w.name] = w.window_name

        # State
        self._running = False
        self._processed_hashes: Dict[str, float] = {}   # hash -> expiry timestamp
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

        # Stats
        self._api_calls_today = 0
        self._signals_filtered = 0

        # Load persisted signals and existing MT5 orders for dedup
        self._load_persisted_signals()
        self._load_existing_mt5_orders()

        logger.info("CopyTrader initialized")
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

        # Main loop
        self._running = True
        logger.info("Copy Trader running. Press Ctrl+C to stop.")

        while self._running:
            try:
                await self._process_cycle()
                self._periodic_cleanup()
                await asyncio.sleep(self.config.capture_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)

        logger.info("Copy Trader stopped")

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
        symbol_name = getattr(self.config, "symbol_name", "XAUUSD.s")
        candidate_files = [
            Path(self.config.mt5_files_dir) / f"{symbol_name}_price.json",
            Path(self.config.mt5_files_dir) / "XAUUSD_price.json",
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

    async def _process_cycle(self):
        """Single processing cycle."""
        # Check daily loss limit
        if self._daily_loss >= self.config.max_daily_loss:
            logger.warning(f"Daily loss limit reached (${self._daily_loss:.2f})")
            return

        # Capture screen regions
        frames = self.capture_service.capture_all_regions(deduplicate=True)

        if not frames:
            return

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

            # --- LOCAL OCR for quick filtering (cancel keywords, dedup) ---
            t_ocr = time.time()
            text = self.ocr_service.extract_text(frame.image_path)
            ocr_ms = (time.time() - t_ocr) * 1000
            logger.debug(f"OCR took {ocr_ms:.0f}ms (full image)")

            if not text or len(text.strip()) < 10:
                continue

            # Check for cancel/withdraw keywords in latest message
            if self._check_cancel_keywords(text, frame.source_name):
                continue

            # Check for duplicate (same text within dedup window)
            text_hash = self._compute_text_hash(text)
            if self._is_hash_active(text_hash):
                logger.debug("Duplicate signal detected, skipping")
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

                # Tier 1: Gemini
                if self.gemini_parser and self.gemini_parser.is_available:
                    logger.info("Sending screenshot to Gemini...")
                    signal = self.gemini_parser.parse_image(frame.image_path)
                    if signal.is_valid:
                        logger.info(f"Gemini result: {signal}")
                    elif signal.error:
                        logger.warning(f"Gemini failed: {signal.error}, trying Groq...")
                        signal = None
                    else:
                        signal = None

                # Tier 2: Groq
                if signal is None and self.groq_parser and self.groq_parser.is_available:
                    logger.info("Sending screenshot to Groq Vision...")
                    signal = self.groq_parser.parse_image(frame.image_path)
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
                logger.info(
                    f"✅ 跨訊息信號收齊！等待了 {time.time() - pending_time:.1f}秒 | {signal}"
                )
                self._pending_signals.pop(source, None)

            # === CONFIRMATION: second Vision call on fresh capture ===
            confirmed = await self._confirm_signal_vision(signal, frame)
            if not confirmed:
                self._log_signal_skip(
                    "二次確認失敗（Vision 重新解析結果不一致）",
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
            signal_id = self.trade_manager.submit_signal(
                signal,
                auto_execute=self.config.auto_execute,
                cancel_after_seconds=self.config.cancel_pending_after_seconds,
                source_window=source_window
            )

            elapsed = time.time() - t_signal_start
            logger.info(f"Signal submitted: {signal_id} (pipeline took {elapsed:.1f}s)")
            self._daily_trades += 1

            self._emit_event("trade_submitted", {"signal_id": signal_id})

            # Clean up screenshot
            Path(frame.image_path).unlink(missing_ok=True)

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

            # Parse with same 3-tier chain
            retry_signal = None
            self._api_calls_today += 1
            if self.gemini_parser and self.gemini_parser.is_available:
                retry_signal = self.gemini_parser.parse_image(frame.image_path)
                if retry_signal and not retry_signal.is_valid and retry_signal.error:
                    retry_signal = None
            if retry_signal is None and self.groq_parser and self.groq_parser.is_available:
                retry_signal = self.groq_parser.parse_image(frame.image_path)
                if retry_signal and not retry_signal.is_valid and retry_signal.error:
                    retry_signal = None
            if retry_signal is None:
                text = self.ocr_service.extract_text(frame.image_path)
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
            else:
                logger.warning(f"  -> MISMATCH (expected {first_key})")

        required = confirm_count - 1
        if matches >= required:
            return True
        else:
            logger.warning(f"Only {matches}/{required} confirmations matched")
            return False

    async def _confirm_signal_with_retries(self, first_signal, first_frame) -> bool:
        """
        Re-capture and re-OCR the same window multiple times to confirm the signal.
        Returns True only if the key fields (direction, entry, SL, TP) are consistent.
        Uses cropped images (bottom 50%) for faster OCR during confirmation.
        """
        confirm_count = self.config.ocr_confirm_count
        confirm_delay = self.config.ocr_confirm_delay

        if confirm_count <= 1:
            return True  # No confirmation needed

        # Extract key fields from first signal for comparison
        first_key = self._signal_key(first_signal)
        logger.info(f"Confirming signal: {first_key} ({confirm_count - 1} more OCR reads needed)")

        matches = 0
        for i in range(confirm_count - 1):
            await asyncio.sleep(confirm_delay)

            # Re-capture the SAME window that produced the original signal
            source = first_frame.source_name
            if source:
                frame = self.capture_service.capture_single_window(source)
            else:
                frames = self.capture_service.capture_all_regions(deduplicate=False)
                frame = frames[0] if frames else None
            if not frame:
                logger.warning(f"Confirmation {i+1}/{confirm_count-1}: capture failed")
                continue
            # Use cropped image (bottom 50%) for faster confirmation OCR
            t_ocr = time.time()
            text = self.ocr_service.extract_text(frame.image_path, crop_bottom_ratio=0.5)
            ocr_ms = (time.time() - t_ocr) * 1000
            logger.info(f"Confirmation OCR took {ocr_ms:.0f}ms (cropped 50%)")

            # Parse (use parse_latest to handle multi-message screenshots)
            retry_signal = None
            if text and len(text.strip()) >= 10:
                is_signal, _ = is_potential_signal(text)
                if is_signal:
                    self._api_calls_today += 1
                    retry_signal = self.signal_parser.parse_latest(text)

            # Fallback: if cropped parse failed, try full image
            if (retry_signal is None or not retry_signal.is_valid) and frame.image_path:
                t_ocr2 = time.time()
                text_full = self.ocr_service.extract_text(frame.image_path)
                ocr_ms2 = (time.time() - t_ocr2) * 1000
                logger.info(f"Confirmation OCR fallback to full image: {ocr_ms2:.0f}ms")
                if text_full and len(text_full.strip()) >= 10:
                    is_signal2, _ = is_potential_signal(text_full)
                    if is_signal2:
                        self._api_calls_today += 1
                        retry_signal = self.signal_parser.parse_latest(text_full)

            Path(frame.image_path).unlink(missing_ok=True)

            if retry_signal is None or not retry_signal.is_valid:
                logger.warning(f"Confirmation {i+1}/{confirm_count-1}: could not parse signal")
                continue

            if not retry_signal.entry_price:
                logger.warning(f"Confirmation {i+1}/{confirm_count-1}: missing entry price")
                continue
            if not retry_signal.stop_loss:
                logger.warning(f"Confirmation {i+1}/{confirm_count-1}: missing SL")
                continue
            if not retry_signal.take_profit or len(retry_signal.take_profit) == 0 or retry_signal.take_profit[0] is None:
                logger.warning(f"Confirmation {i+1}/{confirm_count-1}: missing TP")
                continue

            retry_key = self._signal_key(retry_signal)
            logger.info(f"Confirmation {i+1}/{confirm_count-1}: {retry_key}")

            if retry_key == first_key:
                matches += 1
                logger.info(f"  -> MATCH ({matches}/{confirm_count-1} needed)")
            else:
                logger.warning(f"  -> MISMATCH (expected {first_key})")

        # Require ALL confirmation reads to match
        required = confirm_count - 1
        if matches >= required:
            return True
        else:
            logger.warning(f"Only {matches}/{required} confirmations matched")
            return False

    # Cancel keywords that trigger deletion of all pending orders
    # Multi-char keywords first (more specific), single-char last (prone to false positives)
    CANCEL_KEYWORDS = ['撤單', '撤单', '刪單', '删单', '測單', '测单', '測掉', '撤掉', '撒掉', '取消', '撤', '撒']

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

        # Collect last 3 non-trivial blocks, skipping UI noise
        recent_blocks = []
        for block in reversed(blocks):
            block_text = block.strip()
            if len(block_text) < 1:
                continue
            # Skip blocks that look like UI elements (title bar, ads, tabs)
            if any(noise in block_text for noise in self.UI_NOISE_PATTERNS):
                continue
            recent_blocks.append(block_text)
            if len(recent_blocks) >= 3:
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

        # Extra validation for single-char keywords (high false positive risk):
        # Block must be SHORT (< 30 chars) — real cancel messages are brief like "撤" or "撤 🙈"
        # Long blocks containing "撤" are likely OCR noise from UI elements
        if len(found_keyword) == 1 and len(matched_block) > 30:
            logger.debug(f"Cancel keyword '{found_keyword}' ignored — block too long ({len(matched_block)} chars), likely UI noise")
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
            if source_display and order_source and order_source != source_display:
                continue  # Different source, skip
            self.trade_manager._delete_pending_order(ticket)
            logger.info(f"撤單: 刪除掛單 ticket={ticket} price={order.get('price')} (來源: {order_source or '未知'})")
            cancelled += 1

        # Note: 已成交的持倉不平倉，撤單只影響尚未成交的掛單

        logger.warning(f"撤單結果 [{source_display}]: 刪除 {cancelled} 筆掛單")
        return True

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

        # Check pending orders
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
        except Exception as e:
            logger.warning(f"Failed to read MT5 orders for dedup: {e}")

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
                            )
                            self.trade_manager.orders[signal_id] = managed
                            tracked_count += 1
                            logger.info(
                                f"Restored position for tracking: {signal_id} "
                                f"ticket={ticket} {direction} profit={pos.get('profit', 0)}"
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

        return True

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

    def _compute_text_hash(self, text: str) -> str:
        """Compute hash of text for deduplication."""
        # Normalize text before hashing
        normalized = text.strip().lower()
        # Remove common noise
        for word in ['純粹', '個人', '投資', '分享', '僅供參考']:
            normalized = normalized.replace(word, '')
        return hashlib.md5(normalized.encode()).hexdigest()[:16]

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

        # Clean up temp screenshot files
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
