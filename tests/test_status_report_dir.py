"""上報要用 TradeManager 實際在用的 MT5 目錄，不是 settings 的原始值。

實際踩到的問題：會員沒明確設 mt5_files_dir（或填了不存在的路徑）時，Config
會自動偵測出真正的目錄，下單走偵測後的結果、一切正常；但上報那段直接讀
settings，於是帳戶欄位全是 None，後台把人誤判成「未接上 MT5」。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from copy_trader.central import web_launcher


class _StubHub:
    def __init__(self):
        self.payloads = []

    def report_status(self, payload):
        self.payloads.append(payload)
        return True


def _make_state(settings):
    """做一個只夠 _report_member_status 用的假 state。"""
    state = web_launcher.LauncherState.__new__(web_launcher.LauncherState)
    state.role = "client"
    state.settings = settings
    return state


class ReportDirResolutionTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.real = Path(self._dir.name) / "real_mt5"
        self.real.mkdir()
        (self.real / "account_info.json").write_text(json.dumps({
            "login": 441064104, "server": "Exness-MT5Real43", "currency": "USD",
            "balance": 111.9, "equity": 113.44,
        }), encoding="utf-8")
        (self.real / "positions.json").write_text(
            json.dumps({"positions": [{"symbol": "XAUUSDm", "type": "buy",
                                       "volume": 0.01, "profit": 1.5}]}), encoding="utf-8")
        (self.real / "orders.json").write_text(json.dumps({"orders": []}), encoding="utf-8")

    def tearDown(self):
        self._dir.cleanup()

    def _run(self, settings_dir):
        hub = _StubHub()
        agent = SimpleNamespace(
            hub=hub,
            trade_manager=SimpleNamespace(mt5_files_dir=self.real),  # 偵測後的真實目錄
        )
        state = _make_state({"mt5_files_dir": settings_dir})
        state.client_agent = agent
        state._device_label = lambda: "TESTPC"
        web_launcher.LauncherState._report_member_status(state)
        return hub.payloads[-1] if hub.payloads else None

    def test_uses_trade_manager_dir_when_settings_blank(self):
        """settings 空白（靠自動偵測）—— 這正是出事的情境。"""
        payload = self._run("")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["account"]["login"], 441064104)
        self.assertEqual(payload["account"]["balance"], 111.9)
        self.assertEqual(payload["positions_count"], 1)
        self.assertFalse(payload["mt5_stale"])

    def test_uses_trade_manager_dir_when_settings_wrong(self):
        """settings 填了一個不存在的路徑，也要以實際目錄為準。"""
        payload = self._run(r"D:\this\does\not\exist")
        self.assertEqual(payload["account"]["login"], 441064104)
        self.assertFalse(payload["mt5_stale"])

    def test_settings_dir_still_works(self):
        """settings 有正確路徑時（本機那五台就是這樣）行為不變。"""
        payload = self._run(str(self.real))
        self.assertEqual(payload["account"]["login"], 441064104)


if __name__ == "__main__":
    unittest.main()
