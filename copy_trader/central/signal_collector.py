"""Publish exact LINE database messages as structured trading events."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Dict

from copy_trader.config import DATA_DIR
from copy_trader.line_db.factory import build_line_database_source
from copy_trader.line_db.identity import execution_id, line_event_id
from copy_trader.line_db.ledger import LineMessageLedger
from copy_trader.line_db.models import LineDatabaseMessage
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.signal_parser.strict_parser import StrictParseResult, parse_strict_signal


logger = logging.getLogger(__name__)

_EXACT_CANCEL_COMMANDS = frozenset({"撤", "撤單", "撤掉", "取消", "取消掛單", "全部撤單"})


def normalize_cancel_command(text: str) -> str:
    # Remove whitespace, emoji and decorative punctuation, but retain all
    # letters/numbers. "撤(◐‿◑)" becomes "撤" while "這張不要撤" stays intact.
    return "".join(
        char
        for char in (text or "")
        if unicodedata.category(char)[0] not in {"C", "M", "P", "S", "Z"}
    ).casefold()


def is_cancel_reply(text: str) -> bool:
    return normalize_cancel_command(text) in _EXACT_CANCEL_COMMANDS


def _signal_payload(signal: ParsedSignal, result: StrictParseResult) -> Dict:
    payload = {
        "symbol": signal.symbol or "XAUUSD",
        "direction": signal.direction,
        "entry_price": signal.entry_price,
        "is_market_order": bool(signal.is_market_order),
        "stop_loss": signal.stop_loss,
        "take_profit": list(signal.take_profit or []),
        "lot_size": signal.lot_size,
        "parse_status": result.status,
        "parse_method": f"line_db+{result.profile}",
        "raw_text_summary": signal.raw_text_summary,
        "error": signal.error,
    }
    if result.repair is not None:
        payload["repair"] = {
            "field": result.repair.field,
            "original": list(result.repair.original),
            "corrected": list(result.repair.corrected),
            "method": result.repair.method,
        }
    return payload


def _message_preview(text: str, limit: int = 160) -> str:
    """Compact trusted-provider text for a human-readable reject notice."""
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


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

    def __init__(
        self,
        source,
        publisher: HubPublisher,
        ledger: LineMessageLedger | None = None,
        *,
        clock=time.time,
        shadow_mode: bool = False,
    ):
        self.source = source
        self.publisher = publisher
        self.ledger = ledger or LineMessageLedger()
        self.clock = clock
        self.shadow_mode = bool(shadow_mode)
        self._recall_checked_at: dict[str, float] = {}

    @staticmethod
    def _database_id(message: LineDatabaseMessage) -> str:
        return message.database_id or "unknown-database"

    def _publish(self, payload: Dict) -> Dict:
        response = self.publisher.publish(payload)
        if not response.get("ok"):
            raise RuntimeError(f"hub rejected LINE event: {response}")
        return response

    def _publish_rejection(
        self,
        message: LineDatabaseMessage,
        result: StrictParseResult,
        *,
        parse_status: str | None = None,
        reason: str | None = None,
        signal: ParsedSignal | None = None,
        extra: Dict | None = None,
    ) -> Dict:
        """Publish a non-executable event so LINE Bot can explain the skipped order.

        Rejections share the Hub's deterministic event identity and transport
        idempotency. Member agents ignore this event type and advance their cursor;
        only the notification/audit path renders it.
        """
        target = message.chat.target
        status = str(parse_status or result.status)
        reason_code = str(reason or result.reason)
        parsed_signal = signal or result.signal
        signal_payload = _signal_payload(parsed_signal, result) if parsed_signal else {}
        if signal_payload:
            signal_payload["parse_status"] = status
        payload = {
            "event_id": line_event_id(message.chat.chat_id, message.message_id, "reject"),
            "type": "signal_rejected",
            "source": target.display_name,
            "source_name": target.name,
            "sender": message.sender_name,
            "line_chat_id": message.chat.chat_id,
            "line_message_id": message.message_id,
            "line_rowid": message.rowid,
            "line_revision": message.revision,
            "message_time": message.timestamp.isoformat(),
            "parse_status": status,
            "rejection_reason": reason_code,
            "message_preview": _message_preview(message.text),
            "signal": signal_payload,
        }
        if extra:
            payload.update(extra)
        response = self._publish(payload)
        logger.warning(
            "published LINE rejection notice source=%s message=%s status=%s reason=%s",
            target.display_name,
            message.message_id,
            status,
            reason_code,
        )
        return response

    def _publish_cancel(self, message: LineDatabaseMessage) -> int | None:
        target = message.chat.target
        if not message.is_reply or not is_cancel_reply(message.text):
            return None
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
        if not target.accepts_trade_sender(
            message.related_sender_id,
            message.related_sender_name,
        ):
            logger.info(
                "ignored cancel for non-trusted original sender source=%s sender=%r",
                target.display_name,
                message.related_sender_name,
            )
            return 0

        if self.shadow_mode:
            target_execution_ids = self.ledger.execution_ids(
                self._database_id(message),
                message.chat.chat_id,
                message.related_message_id,
            )
            status = "shadow_cancel" if target_execution_ids else "manual_review_cancel_ledger_miss"
            self.ledger.record_message(
                message,
                parser_profile=target.parser_profile,
                parse_status=status,
            )
            logger.info(
                "shadow LINE cancel source=%s target=%s status=%s",
                target.display_name,
                message.related_message_id,
                status,
            )
            return 0

        target_execution_ids = self.ledger.published_execution_ids(
            self._database_id(message),
            message.chat.chat_id,
            message.related_message_id,
        )
        if not target_execution_ids:
            logger.info(
                "LINE cancel has no published execution in the ledger: %s",
                message.related_message_id,
            )
            self.ledger.record_message(
                message,
                parser_profile=target.parser_profile,
                parse_status="manual_review_cancel_ledger_miss",
            )
            return 0

        event_id = line_event_id(message.chat.chat_id, message.message_id, "cancel")
        payload = {
            "event_id": event_id,
            "type": "cancel_signal",
            "source": target.display_name,
            "source_name": target.name,
            "line_chat_id": message.chat.chat_id,
            "line_message_id": message.message_id,
            "target_line_message_id": message.related_message_id,
            "target_execution_ids": target_execution_ids,
            "target_signals": self.ledger.execution_signals(target_execution_ids),
            "cancel_reason": "line_reply",
            "sender": message.sender_name,
            "message_time": message.timestamp.isoformat(),
            "command": normalize_cancel_command(message.text),
        }
        response = self._publish(payload)
        self.ledger.record_message(
            message,
            parser_profile=target.parser_profile,
            parse_status="accepted_cancel",
        )
        self.ledger.record_cancel_published(
            message,
            event_id=event_id,
            target_message_id=message.related_message_id,
            target_execution_ids=target_execution_ids,
            hub_response=response,
        )
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
        if cancel_count is not None:
            return cancel_count

        if not target.accepts_trade_sender(message.sender_id, message.sender_name):
            logger.debug(
                "ignored LINE message from non-trusted sender source=%s sender=%r",
                target.display_name,
                message.sender_name,
            )
            self.ledger.record_message(
                message,
                parser_profile=target.parser_profile,
                parse_status="rejected_untrusted_sender",
            )
            return 0

        result = parse_strict_signal(body, target.parser_profile)
        if not result.accepted or result.signal is None:
            self.ledger.record_message(
                message,
                parser_profile=result.profile,
                parse_status=result.status,
            )
            logger.debug(
                "LINE message rejected source=%s status=%s reason=%s",
                target.display_name,
                result.status,
                result.reason,
            )
            if result.signal_like and not self.shadow_mode:
                self._publish_rejection(message, result)
            return 0

        signal = result.signal
        index = 0
        order_id = execution_id(message.chat.chat_id, message.message_id, index)
        event_id = line_event_id(message.chat.chat_id, message.message_id, "trade", index)
        signal_payload = _signal_payload(signal, result)
        age_seconds = max(0.0, self.clock() - message.created_time_ms / 1000)
        stale = (
            target.max_trade_age_seconds > 0
            and age_seconds > target.max_trade_age_seconds
        )
        repaired = result.repair is not None
        if repaired:
            # 只有來源專屬規則得到唯一解才會修復。原值／修正值一路帶到 Hub
            # 與 LINE Bot，方便事後對照原文稽核，不做靜默修改。
            logger.warning(
                "repaired malformed signal source=%s message=%s method=%s field=%s %s -> %s",
                target.display_name,
                message.message_id,
                result.repair.method,
                result.repair.field,
                result.repair.original,
                result.repair.corrected,
            )
        base_status = (
            "accepted_tp_repaired"
            if result.reason == "tp_repaired_from_spacing"
            else "accepted_point_repaired"
            if repaired
            else result.status
        )
        signal_payload["parse_status"] = base_status
        parse_status = (
            "rejected_stale_backlog"
            if stale
            else "shadow_accepted" if self.shadow_mode else base_status
        )
        self.ledger.record_message(
            message,
            parser_profile=result.profile,
            parse_status=parse_status,
            executions=[{
                "execution_id": order_id,
                "signal_index": index,
                "event_id": event_id,
                "signal": signal_payload,
            }],
        )
        if stale:
            logger.warning(
                "skipped stale LINE trade source=%s message=%s age=%.1fs limit=%ss",
                target.display_name,
                message.message_id,
                age_seconds,
                target.max_trade_age_seconds,
            )
            if not self.shadow_mode:
                self._publish_rejection(
                    message,
                    result,
                    parse_status="rejected_stale_backlog",
                    reason="stale_backlog",
                    signal=signal,
                    extra={
                        "age_seconds": round(age_seconds, 1),
                        "max_trade_age_seconds": target.max_trade_age_seconds,
                    },
                )
            return 0
        if self.shadow_mode:
            logger.info(
                "shadow LINE trade source=%s message=%s: %s",
                target.display_name,
                message.message_id,
                signal,
            )
            return 0

        payload = {
            "event_id": event_id,
            "type": "trade_signal",
            "execution_id": order_id,
            "source": target.display_name,
            "source_name": target.name,
            "sender": message.sender_name,
            "line_chat_id": message.chat.chat_id,
            "line_message_id": message.message_id,
            "line_rowid": message.rowid,
            "line_revision": message.revision,
            "message_time": message.timestamp.isoformat(),
            "signal_index": index,
            "signal": signal_payload,
        }
        response = self._publish(payload)
        self.ledger.mark_trade_published(order_id, response)
        logger.info(
            "published strict LINE DB signal source=%s message=%s: %s",
            target.display_name,
            message.message_id,
            signal,
        )
        return 1

    @staticmethod
    def _local_iso(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat()

    def _reconcile_recalls(self) -> int:
        """Detect in-place LINE UNSENT transitions for ledgered trades."""
        provider = getattr(self.source, "provider", None)
        fetch_metadata = getattr(provider, "fetch_message_metadata", None)
        if provider is None or not callable(fetch_metadata):
            return 0
        chats = getattr(self.source, "chats", None)
        if not chats:
            return 0

        detected_at = float(self.clock())
        published = 0
        database_id = str(getattr(provider, "database_id", "") or "unknown-database")
        for chat in chats:
            watch_seconds = int(chat.target.recall_watch_seconds or 0)
            if watch_seconds <= 0:
                continue
            watched = self.ledger.watched_messages(
                database_id,
                chat.chat_id,
                created_after_ms=int((detected_at - watch_seconds) * 1000),
                include_shadow=self.shadow_mode,
            )
            previous_check = self._recall_checked_at.get(chat.chat_id)
            for offset in range(0, len(watched), 500):
                batch = watched[offset:offset + 500]
                metadata_by_id = {
                    item.message_id: item
                    for item in fetch_metadata(
                        chat,
                        [str(row["message_id"]) for row in batch],
                    )
                }
                for row in batch:
                    message_id = str(row["message_id"])
                    metadata = metadata_by_id.get(message_id)
                    if metadata is None or not metadata.unsent:
                        continue
                    # 不要求 revision 增加。原本假設收回時 _rev 會從 1 跳到 2
                    # （macOS 版 LINE 的行為），但 Windows 版 LINE 26.3 收回是
                    # 「就地把 _text 清空、_contentMetadata 設 UNSENT，_rev 停在 1」
                    # —— 那個 `_rev 要變大` 的閘門於是永遠擋掉 Windows 的收回，
                    # 導致 yuyu「發錯→收回→重發」時舊掛單留成幽靈單
                    # （2026-08-27 實測：msg ...800 收回後 unsent=True 但 rev=1，
                    # 被這行 skip 掉）。
                    #
                    # 冪等性不靠 revision：下面的 recall_recorded() 記錄過就跳過，
                    # target_execution_ids 又保證只撤真的發布過訂單的訊息。所以
                    # 拿掉這個閘門既修好 Windows、對 macOS 也無害（rev=2 一樣過）。
                    recall_event_id = line_event_id(chat.chat_id, message_id, "recall")
                    if self.ledger.recall_recorded(recall_event_id):
                        continue
                    target_execution_ids = self.ledger.execution_ids(
                        database_id,
                        chat.chat_id,
                        message_id,
                        published_only=not self.shadow_mode,
                    )
                    if not target_execution_ids:
                        continue

                    if self.shadow_mode:
                        self.ledger.record_recall(
                            event_id=recall_event_id,
                            database_id=database_id,
                            chat_id=chat.chat_id,
                            message_id=message_id,
                            target_execution_ids=target_execution_ids,
                            observed_revision=metadata.revision,
                            original_message_time_ms=int(row["created_time_ms"] or 0),
                            observation_window_started_at=previous_check,
                            detected_at=detected_at,
                            state="shadow",
                        )
                        logger.info(
                            "shadow LINE recall source=%s message=%s revision=%s",
                            chat.target.display_name,
                            message_id,
                            metadata.revision,
                        )
                        continue

                    payload = {
                        "event_id": recall_event_id,
                        "type": "cancel_signal",
                        "source": chat.target.display_name,
                        "source_name": chat.target.name,
                        "line_chat_id": chat.chat_id,
                        "line_message_id": message_id,
                        "target_line_message_id": message_id,
                        "target_execution_ids": target_execution_ids,
                        "target_signals": self.ledger.execution_signals(target_execution_ids),
                        "cancel_reason": "line_unsent",
                        "line_revision": metadata.revision,
                        "message_time": self._local_iso(
                            int(row["created_time_ms"] or 0) / 1000
                        ),
                        "recall_detected_at": self._local_iso(detected_at),
                        "recall_observation_window_started_at": (
                            self._local_iso(previous_check) if previous_check else ""
                        ),
                        "recall_time_source": "database_poll_detection",
                    }
                    response = self._publish(payload)
                    self.ledger.record_recall(
                        event_id=recall_event_id,
                        database_id=database_id,
                        chat_id=chat.chat_id,
                        message_id=message_id,
                        target_execution_ids=target_execution_ids,
                        observed_revision=metadata.revision,
                        original_message_time_ms=int(row["created_time_ms"] or 0),
                        observation_window_started_at=previous_check,
                        detected_at=detected_at,
                        state="published",
                        hub_response=response,
                    )
                    published += 1
                    logger.info(
                        "published LINE recall-cancel source=%s message=%s orders=%s revision=%s",
                        chat.target.display_name,
                        message_id,
                        len(target_execution_ids),
                        metadata.revision,
                    )
            self._recall_checked_at[chat.chat_id] = detected_at
        return published

    def run_cycle(self) -> int:
        published = 0
        for message in self.source.poll():
            count = self._process_message(message)
            # If publishing raised, this row is not acknowledged and is retried.
            self.source.acknowledge(message)
            published += count
        published += self._reconcile_recalls()
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
    parser.add_argument(
        "--shadow",
        action="store_true",
        default=str(os.environ.get("LINE_DB_SHADOW_MODE") or "").casefold() in {"1", "true", "yes", "on"},
        help="parse and ledger LINE rows without publishing Hub events",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    source = build_line_database_source(
        database_path=args.database,
        keychain_service=args.keychain_service,
        line_chats=args.chats_json,
        state_path=Path(args.state_file),
    )
    collector = CentralSignalCollector(
        source,
        HubPublisher(args.hub_url, args.token),
        LineMessageLedger(DATA_DIR / "line_message_ledger.sqlite3"),
        shadow_mode=args.shadow,
    )
    if args.once:
        collector.run_cycle()
    else:
        collector.run_forever(args.interval)


if __name__ == "__main__":
    main()
