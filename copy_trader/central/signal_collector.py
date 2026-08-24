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
from copy_trader.signal_capture.clipboard_reader import ClipboardWindow
from copy_trader.signal_capture.line_text_parser import LineMessage
from copy_trader.signal_capture.window_ocr_reader import _CANCEL_KWS
from copy_trader.signal_parser.keyword_filter import is_potential_signal
from copy_trader.signal_parser.regex_parser import ParsedSignal, RegexSignalParser

logger = logging.getLogger(__name__)


# 撤單「討論/問句」排除詞：問句或描述才會出現這些，真正的撤單指令不會。
_CANCEL_EXCLUDE = ("嗎", "?", "？", "有沒有", "會不會", "是不是", "設定", "後來", "怎麼", "為何", "被", "如果", "要不要")


def _cancel_direction_from_message(
    body: str,
    sender: str = "",
    sender_verified: bool = False,
) -> Optional[str]:
    """訊息本身若是「取消/撤」撤單指令 → 回傳方向 (''=不分/'buy'/'sell')，否則 None。

    嚴格條件 (避免把討論/問句誤判成撤單 → 誤撤真單)：
      1. 只認訊號提供者發的 — 其他人閒聊/討論撤單一律不算。
         sender_verified=True 代表該來源已設發送者白名單、上游擷取層過濾過了；
         否則沿用舊的預設 (只認「乘」) 給沒設白名單的來源。
      2. 短訊息 (≤20字)、含撤單關鍵字、非訊號、不含問句/描述詞。
    """
    b = (body or "").strip()
    if not b or len(b) > 20:
        return None
    # 只認提供者發的撤單, 擋掉「你有設定撤單嗎」「後來取消」這類他人討論
    if not sender_verified and "乘" not in (sender or ""):
        return None
    if "止損" in b or "止盈" in b or "xauusd" in b.lower():
        return None  # 這是訊號不是撤單
    if any(x in b for x in _CANCEL_EXCLUDE):
        return None  # 問句/描述, 非撤單指令
    if not any(kw in b for kw in _CANCEL_KWS):
        return None
    if "空" in b:
        return "sell"
    if "多" in b:
        return "buy"
    return ""


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
    """完整 = 方向 + 進場價 + 止損 + 止盈 全都有。

    刻意要求「真的有進場價」，不接受 is_market_order 當替代品：追蹤的提供者每一筆
    都會給進場點、全部是限價單，所以 entry=None 只可能是 OCR 沒讀到。2026-08-12
    因為舊的判定放行了三筆「市價單」，讓同一則訊號被重複下單、還用當下市價成交
    （見 regex_parser._extract_entry 的說明）。這是第二道閘門 —— 即使解析層哪天又
    把什麼誤判成市價，中央也不會把它發出去。
    """
    return bool(
        signal.is_valid
        and signal.direction
        and signal.entry_price is not None
        and signal.stop_loss
        and signal.take_profit
    )


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
        stale_seconds: Optional[float] = None,
    ):
        self.config = config
        self.publisher = publisher
        self.parser = RegexSignalParser()
        self._pending: Dict[str, Dict] = {}
        # 擷取失敗的節流狀態: source → {error, since, last_log, count} (見 _log_capture_failure)
        self._capture_fail: Dict[str, Dict] = {}
        self._processed: Dict[Tuple, float] = {}
        self._processed_ttl = max(60, int(config.signal_dedup_minutes or 10) * 60)
        # 訊號時效鎖: 訊息時間超過這麼久就不發布 (擋歷史洪水/延遲/過期)。0=不限。
        self.max_signal_age_sec = max(0, int(getattr(config, "signal_max_age_minutes", 10) or 0) * 60)
        # 是否跟群組的「取消/撤」訊息。預設關 — 撤單統一交給會員端的逾時刪單。
        self.follow_group_cancel = bool(getattr(config, "follow_group_cancel", False))
        if not self.follow_group_cancel:
            logger.info("訊息撤單已停用 (follow_group_cancel=False) — 掛單改由逾時自動刪單處理")

        windows = [
            ClipboardWindow(
                name=w.name,
                window_name=w.window_name,
                display_name=_window_label(w),
                window_id=getattr(w, "window_id", None),
                allowed_senders=list(getattr(w, "allowed_senders", []) or []),
                required_patterns=list(getattr(w, "required_patterns", []) or []),
            )
            for w in (config.capture_windows or [])
        ]
        if not windows:
            raise RuntimeError("no capture_windows configured")

        # 有設發送者白名單或模板指紋的來源 → 擷取層已確認過是誰報的單,
        # 撤單偵測不必再套「只認乘」的預設。
        self._sender_filtered = {
            w.name for w in windows if w.allowed_senders or w.required_patterns
        }
        for w in windows:
            if w.allowed_senders:
                logger.info("來源 %r 只跟這些發送者: %s", w.label, w.allowed_senders)
            if w.required_patterns:
                logger.info("來源 %r 只跟符合模板指紋的訊息: %s", w.label, w.required_patterns)

        # 採集管道只剩一條：PrintWindow 背景截圖 + OCR。
        #
        # 舊的「搶焦點 → 點聊天區 → Ctrl+A → Ctrl+C」剪貼板管道已經整條移除。
        # 2026-07-30 在 LINE 26.3.0.3916 上實測：合成按鍵進得去 (Ctrl+F 叫得出搜尋列)、
        # 焦點也搶得到，但 Ctrl+A 只會選到空的訊息輸入框，聊天內容完全沒反白 —
        # 掃過 18 個不同的種子點擊位置全部複製到 0 字。而且那個種子點擊還會誤點到
        # 群裡的圖片，開出一個標題跟聊天室一模一樣的 LineMediaPlayer 看圖視窗，
        # 讓下一輪認錯視窗。留著只會讓人以為還有備援可切。
        _src = str(getattr(config, "signal_source", "window_ocr") or "window_ocr").strip().lower()
        if _src not in ("", "window_ocr"):
            logger.warning(
                "config.signal_source=%r 已不支援 (剪貼板管道在新版 LINE 上永遠拿到空字串) → 一律使用 window_ocr",
                _src,
            )

        from copy_trader.signal_capture.window_ocr_reader import WindowOcrReaderService
        self.clipboard = WindowOcrReaderService(
            windows,
            confirm_count=int(getattr(config, "ocr_confirm_count", 2) or 2),
        )
        logger.info(
            "collector initialized (WINDOW_OCR): windows=%s confirm=%s",
            len(windows), getattr(config, "ocr_confirm_count", 2),
        )

    def _cleanup(self) -> None:
        now = time.time()
        for key, ts in list(self._processed.items()):
            if now - ts > self._processed_ttl:
                self._processed.pop(key, None)
        for source, item in list(self._pending.items()):
            if now - item.get("time", now) > 120:
                logger.warning("pending signal expired for %s", source)
                self._pending.pop(source, None)

    # 同一個來源的同一種擷取失敗，最多每這麼久印一次 (秒)。
    #
    # 為什麼需要節流：視窗被關掉時 capture_all() 每一輪都會失敗，而輪詢是每秒一次。
    # 實測 2026-08-11 —— 「鄭」的 LINE 視窗被關掉後，200 行的 log 緩衝區在 3.5 分鐘內
    # 就被同一句 window_not_found 灌滿，把當天所有訊號紀錄整個沖掉，連要查前一小時
    # 那筆真實訊號的發布延遲都做不到。偏偏「視窗掉了 = 訊號漏抓」正是最需要看 log
    # 的時候，結果 log 反而只剩這一句重複幾千次。
    _CAPTURE_WARN_INTERVAL = 300.0

    def _log_capture_failure(self, source: str, error: str) -> None:
        """擷取失敗的節流警告：首次立刻印，之後每 _CAPTURE_WARN_INTERVAL 才印一次。"""
        now = time.time()
        state = self._capture_fail.get(source)
        if state is None or state["error"] != error:
            # 第一次失敗、或錯誤內容變了 → 立刻印
            logger.warning("capture failed for %s: %s", source, error)
            self._capture_fail[source] = {"error": error, "since": now, "last_log": now, "count": 1}
            return
        state["count"] += 1
        if now - state["last_log"] >= self._CAPTURE_WARN_INTERVAL:
            logger.warning(
                "capture failed for %s: %s (已持續 %.0f 分鐘, 累計 %d 次)",
                source, error, (now - state["since"]) / 60.0, state["count"],
            )
            state["last_log"] = now

    def _clear_capture_failure(self, source: str) -> None:
        """這個來源恢復了 → 印一行恢復訊息並清掉狀態 (沒失敗過就什麼都不做)。"""
        state = self._capture_fail.pop(source, None)
        if state:
            logger.info(
                "capture 已恢復: %s (先前失敗 %.0f 分鐘, 累計 %d 次: %s)",
                source, (time.time() - state["since"]) / 60.0, state["count"], state["error"],
            )

    def run_cycle(self) -> int:
        self._cleanup()
        published = 0
        for cap in self.clipboard.capture_all():
            if not cap.ok:
                if cap.error:
                    self._log_capture_failure(cap.display_name, cap.error)
                continue
            self._clear_capture_failure(cap.display_name)
            for msg in cap.new_messages:
                should_mark_seen = False
                abort_source = False
                did_publish = False
                try:
                    count = self._process_message(msg, cap.source_name, cap.display_name)
                    published += count
                    # count > 0 = 這則真的被發布到 Hub 了。採集層要記下這個
                    # 時刻，之後去重擋掉重複訊號時才講得出「我們何時發過」。
                    did_publish = count > 0
                    should_mark_seen = True
                except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
                    logger.warning("message publish failed, will retry source=%s: %s", cap.display_name, e)
                    self.clipboard.force_retry(cap.source_name)
                    # 採集層是靠「已 mark_seen 的訊息」判斷什麼算新的；若這裡 continue
                    # 讓後面的訊息先被標記，這則失敗的訊號下一輪會被當成舊的跳過而永久
                    # 遺失。中斷本批，下一輪整批重試。
                    abort_source = True
                except Exception as e:
                    logger.exception("message processing failed: %s", e)
                    should_mark_seen = True
                finally:
                    if should_mark_seen:
                        self.clipboard.mark_seen(cap.source_name, [msg],
                                                 published=did_publish)
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

        # 撤單指令 (在長度過濾之前, 因「取消」等只有2字)：
        #   OCR 路徑 → body 帶 __CANCEL__:<dir>:<訊號> 標記
        #   剪貼簿路徑 → 訊息本身就是「取消 / 撤掉 / 空單先撤掉」等
        # 預設 follow_group_cancel=False → 完全不理訊息撤單, 改由會員端「掛單逾時
        # 未成交自動刪單」統一處理 (跟多個來源時, 訊息撤單會撤到別群的掛單)。
        cancel_direction = None
        if not self.follow_group_cancel:
            if body.startswith("__CANCEL__"):
                logger.debug("已停用訊息撤單, 忽略 OCR 撤單標記: %r", body[:40])
                return 0
        elif body.startswith("__CANCEL__"):
            parts = body.split(":", 2)
            d = parts[1] if len(parts) > 1 else ""
            cancel_direction = d if d in ("buy", "sell") else ""
        else:
            cancel_direction = _cancel_direction_from_message(
                body, msg.sender, sender_verified=source_name in self._sender_filtered,
            )
        if cancel_direction is not None:
            # follow_group_cancel=False：撤單統一交給會員端的「逾時未進場自動刪單」，
            # 這裡只把撤單訊息丟掉。務必 return，否則 __CANCEL__ 標記的 body
            # 會往下被當成一般訊號解析。
            if not getattr(self.config, "follow_group_cancel", False):
                logger.debug("群組撤單偵測已停用，略過：%r", body[:20])
                return 0
            payload = {
                "type": "cancel_signal",
                "source": source_display,
                "source_name": source_name,
                "direction": cancel_direction,
                "captured_at": time.time(),
                "raw_text": body,
            }
            response = self.publisher.publish(payload)
            if not response.get("ok"):
                raise RuntimeError(f"hub rejected cancel: {response}")
            logger.info("published cancel to hub: source=%s dir=%s (%r)", source_display, cancel_direction or "any", body[:20])
            return 1

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
    parser = argparse.ArgumentParser(description="Run central LINE window-OCR signal collector.")
    parser.add_argument("--hub-url", default=os.environ.get("COPY_TRADER_HUB_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("COPY_TRADER_COLLECTOR_INTERVAL", "1.0")))
    parser.add_argument("--stale-seconds", type=float, default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log-level", default=os.environ.get("COPY_TRADER_LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    publisher = HubPublisher(args.hub_url, args.token)
    collector = CentralSignalCollector(config, publisher, stale_seconds=args.stale_seconds)
    if args.once:
        collector.run_cycle()
    else:
        collector.run_forever(interval=args.interval)


if __name__ == "__main__":
    main()
