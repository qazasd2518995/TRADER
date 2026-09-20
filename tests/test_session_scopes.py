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

    def outlive_the_idle_timeout(self):
        """把會籍延長到遠超過閒置逾時。

        要測「連線逾時」就得把時鐘往前撥超過 SESSION_IDLE_TIMEOUT，但預設的
        basic 是 30 天日曆制 —— 撥過去的同時會籍也到期了，resolve_session 會
        先回 expired，測到的根本不是想測的東西。
        """
        self.store.extend("alice", 3650)


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
        self.outlive_the_idle_timeout()
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


class IdleExpiryStaysReadableTests(_Base):
    """逾時之後要一直回 session_expired，不能變成 session_invalid。

    2026-09-21 實際事故：斷網重開機之後，本機六台掛機端全部顯示「此帳號已在
    其他裝置登入」—— 根本沒有第二台。真正發生的是：

      1. /signals 那一拍發現閒置超時，把 token 清成 NULL，回 session_expired
      2. 緊接著的 /auth/me 拿同一個 token 去查，已經查不到那一列
      3. 於是回 session_invalid，而會員端把它顯示成「已在其他裝置登入」

    面板上寫的原因跟實際原因完全不同，人就跑去找一台不存在的電腦。逾時的
    token 必須留著，這樣每一次都會得到同一個、而且是正確的答案。
    """

    def _expired(self, token):
        with mock.patch.object(M.time, "time",
                               return_value=time.time() + M.SESSION_IDLE_TIMEOUT + 60):
            return self.store.resolve_session(token)

    def test_repeated_calls_keep_saying_expired(self):
        self.outlive_the_idle_timeout()
        token = self.login(M.SCOPE_AGENT, "電腦")
        for attempt in range(3):
            member, err = self._expired(token)
            self.assertIsNone(member)
            self.assertEqual(err, "session_expired",
                             f"第 {attempt + 1} 次查詢變成了 {err}")

    def test_expiry_does_not_destroy_the_token(self):
        """token 被清掉，會員端就再也問不出「為什麼」。"""
        self.outlive_the_idle_timeout()
        token = self.login(M.SCOPE_AGENT, "電腦")
        self._expired(token)
        row = self.store.get_member("alice")
        self.assertTrue(row["online"] is not None)      # 這一列還在
        # 直接確認 token 欄位沒被清空
        with self.store._lock:                           # noqa: SLF001
            found = self.store._conn.execute(            # noqa: SLF001
                "SELECT session_token FROM members WHERE username = ?",
                ("alice",)).fetchone()
        self.assertEqual(found["session_token"], token)

    def test_a_new_device_still_takes_over(self):
        """名額不必靠逾時騰出來 —— 這是把逾時拉長到 30 天的前提。"""
        old_token = self.login(M.SCOPE_AGENT, "舊電腦")
        new_token = self.login(M.SCOPE_AGENT, "新電腦")
        self.assertNotEqual(old_token, new_token)
        member, err = self.store.resolve_session(old_token)
        self.assertIsNone(member, "舊裝置應該已經被踢掉")
        self.assertEqual(err, "session_invalid")
        self.assertIsNotNone(self.store.resolve_session(new_token)[0])

    def test_timeout_is_long_enough_to_survive_a_long_outage(self):
        """24 小時太短：連假停電、搬家、換 ISP 都會讓無人值守的掛機端作廢。"""
        self.assertGreaterEqual(M.SESSION_IDLE_TIMEOUT, 7 * 24 * 3600)


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
