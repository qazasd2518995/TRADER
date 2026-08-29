"""用量計時(進階版以上「非開盤/停止跟單自動暫停」)的回歸測試。

需求(2026-08-29):進階版(PRO)以上, 方案時間變成一份「使用額度」, 只有「黃金開盤」
且「正在跟單」時才扣。體驗/基礎版維持舊的日曆到期制, 完全不受影響。

這裡驗證的都是後端 membership.py 的計費核心 —— 開盤時段判斷、扣款上限(讓停止跟單
造成的空窗不計費)、節流、額度歸零即到期、等級隔離、舊帳號從日曆換算灌入。
"""
import calendar
import os
import tempfile
import time
import unittest
from unittest import mock

from copy_trader.central import membership as M


def _epoch_for_weekday(target_wday: int, hour: int) -> float:
    """回傳某個 UTC 星期幾(週一=0…週日=6)、指定小時的 epoch。不靠死記日期。"""
    base = 1_700_000_000              # 任意參考點
    for i in range(8):
        t = time.gmtime(base + i * 86400)
        if t.tm_wday == target_wday:
            return float(calendar.timegm(
                (t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, 0)))
    raise AssertionError("找不到對應星期")


class GoldMarketOpenTests(unittest.TestCase):
    def test_weekend_is_closed(self):
        self.assertFalse(M.gold_market_open(_epoch_for_weekday(5, 12)))   # 週六整天
        self.assertFalse(M.gold_market_open(_epoch_for_weekday(6, 10)))   # 週日 22:00 前
        self.assertFalse(M.gold_market_open(_epoch_for_weekday(4, 22)))   # 週五 21:00 後

    def test_trading_hours_open(self):
        self.assertTrue(M.gold_market_open(_epoch_for_weekday(6, 23)))    # 週日 22:00 後
        self.assertTrue(M.gold_market_open(_epoch_for_weekday(4, 10)))    # 週五 21:00 前
        self.assertTrue(M.gold_market_open(_epoch_for_weekday(2, 3)))     # 週三凌晨
        self.assertTrue(M.gold_market_open(_epoch_for_weekday(0, 9)))     # 週一


class UsageBillingTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.store = M.MemberStore(os.path.join(self._dir, "members.db"))

    def tearDown(self):
        self.store.close()

    # ── 工具 ────────────────────────────────────────────────────────────
    def _login(self, tier="advanced", days=1, user="u"):
        self.store.create_member(user, tier, password="pw", days=days)
        out, err = self.store.login(user, "pw")
        self.assertEqual(err, "", f"登入失敗: {err}")
        return out

    def _set(self, user, **cols):
        with self.store._lock:
            for k, v in cols.items():
                self.store._conn.execute(
                    f"UPDATE members SET {k}=? WHERE username=? COLLATE NOCASE", (v, user))
            self.store._conn.commit()

    def _get(self, user, col):
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM members WHERE username=? COLLATE NOCASE", (user,)).fetchone()
        return row[col]

    # ── 建立/額度 ────────────────────────────────────────────────────────
    def test_advanced_gets_budget_no_calendar(self):
        out = self._login("advanced", days=2)
        self.assertTrue(out["time_pause"])
        self.assertIsNone(out["expires_at"])                  # 用量制不看日曆
        self.assertAlmostEqual(out["usage"]["seconds_left"], 2 * 86400, delta=5)

    def test_trial_stays_calendar(self):
        out = self._login("trial", days=7)
        self.assertFalse(out["time_pause"])
        self.assertIsNotNone(out["expires_at"])
        self.assertIsNone(out.get("usage"))

    # ── 扣款規則 ────────────────────────────────────────────────────────
    def test_first_tick_only_stamps_no_charge(self):
        out = self._login("advanced", days=1)
        start = out["usage"]["seconds_left"]
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, err = self.store.resolve_session(out["session_token"], consume=True)
        self.assertEqual(err, "")
        self.assertAlmostEqual(m["usage"]["seconds_left"], start, delta=1)  # 沒扣
        self.assertIsNotNone(self._get("u", "last_active_at"))              # 有記時間點

    def test_charges_when_open(self):
        out = self._login("advanced", days=1)
        self._set("u", last_active_at=time.time() - 15)       # 距上次 15 秒
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, _ = self.store.resolve_session(out["session_token"], consume=True)
        left = m["usage"]["seconds_left"]
        self.assertLess(left, 86400)
        self.assertGreater(left, 86400 - M.USAGE_CONSUME_CAP_SECONDS - 1)   # ~15s 被扣
        self.assertTrue(m["usage"]["consuming"])

    def test_no_charge_when_closed(self):
        out = self._login("advanced", days=1)
        self._set("u", last_active_at=time.time() - 15)
        with mock.patch.object(M, "gold_market_open", return_value=False):
            m, _ = self.store.resolve_session(out["session_token"], consume=True)
        self.assertAlmostEqual(m["usage"]["seconds_left"], 86400, delta=1)  # 關盤不扣
        self.assertFalse(m["usage"]["consuming"])

    def test_stop_gap_capped(self):
        """停止跟單造成的長空窗, 恢復後最多只扣一個上限, 等於暫停。"""
        out = self._login("advanced", days=1)
        self._set("u", last_active_at=time.time() - 3600)     # 空窗一小時
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, _ = self.store.resolve_session(out["session_token"], consume=True)
        billed = 86400 - m["usage"]["seconds_left"]
        self.assertLessEqual(billed, M.USAGE_CONSUME_CAP_SECONDS + 1)       # 不是 3600

    def test_throttle_below_interval(self):
        out = self._login("advanced", days=1)
        self._set("u", last_active_at=time.time() - 3)        # < 寫入間隔
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, _ = self.store.resolve_session(out["session_token"], consume=True)
        self.assertAlmostEqual(m["usage"]["seconds_left"], 86400, delta=1)  # 這拍不寫

    def test_not_consuming_does_not_charge(self):
        """/auth/me 這種 consume=False 的呼叫, 就算開盤也不能扣。"""
        out = self._login("advanced", days=1)
        self._set("u", last_active_at=time.time() - 30)
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, _ = self.store.resolve_session(out["session_token"], consume=False)
        self.assertAlmostEqual(m["usage"]["seconds_left"], 86400, delta=1)

    def test_budget_exhaustion_expires(self):
        out = self._login("advanced", days=1)
        self._set("u", usage_seconds_left=5, last_active_at=time.time() - 3600)
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, err = self.store.resolve_session(out["session_token"], consume=True)
        self.assertIsNone(m)
        self.assertEqual(err, "expired")
        # session 應已作廢
        m2, err2 = self.store.resolve_session(out["session_token"], consume=True)
        self.assertIsNone(m2)
        self.assertEqual(err2, "session_invalid")

    # ── 等級隔離 ────────────────────────────────────────────────────────
    def test_basic_tier_never_charged(self):
        out = self._login("basic", days=30, user="b")
        exp0 = self._get("b", "expires_at")
        self._set("b", last_active_at=time.time() - 30)
        with mock.patch.object(M, "gold_market_open", return_value=True):
            m, _ = self.store.resolve_session(out["session_token"], consume=True)
        self.assertIsNone(m.get("usage"))
        self.assertIsNone(self._get("b", "usage_seconds_left"))
        self.assertEqual(self._get("b", "expires_at"), exp0)               # 日曆沒被動

    # ── 舊帳號遷移 ──────────────────────────────────────────────────────
    def test_legacy_advanced_seeds_from_expires(self):
        """升級前就存在的進階版帳號(有 expires_at、無額度)登入時從剩餘日曆換算。"""
        self.store.create_member("leg", "advanced", password="pw", days=1)
        future = time.time() + 2 * 86400
        self._set("leg", usage_seconds_left=None, expires_at=future)       # 假裝成舊帳號
        out, err = self.store.login("leg", "pw")
        self.assertEqual(err, "")
        self.assertAlmostEqual(out["usage"]["seconds_left"], 2 * 86400, delta=5)
        self.assertIsNone(self._get("leg", "expires_at"))                  # 轉為用量制

    # ── 續期 ────────────────────────────────────────────────────────────
    def test_extend_adds_to_budget(self):
        self.store.create_member("e", "advanced", password="pw", days=1)
        res = self.store.extend("e", 2)
        self.assertIsNone(res["expires_at"])
        self.assertAlmostEqual(self._get("e", "usage_seconds_left"), 3 * 86400, delta=5)

    def test_extend_calendar_for_basic(self):
        self.store.create_member("cb", "basic", password="pw", days=10)
        before = self._get("cb", "expires_at")
        self.store.extend("cb", 5)
        self.assertAlmostEqual(self._get("cb", "expires_at"), before + 5 * 86400, delta=5)


if __name__ == "__main__":
    unittest.main()
