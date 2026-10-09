"""順勢模型：Donchian 通道突破 + ATR 移動停損，以及經典 TSMOM 對照。

假說
  黃金有時間序列動能（Moskowitz, Ooi & Pedersen 2012, "Time Series Momentum"，
  樣本含黃金期貨）：資訊被逐步吸收、資金慢慢移動，趨勢一旦形成傾向延續。
  失敗情境：盤整（假突破被來回打停損）與急反轉（移動停損來不及）。

為什麼主模型是通道突破而不是 TSMOM
  通道突破是一筆一筆的單：停損單進場、有初始停損、用「修改停損」做移動停損 ——
  訊號中心 → Hub → 會員端 EA 這條路現在就能執行。TSMOM 要每天調整部位大小，
  現有的跟單架構做不到，所以只拿來確認「黃金的趨勢溢酬存不存在」。

交易規則（通道突破）
  1. 空手時，每根決策 K 線收盤，在最近 n 根的最高點掛買進停損單、最低點掛賣出
     停損單，只活到下一根收盤（突破單隨通道更新是自然的：價格往通道走，通道
     不會往後退，不會有逆勢單那種「越靠近越退」的問題）。
  2. 先觸發的那張成交、另一張撤掉。成交價 = max(掛單價, 開盤價) + 滑價（買）。
  3. 初始停損 = 進場價 ∓ k × ATR（決策當下）。之後每根決策 K 線收盤，停損只往
     有利方向移：多單 max(舊停損, 收盤 − k × ATR)。
  4. 停損在細週期 K 線上判斷；跳空越過停損以開盤價出場，再扣滑價。
  5. 沒有止盈 —— 順勢策略的獲利全靠少數大趨勢，設止盈等於把它們砍掉。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from copy_trader.quant.data import Bars, atr
from copy_trader.quant.model import CONTRACT, Trade


@dataclass(frozen=True)
class BreakoutParams:
    n: int = 55              # 通道長度（決策週期的根數）
    k: float = 3.0           # ATR 倍數：初始停損與移動停損都用它
    atr_n: int = 20

    def key(self) -> str:
        return f"n{self.n}-k{self.k}"


@dataclass(frozen=True)
class TrendExecution:
    slippage: float = 0.10       # 美元。停損單（進場與出場）都會滑
    cost_per_point: float = 0.1  # 成本 = 成交當根點差 × 0.1（XAUUSDm：每點 0.001 美元 × 100 盎司）
    flat_cost: float = 20.0      # 沒有點差資料時用


def run_breakout(dec: Bars, fine: Bars, p: BreakoutParams, ex: TrendExecution = TrendExecution(),
                 start: float = 0.0, end: float = float("inf")) -> List[Trade]:
    n_dec = len(dec)
    if n_dec <= max(p.n, p.atr_n) + 1:
        return []
    hh = np.full(n_dec, np.nan); ll = np.full(n_dec, np.nan)
    hh[p.n - 1:] = np.lib.stride_tricks.sliding_window_view(dec.h, p.n).max(1)
    ll[p.n - 1:] = np.lib.stride_tricks.sliding_window_view(dec.l, p.n).min(1)
    a = atr(dec, p.atr_n)
    ft, fo, fh, fl = fine.t, fine.o, fine.h, fine.l
    trades: List[Trade] = []
    i = max(p.n, p.atr_n)
    while i < n_dec - 1:
        T = float(dec.t[i] + dec.sec)
        if T < start:
            i += 1
            continue
        if T > end:
            break
        if dec.t[i + 1] - dec.t[i] != dec.sec or not (a[i] == a[i]):
            i += 1
            continue
        up, dn = hh[i], ll[i]
        lo_idx = int(np.searchsorted(ft, T, side="left"))
        hi_idx = int(np.searchsorted(ft, T + dec.sec, side="left"))
        if lo_idx >= hi_idx:
            i += 1
            continue
        bm = fh[lo_idx:hi_idx] >= up
        sm = fl[lo_idx:hi_idx] <= dn
        fb = int(np.argmax(bm)) if bm.any() else None
        fs = int(np.argmax(sm)) if sm.any() else None
        if fb is None and fs is None:
            i += 1
            continue
        cands = []
        if fb is not None and (fs is None or fb <= fs):
            f = lo_idx + fb
            cands.append((+1, f, max(up, fo[f]) + ex.slippage))
        if fs is not None and (fb is None or fs <= fb):
            f = lo_idx + fs
            cands.append((-1, f, min(dn, fo[f]) - ex.slippage))
        best: Optional[Trade] = None
        for side, f, entry in cands:                     # 同一根兩邊都觸發 → 取較差的
            tr = _manage(dec, fine, a, i, f, side, entry, p, ex, T)
            if tr is not None and (best is None or tr.usd < best.usd):
                best = tr
        if best is None:
            break                                         # 持倉到資料結尾，不是結果
        trades.append(best)
        # 出場後的下一根決策 K 線收盤才重新掛單
        i = max(i + 1, int(np.searchsorted(dec.t + dec.sec, best.exit_t, side="left")))
    return trades


def _manage(dec: Bars, fine: Bars, a: np.ndarray, i: int, f: int, side: int, entry: float,
            p: BreakoutParams, ex: TrendExecution, decided: float) -> Optional[Trade]:
    """從第 f 根細週期 K 線成交開始，逐根決策 K 線更新移動停損、在細週期上判斷出場。"""
    init_stop = entry - side * p.k * a[i]
    stop = init_stop
    ft, fo, fh, fl = fine.t, fine.o, fine.h, fine.l
    cost = float(fine.sp[f]) * ex.cost_per_point if fine.sp is not None else ex.flat_cost
    j = i + 1                                              # 成交發生在決策 K 線 j 裡
    first = f
    while True:
        seg_end = int(np.searchsorted(ft, dec.t[j] + dec.sec, side="left")) if j < len(dec) else len(ft)
        if first >= len(ft):
            return None
        lo_seg, hi_seg = fl[first:seg_end], fh[first:seg_end]
        hit = (lo_seg <= stop) if side > 0 else (hi_seg >= stop)
        if hit.any():
            k = first + int(np.argmax(hit))
            px = stop
            if (side > 0 and fo[k] < stop) or (side < 0 and fo[k] > stop):
                px = fo[k]                                   # 跳空越過停損
            if k == f and ((side > 0 and fo[k] < stop) or (side < 0 and fo[k] > stop)):
                px = stop                                    # 成交那根的開盤在成交之前，不算跳空
            px -= side * ex.slippage
            return Trade(decided, float(ft[f]), float(ft[k] + fine.sec), side, entry, init_stop,
                         float("nan"), float(px), "trail" if stop != init_stop else "sl", cost)
        if j >= len(dec) - 1:
            return None                                      # 資料結束還在持倉
        # 決策 K 線 j 收盤：停損只往有利方向移
        if a[j] == a[j]:
            cand = dec.c[j] - side * p.k * a[j]
            stop = max(stop, cand) if side > 0 else min(stop, cand)
        j += 1
        first = seg_end


# ── TSMOM 對照（每日、波動率目標）────────────────────────────────────────

def daily_from_h1(h1: Bars) -> Dict[str, np.ndarray]:
    """UTC 日線：當日第一根 H1 開盤、最後一根 H1 收盤。只收有資料的日子。"""
    day = h1.t // 86400
    keys, first_idx = np.unique(day, return_index=True)
    last_idx = np.r_[first_idx[1:] - 1, len(day) - 1]
    # 成本用當天點差的中位數。UTC 00:00 那根正好是換日，點差常放大好幾倍，
    # 拿它當全天成本會不公平地懲罰每天調倉的策略。
    if h1.sp is not None:
        sp = np.asarray([np.median(h1.sp[a:b + 1]) for a, b in zip(first_idx, last_idx)])
    else:
        sp = np.full(len(keys), 200.0)
    return {"day": keys, "open": h1.o[first_idx], "close": h1.c[last_idx], "sp": sp}


def tsmom(d: Dict[str, np.ndarray], lookbacks=(20, 60, 120, 250), target_vol: float = 0.10,
          vol_n: int = 60, cap: float = 3.0, long_only_benchmark: bool = False) -> Dict[str, np.ndarray]:
    """訊號在第 t 日收盤算出，第 t+1 日開盤執行 —— 用收盤價算訊號又在同一個收盤價
    成交，是 TSMOM 回測最常見的前視錯誤。報酬用「開盤到開盤」。"""
    c, o = d["close"], d["open"]
    n = len(c)
    r_cc = np.r_[np.nan, c[1:] / c[:-1] - 1]
    sig = np.zeros(n)
    for L in lookbacks:
        s = np.full(n, np.nan)
        s[L:] = np.sign(c[L:] / c[:-L] - 1)
        sig += np.nan_to_num(s)
    sig /= len(lookbacks)
    if long_only_benchmark:
        sig = np.ones(n)
    vol = np.full(n, np.nan)
    for t in range(vol_n, n):
        vol[t] = np.nanstd(r_cc[t - vol_n + 1:t + 1], ddof=1) * np.sqrt(252)
    w = np.where(vol > 0, sig * np.minimum(target_vol / vol, cap), 0.0)
    w = np.nan_to_num(w)
    # 第 t 日收盤決定的部位 w[t]，在第 t+1 日開盤建立，持有到第 t+2 日開盤
    r_oo = np.r_[o[1:] / o[:-1] - 1, np.nan]            # 第 t 日開盤 → 第 t+1 日開盤
    held = np.r_[0.0, w[:-1]]                            # 第 t 日開盤時持有的部位
    cost = (d["sp"] * 0.001) / o                         # 每單位換手的成本（點差/價格）
    turnover = np.abs(np.diff(np.r_[0.0, held]))
    ret = held * r_oo - turnover * cost
    ret = np.nan_to_num(ret)
    return {"day": d["day"], "ret": ret, "w": held}
