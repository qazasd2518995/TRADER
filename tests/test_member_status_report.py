"""會員端上報 MT5 帳戶／持倉快照 → Hub 儲存 → 管理端讀取的端到端測試。

驗證重點：
  * 會員用自己的 session token 才能上報，管理 token / 無 token 都不行。
  * 管理 token 才讀得到全體快照，會員 token 讀不到別人的。
  * 快照欄位(餘額、淨值、浮盈、持倉數、明細)如實流通，且會被後一次覆寫。
"""
from __future__ import annotations

import json
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
    MemberStatusStore,
    SignalStore,
)

ADMIN = "ADMIN_TOKEN"


class MemberStatusReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.members = membership.MemberStore(str(root / "members.db"))
        self.httpd = HubHTTPServer(
            ("127.0.0.1", 0), HubRequestHandler,
            SignalStore(root / "sig.jsonl"), ADMIN, self.members, MemberStatusStore(),
        )
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

        self.members.create_member("alice", "flagship", password="pw12345678")
        member, _err = self.members.login("alice", "pw12345678", device="PC#1", ip="1.2.3.4")
        self.token = member["session_token"]

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.members.close()
        self._dir.cleanup()

    def _call(self, path, token=None, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method="POST" if data is not None else "GET",
            headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_report_requires_member_session(self):
        self.assertEqual(self._call("/report/status", payload={"account": {}})[0], 401)
        self.assertEqual(self._call("/report/status", token=ADMIN, payload={"account": {}})[0], 401)

    def test_report_and_admin_read(self):
        payload = {
            "account": {"currency": "USD", "balance": 3000.0, "equity": 3187.5, "profit": 187.5},
            "positions": [{"symbol": "XAUUSD", "type": "buy", "volume": 0.06, "profit": 187.5}],
            "positions_count": 1, "orders_count": 2, "mt5_stale": False,
        }
        status, body = self._call("/report/status", token=self.token, payload=payload)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))

        status, body = self._call("/admin/members/status", token=ADMIN)
        self.assertEqual(status, 200)
        snap = body["statuses"]["alice"]
        self.assertEqual(snap["account"]["balance"], 3000.0)
        self.assertEqual(snap["account"]["equity"], 3187.5)
        self.assertEqual(snap["account"]["profit"], 187.5)
        self.assertEqual(snap["positions_count"], 1)
        self.assertEqual(snap["orders_count"], 2)
        self.assertEqual(snap["positions"][0]["symbol"], "XAUUSD")
        self.assertGreater(snap["reported_at"], 0)

    def test_member_token_cannot_read_all(self):
        self.assertEqual(self._call("/admin/members/status", token=self.token)[0], 401)

    def test_latest_report_overwrites(self):
        self._call("/report/status", token=self.token,
                   payload={"mt5_stale": False, "account": {"equity": 1.0}})
        self._call("/report/status", token=self.token,
                   payload={"mt5_stale": True, "account": {}})
        _s, body = self._call("/admin/members/status", token=ADMIN)
        self.assertIs(body["statuses"]["alice"]["mt5_stale"], True)


if __name__ == "__main__":
    unittest.main()
