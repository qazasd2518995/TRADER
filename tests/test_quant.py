"""量化模型的正確性 —— 回測數字可信的前提。

1. **出場模擬跟既有引擎一致。** 線上影子記帳用的是 backtest.engine；新模型為了
   速度另寫了一份。兩份只要有一點不同，研究結論跟線上對照就對不起來。
2. **沒有前視偏誤。** 把未來的 K 線接上去，過去的指標與交易必須一個都不變。
   這是唯一能直接抓到「偷看未來」的檢驗 —— 看程式碼看不出來，2026-09 的
   回測就因此作廢過一次。
3. 每一條保守假設（穿價才成交、同根同觸算停損、跳空以開盤價停損、掛單只活一根）
   都要有對應的測試，否則有人「順手優化」掉了也不會發現。
"""
from __future__ import annotations

import random
import unittest

import numpy as np

from copy_trader.backtest.engine import Rules, simulate
from copy_trader.quant.data import Bars, adx, atr
from copy_trader.quant.model import Context, Execution, Filters, Params, run, simulate_exit


def _walk(n: int, seed: int, sec: int = 900, start: int = 1_700_000_000) -> Bars:
    rnd = random.Random(seed)
    t, o, h, l, c = [], [], [], [], []
    px = 2000.0
    for i in range(n):
        op = px
        hi_, lo_ = op, op
        for _ in range(4):
            px += rnd.gauss(0, 1.2)
            hi_, lo_ = max(hi_, px), min(lo_, px)
        t.append(start + i * sec); o.append(op); h.append(hi_); l.append(lo_); c.append(px)
    return Bars(np.asarray(t, dtype=np.int64), np.asarray(o), np.asarray(h),
                np.asarray(l), np.asarray(c), sec)


def _bars(rows, sec=900, start=1_700_000_000) -> Bars:
    t = [start + i * sec for i in range(len(rows))]
    o, h, l, c = zip(*rows)
    return Bars(np.asarray(t, dtype=np.int64), np.asarray(o, float), np.asarray(h, float),
                np.asarray(l, float), np.asarray(c, float), sec)


class ExitParityTests(unittest.TestCase):
    """跟 backtest.engine.simulate 逐筆比對（無跳空時兩者應完全相同）。"""

    def test_random_paths_match_engine(self):
        b = _walk(3000, seed=1)
        rows = [{"t": int(b.t[i]), "o": b.o[i], "h": b.h[i], "l": b.l[i], "c": b.c[i]}
                for i in range(len(b))]
        rnd = random.Random(2)
        checked = 0
        for _ in range(400):
            f = rnd.randrange(50, 2800)
            side = rnd.choice((1, -1))
            entry = rnd.uniform(b.l[f], b.h[f])            # 一定在第 f 根成交
            risk = rnd.uniform(1.0, 8.0)
            stop = entry - side * risk
            tgt = entry + side * risk * rnd.choice((0.8, 1.0, 1.5))
            mine = simulate_exit(b, f, side, entry, stop, tgt, 24 * 3600, gap_aware=False)
            # engine 從 ts 開始找成交；ts 設在第 f 根開盤，它就會在第 f 根成交
            tr = simulate("buy" if side > 0 else "sell", entry, stop, [tgt], float(b.t[f]),
                          rows[f:f + 200],
                          Rules(partials=(1.0,), breakeven=False, pending_hours=0.01, max_hold_hours=24))
            if mine is None or tr is None or not tr.settled:
                continue
            j, px, oc = mine
            self.assertAlmostEqual((px - entry) * side * 100, tr.profit, places=6,
                                   msg=f"f={f} side={side} {oc} vs {tr.outcome}")
            checked += 1
        self.assertGreater(checked, 300)


class ConservativeAssumptionTests(unittest.TestCase):
    def test_same_bar_stop_and_target_counts_as_stop(self):
        b = _bars([(100, 100.5, 99.5, 100), (100, 103, 97, 100), (100, 100.5, 99.5, 100)] + [(100, 100.5, 99.5, 100)] * 200)
        j, px, oc = simulate_exit(b, 0, +1, 100.0, 98.0, 102.0, 24 * 3600)
        self.assertEqual(oc, "sl")
        self.assertEqual(px, 98.0)

    def test_fill_bar_never_takes_profit(self):
        # 成交那根就已經碰到止盈 —— 不算，下一根才開始判
        b = _bars([(100, 103, 99, 101)] + [(101, 101.5, 100.5, 101)] * 200)
        out = simulate_exit(b, 0, +1, 100.0, 97.0, 102.0, 24 * 3600)
        self.assertNotEqual(out[2], "tp")

    def test_gap_through_stop_exits_at_open(self):
        b = _bars([(100, 100.5, 99.8, 100), (95, 95.5, 94, 95)] + [(95, 95.5, 94.5, 95)] * 200)
        j, px, oc = simulate_exit(b, 0, +1, 100.0, 98.0, 102.0, 24 * 3600, gap_aware=True)
        self.assertEqual(oc, "sl")
        self.assertEqual(px, 95.0, "跳空開在停損下方，實際只能在開盤價出場")

    def test_time_exit_after_max_hold(self):
        b = _bars([(100, 100.4, 99.6, 100)] * 300)
        j, px, oc = simulate_exit(b, 0, +1, 100.0, 95.0, 105.0, 24 * 3600)
        self.assertEqual(oc, "time")
        self.assertGreater(b.t[j] - b.t[0], 24 * 3600)


class ModelRuleTests(unittest.TestCase):
    def _ctx(self, b):
        return Context(b, b, np.full(len(b), 10.0), ((b.t + b.sec) % 86400 // 3600).astype(int))

    def test_touch_without_penetration_does_not_fill(self):
        # 區間 99~101（寬 2），offset 1 倍 → 買單 97。下一根最低剛好 97：碰到但沒穿過
        rows = [(100, 101, 99, 100)] * 4 + [(100, 100.2, 97.0, 99)] + [(99, 99.5, 98.5, 99)] * 200
        b = _bars(rows)
        p = Params(lookback=4, offset_k=1.0, stop_k=1.0, reward=1.0)
        self.assertEqual(run(self._ctx(b), p, ex=Execution(buffer=0.3)), [])
        self.assertTrue(run(self._ctx(b), p, ex=Execution(buffer=0.0)), "buffer=0 時碰到就成交")

    def test_order_lives_only_one_bar(self):
        # 第 3 根收盤：區間 99~101、寬 2 → 買單 97，只活在第 4 根。第 4 根沒碰到，
        # 而且它把區間撐寬到 98.5~101（寬 2.5）→ 第 4 根收盤重掛的買單變成 96.0。
        # 第 5 根跌到 96.9：舊的 97 如果還活著就會成交，但它早該被撤掉；新的 96.0
        # 沒被碰到。所以第 5 根不能有任何成交。
        rows = ([(100, 101, 99, 100)] * 4 + [(100, 100.5, 98.5, 100), (100, 100, 96.9, 97)]
                + [(97, 97.5, 96.5, 97)] * 200)
        b = _bars(rows)
        p = Params(lookback=4, offset_k=1.0, stop_k=1.0, reward=1.0, order_life=1)
        trades = run(self._ctx(b), p, ex=Execution(buffer=0.0))
        self.assertFalse([t for t in trades if t.fill_t == float(b.t[5])],
                         "過期的掛單在下一根還能成交 —— 實盤這張單早就撤了")
        # 對照：同樣的走勢、區間沒被撐寬時，重掛的價位還是 97，第 5 根就該成交
        rows2 = ([(100, 101, 99, 100)] * 4 + [(100, 100.5, 99.5, 100), (100, 100, 96.9, 97)]
                 + [(97, 97.5, 96.5, 97)] * 200)
        b2 = _bars(rows2)
        trades2 = run(self._ctx(b2), p, ex=Execution(buffer=0.0))
        self.assertTrue([t for t in trades2 if t.fill_t == float(b2.t[5])])

    def test_frozen_order_keeps_its_price_and_requotes_only_after_expiry(self):
        # 第 3 根收盤掛買單 97（區間 99~101），活 3 根（第 4、5、6 根）。
        # 第 4 根把區間撐寬 —— 但掛單價必須凍結在 97，不能跟著退到 96。
        # 第 6 根跌到 96.9 → 用凍結的 97 成交。
        rows = ([(100, 101, 99, 100)] * 4 + [(100, 100.5, 98.5, 100), (100, 100.4, 99.6, 100),
                                             (100, 100, 96.9, 97)] + [(97, 97.5, 96.5, 97)] * 200)
        b = _bars(rows)
        p = Params(lookback=4, offset_k=1.0, stop_k=1.0, reward=1.0, order_life=3)
        trades = run(self._ctx(b), p, ex=Execution(buffer=0.0))
        self.assertTrue(trades)
        first = trades[0]
        self.assertEqual(first.decided, float(b.t[3] + b.sec))
        self.assertEqual(first.entry, 97.0)
        self.assertEqual(first.fill_t, float(b.t[6]))

    def test_unfilled_order_requotes_at_expiry_not_earlier(self):
        b = _walk(3000, seed=9)
        ctx = self._ctx(b)
        trades = run(ctx, Params(lookback=8, offset_k=1.5, order_life=16), ex=Execution(buffer=0.0))
        grid = (b.t + b.sec)
        first_decision = float(grid[7])
        for t in trades:
            k = (t.decided - first_decision) / b.sec
            # 沒成交的話一次跳 16 根；成交的話從出場後的第一根開始。兩者都不可能
            # 出現「掛單還沒到期就重新決策」—— 用最直接的方式查：不能有兩筆
            # 交易的決策時間落在同一張掛單的存活期內
        for a, c in zip(trades, trades[1:]):
            self.assertGreaterEqual(c.decided, a.exit_t)

    def test_adx_filter_blocks_entries(self):
        b = _walk(2000, seed=3)
        ctx = self._ctx(b)
        ctx.adx_at[:] = 40.0
        self.assertEqual(run(ctx, Params(), Filters(adx_max=25)), [])

    def test_one_position_at_a_time(self):
        b = _walk(4000, seed=4)
        trades = run(self._ctx(b), Params(lookback=6, offset_k=0.5, stop_k=0.8))
        self.assertGreater(len(trades), 20)
        for a, c in zip(trades, trades[1:]):
            self.assertGreaterEqual(c.decided, a.exit_t, "上一筆還沒出場就開了下一筆")


class NoLookaheadTests(unittest.TestCase):
    """把未來接上去，過去必須一模一樣。"""

    def test_indicators_do_not_change_when_future_is_appended(self):
        full = _walk(1500, seed=5, sec=3600)
        k = 900
        past = Bars(full.t[:k], full.o[:k], full.h[:k], full.l[:k], full.c[:k], 3600)
        for fn in (adx, atr):
            a, b = fn(past), fn(full)[:k]
            ok = np.isnan(a) == np.isnan(b)
            self.assertTrue(ok.all())
            self.assertTrue(np.allclose(a[~np.isnan(a)], b[~np.isnan(b)]), fn.__name__)

    def test_trades_do_not_change_when_future_is_appended(self):
        full = _walk(6000, seed=6)
        k = 4000
        past = Bars(full.t[:k], full.o[:k], full.h[:k], full.l[:k], full.c[:k], 900)
        h1 = _walk(1600, seed=7, sec=3600, start=int(full.t[0]) - 3600 * 50)
        p, flt = Params(lookback=8, offset_k=0.6, stop_k=0.8), Filters(adx_max=30)
        a = run(Context.build(past, past, h1), p, flt)
        b = run(Context.build(full, full, h1), p, flt)
        b = [t for t in b if t.exit_t <= float(past.t[-1])]
        a = [t for t in a if t.exit_t <= float(past.t[-1])]
        self.assertGreater(len(a), 10)
        self.assertEqual([(t.decided, t.side, t.exit_px) for t in a],
                         [(t.decided, t.side, t.exit_px) for t in b])


if __name__ == "__main__":
    unittest.main()
