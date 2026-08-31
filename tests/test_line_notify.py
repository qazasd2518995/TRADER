"""LINE 群組通知：訊號格式化、group id 登記/webhook、簽名驗證、發布廣播。

不會真的打 LINE API —— push 用 monkeypatch 攔截，只驗證「該推什麼、推到哪」。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from copy_trader.central.hub_server import (
    HubHTTPServer,
    HubRequestHandler,
    LineNotifyState,
    SignalStore,
    format_signal_notice,
)


class FormatSignalTests(unittest.TestCase):
    def test_sell_signal(self):
        text = format_signal_notice({
            "type": "trade_signal", "source": "焦點利潤(yuyu)", "message_time": "14:02",
            "signal": {"symbol": "XAUUSD", "direction": "sell",
                       "entry_price": 4445, "stop_loss": 4455, "take_profit": [4430]},
        })
        self.assertIn("賣出 SELL", text)
        self.assertIn("進場 4445", text)
        self.assertIn("止損 4455", text)
        self.assertIn("止盈 4430", text)
        self.assertIn("14:02", text)

    def test_multi_tp_joined(self):
        text = format_signal_notice({
            "type": "trade_signal",
            "signal": {"direction": "buy", "entry_price": 4434,
                       "stop_loss": 4429, "take_profit": [4440, 4445, 4450]},
        })
        self.assertIn("買進 BUY", text)
        self.assertIn("4440／4445／4450", text)

    def test_no_entry_is_not_notified(self):
        self.assertIsNone(format_signal_notice({
            "type": "trade_signal", "signal": {"direction": "buy", "entry_price": None}}))

    def test_cancel_signal(self):
        text = format_signal_notice({
            "type": "cancel_signal", "source": "黃金報單🈲言群", "cancel_reason": "quote_recall",
            "target_signals": [{"entry_price": 4445}]})
        self.assertIn("撤單", text)
        self.assertIn("引用撤單", text)
        self.assertIn("4445", text)


class LineStateTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "line.json"

    def tearDown(self):
        self._dir.cleanup()

    def test_remember_forget_persist(self):
        s = LineNotifyState(self.path, token="T")
        s.remember_group("Gaaa")
        s.remember_group("Gaaa")   # 重複不重覆記
        s.remember_group("Gbbb")
        self.assertCountEqual(s.target_groups(), ["Gaaa", "Gbbb"])
        # 重新載入應保留
        self.assertCountEqual(LineNotifyState(self.path, token="T").target_groups(),
                              ["Gaaa", "Gbbb"])
        s.forget_group("Gaaa")
        self.assertEqual(s.target_groups(), ["Gbbb"])

    def test_disabled_without_token(self):
        self.assertFalse(LineNotifyState(self.path).enabled)
        self.assertTrue(LineNotifyState(self.path, token="T").enabled)

    def test_signature(self):
        s = LineNotifyState(self.path, token="T", secret="sekret")
        body = b'{"events":[]}'
        good = base64.b64encode(hmac.new(b"sekret", body, hashlib.sha256).digest()).decode()
        self.assertTrue(s.verify_signature(body, good))
        self.assertFalse(s.verify_signature(body, "wrong"))
        # 未設 secret 回 None（呼叫端自行決定放行）
        self.assertIsNone(LineNotifyState(self.path, token="T").verify_signature(body, good))

    def test_push_noop_without_token(self):
        self.assertEqual(LineNotifyState(self.path).push_text("hi"), 0)


class WebhookAndBroadcastTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.line = LineNotifyState(root / "line.json", token="TESTTOKEN")
        self.pushed = []
        # 攔截實際 HTTP push，只記錄 (group_id, text)
        self.line._push_one = lambda gid, text: (self.pushed.append((gid, text)) or True)
        self.httpd = HubHTTPServer(
            ("127.0.0.1", 0), HubRequestHandler,
            SignalStore(root / "sig.jsonl"), "ADMIN", None, None, self.line)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self._dir.cleanup()

    def _post(self, path, payload, token=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_webhook_registers_and_removes_group(self):
        self._post("/line/webhook", {"events": [
            {"type": "join", "source": {"type": "group", "groupId": "Gxyz"}}]})
        self.assertIn("Gxyz", self.line.target_groups())
        self._post("/line/webhook", {"events": [
            {"type": "leave", "source": {"type": "group", "groupId": "Gxyz"}}]})
        self.assertNotIn("Gxyz", self.line.target_groups())

    def test_publish_broadcasts_to_group(self):
        self.line.remember_group("Gmembers")
        self._post("/signals", {"signals": [{
            "type": "trade_signal", "source": "yuyu", "message_time": "14:02",
            "signal": {"direction": "sell", "entry_price": 4445,
                       "stop_loss": 4455, "take_profit": [4430]}}]}, token="ADMIN")
        # broadcast 在背景 thread，給它一點時間
        for _ in range(50):
            if self.pushed:
                break
            import time
            time.sleep(0.05)
        self.assertEqual(len(self.pushed), 1)
        gid, text = self.pushed[0]
        self.assertEqual(gid, "Gmembers")
        self.assertIn("賣出 SELL", text)
        self.assertIn("4445", text)


if __name__ == "__main__":
    unittest.main()
