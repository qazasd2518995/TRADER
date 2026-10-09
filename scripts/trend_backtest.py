#!/usr/bin/env python3
"""順勢模型 —— 完整回測與驗證流程（協定跑之前寫死，跑完不改）。

    python scripts/trend_backtest.py

  假說    黃金有時間序列動能（Moskowitz, Ooi & Pedersen 2012）。
  資料    XAUUSDm H1 2018-01 起（8.8 年）；M15 2022-07 起只用來檢查粒度。
  開發期  2018-01 ~ 2023-12（含 2021–23 的盤整，順勢的弱點要在這裡被測到）。
  保留期  2024-01 ~ 最後。決定做完才跑，只跑一次。
  主模型  Donchian 突破 + ATR 移動停損。週期 {H1, H4} × 通道 {20, 55, 100}
          × ATR 倍數 {2, 3, 4} = 18 組。前進最佳化：滾動 24 個月訓練、1 個月測試，
          分數 = 自己與相鄰參數的中位數。
  對照    TSMOM（20/60/120/250 日訊號平均、波動率目標 10%），零參數選擇。
  基準    買進持有黃金。順勢策略在這 8 年大多頭裡賺錢可能只是賺到漲勢，
          所以一定要比 Sharpe、看跟金價的相關、把多空拆開。
  成本    實際點差 + 停損單每次滑價 0.1 美元。

輸出 data/market_history/trend_results.json 給報告用。
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copy_trader.quant import validate as V                                       # noqa: E402
from copy_trader.quant.data import DATA_DIR, load, resample                       # noqa: E402
from copy_trader.quant.trend import (BreakoutParams, TrendExecution, daily_from_h1,  # noqa: E402
                                     run_breakout, tsmom)

DEV_END = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
GRID_N, GRID_K, TFS = (20, 55, 100), (2.0, 3.0, 4.0), ("H1", "H4")
EXEC = TrendExecution(slippage=0.10, cost_per_point=0.1)


def ts(x):
    return datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%d")


def build_table(bars, fine, ex, start, end):
    table, kp = {}, {}
    for tf in TFS:
        dec = bars[tf]
        for n, k in itertools.product(GRID_N, GRID_K):
            p = BreakoutParams(n=n, k=k)
            key = f"{tf}-{p.key()}"
            table[key] = run_breakout(dec, fine[tf], p, ex, start=start, end=end)
            kp[(tf, n, k)] = key
    nb = {}
    for (tf, n, k), key in kp.items():
        out = []
        i, j = GRID_N.index(n), GRID_K.index(k)
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if 0 <= i + di < len(GRID_N) and 0 <= j + dj < len(GRID_K):
                out.append(kp[(tf, GRID_N[i + di], GRID_K[j + dj])])
        nb[key] = out
    return table, nb


def sharpe_ann(x):
    x = np.asarray(x, dtype=float)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(252)) if len(x) > 2 and x.std() > 0 else 0.0


def gold_daily(d, start, end):
    """每個工作日的金價報酬（收盤到收盤），跟 validate.daily_R 同一套日曆。"""
    r = {int(day): rr for day, rr in zip(d["day"][1:], d["close"][1:] / d["close"][:-1] - 1)}
    days = [x for x in range(int(start // 86400), int(end // 86400) + 1) if (x + 3) % 7 < 5]
    return np.asarray([r.get(x, 0.0) for x in days])


def main():
    t0 = time.time()
    h1 = load("XAUUSDm_H1.json", 3600)
    h4 = resample(h1, 4 * 3600)
    m15 = load("XAUUSDm_M15.json", 900)
    bars, fine = {"H1": h1, "H4": h4}, {"H1": h1, "H4": h1}
    start = float(h1.t[0]) + 30 * 86400
    end = float(h1.t[-1])
    print(f"H1 {ts(h1.t[0])} ~ {ts(end)}（{len(h1)} 根），H4 {len(h4)} 根")

    table, nb = build_table(bars, fine, EXEC, start, end)
    print(f"18 組算完（{time.time() - t0:.0f}s）")

    # ── 開發期：前進最佳化 ────────────────────────────────────────────────
    dev_tab = {k: [t for t in v if t.exit_t <= DEV_END] for k, v in table.items()}
    w_dev = V.walk_forward(dev_tab, nb, start, DEV_END, train_months=24, min_trades=20)
    s_dev = V.summarize([t for w in w_dev for t in w.oos])
    print(f"開發期前進 樣本外 {s_dev['n']} 筆  每筆 {s_dev['mean_R']:+.3f}R [{s_dev['ci_lo']:+.3f},{s_dev['ci_hi']:+.3f}]")

    # PBO：開發期 18 組
    cols, trial_sr = [], []
    for key, tr in table.items():
        d = V.daily_R([t for t in tr if t.exit_t <= DEV_END], start, DEV_END)
        cols.append(d)
        trial_sr.append(d.mean() / d.std(ddof=1) if d.std() > 0 else 0.0)
    pbo = V.cscv_pbo(np.stack(cols, axis=1), S=16)
    print(f"PBO（開發期 18 組）= {pbo['pbo']:.2f}")

    # ── 全期前進（保留期只是其中一段，訓練窗只看過去）─────────────────────────
    w_all = V.walk_forward(table, nb, start, end, train_months=24, min_trades=20)
    oos = [t for w in w_all for t in w.oos]
    oos_start = w_all[0].test[0]
    hold = V.summarize([t for w in w_all if w.test[0] >= DEV_END for t in w.oos])
    full = V.summarize(oos)
    print(f"保留期 {hold['n']} 筆  每筆 {hold['mean_R']:+.3f}R [{hold['ci_lo']:+.3f},{hold['ci_hi']:+.3f}]")
    print(f"全期樣本外 {full['n']} 筆  每筆 {full['mean_R']:+.3f}R [{full['ci_lo']:+.3f},{full['ci_hi']:+.3f}]  "
          f"勝率 {full['win']:.0%}  每月 {full['trades_per_month']:.1f} 筆")

    # ── 基準與 beta ─────────────────────────────────────────────────────
    d = daily_from_h1(h1)
    risk = 0.005
    strat_d = V.daily_R(oos, oos_start, end) * risk
    gold_d = gold_daily(d, oos_start, end)
    corr = float(np.corrcoef(strat_d, gold_d)[0, 1])
    longs = [t for t in oos if t.side > 0]; shorts = [t for t in oos if t.side < 0]
    hold_start = DEV_END
    bench = {
        "strategy_sharpe": sharpe_ann(strat_d), "gold_sharpe": sharpe_ann(gold_d), "corr_with_gold": corr,
        "strategy_sharpe_hold": sharpe_ann(V.daily_R([t for t in oos if t.decided >= hold_start], hold_start, end)),
        "gold_sharpe_hold": sharpe_ann(gold_daily(d, hold_start, end)),
        "long": V.summarize(longs), "short": V.summarize(shorts),
    }
    print(f"Sharpe 策略 {bench['strategy_sharpe']:.2f} vs 買進持有 {bench['gold_sharpe']:.2f}，與金價相關 {corr:+.2f}")
    print(f"  多單 {bench['long']['n']} 筆 {bench['long']['mean_R']:+.3f}R   空單 {bench['short']['n']} 筆 {bench['short']['mean_R']:+.3f}R")

    # ── 逐年 ────────────────────────────────────────────────────────────
    years = {}
    for t in oos:
        years.setdefault(datetime.fromtimestamp(t.decided, timezone.utc).year, []).append(t)
    by_year = {}
    for y, v in sorted(years.items()):
        s = V.summarize(v)
        a = datetime(y, 1, 1, tzinfo=timezone.utc).timestamp(); b = datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp()
        g = gold_daily(d, max(a, oos_start), min(b, end))
        s["gold_return"] = float(np.prod(1 + g) - 1)
        s["long_R"] = float(np.mean([t.R for t in v if t.side > 0])) if any(t.side > 0 for t in v) else None
        s["short_R"] = float(np.mean([t.R for t in v if t.side < 0])) if any(t.side < 0 for t in v) else None
        by_year[str(y)] = s
        print(f"  {y}: {s['n']:4} 筆  每筆 {s['mean_R']:+.3f}R  金價 {s['gold_return']:+.0%}  "
              f"多 {s['long_R'] if s['long_R'] is not None else float('nan'):+.2f} 空 {s['short_R'] if s['short_R'] is not None else float('nan'):+.2f}")

    # ── 敏感度 ──────────────────────────────────────────────────────────
    sens = {}
    for slip in (0.1, 0.3, 0.5):
        tab, nb2 = build_table(bars, fine, TrendExecution(slippage=slip, cost_per_point=0.1), start, end)
        s = V.summarize([t for w in V.walk_forward(tab, nb2, start, end, train_months=24, min_trades=20) for t in w.oos])
        sens[f"slip_{slip}"] = {"n": s["n"], "mean_R": s["mean_R"], "ci": [s["ci_lo"], s["ci_hi"]]}
        print(f"  滑價 {slip}: 每筆 {s['mean_R']:+.3f}R")
    R = np.asarray([t.R for t in oos]); rk = np.asarray([t.risk for t in oos]); cst = np.asarray([t.cost for t in oos])
    for m in (1.5, 2.0):
        adj = R - cst * (m - 1) / (rk * 100)
        sens[f"cost_x{m}"] = {"n": len(adj), "mean_R": float(adj.mean()), "ci": list(V.bootstrap_ci(adj))}

    # 粒度：2022-08 起，H1 決策、M15 判停損 vs H1 判停損
    a15 = float(m15.t[0]) + 30 * 86400
    res = {}
    for name, fn in (("H1", h1), ("M15", m15)):
        trs = []
        for nn, kk in itertools.product(GRID_N, GRID_K):
            trs += run_breakout(h1, fn, BreakoutParams(n=nn, k=kk), EXEC, start=a15, end=end)
        s = V.summarize(trs)
        res[name] = {"n": s["n"], "mean_R": s["mean_R"], "ci": [s["ci_lo"], s["ci_hi"]]}
        print(f"  粒度 {name}（H1 九組合併、{ts(a15)} 起）: 每筆 {s['mean_R']:+.3f}R  n={s['n']}")

    # ── TSMOM 對照 ──────────────────────────────────────────────────────
    tm = tsmom(d); bh = tsmom(d, long_only_benchmark=True)
    def seg(x, a, b):
        m = (d["day"] * 86400 >= a) & (d["day"] * 86400 < b)
        return x[m]
    ts_res = {}
    for name, a, b in (("full", start + 250 * 86400, end), ("dev", start + 250 * 86400, DEV_END), ("hold", DEV_END, end)):
        r1, r2 = seg(tm["ret"], a, b), seg(bh["ret"], a, b)
        ts_res[name] = {"tsmom_sharpe": sharpe_ann(r1), "bh_sharpe": sharpe_ann(r2),
                        "tsmom_cagr": float(np.prod(1 + r1) ** (252 / len(r1)) - 1),
                        "bh_cagr": float(np.prod(1 + r2) ** (252 / len(r2)) - 1),
                        "corr": float(np.corrcoef(r1, r2)[0, 1])}
        print(f"  TSMOM {name}: Sharpe {ts_res[name]['tsmom_sharpe']:.2f} vs 買進持有（同波動率）{ts_res[name]['bh_sharpe']:.2f}，相關 {ts_res[name]['corr']:+.2f}")
    ts_year = {}
    for y in range(2019, 2027):
        a = datetime(y, 1, 1, tzinfo=timezone.utc).timestamp(); b = datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp()
        r1, r2 = seg(tm["ret"], a, b), seg(bh["ret"], a, b)
        if len(r1) > 20:
            ts_year[str(y)] = {"tsmom": float(np.prod(1 + r1) - 1), "bh": float(np.prod(1 + r2) - 1)}

    # ── Deflated Sharpe、部位大小、蒙地卡羅 ─────────────────────────────────
    sel_daily = V.daily_R(oos, oos_start, end)
    dsr = V.deflated_sharpe(sel_daily, trial_sr)
    # 更嚴格：把這一輪研究所有試過的組合都算進去（區間逆勢 324 + 這裡 18 + TSMOM 1）
    dsr_strict = V.deflated_sharpe(sel_daily, list(trial_sr) * 19)
    kelly = V.kelly_fraction(oos)
    sizing = {f"{r:.4f}": {**V.equity_stats(oos, r, oos_start, end), **V.monte_carlo(oos, r)}
              for r in (0.0025, 0.005, 0.01)}
    print(f"DSR {dsr['dsr']:.3f}（18 組）/ {dsr_strict['dsr']:.3f}（計入全部 343 組）  Kelly {kelly:.3f}")
    for k, v in sizing.items():
        print(f"  風險 {float(k):.2%}: CAGR {v['cagr']:+.1%}  最大回撤 {v['max_dd']:.1%}  MC p95 {v['dd_p95']:.1%}")

    eq = V.equity_curve(oos, 0.005)
    out = {
        "data": {"h1": [ts(h1.t[0]), ts(end)], "dev_end": ts(DEV_END - 1), "oos_start": ts(oos_start)},
        "dev": s_dev, "pbo": pbo, "holdout": hold, "full": full, "bench": bench, "by_year": by_year,
        "sens": sens, "resolution": res, "tsmom": ts_res, "tsmom_year": ts_year,
        "dsr": dsr, "dsr_strict": dsr_strict, "kelly": kelly, "sizing": sizing,
        "chosen": [[ts(w.test[0]), w.chosen] for w in w_all],
        "curve": [[t.exit_t, round(float(e), 5)] for t, e in zip(oos, eq)],
        "gold_curve": [[float(dd * 86400), float(c)] for dd, c in zip(d["day"], d["close"]) if dd * 86400 >= oos_start][::3],
        "dev_split": ts(DEV_END),
    }
    for s in (out["dev"], out["holdout"], out["full"]):
        s.pop("chosen", None)
    (DATA_DIR / "trend_results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n完成（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main()
