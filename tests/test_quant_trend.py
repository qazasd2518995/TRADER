"""順勢模型的正確性。跟 test_quant.py 同一套紀律：前視、每條保守假設各一條測試。"""
from __future__ import annotations

import unittest

import numpy as np

from copy_trader.quant.data import Bars
from copy_trader.quant.trend import (BreakoutParams, TrendExecution, daily_from_h1,
                                     run_breakout, tsmom)
from tests.test_quant import _bars, _walk


def _range(n_wide=20, n_narrow=8):
    """先用較寬的 K 線把通道撐在 98.5~101.5，之後的盤整 K 線完全在通道內。
    （全部用一樣的 K 線的話，每一根都剛好碰到通道邊緣，停損單碰到就觸發。）"""
    return [(100, 101.5, 98.5, 100)] * n_wide + [(100, 101, 99, 100)] * n_narrow


class BreakoutRuleTests(unittest.TestCase):
    ex0 = TrendExecution(slippage=0.0, cost_per_point=0.0, flat_cost=0.0)

    def test_buy_stop_fills_at_open_when_gapping_through(self):
        # 通道高點 101.5；下一根直接開在 104 → 成交價是 104（不是 101），再加滑價
        # 尾端要有一段下跌讓部位出場 —— 持倉到資料結尾的單不是結果，不會被記錄
        rows = (_range() + [(104, 105, 103.5, 104.5)] + [(104.5, 105, 104, 104.6)] * 40
                + [(104, 104.2, 90, 90.5)] + [(90.5, 91, 90, 90.5)] * 30)
        b = _bars(rows, sec=3600)
        tr = run_breakout(b, b, BreakoutParams(n=20, k=3.0, atr_n=10),
                          TrendExecution(slippage=0.1, cost_per_point=0.0, flat_cost=0.0))
        self.assertTrue(tr)
        self.assertAlmostEqual(tr[0].entry, 104.1)

    def test_trailing_stop_only_moves_in_favour(self):
        # 先漲後跌：停損要停在高點附近，不能跟著跌回去
        up = [(100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(60)]
        down = [(160 - i, 160.5 - i, 158.5 - i, 159 - i) for i in range(80)]
        b = _bars(_range() + up + down, sec=3600)
        tr = run_breakout(b, b, BreakoutParams(n=20, k=2.0, atr_n=10), self.ex0)
        self.assertTrue(tr)
        long_ = tr[0]
        self.assertEqual(long_.side, 1)
        self.assertGreater(long_.exit_px, long_.entry + 30, "移動停損應該鎖住大部分漲幅")

    def test_one_position_at_a_time(self):
        b = _walk(8000, seed=21, sec=3600)
        tr = run_breakout(b, b, BreakoutParams(n=20, k=2.0), self.ex0)
        self.assertGreater(len(tr), 20)
        for a, c in zip(tr, tr[1:]):
            self.assertGreaterEqual(c.decided, a.exit_t)

    def test_no_take_profit_exit(self):
        b = _walk(6000, seed=22, sec=3600)
        tr = run_breakout(b, b, BreakoutParams(n=20, k=3.0), self.ex0)
        self.assertTrue(all(t.outcome in ("sl", "trail") for t in tr))


class BreakoutNoLookaheadTests(unittest.TestCase):
    def test_trades_do_not_change_when_future_is_appended(self):
        full = _walk(9000, seed=23, sec=3600)
        k = 6000
        past = Bars(full.t[:k], full.o[:k], full.h[:k], full.l[:k], full.c[:k], 3600)
        p = BreakoutParams(n=55, k=3.0)
        a = [t for t in run_breakout(past, past, p) if t.exit_t <= float(past.t[-1])]
        b = [t for t in run_breakout(full, full, p) if t.exit_t <= float(past.t[-1])]
        self.assertGreater(len(a), 10)
        self.assertEqual([(t.decided, t.side, t.exit_px) for t in a],
                         [(t.decided, t.side, t.exit_px) for t in b])


class TsmomTests(unittest.TestCase):
    def _h1(self, n_days=700, seed=24):
        b = _walk(n_days * 24, seed=seed, sec=3600)
        return Bars(b.t, b.o, b.h, b.l, b.c, 3600, np.full(len(b), 200.0))

    def test_position_is_decided_one_day_before_it_is_held(self):
        d = daily_from_h1(self._h1())
        out = tsmom(d)
        # 把最後一天的收盤改掉：之前每一天的報酬都不能變（那一天的收盤只影響之後的部位）
        d2 = {k: v.copy() for k, v in d.items()}
        d2["close"][-1] *= 1.5
        out2 = tsmom(d2)
        self.assertTrue(np.allclose(out["ret"][:-1], out2["ret"][:-1]))

    def test_no_future_leak_in_signal(self):
        h1 = self._h1()
        d = daily_from_h1(h1)
        k = 500
        dp = {key: v[:k] for key, v in d.items()}
        a, b = tsmom(dp)["ret"], tsmom(d)["ret"][:k]
        self.assertTrue(np.allclose(a[:-1], b[:-1]), "最後一天開盤到開盤的報酬要等隔天才知道")


if __name__ == "__main__":
    unittest.main()
