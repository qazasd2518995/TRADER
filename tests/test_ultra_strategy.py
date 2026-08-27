import tempfile
import json
import os
import time
import unittest
from pathlib import Path

from copy_trader.central.membership import ULTRA_HIGH_FREQ, tier_entitlements
from copy_trader.central.ultra_strategy import (
    StrategyDecision,
    UltraStrategyConfig,
    UltraStrategyEngine,
    evaluate_market,
)
from copy_trader.signal_parser.regex_parser import ParsedSignal
from copy_trader.trade_manager.manager import OrderStatus, TradeManager


def bar(timestamp, close, *, span=2.0, open_price=None, low=None, high=None):
    open_price = close - 0.1 if open_price is None else open_price
    return {
        "t": timestamp,
        "o": open_price,
        "h": close + span / 2 if high is None else high,
        "l": close - span / 2 if low is None else low,
        "c": close,
        "v": 100,
    }


class RecordingPublisher:
    def __init__(self):
        self.events = []

    def publish(self, payload):
        self.events.append(payload)
        return {"ok": True, "published": [payload]}


class UltraStrategyTests(unittest.TestCase):
    @staticmethod
    def market():
        m1 = [bar(i * 60, 4001.8 + (i % 5) * 0.12, span=1.8) for i in range(400)]
        m1[100] = bar(100 * 60, 4000.7, low=3999.8, high=4001.1)
        m1[250] = bar(250 * 60, 4000.8, low=3999.9, high=4001.2)
        m1[-10] = bar(390 * 60, 4003.0, low=4001.4, high=4003.4)
        m1[-1] = bar(399 * 60, 4001.0, open_price=4000.2, low=3999.7, high=4001.4)
        m15 = [bar(i * 900, 3980.0 + i * 0.75, span=4.0) for i in range(40)]
        h1 = [bar(i * 3600, 3950.0 + i * 1.2, span=6.0) for i in range(40)]
        return m1, m15, h1

    def test_trend_pullback_requires_retested_grid_and_emits_limit_geometry(self):
        m1, m15, h1 = self.market()
        decision = evaluate_market(
            m1,
            m15,
            h1,
            {"bid": 4001.0, "ask": 4001.2},
            UltraStrategyConfig(enabled=True),
        )

        self.assertIsInstance(decision, StrategyDecision)
        self.assertEqual(decision.direction, "buy")
        self.assertEqual(decision.setup, "trend_pullback")
        self.assertEqual(decision.entry_price, 4000.0)
        self.assertLess(decision.stop_loss, decision.entry_price)
        self.assertEqual(len(decision.take_profit), 3)
        self.assertGreaterEqual(decision.retest_count, 2)

    def test_engine_publishes_a_separate_real_limit_event(self):
        with tempfile.TemporaryDirectory() as directory:
            publisher = RecordingPublisher()
            engine = UltraStrategyEngine(
                directory,
                publisher,
                Path(directory) / "state.json",
                UltraStrategyConfig(enabled=True),
                clock=lambda: 30_000.0,
            )
            m1, m15, h1 = self.market()
            engine._market_snapshot = lambda: (
                "XAUUSD",
                2,
                {"bid": 4001.0, "ask": 4001.2},
                m1,
                m15,
                h1,
            )

            self.assertEqual(engine.run_cycle(), 1)
            event = publisher.events[0]
            self.assertEqual(event["source"], ULTRA_HIGH_FREQ)
            self.assertEqual(event["source_name"], "ultra_confluence_v1")
            self.assertEqual(event["type"], "trade_signal")
            self.assertEqual(event["signal"]["pending_order_type"], "limit")
            self.assertFalse(event["signal"]["is_market_order"])
            self.assertNotIn("line_message_id", event)

    def test_market_freshness_uses_trading_symbol_not_another_watchlist_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = int(time.time())
            candle = {"t": now - 60, "o": 4000, "h": 4001, "l": 3999, "c": 4000, "tv": 1}
            for timeframe in ("M1", "M15", "H1"):
                (root / f"rates_{timeframe}.json").write_text(
                    json.dumps({"bars": [candle], "digits": 2}), encoding="utf-8"
                )
            (root / "symbol_info.json").write_text(
                json.dumps({"symbol": "XAUUSD", "digits": 2}), encoding="utf-8"
            )
            target = root / "XAUUSD_price.json"
            target.write_text(
                json.dumps({"bid": 4000, "ask": 4000.4, "timestamp": now}), encoding="utf-8"
            )
            (root / "US500_price.json").write_text(
                json.dumps({"bid": 6000, "ask": 6000.5, "timestamp": now}), encoding="utf-8"
            )
            os.utime(target, (now - 200, now - 200))
            engine = UltraStrategyEngine(
                directory,
                RecordingPublisher(),
                root / "state.json",
                UltraStrategyConfig(enabled=True, max_market_age_seconds=90),
                clock=lambda: float(now),
            )

            self.assertIsNone(engine._market_snapshot())

    def test_expiry_publishes_exact_cancel_even_when_generation_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            publisher = RecordingPublisher()
            engine = UltraStrategyEngine(
                directory,
                publisher,
                Path(directory) / "state.json",
                UltraStrategyConfig(enabled=False),
                clock=lambda: 2_000.0,
            )
            engine.state["active"] = {
                "execution_id": "copy_uhf_test",
                "expires_at": 1_999.0,
                "signal": {"direction": "buy", "entry_price": 4000},
            }

            self.assertEqual(engine.run_cycle(), 1)
            event = publisher.events[0]
            self.assertEqual(event["source"], ULTRA_HIGH_FREQ)
            self.assertEqual(event["type"], "cancel_signal")
            self.assertEqual(event["target_execution_ids"], ["copy_uhf_test"])
            self.assertEqual(event["cancel_reason"], "strategy_expired")

    def test_ultra_source_is_separate_and_flagship_only(self):
        self.assertNotIn(ULTRA_HIGH_FREQ, tier_entitlements("advanced")["sources"])
        self.assertIn(ULTRA_HIGH_FREQ, tier_entitlements("flagship")["sources"])

    def test_member_risk_profile_and_limit_type_reach_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = TradeManager(directory)
            manager.source_profiles = {
                ULTRA_HIGH_FREQ: {
                    "enabled": True,
                    "mode": "flat",
                    "base_lot": 0.01,
                    "max_active_orders": 1,
                    "max_daily_trades": 12,
                    "max_daily_loss": 25,
                }
            }
            signal = ParsedSignal(
                is_valid=True,
                direction="buy",
                entry_price=4000,
                pending_order_type="limit",
                stop_loss=3994,
                take_profit=[4006, 4012, 4018],
            )
            commands = []
            manager._write_command = lambda command: commands.append(command) or True
            manager.submit_signal(
                signal,
                auto_execute=True,
                source_window=ULTRA_HIGH_FREQ,
                signal_id="copy_uhf_live",
            )
            self.assertEqual(commands[0]["pending_order_type"], "limit")
            self.assertEqual(manager.profile_for(ULTRA_HIGH_FREQ)["max_active_orders"], 1)
            self.assertEqual(manager.orders["copy_uhf_live"].status, OrderStatus.SENT)

    def test_source_risk_counts_partial_close_position_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = int(time.time())
            (root / "account_info.json").write_text(
                json.dumps({"gmt_offset": 0}), encoding="utf-8"
            )
            (root / "closed_trades.json").write_text(
                json.dumps({
                    "trades": [
                        {
                            "ticket": 101,
                            "position_id": 88,
                            "comment": "copy_copy_uhf_closed",
                            "close_timestamp": now,
                            "profit": -13.0,
                        },
                        {
                            "ticket": 102,
                            "position_id": 88,
                            "comment": "copy_copy_uhf_closed",
                            "close_timestamp": now,
                            "profit": -13.0,
                        },
                    ]
                }),
                encoding="utf-8",
            )
            manager = TradeManager(directory)
            manager._signal_sources["copy_uhf_closed"] = ULTRA_HIGH_FREQ

            snapshot = manager.source_risk_snapshot(ULTRA_HIGH_FREQ)

            self.assertEqual(snapshot["daily_trades"], 1)
            self.assertEqual(snapshot["daily_profit"], -13.0)


if __name__ == "__main__":
    unittest.main()
