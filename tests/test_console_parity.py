"""手機控制台要跟電腦版會員端「一模一樣」—— 這裡守的是那些會漂開的地方。

2026-09-09 的要求：

1. **畫面上不能出現 LINE 聊天室的名字。** 來源對外一律叫交易頻率。
   Hub 送來的 name 只能當 key，任何看得到的文字都走 label。
2. **策略欄位跟電腦版對齊。** 分批平倉是填每段手數（0.01/0.01/0.01），
   基礎手數自動 = 總和；電腦版沒有的欄位手機也不能有。
3. **手機也能改密碼。** 走既有的 /auth/change-password，控制台的連線改完
   不會被踢掉，電腦那格也不會。
4. **績效指標同一套。** 獲利因子／最大回撤／最大連敗／平均獲利虧損／累計手數／
   各來源 —— 手機不該顯示一個電腦上查不到的數字，也不該少一個。
5. **馬丁層級要看得到。** 電腦版有「第 N 關／下一手」，手機沒有的話會員只看
   得到設定、看不到現在押到哪。
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from copy_trader.central import membership as M
from copy_trader.central.hub_server import (
    HubHTTPServer,
    HubRequestHandler,
    MemberStatusStore,
    SignalStore,
)
from copy_trader.central.web_launcher import LauncherState

ADMIN = "ADMIN_TOKEN"


class _HubCase(unittest.TestCase):
    TIER = "advanced"

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

    def _login(self, scope, device, password="pw12345678"):
        member, err = self.members.login("alice", password, device=device, scope=scope)
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

    def _html(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/console")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8")


class NoChatRoomNamesTests(_HubCase):
    def test_page_source_has_no_chat_room_name(self):
        html = self._html()
        for raw in (M.MID_FREQ, M.HIGH_FREQ):
            self.assertNotIn(raw, html, f"頁面原始碼寫死了聊天室名稱 {raw!r}")

    def test_page_never_prints_the_raw_name(self):
        """舊版把 name 印在標籤下面當副標。name 只能當 key。"""
        compact = re.sub(r"\s+", "", self._html())
        self.assertNotIn('class="sub">\'+esc(name)', compact)
        self.assertNotIn("esc(name)", compact, "name 不該進任何 esc() —— 那代表要顯示")
        # 卡片用索引找回來源，不把名字塞進 DOM 屬性
        self.assertIn('data-i="', compact)
        self.assertNotIn("data-src=", compact)

    def test_view_gives_a_label_and_tier_for_every_source(self):
        _s, body = self._call("/console/settings", token=self.console)
        rows = body["all_sources"]
        self.assertEqual([r["label"] for r in rows],
                         ["低頻交易", "中頻交易", "高頻交易", "超高頻交易"],
                         "順序跟電腦版一樣：低、中、高、超高")
        need = {r["label"]: r["need"] for r in rows}
        self.assertEqual(need["中頻交易"], "trial")
        self.assertEqual(need["高頻交易"], "advanced")
        self.assertEqual(need["超高頻交易"], "flagship")
        self.assertIn("tier_labels", body)
        self.assertEqual(body["tier_labels"]["advanced"], "進階版")

    def test_locked_source_shows_the_tier_needed_not_the_name(self):
        html = self._html()
        self.assertIn("需'+esc(tierLabel(need))", html.replace(" ", ""))


class StrategyParityTests(_HubCase):
    def test_partial_close_is_entered_as_lots_not_ratios(self):
        """電腦版是填每段手數、基礎手數自動 = 總和。手機以前是填比例，兩邊對不上。"""
        html = self._html()
        self.assertIn('data-f="partial_lots"', html)
        self.assertIn("0.01/0.01/0.01", html)
        self.assertNotIn("各止盈出場比例", html)
        self.assertIn("readOnly=true", html.replace(" ", ""), "分批時基礎手數要鎖住")

    def test_martingale_ladder_and_current_level_are_shown(self):
        html = self._html()
        for el in ('data-ladder="1"', 'data-st="level"', 'data-st="next"', 'data-st="losses"'):
            self.assertIn(el, html, f"馬丁階梯少了 {el}")

    def test_ladder_warns_when_a_rung_does_not_grow(self):
        """0.01 × 1.5 = 0.015 被 0.01 跳動吃掉 —— 那一關等於沒加碼，要標出來。"""
        html = self._html()
        self.assertIn("跟前一關一樣", html)

    def test_mid_freq_only_gets_single_and_breakeven(self):
        html = self._html()
        self.assertIn("isMid?[single,be]:[partial,be,single]", html.replace(" ", ""))

    def test_defaults_match_desktop(self):
        """空白列的預設值要跟電腦版 blankSourceRow 一樣，尤其保本距離 3 美元。"""
        compact = re.sub(r"\s+", "", self._html())
        self.assertIn("breakeven_distance:3", compact)
        self.assertIn("multiplier:2,max_level:5", compact)
        self.assertIn('tp_mode:isMid?"single":"partial"', compact)

    def test_tier_locked_options_are_labelled(self):
        html = self._html()
        self.assertIn("（需", html)


class PasswordChangeTests(_HubCase):
    def test_page_has_the_form(self):
        html = self._html()
        for el in ("pwOld", "pwNew", "pwNew2", "pwBtn", "pwMsg"):
            self.assertIn(f'id="{el}"', html, f"改密碼表單缺少 {el}")
        self.assertIn("/auth/change-password", html)

    def test_console_token_can_change_password(self):
        status, body = self._call("/auth/change-password", token=self.console,
                                  payload={"old_password": "pw12345678",
                                           "new_password": "brand-new-99"})
        self.assertEqual(status, 200, body)
        # 新密碼登得進去
        self._login(M.SCOPE_CONSOLE, "iPad", password="brand-new-99")

    def test_agent_stays_logged_in_after_the_change(self):
        """在手機上改個密碼不能把電腦上的跟單踢下線。"""
        self._call("/auth/change-password", token=self.console,
                   payload={"old_password": "pw12345678", "new_password": "brand-new-99"})
        status, _ = self._call("/auth/me", token=self.agent)
        self.assertEqual(status, 200, "電腦那格被踢掉了")
        status, _ = self._call("/auth/me", token=self.console)
        self.assertEqual(status, 200, "手機自己也被踢掉了")

    def test_wrong_old_password(self):
        status, body = self._call("/auth/change-password", token=self.console,
                                  payload={"old_password": "nope",
                                           "new_password": "brand-new-99"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "bad_old_password")

    def test_too_short(self):
        status, body = self._call("/auth/change-password", token=self.console,
                                  payload={"old_password": "pw12345678",
                                           "new_password": "short"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "too_short")

    def test_view_tells_the_page_the_minimum_length(self):
        _s, body = self._call("/console/settings", token=self.console)
        self.assertEqual(body["min_password_length"], M.MIN_PASSWORD_LENGTH)


class SourceStateTests(_HubCase):
    PAYLOAD = {"account": {"balance": 1.0}, "positions": [],
               "positions_count": 0, "orders_count": 0}

    def test_martingale_level_reaches_the_console(self):
        payload = dict(self.PAYLOAD)
        payload["source_state"] = {M.MID_FREQ: {"level": 2, "losses": 2}}
        self._call("/report/status", token=self.agent, payload=payload)
        _s, body = self._call("/console/settings", token=self.console)
        self.assertEqual(body["source_state"], {M.MID_FREQ: {"level": 2, "losses": 2}})

    def test_garbage_state_is_dropped(self):
        payload = dict(self.PAYLOAD)
        payload["source_state"] = "not a dict"
        self._call("/report/status", token=self.agent, payload=payload)
        _s, body = self._call("/console/settings", token=self.console)
        self.assertEqual(body["source_state"], {})


class TradeStatsParityTests(unittest.TestCase):
    """掛機端算的績效要跟電腦版 stats._summarise 同一組指標、同一種算法。"""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, trades, sources=None, timestamp=None):
        data = {"trades": trades}
        if timestamp is not None:
            data["timestamp"] = timestamp
        (self.dir / "closed_trades.json").write_text(json.dumps(data), encoding="utf-8")
        if sources is not None:
            (self.dir / "signal_sources.json").write_text(
                json.dumps(sources, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _t(ticket, profit, volume=0.01, ts=1000, comment="", position_id=None, magic=999999):
        return {"ticket": ticket, "profit": profit, "volume": volume,
                "close_timestamp": ts, "close_time": f"t{ts}", "symbol": "XAUUSD",
                "type": "buy", "comment": comment, "magic": magic,
                "position_id": position_id if position_id is not None else ticket}

    def test_metrics_match_desktop(self):
        # +10, -4, -6, +20 → 獲利因子 30/10 = 3, 最大回撤 10, 最大連敗 2
        self._write([self._t(1, 10, ts=1), self._t(2, -4, ts=2),
                     self._t(3, -6, ts=3), self._t(4, 20, ts=4, volume=0.02)])
        out = LauncherState._trade_stats(self.dir)
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["profit_factor"], 3.0)
        self.assertEqual(out["max_drawdown"], 10.0)
        self.assertEqual(out["max_loss_streak"], 2)
        self.assertEqual(out["avg_win"], 15.0)
        self.assertEqual(out["avg_loss"], -5.0)
        self.assertEqual(out["volume"], 0.05)
        self.assertEqual(out["win_rate"], 50.0)

    def test_partial_closes_merge_into_one_trade(self):
        """分批平倉三段是同一張單。算成三筆的話勝率跟電腦版對不起來。"""
        self._write([self._t(1, 3, ts=1, position_id=100),
                     self._t(2, 3, ts=2, position_id=100),
                     self._t(3, -2, ts=3, position_id=100),
                     self._t(4, -5, ts=4, position_id=200)])
        out = LauncherState._trade_stats(self.dir)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["wins"], 1)
        self.assertEqual(out["losses"], 1)
        self.assertEqual(out["profit_total"], -1.0)
        self.assertEqual(out["volume"], 0.04)
        self.assertEqual(out["recent"][0]["close_time"], "t4")
        self.assertEqual(out["recent"][1]["profit"], 4.0)

    def test_by_source_uses_the_signal_source_map(self):
        self._write([self._t(1, 10, comment="copy_copy_ln_a", ts=1),
                     self._t(2, -4, comment="copy_copy_ln_b", ts=2),
                     self._t(3, 7, comment="copy_copy_ln_c", ts=3),
                     self._t(4, 1, comment="mystery", ts=4)],
                    sources={"copy_ln_a": "A", "copy_ln_b": "A", "copy_ln_c": "B"})
        out = LauncherState._trade_stats(self.dir)
        by = {b["source"]: b for b in out["by_source"]}
        self.assertEqual(set(by), {"A", "B"}, "對不到來源的單不該被猜成某個來源")
        self.assertEqual(by["A"]["trades"], 2)
        self.assertEqual(by["A"]["profit"], 6.0)
        self.assertEqual(by["A"]["win_rate"], 50.0)
        self.assertEqual(by["B"]["win_rate"], 100.0)

    def test_manual_trades_are_excluded(self):
        self._write([self._t(1, 10, magic=0), self._t(2, 5, magic=999999)])
        out = LauncherState._trade_stats(self.dir)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["profit_total"], 5.0)

    def test_empty_has_every_key(self):
        """前端不用另外判斷 —— 空清單也要把每個指標都給出來。"""
        self._write([])
        out = LauncherState._trade_stats(self.dir)
        for key in ("profit_factor", "max_drawdown", "max_loss_streak", "avg_win",
                    "avg_loss", "volume", "by_source", "curve", "recent"):
            self.assertIn(key, out)

    def test_source_state_reads_per_source_levels(self):
        (self.dir / "martingale_state.json").write_text(json.dumps({
            "level": 0, "per_source": {"A": {"level": 2, "losses": 2},
                                       "B": "garbage", "C": {"level": "x"}}}),
            encoding="utf-8")
        self.assertEqual(LauncherState._source_state(self.dir), {"A": {"level": 2, "losses": 2}})

    def test_source_state_missing_file(self):
        self.assertEqual(LauncherState._source_state(self.dir), {})


if __name__ == "__main__":
    unittest.main()
