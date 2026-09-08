"""兩條連線分離：電腦跟單、手機控制，互不踢下線。

需求（2026-09-08）：會員要能用手機控制自己的掛機端。原本一個帳號只有一組
session，新登入直接覆蓋 —— 會員在手機上一登入，自己的電腦就被踢掉、跟單直接
停擺。所以把連線拆成兩格：

  agent    會員的電腦。收訊號、下單。
  console  手機／瀏覽器。只能看狀態、改設定。

這裡守三件事，每一件壞掉都是真的會出事：

1. **兩格互不干擾** —— 手機登入不能停掉跟單。這是整件事的目的。
2. **console 永遠拿不到訊號** —— 那是付費閘門。破了就等於帳號分享有利可圖：
   一個人跟單，其他人用手機把訊號抄走。
3. **授權管控沒有變鬆** —— 「一個帳號只有一台電腦在跟單」必須完全照舊。

另外 console 一律不計費：在手機上滑不該燒掉會員買的時數。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from copy_trader.central import membership as M


class _Base(unittest.TestCase):
    TIER = "basic"          # 日曆制，不牽扯用量計費

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.store = M.MemberStore(os.path.join(self._dir, "members.db"))
        self.store.create_member("alice", self.TIER, password="pw-alice-123")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def login(self, scope=M.SCOPE_AGENT, device="", password="pw-alice-123"):
        member, err = self.store.login("alice", password, device=device, scope=scope)
        self.assertEqual(err, "", f"登入失敗: {err}")
        return member["session_token"]

    def alive(self, token) -> bool:
        member, _err = self.store.resolve_session(token)
        return member is not None


class TwoSlotsDoNotFightTests(_Base):
    """第一件事：手機登入不能把電腦踢下線。"""

    def test_console_login_keeps_agent_alive(self):
        agent = self.login(M.SCOPE_AGENT, "會員的電腦")
        console = self.login(M.SCOPE_CONSOLE, "iPhone")
        self.assertTrue(self.alive(agent), "手機登入把電腦踢掉了 —— 跟單會直接停")
        self.assertTrue(self.alive(console))

    def test_agent_login_keeps_console_alive(self):
        console = self.login(M.SCOPE_CONSOLE, "iPhone")
        agent = self.login(M.SCOPE_AGENT, "會員的電腦")
        self.assertTrue(self.alive(console))
        self.assertTrue(self.alive(agent))

    def test_tokens_are_distinct(self):
        self.assertNotEqual(self.login(M.SCOPE_AGENT), self.login(M.SCOPE_CONSOLE))

    def test_scope_is_reported_back(self):
        self.assertEqual(
            self.store.resolve_session(self.login(M.SCOPE_AGENT))[0]["session_scope"],
            M.SCOPE_AGENT)
        self.assertEqual(
            self.store.resolve_session(self.login(M.SCOPE_CONSOLE))[0]["session_scope"],
            M.SCOPE_CONSOLE)


class LicenceStillEnforcedTests(_Base):
    """第三件事：授權管控不能變鬆。"""

    def test_second_computer_still_kicks_the_first(self):
        first = self.login(M.SCOPE_AGENT, "電腦 A")
        second = self.login(M.SCOPE_AGENT, "電腦 B")
        self.assertFalse(self.alive(first), "兩台電腦同時跟單 = 授權管控破了")
        self.assertTrue(self.alive(second))

    def test_second_phone_kicks_the_first(self):
        first = self.login(M.SCOPE_CONSOLE, "手機 A")
        second = self.login(M.SCOPE_CONSOLE, "手機 B")
        self.assertFalse(self.alive(first))
        self.assertTrue(self.alive(second))

    def test_kicked_previous_flag_is_per_slot(self):
        self.store.login("alice", "pw-alice-123", scope=M.SCOPE_AGENT)
        # 第一次用手機登入，不該回報「踢掉了誰」—— 手機那格本來是空的
        member, _ = self.store.login("alice", "pw-alice-123", scope=M.SCOPE_CONSOLE)
        self.assertFalse(member["kicked_previous"])
        member, _ = self.store.login("alice", "pw-alice-123", scope=M.SCOPE_CONSOLE)
        self.assertTrue(member["kicked_previous"])


class BackwardCompatibilityTests(_Base):
    """已經發出去的會員端不帶 scope，Hub 升級後必須照舊能用。"""

    def test_login_without_scope_is_an_agent(self):
        member, err = self.store.login("alice", "pw-alice-123")
        self.assertEqual(err, "")
        self.assertEqual(member["session_scope"], M.SCOPE_AGENT)

    def test_unknown_scope_falls_back_to_agent(self):
        for junk in ("", None, "admin", "Console", "AGENT", 5, {}):
            self.assertEqual(M.normalize_scope(junk), M.SCOPE_AGENT,
                             f"{junk!r} 應該退回 agent")

    def test_a_junk_scope_cannot_smuggle_console_privileges(self):
        """把 scope 亂填不能變成「不佔用跟單格」的連線。"""
        first = self.login(M.SCOPE_AGENT, "電腦 A")
        member, _ = self.store.login("alice", "pw-alice-123", scope="console ")
        self.assertEqual(member["session_scope"], M.SCOPE_AGENT)
        self.assertFalse(self.alive(first), "亂填 scope 就能多開一台電腦跟單")


class ConsoleIsNotBilledTests(unittest.TestCase):
    """第二件事的一半：手機不能燒掉會員買的時數。"""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.store = M.MemberStore(os.path.join(self._dir, "members.db"))
        # advanced = 用量計時制
        self.store.create_member("bob", "advanced", password="pw-bob-12345")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _left(self):
        return self.store.get_member("bob")["usage_seconds_left"]

    def test_console_polling_does_not_consume(self):
        console, _ = self.store.login("bob", "pw-bob-12345", scope=M.SCOPE_CONSOLE)
        start = self._left()
        self.assertIsNotNone(start, "用量額度應該在登入時初始化")
        now = time.time()
        with mock.patch.object(M, "gold_market_open", return_value=True):
            for step in (60, 120, 600, 3600):
                with mock.patch.object(M.time, "time", return_value=now + step):
                    # 即使呼叫端傳 consume=True，console 也必須不扣
                    member, err = self.store.resolve_session(
                        console["session_token"], consume=True)
                self.assertEqual(err, "")
                self.assertEqual(member["session_scope"], M.SCOPE_CONSOLE)
        self.assertEqual(self._left(), start, "手機連線把會員的時數扣掉了")


class LogoutAndKickTests(_Base):
    def test_logout_only_clears_its_own_slot(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        self.assertTrue(self.store.logout(console))
        self.assertTrue(self.alive(agent), "手機登出把跟單也停了")
        self.assertFalse(self.alive(console))

    def test_logout_of_agent_leaves_console(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        self.assertTrue(self.store.logout(agent))
        self.assertFalse(self.alive(agent))
        self.assertTrue(self.alive(console))

    def test_logout_of_unknown_token_is_false(self):
        self.assertFalse(self.store.logout("nope"))
        self.assertFalse(self.store.logout(""))

    def test_admin_kick_clears_both(self):
        """管理員按「踢下線」的意思是這個人現在給我離線，留一條手機連線不算。"""
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        self.assertTrue(self.store.kick("alice"))
        self.assertFalse(self.alive(agent))
        self.assertFalse(self.alive(console))

    def test_admin_reset_password_clears_both(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        self.store.reset_password("alice")
        self.assertFalse(self.alive(agent))
        self.assertFalse(self.alive(console))


class SelfChangePasswordTests(_Base):
    """會員自己改密碼：兩格都留著。

    多數人會在手機上改密碼；若順手清掉跟單那格，等於改個密碼就把跟單停了。
    真要清乾淨走管理員的 reset_password。
    """

    def test_change_from_console_keeps_agent_following(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        ok, err = self.store.change_password(console, "pw-alice-123", "new-pw-4567")
        self.assertTrue(ok, err)
        self.assertTrue(self.alive(agent), "在手機改密碼把跟單停掉了")
        self.assertTrue(self.alive(console))

    def test_change_from_agent_works_too(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        ok, err = self.store.change_password(agent, "pw-alice-123", "new-pw-4567")
        self.assertTrue(ok, err)
        self.assertTrue(self.alive(agent))
        self.assertTrue(self.alive(console))

    def test_wrong_old_password_still_rejected_from_console(self):
        console = self.login(M.SCOPE_CONSOLE, "手機")
        ok, err = self.store.change_password(console, "wrong", "new-pw-4567")
        self.assertFalse(ok)
        self.assertEqual(err, "bad_old_password")


class IdleTimeoutIsPerSlotTests(_Base):
    def test_idle_console_does_not_expire_the_agent(self):
        agent = self.login(M.SCOPE_AGENT, "電腦")
        console = self.login(M.SCOPE_CONSOLE, "手機")
        now = time.time()
        future = now + M.SESSION_IDLE_TIMEOUT + 60
        # 電腦持續在跟單，所以中途要刷新 last_seen —— 一路不動的話它自己也會
        # 閒置過期，那就測不到「手機過期不連累電腦」這件事了。
        with mock.patch.object(M.time, "time", return_value=now + 3600):
            self.assertIsNotNone(self.store.resolve_session(agent)[0])
        with mock.patch.object(M.time, "time", return_value=future):
            member, err = self.store.resolve_session(console)
            self.assertIsNone(member, "閒置的手機連線應該過期")
            self.assertEqual(err, "session_expired")
            self.assertIsNotNone(self.store.resolve_session(agent)[0],
                                 "手機閒置過期不該連累還在跟單的電腦")


class PublicViewTests(_Base):
    def test_online_still_means_following(self):
        """後台那欄「在線」必須是「正在跟單」，不能因為手機開著就亮。"""
        self.login(M.SCOPE_CONSOLE, "手機")
        row = self.store.get_member("alice")
        self.assertFalse(row["online"], "手機上線讓後台以為訊號有送到")
        self.assertTrue(row["console_online"])
        self.assertEqual(row["console_device"], "手機")

        self.login(M.SCOPE_AGENT, "電腦")
        row = self.store.get_member("alice")
        self.assertTrue(row["online"])
        self.assertEqual(row["session_device"], "電腦")

    def test_no_token_leaks_into_public_view(self):
        self.login(M.SCOPE_AGENT)
        self.login(M.SCOPE_CONSOLE)
        row = self.store.get_member("alice")
        for key, value in row.items():
            self.assertNotIn("token", key,
                             f"{key} 不該出現在對外的會員資料裡")
            self.assertNotIn("password", key)
            del value


if __name__ == "__main__":
    unittest.main()
