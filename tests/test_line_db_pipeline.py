from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from copy_trader.central.membership import HIGH_FREQ, MID_FREQ
from copy_trader.central.signal_collector import CentralSignalCollector, is_cancel_reply
from copy_trader.line_db.factory import (
    migrate_legacy_default_line_chats,
    parse_line_chat_targets,
)
from copy_trader.line_db.identity import execution_id
from copy_trader.line_db.ledger import LineMessageLedger
from copy_trader.line_db.models import LineChatTarget, LineDatabaseMessage, ResolvedLineChat
from copy_trader.line_db.source import LineDatabaseSource


SIGNAL_TEXT = """乘XAUUSD 黃金
Sell：4903
止損：4915
止盈：4885"""

YUYU_SIGNAL_TEXT = """黃金 4070-4071空
Tp 4065 4060 4055
Sl 4076
個人建議不構成投資計畫"""


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
    created_time_ms: int | None = None,
) -> LineDatabaseMessage:
    return LineDatabaseMessage(
        rowid=rowid,
        message_id=message_id,
        chat=chat,
        created_time_ms=created_time_ms or int(time.time() * 1000) + rowid,
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

    def test_first_run_binds_chat_and_sender_ids_then_reuses_them(self):
        class BindingProvider(FakeDatabaseProvider):
            def __init__(self, chat, *, fail_sender_lookup=False):
                super().__init__(chat, latest=0)
                self.received_targets = []
                self.fail_sender_lookup = fail_sender_lookup

            def resolve_chats(self, targets):
                self.received_targets = list(targets)
                target = self.received_targets[0]
                chat_id = target.chat_id or "stable-chat-id"
                return [ResolvedLineChat(target, chat_id, target.chat_kind or "openchat")]

            def resolve_sender_ids(self, _chat, names):
                if self.fail_sender_lookup:
                    raise AssertionError("sender names should not be resolved after binding")
                return {name: f"stable-{index}" for index, name in enumerate(names, 1)}

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "cursor.json"
            first = BindingProvider(self.chat)
            source = LineDatabaseSource(first, [self.target], state)
            bound = source.chats[0]
            self.assertEqual(bound.chat_id, "stable-chat-id")
            self.assertEqual(bound.target.trusted_sender_ids, ("stable-1", "stable-2"))
            self.assertTrue(bound.target.accepts_trade_sender("stable-1", "已改名"))
            self.assertFalse(bound.target.accepts_trade_sender("stranger", "乘"))

            second = BindingProvider(self.chat, fail_sender_lookup=True)
            restored = LineDatabaseSource(second, [self.target], state).chats[0]
            self.assertEqual(second.received_targets[0].chat_id, "stable-chat-id")
            self.assertEqual(restored.target.trusted_sender_ids, ("stable-1", "stable-2"))


class LineChatFactoryTests(unittest.TestCase):
    def test_default_targets_include_existing_mid_and_high_frequency_sources(self):
        targets = parse_line_chat_targets(None)

        self.assertEqual(
            [(target.name, target.chat_name, target.display_name, target.trusted_senders)
             for target in targets],
            [
                (
                    "gold_signal_1",
                    "（乘）黃金報單🈲言群",
                    MID_FREQ,
                    ("乘", "James"),
                ),
                (
                    "high_freq_yuyu",
                    "🈲禁言群🈲 Focus forex 焦點利潤",
                    HIGH_FREQ,
                    ("yuyu（yu__o822",),
                ),
            ],
        )

    def test_exact_legacy_default_is_migrated_without_touching_custom_lists(self):
        legacy = """[
          {
            "name": "gold_signal_1",
            "chat_name": "（乘）黃金報單🈲言群",
            "display_name": "黃金報單🈲言群",
            "trusted_senders": ["乘", "James"]
          }
        ]"""
        migrated, changed = migrate_legacy_default_line_chats(legacy)

        self.assertTrue(changed)
        self.assertEqual(
            [target.name for target in parse_line_chat_targets(migrated)],
            ["gold_signal_1", "high_freq_yuyu"],
        )

        custom = """[{"name":"only_mine","chat_name":"私人測試群"}]"""
        untouched, changed = migrate_legacy_default_line_chats(custom)
        self.assertFalse(changed)
        self.assertEqual(untouched, custom)

    def test_previous_two_source_default_receives_strict_profiles(self):
        previous = [
            {
                "name": "gold_signal_1",
                "chat_name": "（乘）黃金報單🈲言群",
                "display_name": MID_FREQ,
                "trusted_senders": ["乘", "James"],
            },
            {
                "name": "high_freq_yuyu",
                "chat_name": "🈲禁言群🈲 Focus forex 焦點利潤",
                "display_name": HIGH_FREQ,
                "trusted_senders": ["yuyu（yu__o822"],
            },
        ]

        migrated, changed = migrate_legacy_default_line_chats(previous)

        self.assertTrue(changed)
        targets = parse_line_chat_targets(migrated)
        self.assertEqual(
            [(target.parser_profile, target.max_trade_age_seconds) for target in targets],
            [("mid_frequency_v1", 300), ("yuyu_range_v1", 180)],
        )


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
            # Cancellation must come from the durable ledger; quoted text may
            # no longer parse after a future rule change.
            related_text="legacy format no longer supported",
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

    def test_cancel_intent_requires_an_exact_command(self):
        for accepted in ("撤", "撤單", "取消掛單", "全部撤單", "撤(◐‿◑)"):
            self.assertTrue(is_cancel_reply(accepted), accepted)
        for rejected in ("這張不要撤", "撤了嗎", "如果還沒到要撤嗎", "先撤", "撤回"):
            self.assertFalse(is_cancel_reply(rejected), rejected)

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

    def test_yuyu_high_frequency_signal_uses_existing_product_source(self):
        target = LineChatTarget(
            "high_freq_yuyu",
            "🈲禁言群🈲 Focus forex 焦點利潤",
            "焦點利潤(yuyu)",
            ("yuyu（yu__o822",),
            parser_profile="yuyu_range_v1",
        )
        chat = ResolvedLineChat(target, "focus-chat-mid", "openchat")
        trade = message(
            chat,
            1,
            "yuyu-trade-message",
            YUYU_SIGNAL_TEXT,
            sender_id="yuyu-sender-id",
            sender_name="yuyu（yu__o822",
        )
        source = QueueSource([trade])
        publisher = RecordingPublisher()

        self.assertEqual(CentralSignalCollector(source, publisher).run_cycle(), 1)
        self.assertEqual(source.acknowledged, ["yuyu-trade-message"])
        self.assertEqual(len(publisher.payloads), 1)
        event = publisher.payloads[0]
        self.assertEqual(event["source"], "焦點利潤(yuyu)")
        self.assertEqual(event["source_name"], "high_freq_yuyu")
        self.assertEqual(event["signal"]["direction"], "sell")
        self.assertEqual(event["signal"]["entry_price"], 4070.0)
        self.assertEqual(event["signal"]["stop_loss"], 4076.0)
        self.assertEqual(event["signal"]["take_profit"], [4065.0, 4060.0, 4055.0])

    def test_ocr_aliases_and_multiple_orders_are_not_auto_executed(self):
        ocr = SIGNAL_TEXT.replace("止損", "止隕").replace("止盈", "止嬴")
        multiple = SIGNAL_TEXT + "\nBuy：4920\n止損：4900\n止盈：4940"
        source = QueueSource([
            message(self.chat, 10, "ocr-message", ocr),
            message(self.chat, 11, "multiple-message", multiple),
        ])
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(CentralSignalCollector(source, publisher, ledger).run_cycle(), 0)
        self.assertEqual(publisher.payloads, [])
        self.assertEqual(
            ledger.message_record("unknown-database", "chat-mid", "multiple-message")["parse_status"],
            "manual_review",
        )

    def test_trailing_direction_comment_does_not_replace_primary_order(self):
        body = SIGNAL_TEXT + "\n目前不建議追多"
        publisher = RecordingPublisher()

        self.assertEqual(
            CentralSignalCollector(
                QueueSource([message(self.chat, 12, "comment-message", body)]),
                publisher,
            ).run_cycle(),
            1,
        )
        self.assertEqual(publisher.payloads[0]["signal"]["direction"], "sell")

    def test_stale_backlog_trade_is_recorded_but_not_published(self):
        old = int((time.time() - 3600) * 1000)
        row = message(
            self.chat,
            13,
            "stale-message",
            SIGNAL_TEXT,
            created_time_ms=old,
        )
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(CentralSignalCollector(QueueSource([row]), publisher, ledger).run_cycle(), 0)
        self.assertEqual(publisher.payloads, [])
        record = ledger.message_record("unknown-database", "chat-mid", "stale-message")
        self.assertEqual(record["parse_status"], "rejected_stale_backlog")

    def test_shadow_mode_uses_real_parser_and_ledger_without_publishing(self):
        row = message(self.chat, 14, "shadow-message", SIGNAL_TEXT)
        cancel = message(
            self.chat,
            15,
            "shadow-cancel",
            "撤單",
            sender_id="admin-2",
            sender_name="James",
            related_message_id="shadow-message",
            related_sender_id="sender-1",
            related_sender_name="乘",
        )
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(
            CentralSignalCollector(
                QueueSource([row, cancel]),
                publisher,
                ledger,
                shadow_mode=True,
            ).run_cycle(),
            0,
        )
        self.assertEqual(publisher.payloads, [])
        record = ledger.message_record("unknown-database", "chat-mid", "shadow-message")
        self.assertEqual(record["parse_status"], "shadow_accepted")
        self.assertEqual(
            ledger.published_execution_ids("unknown-database", "chat-mid", "shadow-message"),
            [],
        )
        cancel_record = ledger.message_record("unknown-database", "chat-mid", "shadow-cancel")
        self.assertEqual(cancel_record["parse_status"], "shadow_cancel")


if __name__ == "__main__":
    unittest.main()
