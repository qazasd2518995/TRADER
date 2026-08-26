"""Publish exact LINE database messages as structured trading events."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict

from copy_trader.config import DATA_DIR
from copy_trader.line_db.factory import build_line_database_source
from copy_trader.line_db.identity import execution_id, line_event_id
from copy_trader.line_db.models import LineDatabaseMessage
from copy_trader.signal_parser.regex_parser import ParsedSignal, RegexSignalParser


logger = logging.getLogger(__name__)

_CANCEL_WORDS = (
    "取消", "撤單", "撤掉", "撤回", "先撤", "都撤", "撤了", "撤吧", "刪單", "砍單", "撤",
)
_CANCEL_EXCLUDE = (
    "嗎", "?", "？", "有沒有", "會不會", "是不是", "設定", "後來", "怎麼", "為何", "如果", "要不要",
)


def is_cancel_reply(text: str) -> bool:
    body = (text or "").strip()
    if not body or len(body) > 40:
        return False
    if any(word in body for word in _CANCEL_EXCLUDE):
        return False
    if "止損" in body or "止盈" in body or "xauusd" in body.casefold():
        return False
    return any(word in body for word in _CANCEL_WORDS)


def _is_complete(signal: ParsedSignal) -> bool:
    return bool(
        signal.is_valid
        and signal.direction in {"buy", "sell"}
        and signal.entry_price is not None
        and signal.stop_loss is not None
        and signal.take_profit
    )


def _sl_tp_consistent(signal: ParsedSignal) -> bool:
    if not _is_complete(signal):
        return False
    entry = float(signal.entry_price)
    stop_loss = float(signal.stop_loss)
    take_profits = [float(value) for value in signal.take_profit if value is not None]
    if not take_profits:
        return False
    if signal.direction == "buy":
        return stop_loss < entry < min(take_profits)
    return max(take_profits) < entry < stop_loss


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
        "parse_method": "line_db+regex",
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
        request = urllib.request.Request(
            f"{self.hub_url}/signals",
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class CentralSignalCollector:
    """Translate exact LINE rows into Hub events and acknowledge each row."""

    def __init__(self, source, publisher: HubPublisher):
        self.source = source
        self.publisher = publisher
        self.parser = RegexSignalParser()

    def _candidate_signals(self, body: str) -> list[ParsedSignal]:
        candidates = self.parser.parse_all_latest(body)
        if not candidates:
            candidate = self.parser.parse_latest(body)
            candidates = [candidate] if candidate.direction else []
        return [
            signal for signal in candidates
            if _is_complete(signal) and _sl_tp_consistent(signal)
        ]

    def _publish(self, payload: Dict) -> None:
        response = self.publisher.publish(payload)
        if not response.get("ok"):
            raise RuntimeError(f"hub rejected LINE event: {response}")

    def _publish_cancel(self, message: LineDatabaseMessage) -> int:
        target = message.chat.target
        if not message.is_reply or not is_cancel_reply(message.text):
            return 0
        if not target.accepts_cancel_sender(
            message.sender_id,
            message.sender_name,
            message.related_sender_id,
            message.related_sender_name,
        ):
            logger.warning(
                "ignored untrusted LINE cancel reply source=%s sender=%r target_sender=%r",
                target.display_name,
                message.sender_name,
                message.related_sender_name,
            )
            return 0
        if not target.accepts_trade_sender(message.related_sender_name):
            logger.info(
                "ignored cancel for non-trusted original sender source=%s sender=%r",
                target.display_name,
                message.related_sender_name,
            )
            return 0

        original_signals = self._candidate_signals(message.related_text)
        if not original_signals:
            logger.info(
                "LINE reply looked like a cancel but quoted message is not a complete signal: %s",
                message.related_message_id,
            )
            return 0

        target_execution_ids = [
            execution_id(message.chat.chat_id, message.related_message_id, index)
            for index, _signal in enumerate(original_signals)
        ]
        payload = {
            "event_id": line_event_id(message.chat.chat_id, message.message_id, "cancel"),
            "type": "cancel_signal",
            "source": target.display_name,
            "source_name": target.name,
            "line_chat_id": message.chat.chat_id,
            "line_message_id": message.message_id,
            "target_line_message_id": message.related_message_id,
            "target_execution_ids": target_execution_ids,
            "sender": message.sender_name,
            "message_time": message.timestamp.isoformat(),
            "raw_text": message.text,
        }
        self._publish(payload)
        logger.info(
            "published exact LINE reply-cancel source=%s reply=%s target=%s orders=%s",
            target.display_name,
            message.message_id,
            message.related_message_id,
            len(target_execution_ids),
        )
        return 1

    def _process_message(self, message: LineDatabaseMessage) -> int:
        target = message.chat.target
        body = message.text.strip()
        if not body:
            return 0

        cancel_count = self._publish_cancel(message)
        if cancel_count:
            return cancel_count

        if not target.accepts_trade_sender(message.sender_name):
            logger.debug(
                "ignored LINE message from non-trusted sender source=%s sender=%r",
                target.display_name,
                message.sender_name,
            )
            return 0

        published = 0
        for index, signal in enumerate(self._candidate_signals(body)):
            order_id = execution_id(message.chat.chat_id, message.message_id, index)
            payload = {
                "event_id": line_event_id(message.chat.chat_id, message.message_id, "trade", index),
                "type": "trade_signal",
                "execution_id": order_id,
                "source": target.display_name,
                "source_name": target.name,
                "sender": message.sender_name,
                "line_chat_id": message.chat.chat_id,
                "line_message_id": message.message_id,
                "line_rowid": message.rowid,
                "message_time": message.timestamp.isoformat(),
                "signal_index": index,
                "signal": _signal_payload(signal),
                "raw_text": body,
            }
            self._publish(payload)
            published += 1
            logger.info(
                "published LINE DB signal source=%s message=%s index=%s: %s",
                target.display_name,
                message.message_id,
                index,
                signal,
            )
        return published

    def run_cycle(self) -> int:
        published = 0
        for message in self.source.poll():
            count = self._process_message(message)
            # If publishing raised, this row is not acknowledged and is retried.
            self.source.acknowledge(message)
            published += count
        return published

    def run_forever(self, interval: float = 1.0) -> None:
        logger.info("LINE DB collector running")
        try:
            while True:
                self.run_cycle()
                time.sleep(max(0.2, interval))
        except KeyboardInterrupt:
            logger.info("LINE DB collector stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only LINE DB signal collector")
    parser.add_argument("--hub-url", default=os.environ.get("COPY_TRADER_HUB_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    parser.add_argument("--database", default=os.environ.get("LINE_DB_PATH", ""))
    parser.add_argument("--keychain-service", default=os.environ.get("LINE_DB_KEYCHAIN_SERVICE", "line-db-research"))
    parser.add_argument("--chats-json", default=os.environ.get("LINE_DB_CHATS", ""))
    parser.add_argument("--state-file", default=os.environ.get("LINE_DB_CURSOR_STATE", str(DATA_DIR / "line_db_cursor.json")))
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    source = build_line_database_source(
        database_path=args.database,
        keychain_service=args.keychain_service,
        line_chats=args.chats_json,
        state_path=Path(args.state_file),
    )
    collector = CentralSignalCollector(source, HubPublisher(args.hub_url, args.token))
    if args.once:
        collector.run_cycle()
    else:
        collector.run_forever(args.interval)


if __name__ == "__main__":
    main()
