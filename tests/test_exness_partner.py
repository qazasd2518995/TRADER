"""Exness Partnership API 封裝：停用時安全、欄位容錯、彙總正確。

這份 Swagger spec 沒有定義 response schema，實際欄位名要等真實客戶才確定，
所以彙總刻意用一組候選鍵去比對 —— 這裡就針對「不同欄位命名」做測試。
"""
from __future__ import annotations

import unittest

from copy_trader.central.exness_partner import ExnessPartnerClient


class DisabledClientTests(unittest.TestCase):
    def test_no_credentials_is_disabled_and_safe(self):
        c = ExnessPartnerClient(login="", password="")
        self.assertFalse(c.enabled)
        out = c.summary_by_account()
        self.assertFalse(out["enabled"])
        self.assertEqual(out["accounts"], {})
        self.assertEqual(out["error"], "not_configured")

    def test_credentials_make_it_enabled(self):
        self.assertTrue(ExnessPartnerClient(login="a@b.c", password="pw").enabled)


class AggregationTests(unittest.TestCase):
    """直接餵不同欄位命名的假資料，驗證彙總邏輯。"""

    def setUp(self):
        self.client = ExnessPartnerClient(login="a@b.c", password="pw")
        self.client._token = "tok"
        self.client._token_at = 1e18          # 不要真的去認證

    def _run(self, rows):
        self.client._fetch_rows = lambda *a, **k: rows
        self.client._cache_at = 0.0           # 略過快取
        return self.client.summary_by_account(force=True)

    def test_standard_field_names(self):
        out = self._run([
            {"client_account": "277929873", "volume_lots": 1.5, "reward": 12.5},
            {"client_account": "277929873", "volume_lots": 0.5, "reward": 4.0},
            {"client_account": "999", "volume_lots": 2.0, "reward": 20.0},
        ])
        self.assertTrue(out["enabled"])
        acc = out["accounts"]
        self.assertAlmostEqual(acc["277929873"]["volume_lots"], 2.0)
        self.assertAlmostEqual(acc["277929873"]["reward"], 16.5)
        self.assertEqual(acc["277929873"]["rows"], 2)
        self.assertAlmostEqual(acc["999"]["reward"], 20.0)

    def test_alternative_field_names(self):
        """欄位換個名字也要抓得到（spec 沒定義 schema，只能容錯）。"""
        out = self._run([
            {"login": "555", "lots": "3.25", "commission": "7.75"},
        ])
        acc = out["accounts"]["555"]
        self.assertAlmostEqual(acc["volume_lots"], 3.25)
        self.assertAlmostEqual(acc["reward"], 7.75)

    def test_rows_without_account_are_skipped(self):
        out = self._run([{"volume_lots": 1.0, "reward": 5.0}])   # 沒有帳號欄位
        self.assertEqual(out["accounts"], {})

    def test_bad_numbers_do_not_crash(self):
        out = self._run([{"account": "1", "volume": "n/a", "reward": None}])
        self.assertAlmostEqual(out["accounts"]["1"]["volume_lots"], 0.0)
        self.assertAlmostEqual(out["accounts"]["1"]["reward"], 0.0)

    def test_raw_sample_kept_for_field_discovery(self):
        """保留原始列，第一次接上真實客戶時可以用來對準欄位名。"""
        rows = [{"client_account": "1", "reward": 1.0, "unknown_field": "x"}]
        out = self._run(rows)
        self.assertEqual(out["raw_sample"][0]["unknown_field"], "x")



class ClientsOverviewTests(unittest.TestCase):
    """客戶總覽：不依賴會員對應，註冊未交易的客戶也要看得到。"""

    def setUp(self):
        self.client = ExnessPartnerClient(login="a@b.c", password="pw")
        self.client._token = "tok"
        self.client._token_at = 1e18

    def _run(self, rows):
        self.client._fetch_rows = lambda *a, **k: rows
        self.client._clients_cache_at = 0.0
        return self.client.clients_overview(force=True)

    def test_totals_and_sorting(self):
        out = self._run([
            {"client_uid": "u_idle", "client_country": "TW", "client_status": "INACTIVE",
             "kyc_passed": False, "ftd_received": True, "volume_lots": 0.0,
             "reward_usd": "0.00", "deposit_amount": 4, "client_balance": 4},
            {"client_uid": "u_active", "client_country": "TW", "client_status": "ACTIVE",
             "kyc_passed": True, "ftd_received": True, "volume_lots": 0.04,
             "reward_usd": "0.20", "deposit_amount": 3, "client_balance": 3},
        ])
        t = out["totals"]
        self.assertEqual(t["count"], 2)
        self.assertEqual(t["active"], 1)
        self.assertEqual(t["funded"], 2)
        self.assertEqual(t["kyc_passed"], 1)
        self.assertAlmostEqual(t["volume_lots"], 0.04)
        self.assertAlmostEqual(t["reward_usd"], 0.20)   # 字串金額要轉數字
        self.assertAlmostEqual(t["deposit_amount"], 7)
        # 有交易的排前面
        self.assertEqual(out["clients"][0]["client_uid"], "u_active")

    def test_disabled_is_safe(self):
        c = ExnessPartnerClient(login="", password="")
        out = c.clients_overview()
        self.assertFalse(out["enabled"])
        self.assertEqual(out["clients"], [])
        self.assertEqual(out["error"], "not_configured")


if __name__ == "__main__":
    unittest.main()
