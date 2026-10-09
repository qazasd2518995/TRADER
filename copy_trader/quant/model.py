"""區間逆勢模型 —— 規則、成交與出場模擬。

假說（為什麼這個該賺錢）
  黃金日內在沒有新資訊時，價格偏離近期區間之後傾向回歸 —— 掛在區間外側的
  限價單，等於在流動性短暫失衡時提供流動性。它失敗的情境也很明確：趨勢日與
  數據公布，那時候價格離開區間是因為資訊，不是因為失衡。所以過濾器只針對
  這兩件事，而且門檻用文獻慣例值，不拿來最佳化。

交易規則（實盤可以一模一樣地執行）
  1. 每根決策 K 線收盤時，取最近 lookback 根（含剛收完的這根）的高低點。
  2. 空手時，在區間外兩側各掛一張限價單：買在 低點 − offset_k×寬度、
     賣在 高點 + offset_k×寬度。停損 stop_k×寬度，止盈 = 停損 × reward。
  3. 掛單價位**凍結 order_life 根**（預設 16 根 = 4 小時），到期沒成交才在
     下一根收盤重算重掛。先成交的那張生效、另一張撤掉；同時只持有一筆。
  4. 持有超過 max_hold_h 小時，以當時收盤價平倉。

  第 3 點刻意改掉舊回測器的作法：舊的是「這根的單 4 小時內沒成交，就換下一根
  的單」—— 要先知道未來 4 小時才決定得了，實盤做不到。這裡是到期才換。

成交與出場（每一條都偏保守）
  * 價格必須「穿過」掛單價 buffer 美元才算成交，成交價仍記為掛單價。
  * 成交那根 K 線可以打到停損，但不算止盈（那根的順向極值多半發生在成交前）。
  * 同一根同時碰到停損與止盈，算停損。
  * 跳空越過停損，以開盤價出場（舊引擎記在停損價，週一開盤跳空時會高估）。
  * 每手扣 cost 美元來回成本。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from copy_trader.quant.data import Bars, adx

CONTRACT = 100.0


@dataclass(frozen=True)
class Params:
    lookback: int = 8
    offset_k: float = 1.75
    stop_k: float = 0.8
    reward: float = 1.0
    max_hold_h: float = 24.0
    # 掛單活幾根決策 K 線（價位凍結），到期沒成交才在下一根收盤重掛。
    #
    # 這個旋鈕決定了策略的本質，不是細節：
    #   1  = 每根重算重掛。價格靠近時區間會被撐寬，掛單價跟著往後退 —— 只有
    #        「單根暴衝穿過區間」才成交，那多半是新聞或動能，正好最不該接。
    #        2022-07 起的 4 年：中心參數每筆 −0.44R、每月 2.7 筆。
    #   16 = 凍結 4 小時。價格慢慢漂過去也會成交，這才是均值回歸的情境。
    # 兩種實盤都能照做。舊回測器（scripts/backtest_strategy.py）的單也活 4 小時，
    # 但沒成交時「下一根就重掛」—— 要先知道未來 4 小時都不會成交才做得到。
    order_life: int = 16

    def key(self) -> str:
        return (f"lb{self.lookback}-off{self.offset_k}-sl{self.stop_k}-rr{self.reward}"
                + (f"-life{self.order_life}" if self.order_life != 16 else ""))


@dataclass(frozen=True)
class Filters:
    adx_max: float = 0.0              # >0：H1 ADX(14) 超過就不進場（慣例 25）
    skip_hours: Tuple[int, ...] = ()  # UTC 小時，這些時段不掛單

    def key(self) -> str:
        parts = []
        if self.adx_max:
            parts.append(f"adx{int(self.adx_max)}")
        if self.skip_hours:
            parts.append(f"skip{self.skip_hours[0]}-{self.skip_hours[-1] + 1}")
        return "+".join(parts) or "none"


@dataclass(frozen=True)
class Execution:
    buffer: float = 0.3               # 美元。Exness K 線是 bid，買單要 ask 碰到 ≈ 點差
    cost: float = 20.0                # 每手來回 USD（cost_per_point 為 0 時用）
    # >0：成本改用成交那根 K 線的實際點差 × 這個係數（XAUUSDm：0.001 美元/點 × 100 盎司
    # = 0.1）。點差隨年份變很多（2022–24 約 20、2025 約 16、2026 約 26 美元/手），
    # 用固定值不是高估就是低估。
    cost_per_point: float = 0.0
    gap_aware: bool = True


@dataclass
class Trade:
    decided: float
    fill_t: float
    exit_t: float
    side: int
    entry: float
    stop: float
    target: float
    exit_px: float
    outcome: str
    cost: float

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def usd(self) -> float:
        """每 1 手的淨損益。"""
        return (self.exit_px - self.entry) * self.side * CONTRACT - self.cost

    @property
    def R(self) -> float:
        return self.usd / (self.risk * CONTRACT) if self.risk else 0.0


# ── 出場 ────────────────────────────────────────────────────────────────

def simulate_exit(fine: Bars, f: int, side: int, entry: float, stop: float, target: float,
                  hold_sec: float, *, gap_aware: bool = True) -> Optional[Tuple[int, float, str]]:
    """成交在 fine 的第 f 根。回傳 (出場那根索引, 出場價, 結果)；資料不夠走完回 None。"""
    # 成交那根：只判停損
    if (side > 0 and fine.l[f] <= stop) or (side < 0 and fine.h[f] >= stop):
        return f, stop, "sl"
    limit_t = fine.t[f] + hold_sec
    jt = int(np.searchsorted(fine.t, limit_t, side="right"))   # 第一根「持有已超時」的
    # 資料不夠走完 24 小時，不代表判不出結果：手上的 K 線裡已經打到停損或止盈的，
    # 照樣算。只有「到資料結尾都還沒出場」才是真的不知道。（第一版在這裡直接回
    # None，每個回測窗口的最後一天都會少算交易 —— 前視測試抓到的。）
    data_end = jt >= len(fine)
    a = f + 1
    last = len(fine) - 1 if data_end else jt
    lo, hi = fine.l[a:last + 1], fine.h[a:last + 1]
    if side > 0:
        sl_hit, tp_hit = lo <= stop, hi >= target
    else:
        sl_hit, tp_hit = hi >= stop, lo <= target
    ks = int(np.argmax(sl_hit)) if sl_hit.any() else None
    kt = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if ks is not None and (kt is None or ks <= kt):              # 同根同觸 → 停損
        j = a + ks
        px = stop
        if gap_aware:
            op = fine.o[j]
            if (side > 0 and op < stop) or (side < 0 and op > stop):
                px = op                                          # 跳空越過停損
        return j, px, "sl"
    if kt is not None:
        return a + kt, target, "tp"
    if data_end:
        return None                                             # 還在持倉，不是結果
    return jt, float(fine.c[jt]), "time"


# ── 主迴圈 ──────────────────────────────────────────────────────────────

@dataclass
class Context:
    """一次回測要用到的行情，預先算好給所有參數共用。"""
    dec: Bars                      # 決策週期（M15）
    fine: Bars                     # 成交與出場用的細週期（可與 dec 相同）
    adx_at: np.ndarray = field(repr=False)     # 每根決策 K 線收盤當下的 H1 ADX
    hour_at: np.ndarray = field(repr=False)    # 每根決策 K 線收盤當下的 UTC 小時

    @classmethod
    def build(cls, dec: Bars, fine: Bars, h1: Bars) -> "Context":
        a = adx(h1, 14)
        close_t = dec.t + dec.sec
        idx = np.searchsorted(h1.t, close_t - h1.sec, side="right") - 1   # 已收完的 H1
        adx_at = np.where(idx >= 0, a[np.clip(idx, 0, None)], np.nan)
        return cls(dec, fine, adx_at, ((close_t % 86400) // 3600).astype(int))


def run(ctx: Context, p: Params, flt: Filters = Filters(), ex: Execution = Execution(),
        start: float = 0.0, end: float = float("inf")) -> List[Trade]:
    dec, fine = ctx.dec, ctx.fine
    n = len(dec)
    lb = p.lookback
    if n <= lb:
        return []
    win_h = np.lib.stride_tricks.sliding_window_view(dec.h, lb).max(axis=1)
    win_l = np.lib.stride_tricks.sliding_window_view(dec.l, lb).min(axis=1)
    hi = np.full(n, np.nan); lo = np.full(n, np.nan)
    hi[lb - 1:] = win_h; lo[lb - 1:] = win_l          # hi[i] = 第 i-lb+1..i 根的最高

    skip = set(flt.skip_hours)
    hold = p.max_hold_h * 3600
    fl, fh, ft = fine.l, fine.h, fine.t
    trades: List[Trade] = []
    free_at = -1.0
    i = lb - 1
    while i < n - 1:
        T = float(dec.t[i] + dec.sec)                  # 第 i 根收盤 = 決策時刻
        if T < start or T < free_at:
            i += 1
            continue
        if T > end:
            break
        if dec.t[i + 1] - dec.t[i] != dec.sec:         # 下一根不是緊接著的（週末、斷線）
            i += 1
            continue
        width = hi[i] - lo[i]
        if not width > 0:
            i += 1
            continue
        if skip and ctx.hour_at[i] in skip:
            i += 1
            continue
        if flt.adx_max:
            a = ctx.adx_at[i]
            if not (a == a) or a > flt.adx_max:        # NaN 也不進場
                i += 1
                continue

        off, sd = p.offset_k * width, p.stop_k * width
        buy_e, sell_e = lo[i] - off, hi[i] + off
        # 掛單價位凍結 order_life 根決策 K 線（以時間計，跨週末就自然變短）
        life_end = T + p.order_life * dec.sec
        a_idx = int(np.searchsorted(ft, T, side="left"))
        b_idx = int(np.searchsorted(ft, life_end, side="left"))
        if a_idx >= b_idx:
            i += 1
            continue
        bmask = fl[a_idx:b_idx] <= buy_e - ex.buffer
        smask = fh[a_idx:b_idx] >= sell_e + ex.buffer
        fb = int(np.argmax(bmask)) if bmask.any() else None
        fs = int(np.argmax(smask)) if smask.any() else None
        if fb is None and fs is None:
            # 到期沒成交：在掛單到期那一刻的下一根收盤重掛，中間不能偷換價位
            i = max(i + 1, int(np.searchsorted(dec.t, life_end - dec.sec, side="left")))
            continue

        cands = []
        if fb is not None and (fs is None or fb <= fs):
            cands.append((+1, a_idx + fb, buy_e))
        if fs is not None and (fb is None or fs <= fb):
            cands.append((-1, a_idx + fs, sell_e))
        best: Optional[Trade] = None
        for side, f, e in cands:                        # 同一根兩邊都成交 → 取較差的那邊
            stop = e - side * sd
            tgt = e + side * sd * p.reward
            out = simulate_exit(fine, f, side, e, stop, tgt, hold, gap_aware=ex.gap_aware)
            if out is None:
                continue
            j, px, oc = out
            cost = ex.cost
            if ex.cost_per_point and fine.sp is not None:
                cost = float(fine.sp[f]) * ex.cost_per_point
            tr = Trade(T, float(ft[f]), float(ft[j] + fine.sec), side, e, stop, tgt, px, oc, cost)
            if best is None or tr.usd < best.usd:
                best = tr
        if best is None:
            break                                       # 資料結束
        trades.append(best)
        free_at = best.exit_t
        i += 1
    return trades
