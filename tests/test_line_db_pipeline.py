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
from copy_trader.line_db.models import (
    LineChatTarget,
    LineDatabaseMessage,
    LineMessageMetadata,
    ResolvedLineChat,
)
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


class RecallProvider:
    database_id = "unknown-database"

    def __init__(self):
        self.metadata = {}

    def fetch_message_metadata(self, _chat, message_ids):
        return [self.metadata[value] for value in message_ids if value in self.metadata]


class RecallSource(QueueSource):
    def __init__(self, chat, rows):
        super().__init__(rows)
        self.provider = RecallProvider()
        self.chats = [chat]

    def acknowledge(self, row):
        super().acknowledge(row)
        self.rows = [item for item in self.rows if item.message_id != row.message_id]


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
            [
                (
                    target.parser_profile,
                    target.max_trade_age_seconds,
                    target.recall_watch_seconds,
                )
                for target in targets
            ],
            [
                ("mid_frequency_v1", 300, 2592000),
                ("yuyu_range_v1", 180, 2592000),
            ],
        )

        pre_recall = [
            {key: value for key, value in item.items() if key != "recall_watch_seconds"}
            for item in migrated
        ]
        upgraded, changed = migrate_legacy_default_line_chats(pre_recall)
        self.assertTrue(changed)
        self.assertTrue(all("recall_watch_seconds" in item for item in upgraded))


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

    def test_yuyu_malformed_take_profit_is_repaired_and_published(self):
        # 2026-08-27 17:56/18:15 真實掉單:第三個止盈 4595 被打成 460。
        # 系統照 yuyu 固定間距把它外推補回 4595,整條管線仍要正常發布這一單。
        malformed = "黃金 4580-4581多\nTp 4585 4590 460\nSl 4574\n個人建議不構成投資計畫🫶"
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
            "yuyu-typo-message",
            malformed,
            sender_id="yuyu-sender-id",
            sender_name="yuyu（yu__o822",
        )
        source = QueueSource([trade])
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(CentralSignalCollector(source, publisher, ledger).run_cycle(), 1)
        self.assertEqual(len(publisher.payloads), 1)
        signal = publisher.payloads[0]["signal"]
        self.assertEqual(signal["direction"], "buy")
        self.assertEqual(signal["entry_price"], 4580.0)
        self.assertEqual(signal["stop_loss"], 4574.0)
        self.assertEqual(signal["take_profit"], [4585.0, 4590.0, 4595.0])
        # 帳上留可稽核的修復狀態,方便事後對照原文,不做靜默改動。
        recorded = ledger.message_record("unknown-database", "focus-chat-mid", "yuyu-typo-message")
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded["parse_status"], "accepted_tp_repaired")

    def test_ocr_aliases_and_multiple_orders_emit_rejection_notices_without_orders(self):
        ocr = SIGNAL_TEXT.replace("止損", "止隕").replace("止盈", "止嬴")
        multiple = SIGNAL_TEXT + "\nBuy：4920\n止損：4900\n止盈：4940"
        source = QueueSource([
            message(self.chat, 10, "ocr-message", ocr),
            message(self.chat, 11, "multiple-message", multiple),
        ])
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(CentralSignalCollector(source, publisher, ledger).run_cycle(), 0)
        self.assertEqual(len(publisher.payloads), 2)
        self.assertEqual(
            [event["type"] for event in publisher.payloads],
            ["signal_rejected", "signal_rejected"],
        )
        self.assertEqual(
            [event["parse_status"] for event in publisher.payloads],
            ["rejected_unknown_format", "manual_review"],
        )
        self.assertEqual(
            ledger.message_record("unknown-database", "chat-mid", "multiple-message")["parse_status"],
            "manual_review",
        )

    def test_provider_entry_hundred_offset_is_repaired_and_published(self):
        # 2026-08-13 真實類型：SL 與三個 TP 都圍繞 4474，只有進場少 100。
        # yuyu 的完整排列提供唯一解，所以修成 4474 並留下原值供 Bot／總帳稽核。
        typo = "黃金 4374-4375多\nTp 4480 4485 4490\nSl 4469"
        target = LineChatTarget(
            "high_freq_yuyu",
            "🈲禁言群🈲 Focus forex 焦點利潤",
            "焦點利潤(yuyu)",
            ("yuyu（yu__o822",),
            parser_profile="yuyu_range_v1",
        )
        chat = ResolvedLineChat(target, "focus-chat-mid", "openchat")
        row = message(
            chat,
            20,
            "bad-entry-message",
            typo,
            sender_id="yuyu-sender-id",
            sender_name="yuyu（yu__o822",
        )
        source = QueueSource([row])
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()

        self.assertEqual(CentralSignalCollector(source, publisher, ledger).run_cycle(), 1)
        self.assertEqual(source.acknowledged, ["bad-entry-message"])
        self.assertEqual(len(publisher.payloads), 1)
        event = publisher.payloads[0]
        self.assertEqual(event["type"], "trade_signal")
        self.assertEqual(event["signal"]["parse_status"], "accepted_point_repaired")
        self.assertEqual(event["signal"]["entry_price"], 4474.0)
        self.assertEqual(event["signal"]["stop_loss"], 4469.0)
        self.assertEqual(event["signal"]["take_profit"], [4480.0, 4485.0, 4490.0])
        self.assertEqual(event["signal"]["repair"], {
            "field": "entry_price",
            "original": [4374.0],
            "corrected": [4474.0],
            "method": "yuyu_hundred_offset_consensus",
        })
        recorded = ledger.message_record(
            "unknown-database", "focus-chat-mid", "bad-entry-message"
        )
        self.assertEqual(recorded["parse_status"], "accepted_point_repaired")

    def test_provider_stop_loss_hundred_offset_is_repaired_and_published(self):
        # 2026-09-01 16:45：進場與三個 TP 都在 4374–4390，只有 SL 高 100。
        typo = "黃金 4374-4375多\nTp 4380 4385 4390\nSl 4469"
        target = LineChatTarget(
            "high_freq_yuyu",
            "🈲禁言群🈲 Focus forex 焦點利潤",
            "焦點利潤(yuyu)",
            ("yuyu（yu__o822",),
            parser_profile="yuyu_range_v1",
        )
        chat = ResolvedLineChat(target, "focus-chat-mid", "openchat")
        row = message(
            chat,
            21,
            "bad-sl-message",
            typo,
            sender_id="yuyu-sender-id",
            sender_name="yuyu（yu__o822",
        )
        publisher = RecordingPublisher()

        self.assertEqual(CentralSignalCollector(QueueSource([row]), publisher).run_cycle(), 1)
        signal = publisher.payloads[0]["signal"]
        self.assertEqual(signal["entry_price"], 4374.0)
        self.assertEqual(signal["stop_loss"], 4369.0)
        self.assertEqual(signal["repair"]["field"], "stop_loss")
        self.assertEqual(signal["repair"]["original"], [4469.0])
        self.assertEqual(signal["repair"]["corrected"], [4369.0])

    def test_non_hundred_geometry_error_still_emits_rejection_notice(self):
        typo = "黃金 4560-4561多\nTp 4555 4560 4565\nSl 4555"
        target = LineChatTarget(
            "high_freq_yuyu",
            "🈲禁言群🈲 Focus forex 焦點利潤",
            "焦點利潤(yuyu)",
            ("yuyu（yu__o822",),
            parser_profile="yuyu_range_v1",
        )
        chat = ResolvedLineChat(target, "focus-chat-mid", "openchat")
        row = message(
            chat,
            22,
            "ambiguous-point-message",
            typo,
            sender_id="yuyu-sender-id",
            sender_name="yuyu（yu__o822",
        )
        publisher = RecordingPublisher()

        self.assertEqual(CentralSignalCollector(QueueSource([row]), publisher).run_cycle(), 0)
        notice = publisher.payloads[0]
        self.assertEqual(notice["type"], "signal_rejected")
        self.assertEqual(notice["parse_status"], "rejected_invalid_geometry")
        self.assertNotIn("repair", notice["signal"])

    def test_provider_commentary_is_not_misreported_as_a_missed_order(self):
        source = QueueSource([
            message(self.chat, 21, "comment-only", "目前不建議追多，先觀望"),
        ])
        publisher = RecordingPublisher()

        self.assertEqual(CentralSignalCollector(source, publisher).run_cycle(), 0)
        self.assertEqual(publisher.payloads, [])
        self.assertEqual(source.acknowledged, ["comment-only"])

    def test_rejection_notice_publish_failure_does_not_acknowledge_line_row(self):
        bad = SIGNAL_TEXT.replace("Sell：4903", "Buy：4903")
        source = QueueSource([message(self.chat, 22, "bad-geometry", bad)])

        with self.assertRaises(OSError):
            CentralSignalCollector(source, RecordingPublisher(fail=True)).run_cycle()
        self.assertEqual(source.acknowledged, [])

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

    def test_stale_backlog_trade_is_not_executed_but_publishes_reason_notice(self):
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
        self.assertEqual(len(publisher.payloads), 1)
        self.assertEqual(publisher.payloads[0]["type"], "signal_rejected")
        self.assertEqual(publisher.payloads[0]["parse_status"], "rejected_stale_backlog")
        self.assertGreaterEqual(publisher.payloads[0]["age_seconds"], 3599)
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

    def test_in_place_unsent_revision_publishes_exact_ledger_cancel_once(self):
        trade = message(self.chat, 16, "recalled-message", SIGNAL_TEXT)
        source = RecallSource(self.chat, [trade])
        source.provider.metadata[trade.message_id] = LineMessageMetadata(
            message_id=trade.message_id,
            revision=1,
            status=1,
            message_type=1,
            reaction_status="",
            text_sha256="normal",
            created_time_ms=trade.created_time_ms,
            attribute=0,
            event_type="",
            unsent=False,
        )
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()
        current_time = [time.time()]
        collector = CentralSignalCollector(
            source,
            publisher,
            ledger,
            clock=lambda: current_time[0],
        )

        self.assertEqual(collector.run_cycle(), 1)
        source.provider.metadata[trade.message_id] = LineMessageMetadata(
            message_id=trade.message_id,
            revision=2,
            status=1,
            message_type=3,
            reaction_status="",
            text_sha256="recalled-empty",
            created_time_ms=trade.created_time_ms,
            attribute=1,
            event_type="20",
            unsent=True,
        )
        current_time[0] += 1

        self.assertEqual(collector.run_cycle(), 1)
        self.assertEqual(len(publisher.payloads), 2)
        trade_event, recall_event = publisher.payloads
        self.assertEqual(recall_event["type"], "cancel_signal")
        self.assertEqual(recall_event["cancel_reason"], "line_unsent")
        self.assertEqual(recall_event["target_line_message_id"], trade.message_id)
        self.assertEqual(recall_event["target_execution_ids"], [trade_event["execution_id"]])
        self.assertEqual(recall_event["target_signals"][0]["direction"], "sell")
        self.assertEqual(recall_event["target_signals"][0]["entry_price"], 4903.0)
        self.assertEqual(recall_event["line_revision"], 2)
        self.assertEqual(recall_event["recall_time_source"], "database_poll_detection")
        self.assertTrue(recall_event["recall_observation_window_started_at"])

        self.assertEqual(collector.run_cycle(), 0)
        recall_record = ledger.recall_record(recall_event["event_id"])
        self.assertEqual(recall_record["state"], "published")

    def test_windows_unsent_without_revision_bump_still_cancels(self):
        # Windows LINE 26.3 收回訊息時就地清空 _text、把 _contentMetadata 設成
        # UNSENT，但 _rev 停在 1（不像 macOS 版會跳到 2）。舊碼要求 revision 增加,
        # 會永遠擋掉這種收回 —— 2026-08-27 yuyu「發錯→收回→重發」時,舊的 4605
        # 掛單就這樣留成幽靈單。這個測試釘住「revision 沒變也要撤單」。
        trade = message(self.chat, 16, "recalled-message", SIGNAL_TEXT)
        source = RecallSource(self.chat, [trade])
        source.provider.metadata[trade.message_id] = LineMessageMetadata(
            message_id=trade.message_id,
            revision=1,
            status=1,
            message_type=1,
            reaction_status="",
            text_sha256="normal",
            created_time_ms=trade.created_time_ms,
            attribute=0,
            event_type="",
            unsent=False,
        )
        publisher = RecordingPublisher()
        ledger = LineMessageLedger()
        current_time = [time.time()]
        collector = CentralSignalCollector(
            source, publisher, ledger, clock=lambda: current_time[0]
        )
        self.assertEqual(collector.run_cycle(), 1)

        # 收回：unsent=True，但 revision 仍是 1（Windows 版的實際行為）
        source.provider.metadata[trade.message_id] = LineMessageMetadata(
            message_id=trade.message_id,
            revision=1,
            status=1,
            message_type=3,
            reaction_status="",
            text_sha256="recalled-empty",
            created_time_ms=trade.created_time_ms,
            attribute=1,
            event_type="20",
            unsent=True,
        )
        current_time[0] += 1

        # ★ 修好之後：revision 沒變也要偵測到收回並發撤單
        self.assertEqual(collector.run_cycle(), 1)
        self.assertEqual(len(publisher.payloads), 2)
        trade_event, recall_event = publisher.payloads
        self.assertEqual(recall_event["type"], "cancel_signal")
        self.assertEqual(recall_event["cancel_reason"], "line_unsent")
        self.assertEqual(recall_event["target_execution_ids"], [trade_event["execution_id"]])
        self.assertEqual(recall_event["line_revision"], 1)

        # 冪等：再輪一次不會重複發撤單（靠 recall_recorded，不靠 revision）
        self.assertEqual(collector.run_cycle(), 0)
        self.assertEqual(len(publisher.payloads), 2)

    def test_normal_message_never_triggers_false_recall(self):
        # 防呆：一則正常、從未收回的訊號，不管輪詢幾次都不該被誤判成收回。
        trade = message(self.chat, 16, "normal-message", SIGNAL_TEXT)
        source = RecallSource(self.chat, [trade])
        source.provider.metadata[trade.message_id] = LineMessageMetadata(
            message_id=trade.message_id,
            revision=1,
            status=1,
            message_type=1,
            reaction_status="",
            text_sha256="normal",
            created_time_ms=trade.created_time_ms,
            attribute=0,
            event_type="",
            unsent=False,
        )
        publisher = RecordingPublisher()
        collector = CentralSignalCollector(source, publisher, LineMessageLedger())
        self.assertEqual(collector.run_cycle(), 1)
        # 又輪三次，unsent 一直是 False → 不該有任何撤單
        for _ in range(3):
            self.assertEqual(collector.run_cycle(), 0)
        self.assertEqual(len(publisher.payloads), 1)  # 只有原本那筆 trade


if __name__ == "__main__":
    unittest.main()
