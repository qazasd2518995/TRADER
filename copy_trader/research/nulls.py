"""對照組與穩定性檢驗 —— 把「看起來有效」跟「真的有效」分開。

文獻（Bailey & López de Prado 的 Deflated Sharpe、PBO）反覆強調的是同一件事：
試過的變化越多，越容易找到一個樣本內好看的。這裡的每個檢驗都是為了擋那件事：

* matched_limit_null —— 提供者的訊號 vs「同方向、同掛單距離、同停損止盈結構、
  但時刻隨機」的限價單。要比的不是 0，是「隨便掛一張限價單」。限價單在均值回歸
  的市場裡本來就會有正的成交後走勢，不扣掉這一塊，會把限價單的性質誤當成提供者
  的功力。
* grid_baskets / capped_grid —— 把網格 EA 的成交依「整籃平倉」重建，並回推
  「第 N 層就認賠」的反事實，用來回答馬丁格爾到底有沒有底層優勢。
"""
from __future__ import annotations

import random
import statistics as st
from typing import Any, Dict, List, Sequence, Tuple

from copy_trader.backtest.engine import Rules, simulate
from copy_trader.research.dataset import COST_PER_LOT, Bars, atr


def matched_limit_null(rows: Sequence[Dict[str, Any]], m1: Bars, m5: Bars, *,
                       k: int = 25, seed: int = 23) -> Dict[str, Any]:
    """每筆訊號配 k 張隨機時刻的對照限價單，回傳訊號 vs 對照的實現 R。"""
    rnd = random.Random(seed)
    lo_ts, hi_ts = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
    null_R: List[float] = []
    for r in rows:
        sign = 1 if r["direction"] == "buy" else -1
        sl, tp1 = abs(r["entry"] - r["stop"]), abs(r["tp1"] - r["entry"])
        for _ in range(k):
            t = rnd.uniform(lo_ts, hi_ts - 30 * 3600)
            bars = m1 if t >= m1.start + 86400 else m5
            px, a5 = bars.close_at(t), atr(m5.closed_before(t, 30), 14)
            if px is None or not a5:
                continue
            entry = px - sign * r["wait_atr"] * a5
            tr = simulate(r["direction"], entry, entry - sign * sl, [entry + sign * tp1], t,
                          bars.closed_before(t, 2) + bars.window(t, 30), Rules())
            if tr is not None and tr.settled and tr.filled:
                null_R.append((tr.profit - COST_PER_LOT) / (sl * 100))
    sig_R = [r["R"] for r in rows if r.get("filled")]
    perm = [st.fmean(rnd.sample(null_R, len(sig_R))) for _ in range(4000)]
    p = sum(1 for x in perm if x >= st.fmean(sig_R)) / len(perm)
    return {"signal_R": st.fmean(sig_R), "null_R": st.fmean(null_R),
            "excess": st.fmean(sig_R) - st.fmean(null_R), "p_value": p, "n_null": len(null_R)}


def grid_baskets(trades: Sequence[Dict[str, Any]], gap: float = 90.0) -> List[List[Dict[str, Any]]]:
    """同方向、在 gap 秒內一起被平掉的部位 = 一籃（網格 EA 的整籃平倉）。"""
    out: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for x in sorted(trades, key=lambda x: x.get("close_timestamp") or 0):
        if cur and (x["close_timestamp"] - cur[-1]["close_timestamp"] > gap
                    or x["position_type"] != cur[-1]["position_type"]):
            out.append(cur)
            cur = []
        cur.append(x)
    if cur:
        out.append(cur)
    return out


def capped_grid(basket: Sequence[Dict[str, Any]], cap: int) -> Tuple[float, bool]:
    """第 cap+1 層「本來要加碼」的那一刻改成整籃認賠。

    以第 cap+1 層的進場價當平倉價 —— 那就是價格走到那裡的時刻。扣 20 USD/手。
    回傳 (損益, 是否被截斷)。這是近似：不含那一瞬間的滑價。
    """
    legs = sorted(basket, key=lambda x: x["open_timestamp"])
    if len(legs) <= cap:
        return sum(float(x["profit"]) for x in legs), False
    sign = 1 if legs[0]["position_type"] == "buy" else -1
    exit_px = float(legs[cap]["entry_price"])
    pnl = sum((exit_px - float(x["entry_price"])) * sign * float(x["volume"]) * 100
              - COST_PER_LOT * float(x["volume"]) for x in legs[:cap])
    return pnl, True
