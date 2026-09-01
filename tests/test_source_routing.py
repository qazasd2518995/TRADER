from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from copy_trader.central.membership import (
    HIGH_FREQ,
    LOW_FREQ,
    MID_FREQ,
    ULTRA_HIGH_FREQ,
    filter_signals_for,
    tier_entitlements,
)
from copy_trader.central.mt5_client_agent import HubClient, MT5ClientAgent
from copy_trader.central.stats import source_settings
from copy_trader.central.webui import render


def trade_event(seq: int, source: str, execution_id: str) -> dict:
    return {
        "seq": seq,
        "type": "trade_signal",
        "source": source,
        "execution_id": execution_id,
        "signal": {
            "symbol": "XAUUSD",
            "direction": "sell",
            "entry_price": 4070.0,
            "stop_loss": 4076.0,
            "take_profit": [4065.0, 4060.0, 4055.0],
        },
    }


class RecordingManager:
    def __init__(self):
        self.submitted = []

    def is_source_enabled(self, source: str) -> bool:
        return True

    def submit_signal(self, signal, *, auto_execute, source_window, signal_id):
        self.submitted.append((source_window, signal_id, signal))
        return signal_id

    def get_order_status(self, _signal_id):
        return None


class TwoSourceHub:
    hub_url = "https://hub.invalid"
    last_cursor = 2

    def signals_after(self, _last_seq):
        return [
            trade_event(1, MID_FREQ, "copy_ln_mid0000000001"),
            trade_event(2, HIGH_FREQ, "copy_ln_high000000001"),
        ]


class RejectionThenTradeHub:
    hub_url = "https://hub.invalid"
    last_cursor = 2

    def signals_after(self, _last_seq):
        return [
            {
                "seq": 1,
                "type": "signal_rejected",
                "source": HIGH_FREQ,
                "parse_status": "rejected_invalid_geometry",
                "signal": {
                    "direction": "buy", "entry_price": 4374.0,
                    "stop_loss": 4469.0, "take_profit": [4480.0],
                },
            },
            trade_event(2, HIGH_FREQ, "copy_ln_after_reject"),
        ]


class SourceRoutingTests(unittest.TestCase):
    def test_membership_tiers_filter_mid_and_high_frequency_sources(self):
        records = [
            trade_event(1, MID_FREQ, "mid"),
            trade_event(2, HIGH_FREQ, "high"),
        ]

        basic = tier_entitlements("basic")["sources"]
        advanced = tier_entitlements("advanced")["sources"]
        flagship = tier_entitlements("flagship")["sources"]

        self.assertEqual([item["source"] for item in filter_signals_for(records, basic)], [MID_FREQ])
        self.assertEqual(
            [item["source"] for item in filter_signals_for(records, advanced)],
            [MID_FREQ, HIGH_FREQ],
        )
        self.assertEqual(flagship, [MID_FREQ, HIGH_FREQ, ULTRA_HIGH_FREQ, LOW_FREQ])

    def test_hub_client_retains_filtered_page_cursor(self):
        client = HubClient("https://hub.invalid")
        client._request = lambda _path: {
            "ok": True,
            "cursor": 50,
            "signals": [],
            "filtered": 50,
        }

        self.assertEqual(client.signals_after(0), [])
        self.assertEqual(client.last_cursor, 50)

    def test_member_agent_preserves_product_source_when_submitting_to_mt5(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = MT5ClientAgent.__new__(MT5ClientAgent)
            agent.hub = TwoSourceHub()
            agent.state_file = Path(directory) / "client-state.json"
            agent.state = {"last_seq": 0}
            agent.trade_manager = RecordingManager()

            self.assertEqual(agent.run_cycle(), 2)
            self.assertEqual(agent.last_seq, 2)
            self.assertEqual(
                [source for source, _signal_id, _signal in agent.trade_manager.submitted],
                [MID_FREQ, HIGH_FREQ],
            )

    def test_member_agent_skips_rejection_notice_and_continues_to_next_trade(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = MT5ClientAgent.__new__(MT5ClientAgent)
            agent.hub = RejectionThenTradeHub()
            agent.state_file = Path(directory) / "client-state.json"
            agent.state = {"last_seq": 0}
            agent.trade_manager = RecordingManager()

            self.assertEqual(agent.run_cycle(), 1)
            self.assertEqual(agent.last_seq, 2)
            self.assertEqual(len(agent.trade_manager.submitted), 1)
            self.assertEqual(agent.trade_manager.submitted[0][1], "copy_ln_after_reject")

    def test_authorized_sources_appear_before_first_trade(self):
        rows = source_settings(
            {"source_profiles": "{}", "default_lot_size": "0.01"},
            [],
            {},
            {},
            known_sources=tier_entitlements("advanced")["sources"],
        )

        self.assertEqual([row["source"] for row in rows], [MID_FREQ, HIGH_FREQ])
        self.assertTrue(all(row["enabled"] for row in rows))

    def test_member_ui_aliases_use_the_same_product_source_keys(self):
        class ClientState:
            role = "client"
            title = "test"
            auth = {"username": "member"}

        html = render(ClientState())

        self.assertIn(f'"{MID_FREQ}": "中頻交易"', html)
        self.assertIn(f'"{HIGH_FREQ}": "高頻交易"', html)
        self.assertIn(f'"{ULTRA_HIGH_FREQ}": "超高頻交易"', html)
        self.assertNotIn("__HIGH_FREQ_SOURCE_JSON__", html)
        self.assertNotIn("__ULTRA_HIGH_FREQ_SOURCE_JSON__", html)


if __name__ == "__main__":
    unittest.main()
