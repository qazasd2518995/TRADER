#!/usr/bin/env python3
"""在回測引擎上跑「改良方案」對照表，找出值得抄進自家訊號的規則。

    python scripts/optimize_signals.py            # 兩個來源都跑
    python scripts/optimize_signals.py --source yuyu

為什麼要有這支
  backtest_signals.py 回答「照抄他們會怎樣」；這支回答「改哪一條規則會更好」。
  每個方案只動一個旋鈕，才知道效果是誰帶來的。

看數字要注意
  樣本很小（中頻 17 筆、yuyu 79 筆），單一方案贏 20% 可能只是雜訊。
  只有「效果大、方向合理、而且和訊號幾何吻合」的結論才值得上線。
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

# 用 reconfigure 而不是包一層 TextIOWrapper：包一層的話，被匯入時
# 前一個 wrapper 會被回收並把底層 buffer 關掉，另一支就印不出東西。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest_signals import (  # noqa: E402
    SOURCES, Market, Rules, load_signals, simulate,
)


def run(signals, market, rules, keep=None):
    """keep(signal, trade) -> bool，用來做方向／時段篩選。"""
    out = []
    for ts, sig in signals:
        t = simulate(sig, ts, market, rules)
        if not t or not t.filled or t.outcome in ("持倉中", "資料結束"):
            continue
        if keep and not keep(sig, t):
            continue
        out.append(t)
    return out


def stats(trades, cost: float = 0.0):
    """cost = 每筆來回成本（USD／手），點差+手續費。"""
    if not trades:
        return None
    net = [t.profit - cost for t in trades]
    total = sum(net)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    pf = (sum(wins) / abs(sum(losses)) if losses else float("inf"))
    return {"n": len(trades), "total": total, "ev": total / len(trades),
            "wr": len(wins) / len(trades) * 100, "pf": pf}


def variants():
    """(名稱, Rules, 篩選器)。只動一個旋鈕。"""
    hours_bad = {17, 18, 21}
    return [
        ("基準（現行系統設定）", Rules(), None),
        ("① 拿掉保本移損", Rules(breakeven=False), None),
        ("② 保本改移動停損 2.0", Rules(breakeven=False, trail_after_tp1=2.0), None),
        ("③ 保本改移動停損 3.0", Rules(breakeven=False, trail_after_tp1=3.0), None),
        ("④ 保本改移動停損 5.0", Rules(breakeven=False, trail_after_tp1=5.0), None),
        ("⑤ 停損放寬 1.5 倍", Rules(stop_mult=1.5), None),
        ("⑥ 停損收緊 0.7 倍", Rules(stop_mult=0.7), None),
        ("⑦ 只跟買單", Rules(), lambda s, t: s.direction == "buy"),
        ("⑧ 只跟賣單", Rules(), lambda s, t: s.direction == "sell"),
        ("⑨ 避開 17/18/21 點", Rules(), lambda s, t: t.when.hour not in hours_bad),
        ("⑩ 掛單逾時縮到 1h", Rules(pending_hours=1.0), None),
        ("⑪ ①+⑦ 不保本且只買", Rules(breakeven=False),
         lambda s, t: s.direction == "buy"),
        ("⑫ ③+⑨ 移動停損且避開壞時段", Rules(breakeven=False, trail_after_tp1=3.0),
         lambda s, t: t.when.hour not in hours_bad),
        # ── 分批比例：這是我們自己的設定，不是他的訊號，改了不算過擬合 ──
        ("⓭ 分批 100/0/0（TP1 全出）", Rules(partials=(1.0,)), None),
        ("⓮ 分批 50/50/0（不等 TP3）", Rules(partials=(0.5, 0.5)), None),
        ("⓯ 分批 33/33/34（平均）", Rules(partials=(0.34, 0.33, 0.33)), None),
        ("⓰ 分批 20/30/50（養大單）", Rules(partials=(0.2, 0.3, 0.5)), None),
        ("⓱ 完全不分批，只吃 TP3", Rules(partials=(0.0, 0.0, 1.0)), None),
        ("⓲ ⓭ 再收緊停損 0.7", Rules(partials=(1.0,), stop_mult=0.7), None),
    ]


def split_report(key, signals, market, cost):
    """把訊號按時間對半切。規則是從整份資料挑出來的，若只有前半有效、
    後半失效，那就是過擬合而不是真的優勢 —— 這一步不能省。"""
    half = len(signals) // 2
    halves = [("前半段", signals[:half]), ("後半段", signals[half:])]
    print()
    print(f"╔══ {SOURCES[key]['label']}　樣本外驗證"
          + (f"（成本 {cost:.0f} USD/筆）" if cost else ""))
    for name, rules, keep in variants():
        row = f"║ {name:<24}"
        for label, part in halves:
            s = stats(run(part, market, rules, keep), cost)
            row += (f"  {label} n={s['n']:>2} {s['ev']:>+7,.0f}"
                    if s else f"  {label} —")
        print(row)
    print("╚" + "═" * 70)


def sweep_report(key, signals, market, cost):
    """掃描停損倍率。單一倍率贏很多不代表什麼；要看它左右鄰居是不是也贏。
    連續一段都正 = 真的有效；只有一格正 = 挑到雜訊。"""
    half = len(signals) // 2
    base_stop = {"yuyu": 6.0, "mid": 10.0}.get(key, 0.0)
    print()
    print(f"╔══ {SOURCES[key]['label']}　停損倍率掃描（TP1 全出，成本 {cost:.0f} USD/筆）")
    print(f"║ {'倍率':>6}{'實際停損':>10}{'全樣本':>10}{'前半':>9}{'後半':>9}{'成交':>7}")
    for m in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5):
        r = Rules(partials=(1.0,), stop_mult=m)
        a = stats(run(signals, market, r), cost)
        b = stats(run(signals[:half], market, r), cost)
        c = stats(run(signals[half:], market, r), cost)
        if not a:
            continue
        bv, cv = (b or {}).get("ev", 0), (c or {}).get("ev", 0)
        flag = "  ← 兩半皆正" if bv > 0 and cv > 0 else ""
        print(f"║ {m:>6.1f}{base_stop*m:>9.1f}${a['ev']:>+10,.0f}"
              f"{bv:>+9,.0f}{cv:>+9,.0f}{a['n']:>7}{flag}")
    print("╚" + "═" * 58)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SOURCES), action="append")
    ap.add_argument("--cost", type=float, default=0.0,
                    help="每筆來回成本 USD／手（XAUUSD 點差 0.2 美元≈20 USD）")
    ap.add_argument("--sweep", action="store_true",
                    help="掃描停損倍率，看最佳值是連續平原（可信）還是單點針尖（過擬合）")
    ap.add_argument("--split", action="store_true",
                    help="時間對半切：前半找規則、後半驗證，看是不是過擬合")
    args = ap.parse_args()

    market = Market()
    for key in (args.source or list(SOURCES)):
        signals = load_signals(key)
        if args.sweep:
            sweep_report(key, signals, market, args.cost)
            continue
        if args.split:
            split_report(key, signals, market, args.cost)
            continue
        print(f"\n╔══ {SOURCES[key]['label']}   共 {len(signals)} 筆訊號")
        print(f"║ {'方案':<24}{'成交':>5}{'勝率':>8}{'每筆期望值':>13}{'獲利因子':>9}{'總損益':>11}")
        print("║ " + "─" * 68)
        base = None
        for name, rules, keep in variants():
            s = stats(run(signals, market, rules, keep), args.cost)
            if not s:
                continue
            if base is None:
                base = s["ev"]
            delta = "" if s["ev"] == base else f"  ({s['ev']-base:+.0f})"
            pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
            print(f"║ {name:<24}{s['n']:>5}{s['wr']:>7.1f}%"
                  f"{s['ev']:>+12,.0f}{pf:>9}{s['total']:>+11,.0f}{delta}")
        print("╚" + "═" * 70)


if __name__ == "__main__":
    main()
