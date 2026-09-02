"""會員不在線上時發布的報單，上線後一律不補掛。

實際踩到的問題：一台離線的會員端累積了 9 筆待補，一開機就會把幾小時前、
早該進場甚至早該結束的單全部掛出去。撤單是刻意保留的例外 —— 他離線前掛的
單可能還在 MT5，期間發的撤單要補上去把風險收掉。
"""
from __future__ import annotations

import json
import time
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from copy_trader.central import membership
from copy_trader.central.hub_server import (
    HubHTTPServer,
    HubRequestHandler,
    MemberPollTracker,
    SignalStore,
)

ADMIN = "ADMIN_TOKEN"


class PollTrackerTests(unittest.TestCase):
    def test_first_poll_is_resume(self):
        t = MemberPollTracker()
        self.assertTrue(t.touch("alice"))       # 第一次 = 剛上線
        self.assertFalse(t.touch("alice"))      # 緊接著的輪詢 = 在線上

    def test_gap_counts_as_resume(self):
        t = MemberPollTracker(resume_gap=0.05)
        t.touch("alice")
        import time as _t
        _t.sleep(0.08)
        self.assertTrue(t.touch("alice"))       # 隔太久 = 離線過

    def test_blank_key_never_resumes(self):
        self.assertFalse(MemberPollTracker().touch(""))


class ResumeNoBackfillTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.store = SignalStore(root / "sig.jsonl")
        self.members = membership.MemberStore(str(root / "members.db"))
        self.tracker = MemberPollTracker()
        self.httpd = HubHTTPServer(
            ("127.0.0.1", 0), HubRequestHandler, self.store, ADMIN,
            self.members, None, None, self.tracker)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        self.members.create_member("alice", "flagship", password="pw12345678")
        member, _err = self.members.login("alice", "pw12345678", device="PC", ip="1.2.3.4")
        self.token = member["session_token"]

    def tearDown(self):
        self.httpd.shutdown()
        self.members.close()
        self._dir.cleanup()

    def _signals(self, after=0):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/signals?after={after}&limit=50")
        req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    def _publish_trade(self, source="焦點利潤(yuyu)"):
        self.store.publish({"type": "trade_signal", "source": source,
                            "signal": {"direction": "buy", "entry_price": 4300,
                                       "stop_loss": 4290, "take_profit": [4310]}})

    def _publish_cancel(self, source="焦點利潤(yuyu)"):
        self.store.publish({"type": "cancel_signal", "source": source,
                            "target_execution_ids": ["exec_1"]})

    def test_backlog_is_not_backfilled_on_first_poll(self):
        # 會員離線期間累積了 3 筆報單
        for _ in range(3):
            self._publish_trade()
        body = self._signals()
        self.assertTrue(body["resumed"])
        self.assertEqual(body["skipped_stale"], 3)
        self.assertEqual(body["signals"], [])          # 一筆都不補掛
        # 游標仍要前進，否則會員端會卡在同一批
        self.assertEqual(body["cursor"], self.store.latest_seq)

    def test_cancel_survives_resume(self):
        """撤單是例外：離線前掛的單可能還在，期間的撤單要補。"""
        self._publish_trade()
        self._publish_cancel()
        body = self._signals()
        self.assertTrue(body["resumed"])
        self.assertEqual(len(body["signals"]), 1)
        self.assertEqual(body["signals"][0]["type"], "cancel_signal")
        self.assertEqual(body["skipped_stale"], 1)     # 只有那筆報單被丟掉

    def test_stale_signal_blocked_even_while_online(self):
        """在線上但卡住的會員（例如 MT5 沒開、撤單一直重試）恢復後，
        也不能補掛早就過期的報單 —— 這是 poll tracker 擋不住的情況。"""

        first = self._signals()                 # 先讓他「上線」
        self.assertTrue(first["resumed"])
        self._publish_trade()
        # 把這筆的發布時間改成 2 小時前
        with self.store._lock:
            self.store._records[-1]["published_at"] = time.time() - 7200

        body = self._signals(after=first["cursor"])
        self.assertFalse(body["resumed"])       # 他確實在線上
        self.assertEqual(body["signals"], [])   # 但太舊的報單仍不下發
        self.assertEqual(body["skipped_stale"], 1)
        self.assertEqual(body["cursor"], self.store.latest_seq)

    def test_stale_cancel_still_delivered(self):
        """撤單即使很舊也要送 —— 那張掛單可能還在 MT5 上。"""
        first = self._signals()
        self._publish_cancel()
        with self.store._lock:
            self.store._records[-1]["published_at"] = time.time() - 7200
        body = self._signals(after=first["cursor"])
        self.assertEqual(len(body["signals"]), 1)
        self.assertEqual(body["signals"][0]["type"], "cancel_signal")

    def test_signals_flow_normally_once_online(self):
        """上線之後發布的訊號要照常收到 —— 不能因為這個機制而漏掉。"""
        first = self._signals()                        # 這一次被判定為剛上線
        self.assertTrue(first["resumed"])
        self._publish_trade()                          # 上線之後才發布
        body = self._signals(after=first["cursor"])
        self.assertFalse(body["resumed"])
        self.assertEqual(len(body["signals"]), 1)
        self.assertEqual(body["skipped_stale"], 0)
        self.assertEqual(body["signals"][0]["type"], "trade_signal")


if __name__ == "__main__":
    unittest.main()
