#!/usr/bin/env python3
"""自有策略回測 —— 區間逆勢限價，跑歷史資料。

    python scripts/backtest_strategy.py                    # 預設參數，含樣本外
    python scripts/backtest_strategy.py --tf M15 --sweep lookback
    python scripts/backtest_strategy.py --walk             # 滾動前進驗證

跟 backtest_signals.py 的差別
  那支回測「別人的訊號」，受限於他們發了幾筆（中頻只有 22 筆，統計上什麼都
  證明不了）。這支回測「我們自己的規則」，同一段歷史可以產生上千筆交易，
  直接跨過樣本量的牆。出場邏輯兩邊共用 copy_trader/backtest 的引擎。

不偷看未來
  決策只用當根與之前的 K 線；掛單從「決策當根收盤之後」才可能成交
  （decision_ts = 當根時間 + 1 秒，引擎的 slice_from 會排除當根）。

同時只持有一筆
  允許重疊會讓報酬看起來變好，但那是加槓桿不是策略變強。成交之後跳到
  出場那根才繼續找下一筆。
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from copy_trader.backtest import Rules, Trade, simulate          # noqa: E402
from copy_trader.strategy import RangeFadeParams, generate_orders  # noqa: E402

HISTORY = ROOT / "data" / "market_history"
SYMBOL = "XAUUSD247m"
COST_USD = 20.0          # 每 1 手來回成本（點差 0.2 美元）


# ── 資料 ────────────────────────────────────────────────────────────────
def load_bars(timeframe: str) -> List[dict]:
    path = HISTORY / f"{SYMBOL}_{timeframe}.json"
    if not path.is_file():
        raise SystemExit(f"找不到 {path}")
    bars = json.loads(path.read_text(encoding="utf-8"))["bars"]
    bars.sort(key=lambda b: b["t"])
    return bars


def bar_seconds(bars: Sequence[dict]) -> int:
    diffs = [bars[i + 1]["t"] - bars[i]["t"] for i in range(min(500, len(bars) - 1))]
    return int(st.median(diffs)) if diffs else 60


# ── 執行 ────────────────────────────────────────────────────────────────
def run(bars: Sequence[dict], params: RangeFadeParams,
        rules: Optional[Rules] = None, *, start: int = 0,
        end: Optional[int] = None) -> List[Trade]:
    """走過區間，回傳實際成交的每一筆。"""
    rules = rules or Rules(pending_hours=params.pending_hours,
                           max_hold_hours=params.max_hold_hours,
                           partials=(1.0,), breakeven=False)
    sec = bar_seconds(bars)
    # 每次模擬只需要「掛單存活 + 最長持有」這麼多根，不必掃整份歷史，
    # 否則 25000 根 × 每根兩張單會慢到跑不完。
    span = int((params.pending_hours + params.max_hold_hours) * 3600 / sec) + 3

    trades: List[Trade] = []
    last = len(bars) if end is None else min(end, len(bars))
    i = max(start, params.lookback)
    while i < last - 1:
        orders = generate_orders(bars, params, start=i, end=i + 1)
        if not orders:
            i += 1
            continue
        # +1 秒：掛單不能在決策當根成交，那根的高低早就發生過了
        ts = float(bars[i]["t"]) + 1.0
        sub = bars[i:i + span]
        filled = []
        for order in orders:
            t = simulate(order.direction, order.entry, order.stop, order.targets,
                         ts, sub, rules)
            if t and t.filled and t.settled:
                filled.append(t)
        if not filled:
            i += 1
            continue
        # 兩邊都掛，先成交的那邊算數，另一邊取消
        winner = min(filled, key=lambda t: t.minutes_to_fill or 0.0)
        trades.append(winner)
        held = (winner.minutes_to_fill or 0.0) + (winner.minutes_to_exit or 0.0)
        exit_ts = ts + held * 60
        while i < last and bars[i]["t"] <= exit_ts:
            i += 1
    return trades


# ── 統計 ────────────────────────────────────────────────────────────────
def stats(trades: Sequence[Trade], cost: float = COST_USD) -> Optional[dict]:
    if not trades:
        return None
    net = [t.profit - cost for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    total = sum(net)
    n = len(net)
    sd = st.pstdev(net) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n else 0.0
    equity, peak, dd = 0.0, 0.0, 0.0
    for x in net:
        equity += x
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "ev": total / n,
        "total": total,
        "pf": (sum(wins) / abs(sum(losses))) if losses else float("inf"),
        "sd": sd,
        "lo95": total / n - 1.96 * se,
        "hi95": total / n + 1.96 * se,
        "max_dd": dd,
    }


def line(label: str, s: Optional[dict], width: int = 22) -> str:
    if not s:
        return f"║ {label:<{width}}{'—':>8}"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    sig = "✅" if s["lo95"] > 0 else ("❌" if s["hi95"] < 0 else "  ")
    return (f"║ {label:<{width}}{s['n']:>6}{s['win_rate']:>7.1f}%"
            f"{s['ev']:>+9,.0f}{pf:>7}{s['total']:>+11,.0f}"
            f"{s['max_dd']:>10,.0f}  {s['lo95']:>+7,.0f}~{s['hi95']:>+7,.0f} {sig}")


HEAD = (f"║ {'':<22}{'筆數':>6}{'勝率':>8}{'期望值':>9}{'PF':>7}"
        f"{'總損益':>11}{'最大回撤':>10}  {'95% 信賴區間':>17}")


# ── 報告 ────────────────────────────────────────────────────────────────
def report(bars: Sequence[dict], params: RangeFadeParams, tf: str) -> None:
    span_days = (bars[-1]["t"] - bars[0]["t"]) / 86400
    fmt = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    print(f"\n資料：{tf} {len(bars):,} 根  {fmt(bars[0]['t'])} → {fmt(bars[-1]['t'])}"
          f"（{span_days / 365:.2f} 年）  成本 {COST_USD:.0f} USD/手")
    print(f"參數：{params.describe()}")

    half = len(bars) // 2
    print(f"\n╔══ 區間逆勢限價")
    print(HEAD)
    print("║ " + "─" * 88)
    print(line("全樣本", stats(run(bars, params))))
    print(line("前半段", stats(run(bars, params, end=half))))
    print(line("後半段", stats(run(bars, params, start=half))))
    print("╚" + "═" * 90)
    print("  95% 信賴區間不含 0 才算有統計意義（✅）；橫跨 0 代表跟丟硬幣分不出來。")


def sweep(bars: Sequence[dict], params: RangeFadeParams, field: str,
          values: Sequence) -> None:
    """掃一個參數。要的是連續一段都好（平原），不是單點特別好（針尖）。"""
    half = len(bars) // 2
    print(f"\n╔══ 掃描 {field}")
    print(HEAD)
    print("║ " + "─" * 88)
    for v in values:
        p = replace(params, **{field: v})
        whole = stats(run(bars, p))
        a = stats(run(bars, p, end=half))
        b = stats(run(bars, p, start=half))
        both = "  ← 兩半皆正" if a and b and a["ev"] > 0 and b["ev"] > 0 else ""
        print(line(f"{field}={v}", whole) + both)
    print("╚" + "═" * 90)


def walk_forward(bars: Sequence[dict], params: RangeFadeParams,
                 folds: int = 6) -> None:
    """滾動前進：把歷史切成幾段，每段都當一次「沒看過的未來」。

    比單純前後對半嚴格 —— 一次僥倖不會過關，要每段都站得住。
    """
    print(f"\n╔══ 滾動前進驗證（{folds} 段）")
    print(HEAD)
    print("║ " + "─" * 88)
    size = len(bars) // folds
    evs = []
    for k in range(folds):
        lo = k * size
        hi = len(bars) if k == folds - 1 else (k + 1) * size
        s = stats(run(bars, params, start=lo, end=hi))
        fmt = lambda t: datetime.fromtimestamp(t).strftime("%m-%d")
        label = f"第{k + 1}段 {fmt(bars[lo]['t'])}~{fmt(bars[hi - 1]['t'])}"
        print(line(label, s))
        if s:
            evs.append(s["ev"])
    print("╚" + "═" * 90)
    if evs:
        pos = sum(1 for e in evs if e > 0)
        print(f"  {pos}/{len(evs)} 段為正、期望值中位 {st.median(evs):+,.0f}"
              f" —— 每段都正才值得相信，半數上下就是靠運氣。")


def optimize(bars: Sequence[dict], train_frac: float = 0.6,
             top: int = 12) -> None:
    """在前段選參數，在完全沒看過的後段驗收。

    這是唯一誠實的選參數方式。在整份資料上挑最好的那組，然後拿同一份資料
    說它多好 —— 那不叫回測，那叫背答案。後段從頭到尾不參與挑選。
    """
    split = int(len(bars) * train_frac)
    fmt = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    print()
    print(f"訓練段：{fmt(bars[0]['t'])} → {fmt(bars[split - 1]['t'])}"
          f"（{split:,} 根）")
    print(f"測試段：{fmt(bars[split]['t'])} → {fmt(bars[-1]['t'])}"
          f"（{len(bars) - split:,} 根）—— 選參數時完全沒看過")

    grid = []
    for lookback in (2, 3, 4, 6, 8, 12):
        for offset_k in (0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
            for stop_k in (0.3, 0.4, 0.6, 1.0):
                for reward in (0.8, 1.0, 1.5):
                    grid.append(RangeFadeParams(
                        lookback=lookback, offset_k=offset_k,
                        stop_k=stop_k, reward=reward))

    scored = []
    for params in grid:
        s = stats(run(bars, params, end=split))
        # 筆數太少的組合，訓練段的名次純粹是雜訊，不讓它進決選
        if s and s["n"] >= 60:
            scored.append((s["ev"], params, s))
    scored.sort(key=lambda x: -x[0])

    print()
    print(f"╔══ 訓練段前 {top} 名，以及它們在測試段的實際表現")
    print(f"║ {'參數':<34}{'訓練n':>6}{'訓練EV':>9}{'測試n':>7}{'測試EV':>9}"
          f"{'測試PF':>8}{'測試95%區間':>18}")
    print("║ " + "─" * 88)
    held = 0
    for ev, params, tr in scored[:top]:
        te = stats(run(bars, params, start=split))
        label = (f"回看{params.lookback} 掛{params.offset_k:g} "
                 f"損{params.stop_k:g} 盈虧{params.reward:g}")
        if not te:
            print(f"║ {label:<34}{tr['n']:>6}{ev:>+9,.0f}{'—':>7}")
            continue
        mark = ""
        if te["ev"] > 0:
            held += 1
            mark = " ✅" if te["lo95"] > 0 else " ○"
        pf = "∞" if te["pf"] == float("inf") else f"{te['pf']:.2f}"
        print(f"║ {label:<34}{tr['n']:>6}{ev:>+9,.0f}{te['n']:>7}"
              f"{te['ev']:>+9,.0f}{pf:>8}"
              f"   {te['lo95']:>+7,.0f}~{te['hi95']:>+7,.0f}{mark}")
    print("╚" + "═" * 90)
    print(f"  訓練段前 {top} 名裡，測試段仍為正的有 {held} 組"
          f"（{held / max(1, min(top, len(scored))) * 100:.0f}%）。")
    print("  純靠過擬合的話這個比例會接近一半 —— 明顯高於一半才代表抓到真東西。")
    print("  ✅ = 測試段信賴區間不含 0；○ = 為正但統計上還分不出來。")


# ── CLI ─────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="M15", help="用哪個週期決策（預設 M15）")
    ap.add_argument("--lookback", type=int, default=8)
    ap.add_argument("--offset", type=float, default=0.25)
    ap.add_argument("--stop", type=float, default=0.60)
    ap.add_argument("--reward", type=float, default=1.5)
    ap.add_argument("--pending", type=float, default=4.0)
    ap.add_argument("--hold", type=float, default=24.0)
    ap.add_argument("--sweep", help="掃描一個參數：lookback/offset_k/stop_k/reward")
    ap.add_argument("--walk", action="store_true", help="滾動前進驗證")
    ap.add_argument("--optimize", action="store_true",
                    help="前段選參數、後段驗收（唯一誠實的選參數方式）")
    args = ap.parse_args()

    bars = load_bars(args.tf)
    params = RangeFadeParams(
        lookback=args.lookback, offset_k=args.offset, stop_k=args.stop,
        reward=args.reward, pending_hours=args.pending, max_hold_hours=args.hold,
    )
    grids = {
        "lookback": [4, 6, 8, 12, 16, 24, 32, 48],
        "offset_k": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0],
        "stop_k": [0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.5],
        "reward": [0.5, 0.8, 1.0, 1.5, 2.0, 3.0],
    }
    if args.optimize:
        optimize(bars)
    elif args.sweep:
        if args.sweep not in grids:
            raise SystemExit(f"--sweep 只能是 {'/'.join(grids)}")
        sweep(bars, params, args.sweep, grids[args.sweep])
    elif args.walk:
        walk_forward(bars, params)
    else:
        report(bars, params, args.tf)


if __name__ == "__main__":
    main()
