"""
Central LINE signal collector.

Run this on the always-on signal computer. It uses the existing LINE clipboard
reader and regex parser, then publishes normalized signals to the hub.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from copy_trader.config import Config, load_config
from copy_trader.signal_capture.clipboard_reader import ClipboardReaderService, ClipboardWindow
from copy_trader.signal_capture.line_text_parser import LineMessage
from copy_trader.signal_parser.keyword_filter import is_potential_signal
from copy_trader.signal_parser.regex_parser import ParsedSignal, RegexSignalParser

logger = logging.getLogger(__name__)


def _window_label(window) -> str:
    return getattr(window, "display_name", "") or getattr(window, "window_name", "") or getattr(window, "name", "")


def _signal_key(signal: ParsedSignal, source: str) -> Tuple:
    tps = tuple(round(float(tp), 2) for tp in (signal.take_profit or []) if tp is not None)
    return (
        source,
        signal.direction,
        round(float(signal.entry_price or 0), 2),
        bool(signal.is_market_order),
        round(float(signal.stop_loss or 0), 2),
        tps,
    )


def _merge_signal(base: ParsedSignal, new: ParsedSignal) -> ParsedSignal:
    if not base.direction and new.direction:
        base.direction = new.direction
    if base.entry_price is None and new.entry_price is not None:
        base.entry_price = new.entry_price
    if not base.is_market_order and new.is_market_order:
        base.is_market_order = True
        base.entry_price = None
    if base.stop_loss is None and new.stop_loss is not None:
        base.stop_loss = new.stop_loss
    if not base.take_profit and new.take_profit:
        base.take_profit = list(new.take_profit)
    if base.lot_size is None and new.lot_size is not None:
        base.lot_size = new.lot_size
    base.is_valid = bool(base.direction)
    base.confidence = max(base.confidence or 0, new.confidence or 0)
    return base


def _is_complete(signal: ParsedSignal) -> bool:
    has_entry = signal.entry_price is not None or bool(signal.is_market_order)
    return bool(signal.is_valid and signal.direction and has_entry and signal.stop_loss and signal.take_profit)


def _sl_tp_consistent(signal: ParsedSignal) -> bool:
    """SL/TP 必須在方向正確的一側，否則視為解析錯誤，不發布。"""
    sl = signal.stop_loss
    tps = [float(t) for t in (signal.take_profit or []) if t is not None]
    if sl is None or not tps:
        return True
    sl = float(sl)
    entry = float(signal.entry_price) if signal.entry_price is not None else None
    if signal.direction == "buy":
        if not all(tp > sl for tp in tps):
            return False
        return entry is None or (sl < entry < min(tps))
    if signal.direction == "sell":
        if not all(tp < sl for tp in tps):
            return False
        return entry is None or (max(tps) < entry < sl)
    return True


def _signal_payload(signal: ParsedSignal) -> Dict:
    return {
        "symbol": signal.symbol or "XAUUSD",
        "direction": signal.direction,
        "entry_price": signal.entry_price,
        "is_market_order": bool(signal.is_market_order),
        "stop_loss": signal.stop_loss,
        "take_profit": list(signal.take_profit or []),
        "lot_size": signal.lot_size,
        "confidence": signal.confidence,
        "parse_method": signal.parse_method,
        "raw_text_summary": signal.raw_text_summary,
        "error": signal.error,
    }


class HubPublisher:
    def __init__(self, hub_url: str, token: str = "", timeout: float = 5.0):
        self.hub_url = hub_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def publish(self, payload: Dict) -> Dict:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.hub_url}/signals",
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class CentralSignalCollector:
    def __init__(
        self,
        config: Config,
        publisher: HubPublisher,
        copy_mode: str = "all",
        stale_seconds: Optional[float] = None,
    ):
        self.config = config
        self.publisher = publisher
        self.parser = RegexSignalParser()
        self.copy_mode = copy_mode
        self._pending: Dict[str, Dict] = {}
        self._processed: Dict[Tuple, float] = {}
        self._processed_ttl = max(60, int(config.signal_dedup_minutes or 10) * 60)
        # 訊號時效鎖: 訊息時間超過這麼久就不發布 (擋歷史洪水/延遲/過期)。0=不限。
        self.max_signal_age_sec = max(0, int(getattr(config, "signal_max_age_minutes", 10) or 0) * 60)

        windows = [
            ClipboardWindow(
                name=w.name,
                window_name=w.window_name,
                display_name=_window_label(w),
                window_id=getattr(w, "window_id", None),
                screens=int(getattr(config, "clipboard_screens", 2) or 2),
                copy_mode=copy_mode,
            )
            for w in (config.capture_windows or [])
        ]
        if not windows:
            raise RuntimeError("no capture_windows configured")

        # 採集管道：
        #   "window_ocr" = PrintWindow 背景截圖 + OCR (LINE 擋掉合成鍵鼠後的主管道)
        #   其它 (clipboard) = 舊的剪貼板全選複製 (LINE 更新後已失效, 保留為備援)
        signal_source = str(getattr(config, "signal_source", "clipboard") or "clipboard").strip().lower()
        if signal_source == "window_ocr":
            from copy_trader.signal_capture.window_ocr_reader import WindowOcrReaderService
            self.clipboard = WindowOcrReaderService(
                windows,
                confirm_count=int(getattr(config, "ocr_confirm_count", 2) or 2),
            )
            logger.info(
                "collector initialized (WINDOW_OCR): windows=%s confirm=%s",
                len(windows), getattr(config, "ocr_confirm_count", 2),
            )
        else:
            self.clipboard = ClipboardReaderService(
                windows,
                stale_seconds=float(stale_seconds if stale_seconds is not None else getattr(config, "clipboard_stale_seconds", 10.0) or 10.0),
            )
            logger.info("collector initialized (CLIPBOARD): windows=%s copy_mode=%s", len(windows), copy_mode)

    def _cleanup(self) -> None:
        now = time.time()
        for key, ts in list(self._processed.items()):
            if now - ts > self._processed_ttl:
                self._processed.pop(key, None)
        for source, item in list(self._pending.items()):
            if now - item.get("time", now) > 120:
                logger.warning("pending signal expired for %s", source)
                self._pending.pop(source, None)

    def run_cycle(self) -> int:
        self._cleanup()
        published = 0
        for cap in self.clipboard.capture_all():
            if not cap.ok:
                if cap.error:
                    logger.warning("capture failed for %s: %s", cap.display_name, cap.error)
                continue
            for msg in cap.new_messages:
                should_mark_seen = False
                abort_source = False
                try:
                    count = self._process_message(msg, cap.source_name, cap.display_name)
                    published += count
                    should_mark_seen = True
                except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
                    logger.warning("message publish failed, will retry source=%s: %s", cap.display_name, e)
                    self.clipboard.force_retry(cap.source_name)
                    # 全選複製模式的新訊息切點是「最後一則已 mark_seen 的訊息」；
                    # 若這裡 continue 讓後面的訊息先被標記，這則失敗的訊號
                    # 下一輪會被切點跳過而永久遺失。中斷本批，下一輪整批重試。
                    abort_source = True
                except Exception as e:
                    logger.exception("message processing failed: %s", e)
                    should_mark_seen = True
                finally:
                    if should_mark_seen:
                        self.clipboard.mark_seen(cap.source_name, [msg])
                if abort_source:
                    break
        return published

    def _candidate_signals(self, body: str) -> List[ParsedSignal]:
        signals = self.parser.parse_all_latest(body)
        if signals:
            return signals
        sig = self.parser.parse_latest(body)
        return [sig] if sig.is_valid or sig.direction else []

    def _process_message(self, msg: LineMessage, source_name: str, source_display: str) -> int:
        body = (msg.body or "").strip()
        if len(body) < 5:
            return 0

        has_pending = source_name in self._pending
        is_signal, reason = is_potential_signal(body)
        if not is_signal and not has_pending:
            logger.debug("filtered %s: %s", source_display, reason)
            return 0

        # 訊號時效鎖: 訊息時間太舊就跳過 — 擋掉(1)新增視窗時的歷史洪水
        # (2)延遲擷取 (3)被編輯/收回的過期單。需要 msg 有可靠 timestamp。
        if self.max_signal_age_sec > 0 and msg.timestamp is not None:
            try:
                age = (datetime.now() - msg.timestamp).total_seconds()
            except Exception:
                age = 0
            if age > self.max_signal_age_sec:
                logger.info(
                    "跳過過期訊號 (%s, 已過 %.0f 分鐘 > %.0f): %s",
                    msg.time_str, age / 60.0, self.max_signal_age_sec / 60.0, body[:50],
                )
                return 0

        logger.info("LINE message [%s %s %s]: %r", source_display, msg.time_str, msg.sender, body[:100])
        published = 0
        candidates = self._candidate_signals(body)
        if not candidates:
            return 0

        for signal in candidates:
            pending = self._pending.get(source_name)
            if pending:
                signal = _merge_signal(pending["signal"], signal)

            if not signal.is_valid:
                continue

            if not _is_complete(signal):
                self._pending[source_name] = {
                    "signal": signal,
                    "time": pending["time"] if pending else time.time(),
                    "raw_text": body,
                }
                logger.info("pending signal for %s: direction=%s entry=%s sl=%s tp=%s", source_display, signal.direction, signal.entry_price, signal.stop_loss, signal.take_profit)
                continue

            if source_name in self._pending:
                self._pending.pop(source_name, None)

            if not _sl_tp_consistent(signal):
                logger.warning(
                    "skipped reversed signal (SL/TP wrong side) source=%s dir=%s entry=%s sl=%s tp=%s",
                    source_display, signal.direction, signal.entry_price, signal.stop_loss, signal.take_profit,
                )
                continue

            key = _signal_key(signal, source_display)
            if key in self._processed:
                logger.info("duplicate signal skipped: %s", signal)
                continue

            payload = {
                "type": "trade_signal",
                "source": source_display,
                "source_name": source_name,
                "sender": msg.sender,
                "message_time": msg.timestamp.isoformat() if msg.timestamp else msg.time_str,
                "captured_at": time.time(),
                "signal": _signal_payload(signal),
                "raw_text": body,
                "raw_message_key": list(msg.key),
            }
            response = self.publisher.publish(payload)
            if not response.get("ok"):
                raise RuntimeError(f"hub rejected signal: {response}")
            self._processed[key] = time.time()
            published += 1
            logger.info("published signal to hub: %s", signal)

        return published

    def run_forever(self, interval: float = 1.0) -> None:
        logger.info("collector running")
        try:
            while True:
                self.run_cycle()
                time.sleep(max(0.2, interval))
        except KeyboardInterrupt:
            logger.info("collector stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run central LINE clipboard signal collector.")
    parser.add_argument("--hub-url", default=os.environ.get("COPY_TRADER_HUB_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--copy-mode", choices=["all", "tail"], default=os.environ.get("COPY_TRADER_COPY_MODE", "all"))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("COPY_TRADER_COLLECTOR_INTERVAL", "1.0")))
    parser.add_argument("--stale-seconds", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("COPY_TRADER_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    publisher = HubPublisher(args.hub_url, args.token)
    collector = CentralSignalCollector(config, publisher, copy_mode=args.copy_mode, stale_seconds=args.stale_seconds)
    if args.once:
        collector.run_cycle()
    else:
        collector.run_forever(interval=args.interval)


if __name__ == "__main__":
    main()
