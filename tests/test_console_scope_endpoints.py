"""Hub 端點對兩種連線的把關。

`test_session_scopes.py` 驗的是 membership 那一層（哪一格、誰踢誰）。這裡驗的是
**Hub 真的有把控制台擋在訊號外面** —— 那是付費閘門，光是資料層分得開沒有用，
端點放行了就等於沒分。

最重要的一條：拿手機那條 session 去要 /signals，必須拿不到任何一筆訊號。
破了的話帳號分享就有利可圖 —— 一個人跟單，其他人用手機把訊號抄走。
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


class _HubCase(unittest.TestCase):
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

        # flagship = 每個來源都有權限，這樣「拿不到訊號」只可能是 scope 擋的
        self.members.create_member("alice", "flagship", password="pw12345678")
        self.agent = self._login(membership.SCOPE_AGENT, "PC#1")
        self.console = self._login(membership.SCOPE_CONSOLE, "iPhone")

        self._publish({
            "type": "trade_signal", "execution_id": "e1",
            "source": membership.HIGH_FREQ,
            "signal": {"symbol": "XAUUSD", "direction": "buy", "entry_price": 4400.0,
                       "stop_loss": 4390.0, "take_profit": [4410.0]},
        })

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.members.close()
        self._dir.cleanup()

    def _login(self, scope, device):
        member, err = self.members.login(
            "alice", "pw12345678", device=device, ip="1.2.3.4", scope=scope)
        assert err == "", err
        return member["session_token"]

    def _call(self, path, token=None, payload=None, method=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def _publish(self, signal):
        status, _ = self._call("/signals", token=ADMIN, payload=signal)
        assert status == 200, status


class SignalsAreAgentOnlyTests(_HubCase):
    def test_agent_gets_the_signal(self):
        """對照組：同一個帳號、同一筆訊號，電腦那條線拿得到。"""
        status, body = self._call("/signals?after=0", token=self.agent)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["signals"]), 1)

    def test_console_is_refused(self):
        """主角：手機那條線一筆都拿不到。"""
        status, body = self._call("/signals?after=0", token=self.console)
        self.assertEqual(status, 401, "控制台 session 拿到訊號了 —— 付費閘門破了")
        self.assertFalse(body.get("ok"))
        self.assertNotIn("signals", body)

    def test_refusal_reason_is_specific(self):
        """錯誤碼要講得出「用錯連線」，不然會有人對著 session_invalid 找一整天。"""
        _status, body = self._call("/signals?after=0", token=self.console)
        self.assertEqual(body.get("error"), "console_session_not_allowed")

    def test_console_refusal_does_not_break_the_agent(self):
        self._call("/signals?after=0", token=self.console)
        status, body = self._call("/signals?after=0", token=self.agent)
        self.assertEqual(status, 200)
        self.assertEqual(len(body["signals"]), 1)


class StatusReportIsAgentOnlyTests(_HubCase):
    """MT5 快照只有真的接著 MT5 的那台電腦講得出來。"""

    PAYLOAD = {"account": {"balance": 100.0}, "positions": [],
               "positions_count": 0, "orders_count": 0}

    def test_agent_can_report(self):
        status, body = self._call("/report/status", token=self.agent, payload=self.PAYLOAD)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))

    def test_console_cannot_report(self):
        status, _ = self._call("/report/status", token=self.console, payload=self.PAYLOAD)
        self.assertEqual(status, 401, "控制台可以偽造自己的持倉快照")


class AuthMeAcceptsBothTests(_HubCase):
    """/auth/me 兩種都收 —— 手機要靠它顯示等級、到期、額度。"""

    def test_agent(self):
        status, body = self._call("/auth/me", token=self.agent)
        self.assertEqual(status, 200)
        self.assertEqual(body["member"]["session_scope"], membership.SCOPE_AGENT)

    def test_console(self):
        status, body = self._call("/auth/me", token=self.console)
        self.assertEqual(status, 200)
        self.assertEqual(body["member"]["session_scope"], membership.SCOPE_CONSOLE)

    def test_no_token_is_refused(self):
        self.assertEqual(self._call("/auth/me")[0], 401)


class LoginScopePlumbingTests(_HubCase):
    def test_login_defaults_to_agent(self):
        """已經發出去的會員端不帶 scope —— Hub 升級後不能讓他們登不進來。"""
        status, body = self._call("/auth/login", payload={
            "username": "alice", "password": "pw12345678", "device": "舊版會員端"})
        self.assertEqual(status, 200)
        self.assertEqual(body["member"]["session_scope"], membership.SCOPE_AGENT)

    def test_login_as_console(self):
        status, body = self._call("/auth/login", payload={
            "username": "alice", "password": "pw12345678",
            "device": "iPhone", "scope": "console"})
        self.assertEqual(status, 200)
        self.assertEqual(body["member"]["session_scope"], membership.SCOPE_CONSOLE)

    def test_console_login_does_not_cut_the_agent_off(self):
        """整件事的目的：會員用手機登入，電腦照樣收得到訊號。"""
        self._call("/auth/login", payload={
            "username": "alice", "password": "pw12345678",
            "device": "iPhone", "scope": "console"})
        status, body = self._call("/signals?after=0", token=self.agent)
        self.assertEqual(status, 200, "手機登入把電腦踢掉了 —— 跟單會直接停")
        self.assertEqual(len(body["signals"]), 1)

    def test_logout_from_console_leaves_the_agent_following(self):
        status, _ = self._call("/auth/logout", token=self.console, payload={})
        self.assertEqual(status, 200)
        self.assertEqual(self._call("/signals?after=0", token=self.agent)[0], 200)
        self.assertEqual(self._call("/auth/me", token=self.console)[0], 401)


if __name__ == "__main__":
    unittest.main()
