"""區間逆勢限價策略，以及回測驅動的正確性。

回測最容易出的錯是偷看未來，而且錯了不會報錯 —— 只會讓績效變好看。
所以這裡的測試幾乎都在測「有沒有用到當下還不知道的資訊」。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from copy_trader.backtest import Rules, simulate
from copy_trader.strategy import RangeFadeParams, generate_orders
from scripts.backtest_strategy import run


def _bars(seq, start=1_700_000_000, step=900):
    """seq = [(low, high), ...] -> M15 K 線。"""
    return [{"t": start + i * step, "o": (l + h) / 2, "h": h, "l": l, "c": (l + h) / 2}
            for i, (l, h) in enumerate(seq)]


class OrderGenerationTests(unittest.TestCase):
    def test_places_limits_outside_the_range_on_both_sides(self):
        bars = _bars([(100, 110)] * 4)
        p = RangeFadeParams(lookback=4, offset_k=0.5, stop_k=1.0, reward=1.5)
        orders = generate_orders(bars, p, start=4, end=5)
        self.assertEqual(len(orders), 0)          # 只有 4 根，第 5 根不存在

        bars = _bars([(100, 110)] * 5)
        orders = generate_orders(bars, p, start=4, end=5)
        self.assertEqual([o.direction for o in orders], ["buy", "sell"])
        buy, sell = orders
        # 區間 100~110 寬 10，掛單偏移 0.5 倍 = 5
        self.assertAlmostEqual(buy.entry, 95.0)
        self.assertAlmostEqual(sell.entry, 115.0)
        # 停損 1.0 倍寬度、止盈 1.5 倍停損
        self.assertAlmostEqual(buy.stop, 85.0)
        self.assertAlmostEqual(buy.targets[0], 110.0)
        self.assertAlmostEqual(sell.stop, 125.0)
        self.assertAlmostEqual(sell.targets[0], 100.0)

    def test_decision_never_uses_the_current_bar(self):
        """區間只能看決策當根「之前」的 K 線。

        把當根改成極端值，掛單價不該有任何變化 —— 有變就是偷看。
        """
        p = RangeFadeParams(lookback=3, offset_k=0.5)
        base = _bars([(100, 110), (100, 110), (100, 110), (100, 110)])
        before = generate_orders(base, p, start=3, end=4)
        spiked = [dict(b) for b in base]
        spiked[3] = {**spiked[3], "h": 999.0, "l": 1.0}
        after = generate_orders(spiked, p, start=3, end=4)
        self.assertEqual([o.entry for o in before], [o.entry for o in after])

    def test_width_filters_skip_dead_and_wild_markets(self):
        bars = _bars([(100, 101)] * 5)            # 區間只有 1
        p = RangeFadeParams(lookback=4, min_width=5.0)
        self.assertEqual(generate_orders(bars, p, start=4, end=5), [])
        p = RangeFadeParams(lookback=4, max_width=0.5)
        self.assertEqual(generate_orders(bars, p, start=4, end=5), [])
        p = RangeFadeParams(lookback=4, min_width=0.5, max_width=5.0)
        self.assertEqual(len(generate_orders(bars, p, start=4, end=5)), 2)

    def test_zero_width_range_is_skipped(self):
        bars = _bars([(100, 100)] * 5)
        self.assertEqual(generate_orders(bars, RangeFadeParams(lookback=4),
                                         start=4, end=5), [])


class FillBarTests(unittest.TestCase):
    """實際踩到的前視偏誤。

    買限價單是價格「跌」到掛單價才成交，但那根 K 線的最高點通常發生在成交
    之前（開高走低才碰到我們的單）。拿那個高點判止盈，等於用成交前就已經
    發生的價格獲利。修掉之後 reward=0.5 的勝率從 80.7% 掉到 69.7%，
    每筆期望值從 +325 掉到 +76 —— 差距就是憑空生出來的績效。
    """

    def _fill_bar_spanning_both(self):
        # 掛單 100；這根從 108 跌到 99：成交了，而且最高點 108 遠在止盈之上
        return _bars([(101, 102), (99, 108)])

    def test_target_does_not_trigger_on_the_fill_bar(self):
        bars = self._fill_bar_spanning_both()
        t = simulate("buy", 100.0, 95.0, [103.0], bars[0]["t"] + 1, bars)
        self.assertTrue(t.filled)
        self.assertNotEqual(t.outcome, "全部止盈")

    def test_stop_still_triggers_on_the_fill_bar(self):
        """價格是朝逆向走過來的，同一根裡繼續走下去完全可能 —— 止損照判。"""
        bars = _bars([(101, 102), (94, 108)])
        t = simulate("buy", 100.0, 95.0, [103.0], bars[0]["t"] + 1, bars)
        self.assertTrue(t.filled)
        self.assertEqual(t.outcome, "止損")

    def test_target_works_normally_on_later_bars(self):
        bars = _bars([(101, 102), (99, 100.5), (100, 104)])
        t = simulate("buy", 100.0, 95.0, [103.0], bars[0]["t"] + 1, bars)
        self.assertEqual(t.outcome, "全部止盈")

    def test_opt_in_flag_restores_the_old_behaviour(self):
        """留一個開關才能量出這個修正到底改變了多少。"""
        bars = self._fill_bar_spanning_both()
        t = simulate("buy", 100.0, 95.0, [103.0], bars[0]["t"] + 1, bars,
                     Rules(tp_on_fill_bar=True))
        self.assertEqual(t.outcome, "全部止盈")

    def test_sell_side_is_symmetric(self):
        # 掛單 100 賣；這根從 92 漲到 101：成交，且最低 92 遠在止盈之下
        bars = _bars([(98, 99), (92, 101)])
        t = simulate("sell", 100.0, 105.0, [97.0], bars[0]["t"] + 1, bars)
        self.assertTrue(t.filled)
        self.assertNotEqual(t.outcome, "全部止盈")


class DriverTests(unittest.TestCase):
    def test_order_cannot_fill_on_the_decision_bar(self):
        """決策當根的高低早就發生過了，掛單不能在那根成交。"""
        # 前 3 根 100~110；第 4 根（決策當根）暴跌到 80，之後回穩
        bars = _bars([(100, 110)] * 3 + [(80, 110)] + [(100, 110)] * 20)
        p = RangeFadeParams(lookback=3, offset_k=0.5, stop_k=1.0, reward=1.5)
        # 決策發生在 index 3，掛單 95；那根的低點 80 不該讓它成交
        rules = Rules(pending_hours=4, max_hold_hours=24,
                      partials=(1.0,), breakeven=False)
        ts = float(bars[3]["t"]) + 1.0
        t = simulate("buy", 95.0, 85.0, [110.0], ts, bars[3:])
        self.assertFalse(t.filled)

    def test_positions_never_overlap(self):
        """允許重疊會讓報酬看起來變好，但那是加槓桿不是策略變強。"""
        import random
        random.seed(7)
        seq, price = [], 100.0
        for _ in range(600):
            price += random.uniform(-3, 3)
            seq.append((price - random.uniform(0, 2), price + random.uniform(0, 2)))
        bars = _bars(seq)
        trades = run(bars, RangeFadeParams(lookback=6, offset_k=0.5,
                                           stop_k=1.0, reward=1.5))
        self.assertTrue(trades)
        spans = []
        for t in trades:
            start = t.when.timestamp()
            end = start + ((t.minutes_to_fill or 0) + (t.minutes_to_exit or 0)) * 60
            spans.append((start, end))
        spans.sort()
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(prev_end, next_start + 900)

    def test_no_trades_when_price_never_reaches_the_orders(self):
        bars = _bars([(100, 101)] * 200)          # 完全沒波動
        trades = run(bars, RangeFadeParams(lookback=6, offset_k=2.0))
        self.assertEqual(trades, [])


if __name__ == "__main__":
    unittest.main()
