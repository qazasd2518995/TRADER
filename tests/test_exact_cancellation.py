from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from copy_trader.central.mt5_client_agent import MT5ClientAgent
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.trade_manager.manager import CancelState, OrderStatus, TradeManager


def signal() -> ParsedSignal:
    return ParsedSignal(
        is_valid=True,
        symbol="XAUUSD",
        direction="sell",
        entry_price=4903.0,
        stop_loss=4915.0,
        take_profit=[4885.0],
    )


class ExactCancellationTests(unittest.TestCase):
    def test_only_named_pending_order_is_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            first = "copy_ln_1111111111111111"
            second = "copy_ln_2222222222222222"
            manager.submit_signal(signal(), auto_execute=False, signal_id=first)
            manager.submit_signal(signal(), auto_execute=False, signal_id=second)
            manager.orders[first].status = OrderStatus.SENT
            manager.orders[first].ticket = 101
            manager.orders[second].status = OrderStatus.SENT
            manager.orders[second].ticket = 202

            deleted = []
            manager._delete_pending_order = lambda ticket, signal_id="": deleted.append((ticket, signal_id)) or True
            self.assertFalse(manager.cancel_pending_order(first))
            self.assertEqual(deleted, [(101, first)])
            self.assertEqual(manager.orders[first].status, OrderStatus.SENT)
            self.assertTrue(manager.orders[first].cancel_delete_sent)
            self.assertEqual(manager.orders[first].cancel_state, CancelState.COMMAND_SENT)
            self.assertEqual(manager.orders[second].status, OrderStatus.SENT)

            manager._get_pending_orders = lambda allow_none=False: [{"ticket": 202}]
            manager._get_positions = lambda allow_none=False: []
            manager.orders[first].vanish_detected_at = time.time() - 5
            manager._check_vanished_orders()

            self.assertEqual(manager.orders[first].status, OrderStatus.CANCELLED)
            self.assertEqual(manager.orders[first].cancel_state, CancelState.MT5_CONFIRMED)
            self.assertTrue(manager.cancel_pending_order(first))

    def test_exact_cancel_never_closes_a_filled_position(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            order_id = "copy_ln_3333333333333333"
            manager.submit_signal(signal(), auto_execute=False, signal_id=order_id)
            manager.orders[order_id].status = OrderStatus.FILLED
            manager.orders[order_id].ticket = 303
            manager._close_position = lambda _ticket: self.fail("filled position was closed")
            self.assertTrue(manager.cancel_pending_order(order_id))

    def test_pending_order_without_ticket_requests_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            order_id = "copy_ln_5555555555555555"
            manager.submit_signal(signal(), auto_execute=False, signal_id=order_id)
            manager.orders[order_id].status = OrderStatus.SENT
            self.assertFalse(manager.cancel_pending_order(order_id))
            self.assertTrue(manager.orders[order_id].cancel_requested)

    def test_mt5_delete_failure_keeps_order_pending_and_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            order_id = "copy_ln_7777777777777777"
            manager.submit_signal(signal(), auto_execute=False, signal_id=order_id)
            order = manager.orders[order_id]
            order.status = OrderStatus.SENT
            order.ticket = 707
            order.cancel_requested = True
            order.cancel_state = CancelState.COMMAND_SENT
            order.cancel_sent_at = time.time()
            (Path(directory) / "trade_results.txt").write_text(
                f"2026.08.27 01:00 | delete | FAIL | ticket:707 | XAUUSD | {order_id} | retcode:10006 | rejected\n",
                encoding="utf-8",
            )

            manager._check_trade_results()

            self.assertEqual(order.status, OrderStatus.SENT)
            self.assertEqual(order.cancel_state, CancelState.FAILED_RETRY)
            sent = []
            manager._delete_pending_order = lambda ticket, signal_id="": sent.append((ticket, signal_id)) or True
            self.assertFalse(manager.cancel_pending_order(order_id))
            self.assertEqual(sent, [(707, order_id)])
            self.assertEqual(order.cancel_state, CancelState.COMMAND_SENT)
            self.assertEqual(order.cancel_attempts, 1)

    def test_same_line_execution_id_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            order_id = "copy_ln_4444444444444444"
            self.assertEqual(manager.submit_signal(signal(), auto_execute=False, signal_id=order_id), order_id)
            self.assertEqual(manager.submit_signal(signal(), auto_execute=False, signal_id=order_id), order_id)
            self.assertEqual(list(manager.orders), [order_id])


class _OneCancelHub:
    hub_url = "https://hub.invalid"

    def signals_after(self, _last_seq):
        return [{
            "seq": 7,
            "type": "cancel_signal",
            "line_message_id": "cancel-message",
            "target_line_message_id": "trade-message",
            "target_execution_ids": ["copy_ln_6666666666666666"],
        }]


class _DeferredManager:
    def cancel_pending_order(self, _signal_id, reason=""):
        return False


class ClientCursorTests(unittest.TestCase):
    def test_cancel_without_ticket_does_not_advance_hub_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = MT5ClientAgent.__new__(MT5ClientAgent)
            agent.hub = _OneCancelHub()
            agent.state_file = Path(directory) / "client-state.json"
            agent.state = {"last_seq": 6}
            agent.trade_manager = _DeferredManager()
            self.assertEqual(agent.run_cycle(), 0)
            self.assertEqual(agent.last_seq, 6)

    def test_filtered_only_batch_advances_to_hub_cursor(self):
        class FilteredHub:
            hub_url = "https://hub.invalid"
            last_cursor = 12

            def signals_after(self, _last_seq):
                return []

        with tempfile.TemporaryDirectory() as directory:
            agent = MT5ClientAgent.__new__(MT5ClientAgent)
            agent.hub = FilteredHub()
            agent.state_file = Path(directory) / "client-state.json"
            agent.state = {"last_seq": 6}
            agent.trade_manager = _DeferredManager()

            self.assertEqual(agent.run_cycle(), 0)
            self.assertEqual(agent.last_seq, 12)


if __name__ == "__main__":
    unittest.main()
