#!/usr/bin/env python3
"""順勢突破擴到多市場 —— 同一個規則，不新增任何參數（協定跑之前寫死）。

    python scripts/multi_trend_backtest.py

  假說    趨勢策略的超額報酬來自跨市場分散（Moskowitz, Ooi & Pedersen 2012 用了 58 個市場）。
          只做黃金時樣本太少（每月 2.3 筆）、而且獲利集中在多單；跨市場可以同時解決兩件事。
  市場    22 個，預先選定（不含加密貨幣）：金、銀、白金、兩種原油、7 個主要外匯、
          10 個股價指數。都用標準合約，不用放大版。
  規則    跟 copy_trader/quant/trend.py 完全相同。
  主檢驗  固定中間參數 H4、通道 55、ATR 3 倍 —— 零參數選擇。
  次檢驗  組合層級前進最佳化：每月用前 24 個月的「全組合」平均 R 替**整個組合**選一組
          （18 組候選、鄰居中位數），不讓各市場各自挑參數。
  成本    每根 K 線的實際點差；滑價 = 該商品點差中位數的一半（兩者都換算成價格單位）。
          點差為 0 的 K 線是缺資料，用該商品非零點差的中位數補；整個商品都沒有點差資料
          就從組合剔除（不能拿零成本的商品去灌績效）。
  部位    每個市場同時最多一筆；每筆風險相同（固定權益比例）。
  期間    開發期到 2023 年底，保留期 2024 起，只跑一次。

全部以 R 計算 —— 各商品報價單位與合約大小不同，只有「以停損距離為單位」才能加總。
輸出 data/market_history/multi_trend_results.json。
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copy_trader.quant import validate as V                                      # noqa: E402
from copy_trader.quant.data import DATA_DIR, Bars, from_rows, resample           # noqa: E402
from copy_trader.quant.trend import BreakoutParams, TrendExecution, run_breakout  # noqa: E402

MULTI = DATA_DIR / "multi"
CLASSES = {
    "金屬": ["XAUUSDm", "XAGUSDm", "XPTUSDm"],
    "能源": ["USOILm", "UKOILm"],
    "外匯": ["EURUSDm", "GBPUSDm", "AUDUSDm", "NZDUSDm", "USDJPYm", "USDCADm", "USDCHFm"],
    "指數": ["US30m", "US500m", "USTECm", "DE30m", "UK100m", "FR40m", "STOXX50m", "JP225m", "AUS200m", "HK50m"],
}
CLASS_OF = {s: c for c, ss in CLASSES.items() for s in ss}
DEV_END = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
PRIMARY = ("H4", BreakoutParams(n=55, k=3.0))
GRID_N, GRID_K, TFS = (20, 55, 100), (2.0, 3.0, 4.0), ("H1", "H4")
PRIOR_TRIALS = 343            # 這一輪研究先前已經試過的組合數（區間逆勢 324 + 黃金順勢 18 + TSMOM 1）


def ts(x):
    return datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%d")


def load_universe(meta):
    out, dropped = {}, {}
    for sym in CLASS_OF:
        p = MULTI / f"{sym}_H1.json"
        if not p.exists():
            dropped[sym] = "沒有資料"
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        if len(rows) < 5000:
            dropped[sym] = f"資料太少（{len(rows)} 根）"
            continue
        sp = np.asarray([r["sp"] for r in rows], dtype=float)
        nz = sp[sp > 0]
        if len(nz) < len(sp) * 0.5:
            dropped[sym] = f"超過一半的 K 線沒有點差資料（{1 - len(nz) / len(sp):.0%}）"
            continue
        fill = float(np.median(nz))
        for r in rows:
            if r["sp"] <= 0:
                r["sp"] = fill
        h1 = from_rows(rows, 3600)
        point = float(meta[sym]["point"])
        ex = TrendExecution(slippage=0.5 * fill * point, cost_per_point=point * 100, flat_cost=0.0)
        out[sym] = {"H1": h1, "H4": resample(h1, 4 * 3600), "ex": ex, "spread_px": fill * point}
    return out, dropped


def run_config(U, tf, p, start, end):
    return {sym: run_breakout(u[tf], u["H1"], p, u["ex"], start=start, end=end) for sym, u in U.items()}


def pooled(by_sym):
    out = [t for v in by_sym.values() for t in v]
    out.sort(key=lambda t: t.exit_t)
    return out


def sharpe_ann(x):
    x = np.asarray(x, dtype=float)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(252)) if len(x) > 2 and x.std() > 0 else 0.0


def daily_close_returns(bars: Bars, start, end):
    day = bars.t // 86400
    keys, last = np.unique(day, return_index=False), None
    closes = {}
    for d, c in zip(day, bars.c):
        closes[int(d)] = c
    days = [x for x in range(int(start // 86400), int(end // 86400) + 1) if (x + 3) % 7 < 5]
    out, prev = [], None
    for d in days:
        c = closes.get(d)
        out.append(0.0 if (c is None or prev is None) else c / prev - 1)
        if c is not None:
            prev = c
    return np.asarray(out)


def max_concurrent(trades):
    ev = sorted([(t.fill_t, 1) for t in trades] + [(t.exit_t, -1) for t in trades])
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def main():
    t0 = time.time()
    meta = json.loads((MULTI / "meta.json").read_text(encoding="utf-8"))
    U, dropped = load_universe(meta)
    print(f"可用市場 {len(U)} 個；剔除 {len(dropped)}：{dropped}")
    # 固定從 2018-03 開始；各市場有資料（且指標暖機完）之後才加入組合。
    # 不等「全部都有資料」才開始，否則最晚上架的那個商品會決定整個回測的長度。
    start = datetime(2018, 3, 1, tzinfo=timezone.utc).timestamp()
    end = min(float(u["H1"].t[-1]) for u in U.values())
    print(f"期間 {ts(start)} ~ {ts(end)}")

    # ── 主檢驗：固定參數，零選擇 ─────────────────────────────────────────
    tf, p = PRIMARY
    prim = run_config(U, tf, p, start, end)
    allp = pooled(prim)
    seg = lambda trs, a, b: [t for t in trs if a <= t.decided < b]
    summ = {name: V.summarize(seg(allp, a, b)) for name, a, b in
            (("full", start, end), ("dev", start, DEV_END), ("hold", DEV_END, end))}
    for k, s in summ.items():
        print(f"主檢驗 {k:4}: {s['n']:5} 筆  每筆 {s['mean_R']:+.3f}R [{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  每月 {s['trades_per_month']:.1f} 筆")

    r = 0.0025
    port_d = sum(V.daily_R(v, start, end) for v in prim.values()) * r
    def port_seg(a, b):
        return sum(V.daily_R(seg(v, a, b), a, b) for v in prim.values()) * r
    gold_d = daily_close_returns(U["XAUUSDm"]["H1"], start, end) if "XAUUSDm" in U else None
    spx_d = daily_close_returns(U["US500m"]["H1"], start, end) if "US500m" in U else None
    bench = {
        "sharpe_full": sharpe_ann(port_d), "sharpe_dev": sharpe_ann(port_seg(start, DEV_END)),
        "sharpe_hold": sharpe_ann(port_seg(DEV_END, end)),
        "gold_only_sharpe": sharpe_ann(V.daily_R(prim.get("XAUUSDm", []), start, end)),
        "corr_gold": float(np.corrcoef(port_d, gold_d)[0, 1]) if gold_d is not None else None,
        "corr_spx": float(np.corrcoef(port_d, spx_d)[0, 1]) if spx_d is not None else None,
        "max_concurrent": max_concurrent(allp),
        "long": V.summarize([t for t in allp if t.side > 0]), "short": V.summarize([t for t in allp if t.side < 0]),
    }
    print(f"組合 Sharpe 全期 {bench['sharpe_full']:.2f}（開發 {bench['sharpe_dev']:.2f} / 保留 {bench['sharpe_hold']:.2f}），"
          f"只做黃金 {bench['gold_only_sharpe']:.2f}；與金價相關 {bench['corr_gold']:+.2f}、與 S&P500 {bench['corr_spx']:+.2f}；"
          f"同時最多 {bench['max_concurrent']} 筆")
    print(f"  多單 {bench['long']['n']} 筆 {bench['long']['mean_R']:+.3f}R   空單 {bench['short']['n']} 筆 {bench['short']['mean_R']:+.3f}R")

    by_class = {}
    for c, syms in CLASSES.items():
        trs = [t for s in syms if s in prim for t in prim[s]]
        if trs:
            s = V.summarize(trs)
            s["long_R"] = float(np.mean([t.R for t in trs if t.side > 0])) if any(t.side > 0 for t in trs) else None
            s["short_R"] = float(np.mean([t.R for t in trs if t.side < 0])) if any(t.side < 0 for t in trs) else None
            by_class[c] = s
            print(f"  {c}: {s['n']:4} 筆  每筆 {s['mean_R']:+.3f}R [{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  多 {s['long_R']:+.2f} 空 {s['short_R']:+.2f}")
    by_sym = {sym: {**V.summarize(v), "class": CLASS_OF[sym]} for sym, v in prim.items() if v}
    years = {}
    for t in allp:
        years.setdefault(datetime.fromtimestamp(t.decided, timezone.utc).year, []).append(t)
    by_year = {}
    for y, v in sorted(years.items()):
        s = V.summarize(v)
        s["long_R"] = float(np.mean([t.R for t in v if t.side > 0])) if any(t.side > 0 for t in v) else None
        s["short_R"] = float(np.mean([t.R for t in v if t.side < 0])) if any(t.side < 0 for t in v) else None
        by_year[str(y)] = s
        print(f"  {y}: {s['n']:4} 筆  每筆 {s['mean_R']:+.3f}R  多 {s['long_R'] if s['long_R'] is not None else float('nan'):+.2f}  空 {s['short_R'] if s['short_R'] is not None else float('nan'):+.2f}")

    # ── 次檢驗：組合層級前進最佳化 ───────────────────────────────────────
    table, kp = {}, {}
    for tf2, n, k in itertools.product(TFS, GRID_N, GRID_K):
        key = f"{tf2}-n{n}-k{k}"
        table[key] = pooled(run_config(U, tf2, BreakoutParams(n=n, k=k), start, end))
        kp[(tf2, n, k)] = key
    nb = {}
    for (tf2, n, k), key in kp.items():
        i, j = GRID_N.index(n), GRID_K.index(k)
        nb[key] = [kp[(tf2, GRID_N[i + di], GRID_K[j + dj])] for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1))
                   if 0 <= i + di < 3 and 0 <= j + dj < 3]
    print(f"18 組算完（{time.time() - t0:.0f}s）")
    w = V.walk_forward(table, nb, start, end, train_months=24, min_trades=50)
    wf_oos = [t for x in w for t in x.oos]
    wf = {name: V.summarize([t for t in wf_oos if a <= t.decided < b]) for name, a, b in
          (("full", start, end), ("dev", start, DEV_END), ("hold", DEV_END, end))}
    for k2, s in wf.items():
        print(f"前進最佳化 {k2:4}: {s['n']:5} 筆  每筆 {s['mean_R']:+.3f}R [{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]")
    cols, trial_sr = [], []
    for key, trs in table.items():
        dd = V.daily_R([t for t in trs if t.decided < DEV_END], start, DEV_END)
        cols.append(dd)
        trial_sr.append(dd.mean() / dd.std(ddof=1) if dd.std() > 0 else 0.0)
    pbo = V.cscv_pbo(np.stack(cols, axis=1), S=16)
    print(f"PBO（組合層級 18 組）= {pbo['pbo']:.2f}")

    # ── 統計 ────────────────────────────────────────────────────────────
    sel = sum(V.daily_R(v, start, end) for v in prim.values())
    dsr = V.deflated_sharpe(sel, trial_sr)
    dsr_strict = V.deflated_sharpe(sel, list(trial_sr) * max(1, round((PRIOR_TRIALS + 19) / 18)))
    sr_t = summ["full"]["sharpe_trade"]
    need = (1.96 / sr_t) ** 2 if sr_t > 0 else float("inf")
    sizing = {f"{rr:.4f}": {**V.equity_stats(allp, rr, start, end), **V.monte_carlo(allp, rr)} for rr in (0.001, 0.0025, 0.005)}
    print(f"DSR {dsr['dsr']:.3f}（18 組）/ {dsr_strict['dsr']:.3f}（計入全部 {PRIOR_TRIALS + 19} 組）；"
          f"要讓區間離開 0 約需 {need:.0f} 筆，目前每月 {summ['full']['trades_per_month']:.1f} 筆")
    for k2, v in sizing.items():
        print(f"  每筆風險 {float(k2):.2%}: CAGR {v['cagr']:+.1%}  最大回撤 {v['max_dd']:.1%}  MC p95 {v['dd_p95']:.1%}  Sharpe {v['sharpe_ann']:.2f}")

    # 成本敏感度（主檢驗）：點差與滑價 × 2
    U2 = {s: {**u, "ex": replace(u["ex"], slippage=u["ex"].slippage * 2, cost_per_point=u["ex"].cost_per_point * 2)}
          for s, u in U.items()}
    s2 = V.summarize(pooled(run_config(U2, tf, p, start, end)))
    print(f"成本 × 2：每筆 {s2['mean_R']:+.3f}R [{s2['ci_lo']:+.3f},{s2['ci_hi']:+.3f}]")

    eq = V.equity_curve(allp, r)
    out = {
        "period": [ts(start), ts(end)], "dev_end": ts(DEV_END - 1), "universe": list(U), "dropped": dropped,
        "primary": {"tf": tf, "n": p.n, "k": p.k}, "summary": summ, "bench": bench, "by_class": by_class,
        "by_sym": by_sym, "by_year": by_year, "wf": wf, "pbo": pbo, "dsr": dsr, "dsr_strict": dsr_strict,
        "need_n": need, "sizing": sizing, "cost_x2": {"mean_R": s2["mean_R"], "ci": [s2["ci_lo"], s2["ci_hi"]]},
        "chosen": [[ts(x.test[0]), x.chosen] for x in w], "prior_trials": PRIOR_TRIALS,
        "curve": [[t.exit_t, round(float(e), 5)] for t, e in zip(allp, eq)][::2],
        "gold_curve": [[t.exit_t, round(float(e), 5)] for t, e in zip(prim.get("XAUUSDm", []), V.equity_curve(prim.get("XAUUSDm", []), r))],
    }
    for s in list(out["summary"].values()) + list(out["wf"].values()):
        s.pop("chosen", None)
    (DATA_DIR / "multi_trend_results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n完成（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main()
