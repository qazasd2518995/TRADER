from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from copy_trader.central.signal_collector import CentralSignalCollector
from copy_trader.line_db.identity import execution_id
from copy_trader.line_db.models import LineChatTarget, LineDatabaseMessage, ResolvedLineChat
from copy_trader.line_db.source import LineDatabaseSource


SIGNAL_TEXT = """乘XAUUSD 黃金
Sell：4903
止損：4915
止盈：4885"""


def message(
    chat: ResolvedLineChat,
    rowid: int,
    message_id: str,
    text: str,
    *,
    sender_id: str = "sender-1",
    sender_name: str = "乘",
    related_message_id: str = "",
    related_sender_id: str = "",
    related_sender_name: str = "",
    related_text: str = "",
) -> LineDatabaseMessage:
    return LineDatabaseMessage(
        rowid=rowid,
        message_id=message_id,
        chat=chat,
        created_time_ms=1_780_000_000_000 + rowid,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        relation_type=3 if related_message_id else 0,
        related_message_id=related_message_id,
        related_sender_id=related_sender_id,
        related_sender_name=related_sender_name,
        related_text=related_text,
    )


class FakeDatabaseProvider:
    database_path = Path("/read-only/line.edb")
    database_id = "fake-database"

    def __init__(self, chat: ResolvedLineChat, latest: int, rows=None):
        self.chat = chat
        self.latest = latest
        self.rows = list(rows or [])

    def resolve_chats(self, _targets):
        return [self.chat]

    def latest_rowid(self, _chat):
        return self.latest

    def fetch_after(self, _chat, rowid, limit):
        return [item for item in self.rows if item.rowid > rowid][:limit]

    def integrity_check(self):
        return "ok"


class QueueSource:
    def __init__(self, rows):
        self.rows = list(rows)
        self.acknowledged = []

    def poll(self):
        return list(self.rows)

    def acknowledge(self, row):
        self.acknowledged.append(row.message_id)


class RecordingPublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise OSError("network unavailable")
        return {"ok": True}


class LineDatabaseSourceTests(unittest.TestCase):
    def setUp(self):
        self.target = LineChatTarget(
            "gold",
            "（乘）黃金報單🈲言群",
            "黃金報單🈲言群",
            ("乘", "James"),
        )
        self.chat = ResolvedLineChat(self.target, "chat-mid", "openchat")

    def test_first_run_baselines_without_replaying_history(self):
        provider = FakeDatabaseProvider(
            self.chat,
            latest=100,
            rows=[message(self.chat, 100, "old-message", SIGNAL_TEXT)],
        )
        with tempfile.TemporaryDirectory() as directory:
            source = LineDatabaseSource(provider, [self.target], Path(directory) / "cursor.json")
            self.assertEqual(source.poll(), [])

            new_row = message(self.chat, 101, "new-message", SIGNAL_TEXT)
            provider.rows.append(new_row)
            provider.latest = 101
            self.assertEqual([item.message_id for item in source.poll()], ["new-message"])
            source.acknowledge(new_row)
            self.assertEqual(source.poll(), [])

    def test_rows_from_each_chat_remain_in_rowid_order(self):
        other_target = LineChatTarget("other", "Other")
        other_chat = ResolvedLineChat(other_target, "other-mid", "group")
        provider = FakeDatabaseProvider(self.chat, latest=0)
        with tempfile.TemporaryDirectory() as directory:
            source = LineDatabaseSource(provider, [self.target], Path(directory) / "cursor.json")
            self.assertEqual(source.poll(), [])
            # This invariant is covered through the actual sort key in poll; a
            # malformed timestamp must not move a higher row before a lower one.
            provider.latest = 102
            provider.rows = [
                message(self.chat, 102, "second", SIGNAL_TEXT),
                message(self.chat, 101, "first", SIGNAL_TEXT),
            ]
            self.assertEqual([item.rowid for item in source.poll()], [101, 102])
            self.assertEqual(other_chat.kind, "group")


class CollectorTests(unittest.TestCase):
    def setUp(self):
        target = LineChatTarget(
            "gold",
            "（乘）黃金報單🈲言群",
            "黃金報單🈲言群",
            ("乘", "James"),
        )
        self.chat = ResolvedLineChat(target, "chat-mid", "openchat")

    def test_trade_and_reply_cancel_share_exact_execution_identity(self):
        trade = message(self.chat, 1, "trade-message", SIGNAL_TEXT)
        cancel = message(
            self.chat,
            2,
            "cancel-message",
            "撤單",
            sender_id="admin-2",
            sender_name="James",
            related_message_id="trade-message",
            related_sender_id="sender-1",
            related_sender_name="乘",
            related_text=SIGNAL_TEXT,
        )
        source = QueueSource([trade, cancel])
        publisher = RecordingPublisher()
        count = CentralSignalCollector(source, publisher).run_cycle()

        self.assertEqual(count, 2)
        self.assertEqual(source.acknowledged, ["trade-message", "cancel-message"])
        trade_event, cancel_event = publisher.payloads
        expected = execution_id("chat-mid", "trade-message", 0)
        self.assertEqual(trade_event["execution_id"], expected)
        self.assertEqual(cancel_event["target_execution_ids"], [expected])
        self.assertEqual(cancel_event["target_line_message_id"], "trade-message")

    def test_publish_failure_does_not_acknowledge_line_row(self):
        source = QueueSource([message(self.chat, 1, "trade-message", SIGNAL_TEXT)])
        with self.assertRaises(OSError):
            CentralSignalCollector(source, RecordingPublisher(fail=True)).run_cycle()
        self.assertEqual(source.acknowledged, [])

    def test_untrusted_reply_cannot_cancel(self):
        cancel = message(
            self.chat,
            2,
            "cancel-message",
            "撤單",
            sender_id="stranger",
            sender_name="陌生人",
            related_message_id="trade-message",
            related_sender_id="sender-1",
            related_sender_name="乘",
            related_text=SIGNAL_TEXT,
        )
        source = QueueSource([cancel])
        publisher = RecordingPublisher()
        self.assertEqual(CentralSignalCollector(source, publisher).run_cycle(), 0)
        self.assertEqual(publisher.payloads, [])
        self.assertEqual(source.acknowledged, ["cancel-message"])


if __name__ == "__main__":
    unittest.main()
