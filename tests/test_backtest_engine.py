"""回測引擎的出場邏輯。

實際踩到的 bug：中頻只有一檔止盈，但分批表 [0.5, 0.3, 0.2] 在最後一檔只平 50%，
剩下的部位從沒結算，於是他的獲利被硬生生砍半（平均 +821 而不是 +1500），
整個來源被誤判成大虧。最後一檔一定要把 remaining 全平掉。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest_signals import Market, Rules, simulate


def _bars(seq, start=1_700_000_000, step=60):
    """seq = [(low, high), ...] → K 線。收盤取中點，足夠測出場判斷。"""
    return [{"t": start + i * step, "o": (l + h) / 2, "h": h, "l": l, "c": (l + h) / 2}
            for i, (l, h) in enumerate(seq)]


class _FakeMarket(Market):
    def __init__(self, bars):
        self.frames = {"M1": bars}


def _sig(direction, entry, stop, tps):
    return SimpleNamespace(direction=direction, entry_price=entry,
                           stop_loss=stop, take_profit=tps)


class SingleTargetTests(unittest.TestCase):
    """單一止盈檔（中頻的形狀）—— 這就是出過 bug 的情境。"""

    def test_single_target_closes_whole_position(self):
        bars = _bars([(3300, 3301), (3299, 3302), (3300, 3316)])
        t = simulate(_sig("buy", 3300.0, 3290.0, [3315.0]), bars[0]["t"], _FakeMarket(bars))
        self.assertTrue(t.filled)
        self.assertEqual(t.outcome, "全部止盈")
        # 15 美元 × 1 手（100 盎司）= 1500，不是只平一半的 750
        self.assertAlmostEqual(t.profit, 1500.0, places=2)

    def test_sell_side_single_target(self):
        bars = _bars([(3299, 3300), (3298, 3301), (3284, 3300)])
        t = simulate(_sig("sell", 3300.0, 3310.0, [3285.0]), bars[0]["t"], _FakeMarket(bars))
        self.assertEqual(t.outcome, "全部止盈")
        self.assertAlmostEqual(t.profit, 1500.0, places=2)


class MultiTargetTests(unittest.TestCase):
    """三檔止盈（yuyu 的形狀）。"""

    def test_partials_and_breakeven(self):
        """觸及 TP1 平 50%，停損移到進場價，回到進場價出場 = 只留 TP1 的一半獲利。"""
        bars = _bars([(3300, 3301), (3300, 3306), (3299, 3301)])
        t = simulate(_sig("buy", 3300.0, 3294.0, [3305.0, 3310.0, 3315.0]),
                     bars[0]["t"], _FakeMarket(bars))
        self.assertEqual(t.outcome, "保本")
        self.assertAlmostEqual(t.profit, 0.5 * 5 * 100, places=2)   # +250

    def test_all_targets_sum_to_full_position(self):
        bars = _bars([(3300, 3301), (3300, 3306), (3302, 3311), (3302, 3316)])
        t = simulate(_sig("buy", 3300.0, 3294.0, [3305.0, 3310.0, 3315.0]),
                     bars[0]["t"], _FakeMarket(bars))
        self.assertEqual(t.outcome, "全部止盈")
        # 0.5×5 + 0.3×10 + 0.2×15 = 8.5 美元 → 850 USD
        self.assertAlmostEqual(t.profit, 850.0, places=2)

    def test_tp1_all_out_rule(self):
        """partials=(1.0,) 要在首檔就整筆出場，不留尾單。"""
        bars = _bars([(3300, 3301), (3300, 3306), (3290, 3300)])
        t = simulate(_sig("buy", 3300.0, 3294.0, [3305.0, 3310.0, 3315.0]),
                     bars[0]["t"], _FakeMarket(bars), Rules(partials=(1.0,)))
        self.assertAlmostEqual(t.profit, 500.0, places=2)


class OrderingTests(unittest.TestCase):
    """必須逐根按時間走 —— 只比較區間最高最低會兩邊都判成觸及。"""

    def test_stop_before_target_is_a_loss(self):
        bars = _bars([(3300, 3301), (3294, 3300), (3300, 3316)])
        t = simulate(_sig("buy", 3300.0, 3295.0, [3315.0]), bars[0]["t"], _FakeMarket(bars))
        self.assertEqual(t.outcome, "止損")
        self.assertLess(t.profit, 0)

    def test_same_bar_tie_is_pessimistic_by_default(self):
        bars = _bars([(3300, 3301), (3294, 3316)])
        args = (_sig("buy", 3300.0, 3295.0, [3315.0]), bars[0]["t"], _FakeMarket(bars))
        self.assertEqual(simulate(*args).outcome, "止損")
        self.assertEqual(simulate(*args, Rules(optimistic=True)).outcome, "全部止盈")

    def test_unfilled_order_expires(self):
        bars = _bars([(3300, 3301)] * 300)      # 價格從沒碰到掛單價
        t = simulate(_sig("buy", 3280.0, 3270.0, [3295.0]), bars[0]["t"], _FakeMarket(bars))
        self.assertFalse(t.filled)
        self.assertEqual(t.outcome, "未成交")


class RulesTests(unittest.TestCase):
    def test_stop_mult_widens_and_tightens(self):
        bars = _bars([(3300, 3301), (3295, 3300), (3300, 3316)])
        sig = _sig("buy", 3300.0, 3294.0, [3315.0])      # 停損距離 6
        # 收緊到 0.7（停損 4.2）→ 第二根就被掃掉
        self.assertEqual(simulate(sig, bars[0]["t"], _FakeMarket(bars),
                                  Rules(stop_mult=0.7)).outcome, "止損")
        # 原始距離 6 → 撐過去吃到止盈
        self.assertEqual(simulate(sig, bars[0]["t"], _FakeMarket(bars)).outcome, "全部止盈")


if __name__ == "__main__":
    unittest.main()
