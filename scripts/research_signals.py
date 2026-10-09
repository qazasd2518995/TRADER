#!/usr/bin/env python3
"""訊號來源量化研究 —— 從原始資料重現報告裡的每一張表。

    python scripts/research_signals.py            # 重建資料集並跑全部
    python scripts/research_signals.py --cached   # 用上次建好的資料集

資料（都在 data/market_history/，不進版控）：
  hub_signals.json              Hub 全部訊號（seq 1 起）
  XAUUSD247m_{M1,M5,H1}_full    MT5-4（Exness，GMT+0）用 MetaTrader5 套件拉的歷史
  XAUUSD247m_M15.json           2026-09-04 那次研究用的 M15 —— 它的最後一根就是
                                「樣本內」的終點，之後的資料才是真正的前進檢驗

超高頻的來源帳戶交易紀錄直接讀 C:\\Program Files\\MetaTrader 5 的 closed_trades.json
（EA 匯出的近 30 天）。**刻意不用 MetaTrader5 套件連那台** —— 那是代理商操作的實倉，
我們的程式不該有任何東西掛在它上面。

輸出 data/market_history/research_results.json 給報告用。
"""
from __future__ import annotations

import argparse
import bisect
import itertools
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copy_trader.backtest.engine import Rules, simulate                     # noqa: E402
from copy_trader.research import dataset as D                               # noqa: E402
from copy_trader.research import meta_model as M                            # noqa: E402
from copy_trader.research import nulls as N                                 # noqa: E402
from copy_trader.strategy.range_fade import RangeFadeParams, generate_orders  # noqa: E402

SOURCE_MT5 = Path(r"C:\Program Files\MetaTrader 5\MQL5\Files\closed_trades.json")


def boot(xs, n=4000, seed=7):
    rnd = random.Random(seed)
    ms = sorted(st.fmean(rnd.choices(xs, k=len(xs))) for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def read_any(path: Path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp950", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


# ── 1. LINE 來源 ─────────────────────────────────────────────────────────

def line_sources(rows, m1, m5):
    out = {}
    for src in D.LINE_SOURCES:
        rs = [r for r in rows if r["source"] == src]
        fl = [r for r in rs if r.get("filled")]
        R = [r["R"] for r in fl]
        lo, hi = boot(R)
        null = N.matched_limit_null(rs, m1, m5)
        wf = M.walk_forward(rs, min_train=60, step=15)
        tk = [x.row["R"] for x in wf if x.take]
        al = [x.row["R"] for x in wf]
        out[src] = {
            "signals": len(rs), "filled": len(fl), "fill_rate": len(fl) / len(rs),
            "mean_R": st.fmean(R), "ci": [lo, hi], "win_rate": sum(r["win"] for r in fl) / len(fl),
            "markout": {m: st.fmean([r[f"mk_{m}"] for r in fl if r.get(f"mk_{m}") is not None])
                        for m in D.MARKOUT_MINUTES},
            "null": null,
            "meta": {"oos": len(wf), "all_R": st.fmean(al), "take_R": st.fmean(tk) if tk else None,
                     "n_take": len(tk),
                     "auc": M.auc([x.p for x in wf], [1 if x.row["R"] > 0 else 0 for x in wf])},
            "equity": _equity(sorted(fl, key=lambda r: r["ts"]), "R"),
        }
    return out


def _equity(rows, key):
    eq, pts = 0.0, []
    for r in rows:
        eq += r[key]
        pts.append([r["ts"], round(eq, 3)])
    return pts


# ── 2. 超高頻（網格 EA）─────────────────────────────────────────────────

def ultra():
    T = (read_any(SOURCE_MT5) or {}).get("trades") or []
    if not T:
        return None
    B = N.grid_baskets(T)
    layers = {}
    for b in B:
        k = min(len(b), 9)
        layers.setdefault(k, []).append(sum(float(x["profit"]) for x in b))
    caps = {}
    for cap in (3, 4, 5, 6, 7, 8, 10, 12):
        res = [N.capped_grid(b, cap) for b in B]
        caps[cap] = {"total": sum(p for p, _ in res), "worst": min(p for p, _ in res)}
    eq, peak, mdd, curve = 0.0, 0.0, 0.0, []
    for b in B:
        eq += sum(float(x["profit"]) for x in b)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        curve.append([b[-1]["close_timestamp"], round(eq, 2)])
    worst = sorted(B, key=lambda b: sum(float(x["profit"]) for x in b))[:2]
    return {
        "trades": len(T), "baskets": len(B), "total": sum(float(x["profit"]) for x in T),
        "basket_win": sum(1 for b in B if sum(float(x["profit"]) for x in b) > 0) / len(B),
        "layers": {k: {"n": len(v), "total": sum(v), "win": sum(p > 0 for p in v) / len(v)}
                   for k, v in sorted(layers.items())},
        "caps": caps, "max_dd": mdd, "peak": peak, "equity": curve,
        "worst": [{"legs": len(b), "lots": sum(float(x["volume"]) for x in b),
                   "maxlot": max(float(x["volume"]) for x in b),
                   "pnl": sum(float(x["profit"]) for x in b),
                   "t": b[-1]["close_timestamp"], "side": b[0]["position_type"]} for b in worst],
    }


# ── 3. 自有策略：區間逆勢，前進檢驗 ─────────────────────────────────────

def range_fade_forward():
    m5 = json.loads((D.DATA_DIR / "XAUUSD247m_M5_full.json").read_text(encoding="utf-8"))
    m1 = sorted(json.loads((D.DATA_DIR / "XAUUSD247m_M1_full.json").read_text(encoding="utf-8")),
                key=lambda b: b["t"])
    m1t = [b["t"] for b in m1]
    g = {}
    for b in m5:
        g.setdefault(b["t"] - b["t"] % 900, []).append(b)
    m15 = [{"t": t, "o": v[0]["o"], "h": max(x["h"] for x in v), "l": min(x["l"] for x in v),
            "c": v[-1]["c"]} for t, v in sorted(g.items()) if len(v) == 3]
    old = json.loads((D.DATA_DIR / "XAUUSD247m_M15.json").read_text(encoding="utf-8"))["bars"]
    cut = max(b["t"] for b in old)               # 2026-09-04 研究看過的最後一根

    def run(p, buffer=0.0):
        rules = Rules(pending_hours=p.pending_hours, max_hold_hours=p.max_hold_hours,
                      partials=(1.0,), breakeven=False)
        out, busy = [], 0.0
        i = next(k for k, b in enumerate(m15) if b["t"] > cut) - p.lookback
        while i < len(m15) - 1:
            if m15[i]["t"] <= cut or m15[i]["t"] + 900 <= busy:
                i += 1
                continue
            ts = m15[i]["t"] + 900 + 1               # 決策根收完之後才生效
            a = bisect.bisect_left(m1t, ts)
            sub = m1[max(0, a - 2):a + 29 * 60]
            best = None
            for o in generate_orders(m15, p, start=i, end=i + 1):
                sign = 1 if o.direction == "buy" else -1
                # 價格要「穿過」掛單價 buffer 才算成交；但成交價仍是原掛單價，
                # 所以每 1 手扣 buffer×100。（2026-10-09 第一版把成交價也一起往有利
                # 方向移了 —— 那等於「更難成交、成交價更好」，一點都不保守。）
                t = simulate(o.direction, o.entry - sign * buffer, o.stop, o.targets, ts, sub, rules)
                if t and t.filled and t.settled:
                    t.profit -= buffer * 100
                    if best is None or (t.minutes_to_fill or 0) < (best.minutes_to_fill or 0):
                        best = t
            if best is not None:
                out.append(best)
                busy = ts + ((best.minutes_to_fill or 0) + (best.minutes_to_exit or 0)) * 60
            i += 1
        return out

    fam = []
    for lb, off, sk, rw in itertools.product((6, 8, 12), (1.5, 1.75, 2.0), (0.6, 0.8, 1.0), (0.8, 1.0, 1.5)):
        tr = run(RangeFadeParams(lookback=lb, offset_k=off, stop_k=sk, reward=rw), buffer=0.3)
        if tr:
            fam.append(st.fmean(t.profit - D.COST_PER_LOT for t in tr))
    center = RangeFadeParams(lookback=8, offset_k=1.75, stop_k=0.8, reward=1.0)
    scen = {}
    for name, buf in (("touch", 0.0), ("through_0.3", 0.3), ("through_0.8", 0.8)):
        tr = run(center, buffer=buf)
        net = [t.profit - D.COST_PER_LOT for t in tr]
        lo, hi = boot(net)
        scen[name] = {"n": len(net), "ev": st.fmean(net), "ci": [lo, hi],
                      "win": sum(x > 0 for x in net) / len(net)}
    tr = run(center, buffer=0.3)
    eq, curve = 0.0, []
    for t in tr:
        eq += t.profit - D.COST_PER_LOT
        curve.append([t.when.timestamp(), round(eq, 1)])
    return {"family_n": len(fam), "family_pos": sum(e > 0 for e in fam),
            "family_median": st.median(fam), "family_worst": min(fam), "family_best": max(fam),
            "scenarios": scen, "equity": curve, "cut": cut}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true")
    args = ap.parse_args()

    path = D.DATA_DIR / "research_dataset.json"
    if args.cached and path.exists():
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        rows = D.build()
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    m1 = D.load_bars("XAUUSD247m_M1_full.json", 60)
    m5 = D.load_bars("XAUUSD247m_M5_full.json", 300)

    res = {"line": line_sources(rows, m1, m5), "ultra": ultra(), "range_fade": range_fade_forward()}
    (D.DATA_DIR / "research_results.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")

    for src, s in res["line"].items():
        nl, mt = s["null"], s["meta"]
        print(f"\n■ {src}：{s['signals']} 筆訊號、成交 {s['filled']}（{s['fill_rate']:.0%}）")
        print(f"  照做每筆 {s['mean_R']:+.3f}R  95%CI [{s['ci'][0]:+.3f}, {s['ci'][1]:+.3f}]  勝率 {s['win_rate']:.0%}")
        print(f"  vs 隨機限價單 {nl['null_R']:+.3f}R → 超額 {nl['excess']:+.3f}R（p={nl['p_value']:.3f}）")
        print(f"  meta-labeling 樣本外 {mt['oos']} 筆：全跟 {mt['all_R']:+.3f} / 模型挑 {mt['take_R']:+.3f}（{mt['n_take']} 筆）AUC {mt['auc']:.2f}")
    u = res["ultra"]
    if u:
        print(f"\n■ 超高頻（來源帳戶近 30 天）：{u['trades']} 筆 / {u['baskets']} 籃，總損益 {u['total']:+.0f}，"
              f"籃勝率 {u['basket_win']:.0%}，最大回撤 {u['max_dd']:+.0f}")
        print("  設層數上限：" + "  ".join(f"{k}層 {v['total']:+.0f}" for k, v in u["caps"].items()))
    rf = res["range_fade"]
    print(f"\n■ 自有區間逆勢（參數凍結於 2026-09-04，之後的資料）：家族 {rf['family_pos']}/{rf['family_n']} 組為正，"
          f"中位每筆 {rf['family_median']:+.0f} USD/手")
    for k, v in rf["scenarios"].items():
        print(f"  中心點 {k:12} n={v['n']}  每筆 {v['ev']:+.0f}  95%CI [{v['ci'][0]:+.0f}, {v['ci'][1]:+.0f}]  勝率 {v['win']:.0%}")


if __name__ == "__main__":
    main()
