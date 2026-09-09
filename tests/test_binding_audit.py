"""綁定體檢：誰的掛機端綁到哪台 MT5，以及自動抓出錯配。

會員端跟 MT5 的綁定是「mt5_files_dir 指到某個 MT5 終端機資料夾」，**不是綁帳號**。
那個資料夾裡的 MT5 登入誰，訊號就送給誰。所以有兩種錯配會直接造成事故：

  * 兩個會員端指到同一個資料夾 → 同一個 MT5 帳戶被下兩次單
  * 某台 MT5 被換了帳號     → 訊號靜靜地送去非預期的帳戶

用眼睛比對每台機器的設定檔遲早會漏，所以由 Hub 自己算。這裡守的就是那個算法。
"""
from __future__ import annotations

import unittest

from copy_trader.central.hub_server import MemberStatusStore


def _snap(login=None, folder="", instance="", stale=False):
    return {"account": {"login": login} if login else {},
            "mt5_files_dir": folder, "instance": instance, "mt5_stale": stale,
            "positions": [], "positions_count": 0, "orders_count": 0}


class DuplicateBindingTests(unittest.TestCase):
    """最嚴重的一類：同一個帳戶被下兩次單。"""

    def setUp(self):
        self.store = MemberStatusStore()

    def test_same_folder_is_flagged_on_both(self):
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.store.update("b", _snap(222, r"D:\MT5-2\MQL5\Files", "3"))
        issues = self.store.audit()
        self.assertIn("duplicate_mt5_dir", issues["a"])
        self.assertIn("duplicate_mt5_dir", issues["b"], "兩邊都要標，只標一邊會以為是對方的錯")

    def test_folder_comparison_ignores_case_and_slashes(self):
        """Windows 路徑大小寫不敏感，比對時沒正規化就抓不到。"""
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.store.update("b", _snap(222, r"d:\mt5-2\mql5\files", "3"))
        self.assertIn("duplicate_mt5_dir", self.store.audit()["a"])

    def test_same_mt5_login_from_different_folders(self):
        """兩台不同的 MT5 終端機登入同一個帳號 —— 資料夾不同，但一樣會重複下單。"""
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.store.update("b", _snap(111, r"D:\MT5-7\MQL5\Files", "7"))
        issues = self.store.audit()
        self.assertIn("duplicate_mt5_login", issues["a"])
        self.assertIn("duplicate_mt5_login", issues["b"])

    def test_distinct_bindings_are_clean(self):
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.store.update("b", _snap(222, r"D:\MT5-3\MQL5\Files", "3"))
        self.assertEqual(self.store.audit(), {}, "正常設定不該有任何警告")

    def test_missing_folder_is_not_a_duplicate(self):
        """兩個都還沒回報資料夾（舊版會員端），不能被當成撞在一起。"""
        self.store.update("a", _snap(111, "", ""))
        self.store.update("b", _snap(222, "", ""))
        for user in ("a", "b"):
            self.assertNotIn("duplicate_mt5_dir", self.store.audit().get(user, []))


class AccountChangeTests(unittest.TestCase):
    def setUp(self):
        self.store = MemberStatusStore()

    def test_login_change_is_flagged(self):
        self.store.update("a", _snap(111, r"D:\MT5-4\MQL5\Files", "4"))
        self.store.update("a", _snap(999, r"D:\MT5-4\MQL5\Files", "4"))
        self.assertIn("mt5_login_changed", self.store.audit()["a"])
        self.assertEqual(self.store.snapshot()["a"]["mt5_login_changed_from"], 111)

    def test_flag_survives_later_reports(self):
        """旗標一升起就要留著。每 10 秒一次的上報若把它沖掉，等於沒人看得到。"""
        self.store.update("a", _snap(111, r"D:\MT5-4\MQL5\Files", "4"))
        self.store.update("a", _snap(999, r"D:\MT5-4\MQL5\Files", "4"))
        for _ in range(5):
            self.store.update("a", _snap(999, r"D:\MT5-4\MQL5\Files", "4"))
        self.assertIn("mt5_login_changed", self.store.audit()["a"])

    def test_steady_account_is_not_flagged(self):
        for _ in range(3):
            self.store.update("a", _snap(111, r"D:\MT5-4\MQL5\Files", "4"))
        self.assertEqual(self.store.audit(), {})


class BridgeHealthTests(unittest.TestCase):
    def setUp(self):
        self.store = MemberStatusStore()

    def test_no_account_means_no_bridge(self):
        self.store.update("a", _snap(None, r"D:\MT5-9\MQL5\Files", "9"))
        self.assertIn("no_mt5_bridge", self.store.audit()["a"])

    def test_stale_means_mt5_closed(self):
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2", stale=True))
        issues = self.store.audit()["a"]
        self.assertIn("mt5_not_running", issues)
        self.assertNotIn("no_mt5_bridge", issues, "有帳號只是檔案舊了，不是接不上")

    def test_member_that_never_reported(self):
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        issues = self.store.audit(["a", "ghost"])
        self.assertEqual(issues.get("ghost"), ["never_reported"])
        self.assertNotIn("a", issues)

    def test_known_list_is_optional(self):
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.assertEqual(self.store.audit(), {})


class StaleRecordTests(unittest.TestCase):
    """已經走掉的掛機端不該還在參與重複偵測。

    這個 store 不會過期，所以會員換帳號或停掉掛機端之後，換之前的最後一筆
    快照會一直留著。2026-09-09 把 instance 4 從 ops4 轉給 trial04 時，
    ops4 的舊快照就讓「兩個會員指到同一台 MT5」誤報了一次。
    """

    def setUp(self):
        self.store = MemberStatusStore()

    def _age(self, user, seconds):
        with self.store._lock:                       # noqa: SLF001
            self.store._by_user[user]["reported_at"] -= seconds

    def test_handover_does_not_look_like_a_duplicate(self):
        self.store.update("ops4", _snap(277942668, r"D:\MT5-4\MQL5\Files", "4"))
        self._age("ops4", 300)                       # 舊的那個早就不回報了
        self.store.update("trial04", _snap(277942668, r"D:\MT5-4\MQL5\Files", "4"))
        issues = self.store.audit()
        self.assertNotIn("duplicate_mt5_dir", issues.get("trial04", []),
                         "接手的那個被誤判成重複下單")
        self.assertEqual(issues.get("ops4"), ["agent_offline"])

    def test_offline_record_only_says_offline(self):
        """離線的不該再報「接不上 MT5」—— 那是它離線前的狀態，現在沒東西在跑。"""
        self.store.update("gone", _snap(None, r"D:\MT5-9\MQL5\Files", "9"))
        self._age("gone", 999)
        self.assertEqual(self.store.audit()["gone"], ["agent_offline"])

    def test_real_duplicates_still_caught(self):
        """兩個都還在回報時，該報的還是要報。"""
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self.store.update("b", _snap(222, r"D:\MT5-2\MQL5\Files", "3"))
        issues = self.store.audit()
        self.assertIn("duplicate_mt5_dir", issues["a"])
        self.assertIn("duplicate_mt5_dir", issues["b"])

    def test_threshold_is_generous_enough_for_a_hiccup(self):
        """每 10 秒回報一次，偶爾一次網路抖動不該被當成離線。"""
        self.store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        self._age("a", 60)
        self.assertNotIn("a", self.store.audit())


class IssueTextTests(unittest.TestCase):
    def test_every_code_has_wording(self):
        """後台直接顯示這些字。少一個就會在畫面上露出程式碼代號。"""
        store = MemberStatusStore()
        store.update("a", _snap(111, r"D:\MT5-2\MQL5\Files", "2"))
        store.update("b", _snap(111, r"D:\MT5-2\MQL5\Files", "3"))
        store.update("b", _snap(222, r"D:\MT5-2\MQL5\Files", "3"))
        store.update("c", _snap(None, "", ""))
        store.update("d", _snap(333, r"D:\MT5-5\MQL5\Files", "5", stale=True))
        seen = set()
        for codes in store.audit(["a", "b", "c", "d", "ghost"]).values():
            seen.update(codes)
        self.assertTrue(seen, "測試本身要真的產生問題碼")
        for code in seen:
            self.assertIn(code, MemberStatusStore.ISSUE_TEXT, f"{code} 沒有對應的中文說明")


if __name__ == "__main__":
    unittest.main()
