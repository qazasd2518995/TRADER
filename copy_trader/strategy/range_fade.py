"""區間逆勢限價（range fade）—— 中頻那位的打法，寫成可回測的規則。

觀察到的行為（2026-09-04，22 筆訊號）
  買單掛在前 2 小時區間的 -2.3% 位置（低於區間最低點），距市價 10.5 美元；
  賣單掛在 66.9% 位置，距市價 5.1 美元；逆勢比例 78% / 85%。
  也就是：把限價單掛在最近區間之外，等價格走過去才進場。

為什麼參數用「區間寬度的倍數」而不是固定美元
  他本人用的是固定美元（停損 10、止盈 15）。但固定金額綁死在某個波動率
  水準上 —— 金價從 2000 走到 4500 的期間，10 美元的意義完全不同。
  用區間寬度的倍數表達，同一組參數才可能跨市況成立。這是刻意偏離他的
  原始做法：我們要的是能持續運作的規則，不是精確的模仿。

刻意不做的事
  1. 不預測方向。兩邊都掛，market 走到哪邊算哪邊（走到就取消另一邊）。
     方向判斷是最容易過擬合的地方，而我們沒有證據顯示他們判得準。
  2. 同時只持有一筆。允許重疊會讓報酬看起來變好，但那是加槓桿，
     不是策略變強。
  3. 進場價一律在「決策當根收盤之後」才生效，決策只看當根與之前。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class RangeFadeParams:
    """一組完整的策略設定。全部都是無單位的倍數，只有時間是小時。"""

    lookback: int = 8               # 參考區間用幾根 K 線
    offset_k: float = 0.25          # 掛單距區間邊緣多遠（區間寬度的倍數）
    stop_k: float = 0.60            # 停損距進場多遠（區間寬度的倍數）
    reward: float = 1.5             # 止盈 = 停損 × 這個倍數
    pending_hours: float = 4.0      # 掛單多久沒成交就撤
    max_hold_hours: float = 24.0    # 成交後最長持有
    # 區間太窄代表沒行情，掛出去只會被雜訊掃；太寬代表剛出過大事，
    # 這時候的「區間」不具參考性。兩端都要擋掉。
    min_width: float = 0.0          # 區間寬度下限（美元），0 = 不擋
    max_width: float = 0.0          # 區間寬度上限（美元），0 = 不擋

    def describe(self) -> str:
        return (f"回看{self.lookback}根 · 掛單{self.offset_k:.2f}倍 · "
                f"停損{self.stop_k:.2f}倍 · 盈虧比{self.reward:.1f}")


@dataclass
class Order:
    """一張還沒進場的掛單。出場交給 backtest 引擎，這裡只描述進場意圖。"""

    ts: float                       # 決策時間（掛單當下）
    direction: str                  # buy / sell
    entry: float
    stop: float
    targets: List[float]
    range_width: float              # 決策當下的區間寬度，事後分析用
    index: int                      # 決策發生在第幾根 K 線


def generate_orders(
    bars: Sequence[dict],
    params: RangeFadeParams,
    *,
    start: int = 0,
    end: Optional[int] = None,
) -> List[Order]:
    """走過每一根 K 線，產出應該掛的單。

    每根 K 線收盤時看前 lookback 根的高低，在區間外側兩邊各掛一張限價單。
    只回傳「意圖」—— 哪一張先成交、之後怎麼走，由呼叫端用出場引擎決定，
    這樣自有策略和跟單訊號會走完全同一套出場邏輯。

    只看 bars[:i+1] 決策，不會偷看未來。
    """
    out: List[Order] = []
    last = len(bars) if end is None else min(end, len(bars))
    first = max(start, params.lookback)
    for i in range(first, last):
        window = bars[i - params.lookback:i]
        if len(window) < params.lookback:
            continue
        hi = max(b["h"] for b in window)
        lo = min(b["l"] for b in window)
        width = hi - lo
        if width <= 0:
            continue
        if params.min_width and width < params.min_width:
            continue
        if params.max_width and width > params.max_width:
            continue

        ts = float(bars[i]["t"])
        stop_dist = width * params.stop_k
        if stop_dist <= 0:
            continue
        offset = width * params.offset_k

        buy_entry = lo - offset
        sell_entry = hi + offset
        out.append(Order(ts=ts, direction="buy", entry=buy_entry,
                         stop=buy_entry - stop_dist,
                         targets=[buy_entry + stop_dist * params.reward],
                         range_width=width, index=i))
        out.append(Order(ts=ts, direction="sell", entry=sell_entry,
                         stop=sell_entry + stop_dist,
                         targets=[sell_entry - stop_dist * params.reward],
                         range_width=width, index=i))
    return out
