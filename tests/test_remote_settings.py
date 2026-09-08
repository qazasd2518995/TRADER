"""手機遠端控制掛機端：期望狀態同步。

需求（2026-09-08）：會員在外面要能暫停跟單、改手數。做法是「期望狀態」而不是
「指令佇列」—— 掛機端關了兩小時再開，佇列裡躺著一串「暫停/繼續/暫停」照順序
重播毫無意義；期望狀態只要收斂到最後一次的意圖，重複讀取也是冪等的。

通道刻意不另開：掛機端本來就每 10 秒 POST /report/status，回應直接夾帶期望
設定，一來一回就同步完。

這裡守四件事：

1. **預設是空的** —— 功能上線的當下不能改變任何人的行為。要等會員真的按了
   什麼，那個鍵才會出現；沒出現的鍵掛機端維持原樣。
2. **手數上限在伺服器端夾** —— 會員端在他自己的電腦上，設定檔想改就改；
   但從我們伺服器發出去的值必須合規，否則等級形同虛設。
3. **兩邊不打架** —— 會員在電腦上自己按了停止，不能下一輪就被 Hub 的舊值
   打開回去。
4. **設定要落地** —— Hub 重啟後，按了暫停的人必須還是暫停。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from copy_trader.central import membership as M
from copy_trader.central import web_launcher
from copy_trader.central.hub_server import (
    HubHTTPServer,
    HubRequestHandler,
    MemberStatusStore,
    SignalStore,
)

ADMIN = "ADMIN_TOKEN"


# ── 純資料層 ────────────────────────────────────────────────────────────────
class SanitizeTests(unittest.TestCase):
    def test_unknown_keys_are_dropped_not_fatal(self):
        """舊/新版手機頁面送到對方認不得的欄位，只該少生效一項，不是整包失敗。"""
        clean, rejected = M.sanitize_settings(
            {"following": True, "hub_url": "http://evil", "token": "x"})
        self.assertEqual(clean, {"following": True})
        self.assertIn("unknown:hub_url", rejected)
        self.assertIn("unknown:token", rejected)

    def test_following_accepts_the_shapes_json_actually_produces(self):
        for raw, want in ((True, True), (False, False), (1, True), (0, False),
                          ("true", True), ("false", False)):
            clean, _ = M.sanitize_settings({"following": raw})
            self.assertEqual(clean.get("following"), want, f"{raw!r}")

    def test_following_rejects_nonsense(self):
        for raw in ("yes", 2, None, [], {}):
            clean, rejected = M.sanitize_settings({"following": raw})
            self.assertNotIn("following", clean, f"{raw!r} 不該被當成布林")
            self.assertIn("following:not_a_bool", rejected)

    def test_lot_is_capped_by_tier(self):
        clean, rejected = M.sanitize_settings({"lot_size": 5.0}, max_lot=0.10)
        self.assertEqual(clean["lot_size"], 0.10)
        self.assertIn("lot_size:capped_by_tier", rejected)

    def test_unlimited_tier_still_has_an_absolute_ceiling(self):
        clean, _ = M.sanitize_settings({"lot_size": 10_000.0}, max_lot=None)
        self.assertEqual(clean["lot_size"], M.LOT_MAX)

    def test_lot_below_minimum_is_lifted(self):
        clean, rejected = M.sanitize_settings({"lot_size": 0.0})
        self.assertEqual(clean["lot_size"], M.LOT_MIN)
        self.assertIn("lot_size:below_min", rejected)

    def test_garbage_lot_is_refused(self):
        for raw in ("abc", None, [], float("nan"), float("inf")):
            clean, _ = M.sanitize_settings({"lot_size": raw})
            self.assertNotIn("lot_size", clean, f"{raw!r}")

    def test_one_bad_key_does_not_sink_the_good_one(self):
        clean, _ = M.sanitize_settings({"following": True, "lot_size": "abc"})
        self.assertEqual(clean, {"following": True})


class StoreSettingsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = os.path.join(self._dir, "m.db")
        self.store = M.MemberStore(self.path)
        self.store.create_member("alice", "basic", password="pw12345678")  # max_lot 0.10

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_starts_empty(self):
        """上線當下不能改變任何人的行為 —— 沒有預設值，只有空的。"""
        self.assertEqual(self.store.get_settings("alice"), {})

    def test_merge_not_replace(self):
        self.store.update_settings("alice", {"following": True})
        self.store.update_settings("alice", {"lot_size": 0.05})
        self.assertEqual(self.store.get_settings("alice"),
                         {"following": True, "lot_size": 0.05})

    def test_tier_ceiling_applied_at_write_time(self):
        merged, rejected = self.store.update_settings("alice", {"lot_size": 9.9})
        self.assertEqual(merged["lot_size"], 0.10)
        self.assertIn("lot_size:capped_by_tier", rejected)

    def test_downgrade_takes_effect_on_next_write(self):
        """後台把人降級之後，他下次改設定就該被新上限夾，不必等重新登入。"""
        self.store.create_member("bob", "flagship", password="pw12345678")
        merged, _ = self.store.update_settings("bob", {"lot_size": 5.0})
        self.assertEqual(merged["lot_size"], 5.0)
        self.store.update_member("bob", tier="basic")
        merged, rejected = self.store.update_settings("bob", {"lot_size": 5.0})
        self.assertEqual(merged["lot_size"], 0.10)
        self.assertIn("lot_size:capped_by_tier", rejected)

    def test_survives_restart(self):
        """Hub 重啟後，按了暫停的人必須還是暫停。"""
        self.store.update_settings("alice", {"following": False, "lot_size": 0.03})
        self.store.close()
        reopened = M.MemberStore(self.path)
        try:
            self.assertEqual(reopened.get_settings("alice"),
                             {"following": False, "lot_size": 0.03})
        finally:
            reopened.close()

    def test_meta_records_who_and_when(self):
        self.store.update_settings("alice", {"following": True}, source="console")
        meta = self.store.settings_meta("alice")
        self.assertEqual(meta["updated_by"], "console")
        self.assertGreater(meta["updated_at"], 0)

    def test_corrupt_json_is_treated_as_no_settings(self):
        """壞掉的設定不能讓整個帳號炸掉 —— 當成沒設定，會員端維持原樣。"""
        with self.store._lock:                      # noqa: SLF001 — 故意寫壞
            self.store._conn.execute(
                "UPDATE members SET settings_json = ? WHERE username = ?",
                ("{not json", "alice"))
            self.store._conn.commit()
        self.assertEqual(self.store.get_settings("alice"), {})

    def test_unknown_member(self):
        merged, rejected = self.store.update_settings("nobody", {"following": True})
        self.assertEqual(merged, {})
        self.assertIn("no_such_member", rejected)


# ── Hub 端點 ────────────────────────────────────────────────────────────────
class _HubCase(unittest.TestCase):
    TIER = "basic"          # max_lot = 0.10，用來驗伺服器端夾制

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.members = M.MemberStore(str(root / "members.db"))
        self.status = MemberStatusStore()
        self.httpd = HubHTTPServer(
            ("127.0.0.1", 0), HubRequestHandler,
            SignalStore(root / "sig.jsonl"), ADMIN, self.members, self.status)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.members.create_member("alice", self.TIER, password="pw12345678")
        self.agent = self._login(M.SCOPE_AGENT, "PC")
        self.console = self._login(M.SCOPE_CONSOLE, "iPhone")

    def tearDown(self):
        self.httpd.shutdown()
        self.members.close()
        self._dir.cleanup()

    def _login(self, scope, device):
        member, err = self.members.login(
            "alice", "pw12345678", device=device, scope=scope)
        assert err == "", err
        return member["session_token"]

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


class ConsoleSettingsEndpointTests(_HubCase):
    def test_read_starts_empty(self):
        status, body = self._call("/console/settings", token=self.console)
        self.assertEqual(status, 200)
        self.assertEqual(body["desired"], {})

    def test_write_then_read(self):
        status, body = self._call("/console/settings", token=self.console,
                                  payload={"settings": {"following": False}})
        self.assertEqual(status, 200)
        self.assertEqual(body["desired"], {"following": False})
        self.assertEqual(self._call("/console/settings", token=self.console)[1]["desired"],
                         {"following": False})

    def test_server_caps_the_lot_and_says_so(self):
        """會員端在他自己的電腦上，設定檔想改就改；伺服器發出去的值必須合規。"""
        _s, body = self._call("/console/settings", token=self.console,
                              payload={"settings": {"lot_size": 99.0}})
        self.assertEqual(body["desired"]["lot_size"], 0.10)
        self.assertIn("lot_size:capped_by_tier", body["rejected"])

    def test_agent_cannot_use_the_console_endpoint(self):
        """掛機端不該從這條路改自己的設定 —— 它走 /report/status 的 push。"""
        self.assertEqual(self._call("/console/settings", token=self.agent)[0], 401)
        self.assertEqual(
            self._call("/console/settings", token=self.agent,
                       payload={"settings": {"following": True}})[0], 401)

    def test_admin_token_is_not_a_member(self):
        self.assertEqual(self._call("/console/settings", token=ADMIN)[0], 401)

    def test_no_token(self):
        self.assertEqual(self._call("/console/settings")[0], 401)

    def test_body_without_settings_key_changes_nothing(self):
        self._call("/console/settings", token=self.console,
                   payload={"settings": {"following": True}})
        _s, body = self._call("/console/settings", token=self.console,
                              payload={"following": False})     # 少了 settings 包裹
        self.assertEqual(body["desired"], {"following": True})

    def test_view_reports_whether_the_agent_is_even_alive(self):
        """會員按了暫停就以為安全了 —— 但他控制的是自己家那台電腦。"""
        _s, body = self._call("/console/settings", token=self.console)
        self.assertIn("agent_online", body)
        self.assertIn("agent_reported_at", body)
        self.assertIn("applied", body)


class ConsolePageTests(_HubCase):
    def _get_html(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_page_is_served_without_a_token(self):
        """這一頁就是登入畫面，要它先登入才拿得到就沒人進得去了。"""
        status, html = self._get_html("/console")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("loginBtn", html)

    def test_trailing_slash_works_too(self):
        self.assertEqual(self._get_html("/console/")[0], 200)

    def test_page_logs_in_with_console_scope(self):
        """頁面若送成 agent，會員一開手機就把自己的電腦踢下線。"""
        _s, html = self._get_html("/console")
        self.assertIn('scope: "console"', html)

    def test_page_tells_the_user_it_controls_their_own_pc(self):
        _s, html = self._get_html("/console")
        self.assertIn("電腦沒開機", html)

    def test_page_has_no_third_party_assets(self):
        """Hub 只有標準庫、也沒有外連資源 —— 整頁必須自帶。"""
        _s, html = self._get_html("/console")
        for bad in ("http://", "https://", "cdn.", "<img"):
            self.assertNotIn(bad, html, f"頁面不該有外部資源：{bad}")


class ReportStatusCarriesSettingsTests(_HubCase):
    PAYLOAD = {"account": {"balance": 1.0}, "positions": [],
               "positions_count": 0, "orders_count": 0}

    def _report(self, extra=None):
        payload = dict(self.PAYLOAD)
        payload.update(extra or {})
        return self._call("/report/status", token=self.agent, payload=payload)

    def test_response_carries_desired_settings(self):
        """往下的通道：不另開端點，回應夾帶就好。"""
        self._call("/console/settings", token=self.console,
                   payload={"settings": {"following": False, "lot_size": 0.02}})
        status, body = self._report()
        self.assertEqual(status, 200)
        self.assertEqual(body["settings"], {"following": False, "lot_size": 0.02})

    def test_response_is_empty_when_nothing_was_ever_set(self):
        self.assertEqual(self._report()[1]["settings"], {})

    def test_applied_settings_reach_the_console(self):
        self._report({"settings_applied": {"following": True, "lot_size": 0.01}})
        _s, body = self._call("/console/settings", token=self.console)
        self.assertEqual(body["applied"], {"following": True, "lot_size": 0.01})

    def test_agent_push_updates_desired(self):
        """會員在電腦上自己按了停止 —— 期望值要跟著改，否則下一輪又被打開。"""
        self._call("/console/settings", token=self.console,
                   payload={"settings": {"following": True}})
        self._report({"settings_push": {"following": False}})
        self.assertEqual(self._report()[1]["settings"]["following"], False)
        self.assertEqual(
            self.members.settings_meta("alice")["updated_by"], "agent")

    def test_agent_push_is_also_capped(self):
        """推上來的值一樣要夾 —— 會員端是在他自己的電腦上，不可信。"""
        self._report({"settings_push": {"lot_size": 50.0}})
        self.assertEqual(self._report()[1]["settings"]["lot_size"], 0.10)


# ── 掛機端的對齊邏輯 ────────────────────────────────────────────────────────
class _FakeHub:
    def __init__(self, settings=None):
        self.payloads = []
        self.settings = settings or {}

    def report_status(self, payload):
        self.payloads.append(payload)
        return {"ok": True, "settings": self.settings}


def _state(**kwargs):
    """只夠遠端設定那段用的假 state。"""
    st = web_launcher.LauncherState.__new__(web_launcher.LauncherState)
    st.role = "client"
    st.settings = {"default_lot_size": "0.01"}
    st.worker = None
    st.client_agent = None
    st._last_desired = None
    st.saved = []
    st.logs_written = []
    st.started = 0
    st.stopped = 0
    st.save_settings = lambda data: (st.saved.append(data),
                                     st.settings.update(data))[0]
    st._log = st.logs_written.append
    st.start_service = lambda: setattr(st, "started", st.started + 1)
    st.stop_service = lambda: setattr(st, "stopped", st.stopped + 1)
    for k, v in kwargs.items():
        setattr(st, k, v)
    return st


class AgentReconcileTests(unittest.TestCase):
    def test_empty_desired_changes_nothing(self):
        """上線當下不能動到任何人。"""
        st = _state()
        st._sync_remote_settings({})
        self.assertEqual(st.saved, [])
        self.assertEqual(st.started, 0)
        self.assertEqual(st.stopped, 0)

    def test_absent_key_is_not_an_opinion(self):
        """只給手數時不能順便把跟單關掉。"""
        st = _state()
        st._sync_remote_settings({"lot_size": 0.05})
        self.assertEqual(st.started, 0)
        self.assertEqual(st.stopped, 0)
        self.assertEqual(st.settings["default_lot_size"], "0.05")

    def test_following_true_starts(self):
        st = _state()
        st._sync_remote_settings({"following": True})
        self.assertEqual(st.started, 1)

    def test_following_false_when_already_stopped_does_nothing(self):
        st = _state()
        st._sync_remote_settings({"following": False})
        self.assertEqual(st.stopped, 0, "本來就停著，不該再喊一次停")

    def test_repeated_identical_desired_is_idempotent(self):
        """每 10 秒收到同一份設定，不能每次都重按一遍。"""
        st = _state()
        for _ in range(5):
            st._sync_remote_settings({"lot_size": 0.05})
        self.assertEqual(len(st.saved), 1)

    def test_start_failure_does_not_propagate(self):
        """沒登入/額度用盡時啟動會丟例外 —— 這是旁路，不能炸掉上報執行緒。"""
        def boom():
            raise PermissionError("not_logged_in")
        st = _state(start_service=boom)
        st._sync_remote_settings({"following": True})       # 不該丟出來
        self.assertTrue(any("失敗" in line for line in st.logs_written))

    def test_junk_response_is_ignored(self):
        st = _state()
        for junk in (None, "settings", 5, []):
            st._sync_remote_settings(junk)
        self.assertEqual(st.saved, [])


class TradeStatsTests(unittest.TestCase):
    """手機控制台的績效數字。

    最容易錯的是時間：closed_trades.json 的 close_timestamp 和檔案自己的
    timestamp 都是 **MT5 伺服器時間**（實測比本機快 3 小時）。拿本機時鐘去切
    「今天」會整整錯開三小時 —— 這個坑 K 線那邊已經踩過一次了。
    """

    DAY = 86400

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.dir = Path(self._dir)

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write(self, trades, now):
        (self.dir / "closed_trades.json").write_text(
            json.dumps({"timestamp": now, "trades": trades}), encoding="utf-8")

    def _stats(self):
        return web_launcher.LauncherState._trade_stats(self.dir)   # noqa: SLF001

    def _t(self, profit, ts, magic=999999):
        return {"symbol": "XAUUSD", "type": "buy", "volume": 0.01, "magic": magic,
                "profit": profit, "close_timestamp": ts, "close_time": "x"}

    def test_missing_file(self):
        s = self._stats()
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["win_rate"])
        self.assertEqual(s["curve"], [])

    def test_buckets_use_the_broker_clock_not_ours(self):
        """券商時鐘的今天。用本機時鐘算的話這筆會被算到昨天去。"""
        broker_now = 1_700_000_000.0                    # 跟本機時鐘無關的一個時刻
        day_start = broker_now - (broker_now % self.DAY)
        self._write([
            self._t(10.0, day_start + 60),              # 今天
            self._t(-4.0, day_start - 60),              # 昨天（差 2 分鐘）
            self._t(7.0, day_start - 3 * self.DAY),     # 三天前 → 算進近 7 日
            self._t(99.0, day_start - 30 * self.DAY),   # 一個月前 → 只進累計
        ], broker_now)
        s = self._stats()
        self.assertEqual(s["profit_today"], 10.0)
        self.assertEqual(s["profit_week"], 13.0)        # 10 - 4 + 7
        self.assertEqual(s["profit_total"], 112.0)

    def test_only_counts_our_own_orders(self):
        """會員自己手動下的單不該混進跟單績效。"""
        now = 1_700_000_000.0
        self._write([self._t(100.0, now), self._t(-500.0, now, magic=0)], now)
        s = self._stats()
        self.assertEqual(s["total"], 1)
        self.assertEqual(s["profit_total"], 100.0)

    def test_win_rate_ignores_break_even(self):
        now = 1_700_000_000.0
        self._write([self._t(5.0, now), self._t(-5.0, now), self._t(0.0, now)], now)
        s = self._stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["win_rate"], 50.0)

    def test_curve_is_cumulative_and_capped(self):
        now = 1_700_000_000.0
        self._write([self._t(1.0, now - i) for i in range(50, 0, -1)], now)
        s = self._stats()
        self.assertEqual(len(s["curve"]), web_launcher.LauncherState.CURVE_POINTS)
        self.assertEqual(s["curve"][-1], 50.0, "最後一點要是累計總和")
        self.assertLess(s["curve"][0], s["curve"][-1], "曲線要是累加的")

    def test_recent_is_newest_first_and_short(self):
        now = 1_700_000_000.0
        self._write([self._t(float(i), now - (10 - i)) for i in range(10)], now)
        s = self._stats()
        self.assertEqual(len(s["recent"]), web_launcher.LauncherState.RECENT_TRADES)
        self.assertEqual(s["recent"][0]["profit"], 9.0, "最新的要排第一")

    def test_garbage_rows_do_not_crash(self):
        now = 1_700_000_000.0
        self._write([self._t("abc", now), self._t(3.0, "xyz"), self._t(2.0, now)], now)
        s = self._stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["profit_total"], 5.0)

    def test_falls_back_when_file_has_no_timestamp(self):
        """沒有 timestamp 就拿最後一筆成交當「現在」，仍是同一個時鐘。"""
        last = 1_700_000_000.0
        (self.dir / "closed_trades.json").write_text(
            json.dumps({"trades": [self._t(8.0, last)]}), encoding="utf-8")
        s = self._stats()
        self.assertEqual(s["profit_today"], 8.0)


class WorksWhileStoppedTests(unittest.TestCase):
    """沒在跟單時也要同步 —— 否則手機只能暫停、不能恢復。

    「恢復」才是會員在外面最想按的那個。上報迴圈原本跑在跟單迴圈裡，服務一停
    client_agent 就是 None，手機寫的期望設定沒有人去讀。
    """

    def _stopped_state(self, hub):
        st = _state()
        st.role = "client"
        st.auth = {"session_token": "tok"}
        st.client_agent = None                  # 沒在跟單
        st._hub_base = lambda: "http://hub"
        st._idle_hub = lambda: hub
        st._device_label = lambda: "PC"
        return st

    def test_reports_even_with_no_agent(self):
        hub = _FakeHub()
        st = self._stopped_state(hub)
        st._report_member_status()
        self.assertEqual(len(hub.payloads), 1,
                         "停止跟單時完全不回報，手機就分不出「暫停中」和「電腦沒開」")
        self.assertFalse(hub.payloads[0]["settings_applied"]["following"])

    def test_phone_can_start_a_stopped_agent(self):
        """整個功能最重要的一條路。"""
        hub = _FakeHub({"following": True})
        st = self._stopped_state(hub)
        st._report_member_status()
        self.assertEqual(st.started, 1, "手機按開始，停著的掛機端沒有被叫起來")

    def test_no_hub_no_crash(self):
        st = self._stopped_state(None)
        st._idle_hub = lambda: None
        st._report_member_status()               # 不該丟例外

    def test_idle_hub_needs_login(self):
        st = _state()
        st.role = "client"
        st.auth = None
        st._hub_base = lambda: "http://hub"
        self.assertIsNone(st._idle_hub(), "沒登入就不該憑空生出一條連線")

    def test_idle_hub_actually_builds_a_client(self):
        """成功路徑也要測。

        原本只測了「沒登入回 None」，於是 _idle_hub 裡少了 HubClient 的匯入、
        NameError 被 except 吞掉、永遠回 None —— 測試全綠，功能整條沒作用。
        只測失敗路徑等於沒測。
        """
        from copy_trader.central.mt5_client_agent import HubClient
        st = _state()
        st.role = "client"
        st.auth = {"session_token": "tok-123", "username": "alice"}
        st._hub_base = lambda: "http://hub.example"
        hub = st._idle_hub()
        self.assertIsInstance(hub, HubClient)
        self.assertEqual(hub.token, "tok-123")

    def test_idle_hub_needs_a_hub_url(self):
        st = _state()
        st.role = "client"
        st.auth = {"session_token": "tok-123"}
        st._hub_base = lambda: ""
        self.assertIsNone(st._idle_hub())


class AgentPushesLocalChangesTests(unittest.TestCase):
    """第三件事：兩邊不打架。"""

    def _report(self, st, hub):
        st.client_agent = SimpleNamespace(hub=hub, trade_manager=None)
        st._device_label = lambda: "PC"
        st._report_member_status()
        return hub.payloads[-1] if hub.payloads else None

    def test_local_change_is_pushed_not_overwritten(self):
        st = _state()
        st._last_desired = {"following": False}     # 上一輪 Hub 說「停」
        st.worker = SimpleNamespace(is_alive=lambda: True)   # 但本機被按成「跑」
        hub = _FakeHub({"following": False})
        payload = self._report(st, hub)
        self.assertEqual(payload["settings_push"], {"following": True},
                         "本機的變更沒被推上去，下一輪就會被舊值改回來")

    def test_no_push_when_in_agreement(self):
        st = _state()
        st._last_desired = {"following": False}
        hub = _FakeHub({"following": False})
        payload = self._report(st, hub)
        self.assertNotIn("settings_push", payload)

    def test_first_ever_report_does_not_push(self):
        """還沒問過 Hub 就先推，會把會員在手機上設好的東西洗掉。"""
        st = _state()                       # _last_desired = None
        hub = _FakeHub({"following": True})
        payload = self._report(st, hub)
        self.assertNotIn("settings_push", payload)

    def test_applied_is_always_reported(self):
        st = _state()
        payload = self._report(st, _FakeHub())
        self.assertEqual(payload["settings_applied"]["following"], False)
        self.assertEqual(payload["settings_applied"]["lot_size"], 0.01)


if __name__ == "__main__":
    unittest.main()
