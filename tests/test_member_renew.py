"""renew 是「重設額度」，不是像 extend 那樣累加。

試用帳號用掉一半後要「重整成 7 天」，用 extend 會變成 剩餘+7；renew 才會
把剩餘與分母一起設回 7 天整。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from copy_trader.central import membership

DAY = 86400.0


class MemberRenewTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.store = membership.MemberStore(str(Path(self._dir.name) / "m.db"))

    def tearDown(self):
        self.store.close()
        self._dir.cleanup()

    def _burn(self, username, left_seconds):
        """模擬已經用掉一部分額度。"""
        with self.store._lock:
            self.store._conn.execute(
                "UPDATE members SET usage_seconds_left = ? WHERE username = ?",
                (left_seconds, username))
            self.store._conn.commit()

    def test_renew_resets_usage_instead_of_adding(self):
        self.store.create_member("t02", "advanced", password="pw12345678", days=7)
        self._burn("t02", 4.32 * DAY)          # 用掉約 2.7 天

        out = self.store.renew("t02", 7)
        self.assertAlmostEqual(out["usage_seconds_left"], 7 * DAY, places=3)
        self.assertAlmostEqual(out["usage_seconds_total"], 7 * DAY, places=3)
        self.assertIsNone(out["expires_at"])    # 用量制不看日曆

    def test_extend_still_adds(self):
        """對照組：extend 的語意維持累加，不能被這次改動影響。"""
        self.store.create_member("t03", "advanced", password="pw12345678", days=7)
        self._burn("t03", 4 * DAY)
        out = self.store.extend("t03", 7)
        self.assertAlmostEqual(out["usage_seconds_left"], 11 * DAY, places=3)

    def test_renew_calendar_tier_sets_expiry(self):
        """日曆制等級：renew 應該重新從現在起算，而不是留著舊到期日。"""
        self.store.create_member("t04", "trial", password="pw12345678", days=7)
        out = self.store.renew("t04", 7)
        self.assertIsNotNone(out["expires_at"])
        self.assertIsNone(out["usage_seconds_left"])

    def test_renew_unknown_user(self):
        with self.assertRaises(ValueError):
            self.store.renew("nobody", 7)


if __name__ == "__main__":
    unittest.main()
