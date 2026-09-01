"""本金比例動態手數：資金基準、0.01 下限、權限與 Hub cursor 回歸測試。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from copy_trader.central.membership import MID_FREQ, tier_entitlements
from copy_trader.central.mt5_client_agent import MT5ClientAgent
from copy_trader.central.web_launcher import LauncherState
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.trade_manager.manager import LotSizingError, OrderStatus, TradeManager


def signal(*, entry=4000.0, stop=3990.0, direction="buy", market=False):
    return ParsedSignal(
        is_valid=True,
        direction=direction,
        entry_price=None if market else entry,
        is_market_order=market,
        stop_loss=stop,
        take_profit=[4010.0] if direction == "buy" else [3980.0],
    )


class DynamicLotTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.manager = TradeManager(self._temp.name)
        self.manager.source_profiles = {
            MID_FREQ: {
                "enabled": True,
                "mode": "risk_percent",
                "risk_percent": 0.5,
                "tp_mode": "single",
            }
        }
        self._write_account(balance=10_000, equity=8_000)
        self._write_symbol()

    def tearDown(self):
        self._temp.cleanup()

    def _write_account(self, **overrides):
        data = {"balance": 10_000, "equity": 10_000, "terminal_connected": True}
        data.update(overrides)
        (self.root / "account_info.json").write_text(json.dumps(data), encoding="utf-8")

    def _write_symbol(self, **overrides):
        data = {
            "symbol": "XAUUSD",
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "tick_size": 0.01,
            "tick_value": 1.0,
            "tick_value_loss": 1.0,
        }
        data.update(overrides)
        (self.root / "symbol_info.json").write_text(json.dumps(data), encoding="utf-8")

    def test_uses_lower_of_balance_and_equity(self):
        # min(10000, 8000) * 0.5% = $40 risk.  A $10 XAU stop costs
        # 10 / 0.01 * $1 = $1000 per lot, therefore the order is 0.04 lot.
        self.assertEqual(self.manager.calculate_dynamic_lot(signal(), MID_FREQ), 0.04)

    def test_always_rounds_down_to_volume_step(self):
        self.manager.source_profiles[MID_FREQ]["risk_percent"] = 0.37
        # $29.60 / $1000 = 0.0296; never round up past the budget.
        self.assertEqual(self.manager.calculate_dynamic_lot(signal(), MID_FREQ), 0.02)

    def test_uses_loss_tick_value_and_broker_max(self):
        self.manager.source_profiles[MID_FREQ]["risk_percent"] = 5
        self._write_symbol(tick_value=0.5, tick_value_loss=1.0, volume_max=0.30)
        # Raw result is 0.40, capped to broker max 0.30. tick_value_loss wins
        # over the less conservative generic tick_value.
        self.assertEqual(self.manager.calculate_dynamic_lot(signal(), MID_FREQ), 0.30)

    def test_market_buy_uses_ask_not_mid_price(self):
        self.manager.source_profiles[MID_FREQ]["risk_percent"] = 1
        self._write_account(balance=10_000, equity=10_000)
        (self.root / "XAUUSD_price.json").write_text(
            json.dumps({"bid": 3999.0, "ask": 4001.0}), encoding="utf-8"
        )
        # ask 4001 to SL 3991 = $10, so $100 risk => 0.10 lot.
        self.assertEqual(
            self.manager.calculate_dynamic_lot(signal(stop=3991, market=True), MID_FREQ),
            0.10,
        )

    def test_below_point_zero_one_is_permanently_rejected_without_command(self):
        self._write_account(balance=1_000, equity=1_000)
        commands = []
        self.manager._write_command = lambda command: commands.append(command) or True
        signal_id = self.manager.submit_signal(
            signal(), auto_execute=True, source_window=MID_FREQ, signal_id="copy_dyn_too_small"
        )
        order = self.manager.get_order_status(signal_id)
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("0.01", order.failure_reason)
        self.assertEqual(commands, [])

    def test_stale_account_file_is_transient_and_retriable(self):
        old = time.time() - self.manager.DYNAMIC_ACCOUNT_STALE_SECONDS - 1
        os.utime(self.root / "account_info.json", (old, old))
        with self.assertRaises(LotSizingError) as caught:
            self.manager.calculate_dynamic_lot(signal(), MID_FREQ)
        self.assertTrue(caught.exception.transient)

    def test_successful_order_sends_calculated_lot(self):
        commands = []
        self.manager._write_command = lambda command: commands.append(command) or True
        signal_id = self.manager.submit_signal(
            signal(), auto_execute=True, source_window=MID_FREQ, signal_id="copy_dyn_live"
        )
        self.assertEqual(self.manager.get_order_status(signal_id).status, OrderStatus.SENT)
        self.assertEqual(commands[0]["lot_size"], 0.04)

    def test_profile_caps_injected_risk_percent_at_five(self):
        self.manager.source_profiles[MID_FREQ]["risk_percent"] = 99
        self.assertEqual(self.manager.profile_for(MID_FREQ)["risk_percent"], 5.0)


class DynamicLotEntitlementTests(unittest.TestCase):
    def test_only_flagship_has_dynamic_lot(self):
        self.assertFalse(tier_entitlements("trial")["dynamic_lot"])
        self.assertFalse(tier_entitlements("basic")["dynamic_lot"])
        self.assertFalse(tier_entitlements("advanced")["dynamic_lot"])
        self.assertTrue(tier_entitlements("flagship")["dynamic_lot"])

    def test_backend_clamps_non_flagship_forged_setting(self):
        state = LauncherState.__new__(LauncherState)
        state.role = "client"
        state.auth = {"entitlements": tier_entitlements("advanced")}
        result = state._clamp_to_entitlements({
            MID_FREQ: {"enabled": True, "mode": "risk_percent", "risk_percent": 5}
        })
        self.assertEqual(result[MID_FREQ]["mode"], "flat")

    def test_flagship_keeps_dynamic_setting(self):
        state = LauncherState.__new__(LauncherState)
        state.role = "client"
        state.auth = {"entitlements": tier_entitlements("flagship")}
        result = state._clamp_to_entitlements({
            MID_FREQ: {"enabled": True, "mode": "risk_percent", "risk_percent": 0.5}
        })
        self.assertEqual(result[MID_FREQ]["mode"], "risk_percent")


class _RejectedManager:
    def is_source_enabled(self, _source):
        return True

    def source_risk_snapshot(self, source):
        return {"source": source, "active_orders": 0, "daily_trades": 0, "daily_profit": 0}

    def profile_for(self, _source):
        return {"max_active_orders": 0, "max_daily_trades": 0,
                "max_daily_loss": 0, "max_daily_profit": 0}

    def submit_signal(self, signal, *, auto_execute, source_window, signal_id):
        self.order = type("Order", (), {
            "status": OrderStatus.REJECTED,
            "failure_reason": "低於 0.01 手",
        })()
        return signal_id

    def get_order_status(self, _signal_id):
        return self.order


class _OneSignalHub:
    hub_url = "https://hub.invalid"
    last_cursor = 1

    def signals_after(self, _cursor):
        return [{
            "seq": 1,
            "type": "trade_signal",
            "source": MID_FREQ,
            "execution_id": "copy_dyn_rejected",
            "signal": {
                "symbol": "XAUUSD",
                "direction": "buy",
                "entry_price": 4000,
                "stop_loss": 3990,
                "take_profit": [4010],
            },
        }]


class RejectedCursorTests(unittest.TestCase):
    def test_permanent_risk_rejection_advances_cursor_without_accept_count(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = MT5ClientAgent.__new__(MT5ClientAgent)
            agent.hub = _OneSignalHub()
            agent.state_file = Path(directory) / "state.json"
            agent.state = {"last_seq": 0}
            agent.trade_manager = _RejectedManager()
            self.assertEqual(agent.run_cycle(), 0)
            self.assertEqual(agent.last_seq, 1)
            self.assertNotIn("source_daily_accepts", agent.state)


if __name__ == "__main__":
    unittest.main()
