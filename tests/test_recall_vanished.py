"""訊息從資料庫整列消失，也要算成收回。

2026-09-07 實際事故：中頻 14:04:28 貼了「Buy 4388 / 止損 4380 / 止盈 4405」，
31 秒後收回改貼 4385。那一列被整個刪掉，fetch_message_metadata 的
WHERE _id IN (...) 就查不到它，原本的 `if metadata is None: continue` 把它當成
「沒事」跳過 —— 4388 那張單留在會員那裡變成幽靈單，來源早就撤回了。

這是收回的第三種形態：
  macOS 版        _rev 1→2、列還在
  Windows 26.3    _rev 停在 1、列還在但標記 unsent
  這次            列整個不見

誤撤一張有效掛單比漏撤更糟，所以一半的測試在「該撤的有撤」，
另一半在「不該撤的絕對不能撤」。
"""
from __future__ import annotations

import time
import unittest

from copy_trader.central.signal_collector import CentralSignalCollector
from copy_trader.line_db.ledger import LineMessageLedger
from copy_trader.line_db.models import LineChatTarget, LineMessageMetadata, ResolvedLineChat

from tests.test_line_db_pipeline import (
    SIGNAL_TEXT,
    RecallSource,
    RecordingPublisher,
    message,
)


def _present(msg, *, unsent: bool = False, revision: int = 1) -> LineMessageMetadata:
    return LineMessageMetadata(
        message_id=msg.message_id,
        revision=revision,
        status=1,
        message_type=1,
        reaction_status="",
        text_sha256="normal",
        created_time_ms=msg.created_time_ms,
        attribute=0,
        event_type="",
        unsent=unsent,
    )


class _Fixture(unittest.TestCase):
    def setUp(self):
        target = LineChatTarget(
            "gold", "（乘）黃金報單🈲言群", "黃金報單🈲言群", ("乘", "James"))
        self.chat = ResolvedLineChat(target, "chat-mid", "openchat")
        self.trade = message(self.chat, 16, "vanished-message", SIGNAL_TEXT)
        # 同一個聊天室裡另一則訊息，用來證明「資料庫本身讀得到」
        self.other = message(self.chat, 17, "other-message", SIGNAL_TEXT)
        self.source = RecallSource(self.chat, [self.trade])
        # 真實的 provider 有 latest_rowid，收回偵測靠它判斷資料庫還讀得到
        self.source.provider.latest_rowid = lambda _chat: 99
        self.source.provider.metadata[self.trade.message_id] = _present(self.trade)
        self.publisher = RecordingPublisher()
        self.ledger = LineMessageLedger()
        self.now = [time.time()]
        self.collector = CentralSignalCollector(
            self.source, self.publisher, self.ledger, clock=lambda: self.now[0])
        # 先把訊號發出去，它才會進入收回觀察範圍
        self.assertEqual(self.collector.run_cycle(), 1)
        self.trade_event = self.publisher.payloads[0]

    def _vanish(self):
        """訊息整列消失，但同批還有別的讀得到。"""
        self.source.provider.metadata.pop(self.trade.message_id, None)
        self.source.provider.metadata[self.other.message_id] = _present(self.other)

    def _tick(self, seconds=5):
        self.now[0] += seconds
        return self.collector.run_cycle()


class VanishedIsARecallTests(_Fixture):
    def test_first_disappearance_only_observes(self):
        """第一次查不到先觀察，不急著撤 —— 可能只是 LINE 正在改寫那一列。"""
        self._vanish()
        self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 1)      # 只有原本那筆訊號

    def test_second_disappearance_publishes_cancel(self):
        self._vanish()
        self._tick()                                            # 第一輪：觀察
        self.assertEqual(self._tick(), 1)                       # 第二輪：確認消失
        self.assertEqual(len(self.publisher.payloads), 2)
        recall = self.publisher.payloads[1]
        self.assertEqual(recall["type"], "cancel_signal")
        self.assertEqual(recall["cancel_reason"], "line_deleted")
        self.assertEqual(recall["target_line_message_id"], self.trade.message_id)
        self.assertEqual(recall["target_execution_ids"],
                         [self.trade_event["execution_id"]])
        # 讀不到版本號時用 -1 而不是 None：帳本那欄是 INTEGER NOT NULL
        self.assertEqual(recall["line_revision"], -1)

    def test_ledger_write_does_not_violate_not_null(self):
        """真的寫進帳本。observed_revision 是 NOT NULL，傳 None 會在這裡炸。"""
        self._vanish()
        self._tick()
        self._tick()
        record = self.ledger.recall_record(self.publisher.payloads[1]["event_id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], "published")
        self.assertEqual(record["observed_revision"], -1)

    def test_does_not_publish_twice(self):
        self._vanish()
        self._tick()
        self._tick()
        self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 2)


class DoNotCancelValidOrdersTests(_Fixture):
    """誤撤一張有效掛單比漏撤更糟，這幾種情況一張都不能撤。"""

    def test_unreadable_database_cancels_nothing(self):
        """資料庫被鎖住／換帳號換了檔／金鑰不對 —— 探測本身就失敗。

        這是最危險的情境：整個資料庫讀不到時，如果把「查不到」當成收回，
        會一次撤掉觀察窗裡所有有效掛單。
        """
        self.source.provider.metadata.clear()

        def _boom(_chat):
            raise OSError("database is locked")

        self.source.provider.latest_rowid = _boom
        for _ in range(3):
            self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 1)

    def test_probe_returning_zero_cancels_nothing(self):
        """探測回 0（讀到空的／換了新檔）也不能當成訊息被刪。"""
        self.source.provider.metadata.clear()
        self.source.provider.latest_rowid = lambda _chat: 0
        for _ in range(3):
            self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 1)

    def test_vanished_with_healthy_database_does_cancel(self):
        """對照組：資料庫探得到、只有那一則不見 —— 這才該撤。

        觀察窗裡只有這一則訊息，所以批次查詢會是空的；靠獨立探測才判得出來。
        """
        self.source.provider.metadata.clear()      # 批次查不到任何東西
        self._tick()
        self.assertEqual(self._tick(), 1)
        self.assertEqual(self.publisher.payloads[1]["cancel_reason"], "line_deleted")

    def test_message_that_comes_back_is_not_cancelled(self):
        """第一輪讀不到、第二輪又出現 —— 那只是讀取瞬間的競態。"""
        self._vanish()
        self._tick()                                            # 觀察中
        self.source.provider.metadata[self.trade.message_id] = _present(self.trade)
        self.assertEqual(self._tick(), 0)
        self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 1)

    def test_present_and_not_unsent_is_untouched(self):
        for _ in range(3):
            self.assertEqual(self._tick(), 0)
        self.assertEqual(len(self.publisher.payloads), 1)


class UnsentStillWorksTests(_Fixture):
    """原本那條路（列還在但標記 unsent）不能被改壞。"""

    def test_unsent_publishes_immediately_without_waiting(self):
        self.source.provider.metadata[self.trade.message_id] = _present(
            self.trade, unsent=True, revision=2)
        self.assertEqual(self._tick(), 1)                       # 不用等第二輪
        recall = self.publisher.payloads[1]
        self.assertEqual(recall["cancel_reason"], "line_unsent")
        self.assertEqual(recall["line_revision"], 2)


if __name__ == "__main__":
    unittest.main()
