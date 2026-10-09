#!/usr/bin/env python3
"""區間逆勢模型 —— 完整回測與驗證流程。

    python scripts/quant_backtest.py

研究協定（跑之前就寫死，跑完不改）：

  資料    XAUUSDm（Exness 標準黃金，GMT+0）M15 2022-07 起、H1 2018 起。
          XAUUSD247m 只有 2025-12 之後，太短。兩者是同一個現貨標的。
  開發期  資料開頭 ~ 2026-06-30。所有選擇（參數、過濾器）只看這段。
  保留期  2026-07-01 ~ 最後。決定都做完之後才跑，只跑一次。
  候選    參數 81 組（09-04 研究定下的家族）× 過濾器 4 種 = 324 組。
          過濾器門檻用文獻慣例值（ADX 25、美國數據時段 UTC 12–16），不最佳化。
  成本    成交那根 K 線的實際點差（2022–24 約 20、2025 約 16、2026 約 26 美元/手）。
  成交    碰到即成交（點差已計入成本）；另做「須穿價 0.3」的敏感度。
  掛單    價位凍結 4 小時，到期才重掛（舊回測器「沒成交就下一根重掛」用了未來資訊）。
  選參數  滾動 12 個月訓練 → 下 1 個月測試；分數 = 自己與相鄰參數的中位數。
  選過濾器 開發期前進最佳化的樣本外平均 R 最高者。

輸出 data/market_history/quant_results.json 給報告用。
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from copy_trader.quant import validate as V                                   # noqa: E402
from copy_trader.quant.data import DATA_DIR, load                             # noqa: E402
from copy_trader.quant.model import Context, Execution, Filters, Params, Trade, run  # noqa: E402

GRID = {"lookback": (6, 8, 12), "offset_k": (1.5, 1.75, 2.0),
        "stop_k": (0.6, 0.8, 1.0), "reward": (0.8, 1.0, 1.5)}
FILTERS = {"none": Filters(), "adx25": Filters(adx_max=25),
           "us_data": Filters(skip_hours=(12, 13, 14, 15)),
           "adx25+us_data": Filters(adx_max=25, skip_hours=(12, 13, 14, 15))}
EXEC = Execution(buffer=0.0, cost_per_point=0.1)    # 成本 = 成交當根的實際點差
DEV_END = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
CENTER = Params(lookback=8, offset_k=1.75, stop_k=0.8, reward=1.0)


def ts(x: float) -> str:
    return datetime.fromtimestamp(x, timezone.utc).strftime("%Y-%m-%d")


def all_params():
    for lb, off, sk, rw in itertools.product(*GRID.values()):
        yield Params(lookback=lb, offset_k=off, stop_k=sk, reward=rw)


def neighbours(keys_by_params):
    """參數格子上「只差一格」的鄰居（同一個過濾器）。"""
    axes = list(GRID)
    out = {}
    for p, key in keys_by_params.items():
        nb = []
        for ax in axes:
            vals = GRID[ax]
            i = vals.index(getattr(p, ax))
            for j in (i - 1, i + 1):
                if 0 <= j < len(vals):
                    q = Params(**{**p.__dict__, ax: vals[j]})
                    if q in keys_by_params:
                        nb.append(keys_by_params[q])
        out[key] = nb
    return out


def compute_table(ctx, flt, ex, start, end):
    table, kp = {}, {}
    for p in all_params():
        key = p.key()
        table[key] = run(ctx, p, flt, ex, start=start, end=end)
        kp[p] = key
    return table, neighbours(kp)


def stitched(windows):
    return [t for w in windows for t in w.oos]


def wf_summary(windows, start, end):
    oos = stitched(windows)
    s = V.summarize(oos)
    if windows:
        is_mean = float(np.mean([w.is_mean_R for w in windows]))
        s["wfe"] = (s["mean_R"] / is_mean) if is_mean > 0 and s.get("n") else None
        s["months"] = len(windows)
        s["chosen"] = [w.chosen for w in windows]
    return s


def main():
    t0 = time.time()
    dec = load("XAUUSDm_M15.json", 900)
    h1 = load("XAUUSDm_H1.json", 3600)
    start = float(dec.t[0]) + 30 * 86400                      # ADX 暖機
    end = float(dec.t[-1])
    ctx = Context.build(dec, dec, h1)
    print(f"資料 M15 {ts(dec.t[0])} ~ {ts(dec.t[-1])}（{len(dec)} 根），H1 {ts(h1.t[0])} 起")
    print(f"開發期 ~ {ts(DEV_END - 1)}，保留期 {ts(DEV_END)} ~ {ts(end)}\n")

    # ── 1. 全部 324 組的交易，只算一次給所有檢驗共用 ─────────────────────
    tables, nbs = {}, {}
    for name, flt in FILTERS.items():
        tables[name], nbs[name] = compute_table(ctx, flt, EXEC, start, end)
        print(f"  {name:14} 81 組算完（{time.time() - t0:.0f}s）")

    # ── 2. 開發期：前進最佳化，選過濾器 ──────────────────────────────────
    dev = {}
    for name in FILTERS:
        dev_tab = {k: [t for t in v if t.decided < DEV_END and t.exit_t <= DEV_END] for k, v in tables[name].items()}
        w = V.walk_forward(dev_tab, nbs[name], start, DEV_END)
        dev[name] = wf_summary(w, start, DEV_END)
        s = dev[name]
        print(f"  開發期前進 {name:14} 樣本外 {s['n']:4} 筆  每筆 {s['mean_R']:+.3f}R "
              f"[{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  WFE {s.get('wfe') or 0:.2f}")
    chosen_filter = max(dev, key=lambda k: dev[k]["mean_R"])
    print(f"\n  → 選定過濾器：{chosen_filter}（依開發期樣本外平均 R）")

    # ── 3. PBO：開發期、全部 324 組 ─────────────────────────────────────
    cols, names, trial_sr = [], [], []
    for name in FILTERS:
        for key, tr in tables[name].items():
            d = V.daily_R([t for t in tr if t.decided < DEV_END], start, DEV_END)
            cols.append(d); names.append(f"{name}|{key}")
            trial_sr.append(d.mean() / d.std(ddof=1) if d.std() > 0 else 0.0)
    pbo = V.cscv_pbo(np.stack(cols, axis=1), S=16)
    print(f"  PBO（開發期 324 組、16 塊、{pbo['splits']} 種切法）= {pbo['pbo']:.2f}")

    # ── 4. 保留期：只跑一次 ──────────────────────────────────────────────
    w_all = V.walk_forward(tables[chosen_filter], nbs[chosen_filter], start, end)
    w_hold = [w for w in w_all if w.test[0] >= DEV_END]
    w_dev = [w for w in w_all if w.test[0] < DEV_END]
    hold = wf_summary(w_hold, DEV_END, end)
    full = wf_summary(w_all, start, end)
    print(f"  保留期 樣本外 {hold.get('n', 0)} 筆  每筆 {hold.get('mean_R', 0):+.3f}R "
          f"[{hold.get('ci_lo', 0):+.3f},{hold.get('ci_hi', 0):+.3f}]")

    oos = stitched(w_all)
    oos_start = w_all[0].test[0]

    # ── 5. 對照：固定中心參數（不再最佳化）、無過濾 ─────────────────────
    center_fixed = [t for t in tables[chosen_filter][CENTER.key()] if t.decided >= oos_start]
    center_none = [t for t in tables["none"][CENTER.key()] if t.decided >= oos_start]

    # ── 6. 穩健性 ───────────────────────────────────────────────────────
    R = np.asarray([t.R for t in oos])
    risk_per_trade = np.asarray([t.risk for t in oos])
    cost_sens = {}
    for mult in (1.0, 1.5, 2.0):
        cost_now = np.asarray([t.cost for t in oos])
        adj = R - cost_now * (mult - 1) / (risk_per_trade * 100)
        cost_sens[f"x{mult}"] = {"mean_R": float(adj.mean()), "ci": V.bootstrap_ci(adj)}
    buf_sens = {}
    for b in (0.0, 0.3, 0.6):
        tab, nb = compute_table(ctx, FILTERS[chosen_filter], Execution(buffer=b, cost_per_point=0.1), start, end)
        s = wf_summary(V.walk_forward(tab, nb, start, end), start, end)
        buf_sens[str(b)] = {"n": s["n"], "mean_R": s["mean_R"], "ci": [s["ci_lo"], s["ci_hi"]]}
        print(f"  成交敏感度 穿價 {b}: 每筆 {s['mean_R']:+.3f}R  n={s['n']}")

    years = {}
    for t in oos:
        years.setdefault(datetime.fromtimestamp(t.decided, timezone.utc).year, []).append(t)
    by_year = {str(y): V.summarize(v) for y, v in sorted(years.items())}

    # 參數平原：開發期、選定過濾器，(掛單倍數, 停損倍數) 的平均 R（對回看與盈虧比取平均）
    plateau = {}
    for p in all_params():
        tr = [t for t in tables[chosen_filter][p.key()] if t.decided < DEV_END]
        if tr:
            plateau.setdefault((p.offset_k, p.stop_k), []).append(float(np.mean([t.R for t in tr])))
    plateau = {f"{a}|{b}": float(np.mean(v)) for (a, b), v in plateau.items()}

    # ── 7. 部位大小、蒙地卡羅、Deflated Sharpe ──────────────────────────
    kelly = V.kelly_fraction(oos)
    sizing = {}
    for r in (0.0025, 0.005, 0.01, kelly / 4 if kelly > 0 else 0.0):
        if r > 0:
            sizing[f"{r:.4f}"] = {**V.equity_stats(oos, r, oos_start, end), **V.monte_carlo(oos, r)}
    sel_daily = V.daily_R(oos, oos_start, end)
    dsr = V.deflated_sharpe(sel_daily, trial_sr)

    # ── 8. 細粒度驗證：有 M5 的期間，用 M5 判成交與出場再跑一次 ────────────
    res_check = None
    m5_path = DATA_DIR / "XAUUSDm_M5.json"
    if m5_path.exists():
        m5 = load("XAUUSDm_M5.json", 300)
        a, b = float(m5.t[0]) + 86400, end
        ctx5 = Context.build(dec, m5, h1)
        res_check = {}
        for name, c in (("M15", ctx), ("M5", ctx5)):
            tab, nb = compute_table(c, FILTERS[chosen_filter], EXEC, start, end)
            w = [x for x in V.walk_forward(tab, nb, start, end) if x.test[0] >= a]
            s = wf_summary(w, a, b)
            res_check[name] = {"n": s.get("n", 0), "mean_R": s.get("mean_R"), "ci": [s.get("ci_lo"), s.get("ci_hi")]}
            print(f"  粒度 {name}（{ts(a)} 起）: 每筆 {s.get('mean_R', 0):+.3f}R  n={s.get('n', 0)}")

    # ── 輸出 ────────────────────────────────────────────────────────────
    def curve(trs, risk=0.005):
        eq = V.equity_curve(trs, risk)
        return [[t.exit_t, round(float(e), 5)] for t, e in zip(trs, eq)]

    out = {
        "data": {"m15": [ts(dec.t[0]), ts(dec.t[-1])], "h1_from": ts(h1.t[0]), "dev_end": ts(DEV_END - 1),
                 "oos_start": ts(oos_start), "end": ts(end), "bars": len(dec)},
        "protocol": {"configs": len(names), "filters": list(FILTERS), "cost": "spread", "buffer": EXEC.buffer},
        "dev": dev, "chosen_filter": chosen_filter, "pbo": pbo,
        "holdout": hold, "full": full, "by_year": by_year,
        "center_fixed": V.summarize(center_fixed), "center_none": V.summarize(center_none),
        "cost_sens": cost_sens, "buf_sens": buf_sens, "plateau": plateau,
        "kelly": kelly, "sizing": sizing, "dsr": dsr, "resolution": res_check,
        "chosen_params": [w.chosen for w in w_all],
        "months": [[ts(w.test[0]), w.chosen, float(np.sum([t.R for t in w.oos])), len(w.oos)] for w in w_all],
        "curve": curve(oos), "curve_center_none": curve(center_none),
        "dev_split": ts(DEV_END),
    }
    (DATA_DIR / "quant_results.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n完成（{time.time() - t0:.0f}s）")
    s = full
    print(f"全期樣本外（{out['data']['oos_start']} ~ {out['data']['end']}）{s['n']} 筆  每筆 {s['mean_R']:+.3f}R "
          f"[{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}]  勝率 {s['win']:.0%}  PF {s['pf']:.2f}  每月 {s['trades_per_month']:.0f} 筆")
    print(f"DSR = {dsr['dsr']:.3f}（年化 Sharpe {dsr['sr_ann']:.2f}，試過 {dsr['trials']} 組）  Kelly = {kelly:.3f}")
    for k, v in sizing.items():
        print(f"  每筆風險 {float(k):.2%}: CAGR {v['cagr']:+.1%}  最大回撤 {v['max_dd']:.1%}  "
              f"蒙地卡羅回撤 p95 {v['dd_p95']:.1%}  年化 Sharpe {v['sharpe_ann']:.2f}")
    for y, v in by_year.items():
        print(f"  {y}: {v['n']:4} 筆  每筆 {v['mean_R']:+.3f}R  [{v['ci_lo']:+.3f},{v['ci_hi']:+.3f}]")


if __name__ == "__main__":
    main()
