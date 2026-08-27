"""掛單逾時未成交自動撤單的回歸測試。

需求(2026-08-28):限價掛單掛超過 4 小時還沒進場,就自動撤掉——等同一次撤單,
但由時間觸發。只能碰未成交的掛單,已成交部位絕對不能被平掉。
"""
import time
import tempfile
import unittest

from copy_trader.trade_manager.manager import (
    TradeManager, ManagedOrder, OrderStatus, CancelState,
)
from copy_trader.signal_parser.regex_parser import ParsedSignal


def _signal():
    return ParsedSignal(
        is_valid=True, symbol="XAUUSD", direction="buy",
        entry_price=4450.0, stop_loss=4440.0, take_profit=[4460.0],
        confidence=1.0,
    )


def _order(signal_id, status, age_seconds, ticket=111):
    order = ManagedOrder(signal_id=signal_id, signal=_signal(), status=status, ticket=ticket)
    order.created_at = time.time() - age_seconds
    return order


class UnfilledTimeoutTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.mgr = TradeManager(self._dir.name)
        self.cancelled = []
        # 攔截真正的撤單動作(它會去讀 orders.json / 寫 commands.json),只記下被要求撤誰
        self.mgr.cancel_pending_order = lambda sid, reason="": self.cancelled.append((sid, reason)) or True

    def tearDown(self):
        self._dir.cleanup()

    def _run(self, *orders):
        with self.mgr._lock:
            for o in orders:
                self.mgr.orders[o.signal_id] = o
        self.mgr._check_unfilled_timeout()

    def test_pending_order_past_4h_is_cancelled(self):
        self._run(_order("s1", OrderStatus.PENDING, age_seconds=4 * 3600 + 60))
        self.assertEqual([sid for sid, _ in self.cancelled], ["s1"])
        self.assertEqual(self.cancelled[0][1], "unfilled_timeout_4h")

    def test_sent_order_past_4h_is_cancelled(self):
        # 認領自 MT5 的既有掛單狀態是 SENT,一樣要能逾時撤
        self._run(_order("s2", OrderStatus.SENT, age_seconds=5 * 3600))
        self.assertEqual([sid for sid, _ in self.cancelled], ["s2"])

    def test_recent_pending_order_is_left_alone(self):
        self._run(_order("s3", OrderStatus.PENDING, age_seconds=3 * 3600))  # 只掛 3 小時
        self.assertEqual(self.cancelled, [])

    def test_filled_position_is_never_touched_even_if_old(self):
        # 已成交部位就算掛牌時間很久,也絕對不能被自動撤/平
        self._run(_order("s4", OrderStatus.FILLED, age_seconds=10 * 3600))
        self.assertEqual(self.cancelled, [])

    def test_boundary_just_under_4h_not_cancelled(self):
        self._run(_order("s5", OrderStatus.PENDING, age_seconds=4 * 3600 - 30))
        self.assertEqual(self.cancelled, [])

    def test_mixed_batch_only_stale_unfilled_cancelled(self):
        self._run(
            _order("old_pending", OrderStatus.PENDING, age_seconds=6 * 3600),
            _order("new_pending", OrderStatus.PENDING, age_seconds=600),
            _order("old_filled", OrderStatus.FILLED, age_seconds=6 * 3600),
        )
        self.assertEqual([sid for sid, _ in self.cancelled], ["old_pending"])


if __name__ == "__main__":
    unittest.main()
