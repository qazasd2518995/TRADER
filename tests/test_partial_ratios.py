"""每個訊號來源自訂分批比例的回歸測試。

需求(2026-08-29):分批平倉的三段比例要能每個來源各自設,並且照 MT5 最低 0.01 手
去算「這個比例的最低手數」。後端要能吃 profile 的 partial_ratios、沒給就回退全域。
"""
import tempfile
import unittest

from copy_trader.trade_manager.manager import TradeManager


class PartialRatiosTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tm = TradeManager(self._d.name)
        self.tm.partial_close_ratios = [0.5, 0.3, 0.2]

    def tearDown(self):
        self._d.cleanup()

    def test_profile_reads_custom_ratios(self):
        self.tm.source_profiles = {"S": {"tp_mode": "partial", "partial_ratios": [0.6, 0.2, 0.2]}}
        self.assertEqual(self.tm.profile_for("S")["partial_ratios"], [0.6, 0.2, 0.2])

    def test_profile_falls_back_to_global_ratios(self):
        self.tm.source_profiles = {"S": {"tp_mode": "partial"}}
        self.assertEqual(self.tm.profile_for("S")["partial_ratios"], [0.5, 0.3, 0.2])

    def test_invalid_ratios_fall_back_to_global(self):
        self.tm.source_profiles = {"S": {"partial_ratios": ["x"]}}
        self.assertEqual(self.tm.profile_for("S")["partial_ratios"], [0.5, 0.3, 0.2])

    def test_plan_uses_supplied_ratios(self):
        tps = [10.0, 20.0, 30.0]  # 3 個止盈 → 2 中間關 + 尾段
        # 0.05 手、比例 50/30/20:chunk1=0.025→0.03? round(0.05*0.5,2)=0.03,
        # round(0.05*0.3,2)=0.02(0.015 進位),tail=0.05-0.03-0.02=0.00 → 無效。
        # 用 0.10 手比較好算:0.05 / 0.03 / tail 0.02
        plan = self.tm._plan_partial_chunks(0.10, tps, [0.5, 0.3, 0.2])
        self.assertEqual(plan, [0.05, 0.03])  # 尾段 0.02 由 MT5 TP 收

    def test_extreme_ratio_needs_bigger_lot(self):
        tps = [10.0, 20.0, 30.0]
        # 比例 80/10/10:0.05 手時 chunk2=round(0.05*0.1,2)=0.01 ok,chunk1=0.04,
        # tail=0.05-0.04-0.01=0.00 <0.01 → 無效(回退整包)
        self.assertEqual(self.tm._plan_partial_chunks(0.05, tps, [0.8, 0.1, 0.1]), [])
        # 手數夠大就成立
        self.assertTrue(self.tm._plan_partial_chunks(0.20, tps, [0.8, 0.1, 0.1]))

    def test_breakeven_source_keeps_partial_ratios_field(self):
        # 就算是保本移損,profile 仍帶著 partial_ratios(切回分批時才有值可用)
        self.tm.source_profiles = {"S": {"tp_mode": "breakeven", "partial_ratios": [0.4, 0.3, 0.3]}}
        p = self.tm.profile_for("S")
        self.assertEqual(p["tp_mode"], "breakeven")
        self.assertEqual(p["partial_ratios"], [0.4, 0.3, 0.3])


if __name__ == "__main__":
    unittest.main()
