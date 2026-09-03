"""逐根重放一筆報單，算出它在給定執行規則下的結果。

為什麼要逐根推進
  只看「區間內最高/最低有沒有碰到 TP/SL」會同時判定兩者都觸及，勝率完全失真。
  必須按時間順序走，先碰到哪個就是哪個。

明確列出的假設（會影響數字，解讀時要記得）
  1. 同一根 K 線同時觸及 TP 與 SL —— 保守假設「先觸及 SL」。K 線不含路徑資訊，
     這是固有限制；寧可低估勝率，不要高估。Rules.optimistic 可以翻到另一邊，
     兩者夾出真實績效的區間。
  2. 掛單 pending_hours 小時內沒成交就撤單（對齊系統的逾時撤單行為）。
  3. 成交後最多持有 max_hold_hours，之後以當時價格平倉。
  4. 多檔止盈按 partials 分批；觸及第一檔後停損移到進場價（保本移損）。
     這是系統對 yuyu 實際採用的設定，不模擬就會嚴重低估他的績效。
  5. 不計點差與手續費 —— 呼叫端要自己扣（1 手黃金點差 0.2 美元 ≈ 20 USD）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

PENDING_HOURS = 4.0             # 掛單多久沒成交就撤
MAX_HOLD_HOURS = 24.0           # 成交後最長持有
PARTIAL_RATIOS = (0.5, 0.3, 0.2)
CONTRACT_SIZE = 100.0           # 1 手黃金 = 100 盎司，1 美元價差 = 100 USD


@dataclass(frozen=True)
class Rules:
    """要比較的執行設定。所有旋鈕都在這，引擎本身不用複製第二份。"""

    breakeven: bool = True            # 觸及首檔止盈後把停損移到進場價
    stop_mult: float = 1.0            # 停損距離倍率（1.5 = 放寬到 1.5 倍）
    tp_mult: float = 1.0              # 止盈距離倍率
    optimistic: bool = False          # 同根同時觸及時改判 TP
    pending_hours: float = PENDING_HOURS
    max_hold_hours: float = MAX_HOLD_HOURS
    trail_after_tp1: float = 0.0      # >0 = 首檔止盈後改用這個距離的移動停損
    partials: tuple[float, ...] = PARTIAL_RATIOS


@dataclass
class Trade:
    when: datetime
    direction: str
    entry: float
    stop: float
    targets: list[float]
    market_at_signal: float
    timeframe: str = ""
    filled: bool = False
    outcome: str = "未成交"     # 未成交/止損/保本/部分止盈/全部止盈/逾時平倉/持倉中/資料結束
    profit: float = 0.0         # 每 1 手的美元損益
    mae: float = 0.0            # 最大逆行（美元）
    mfe: float = 0.0            # 最大順行（美元）
    minutes_to_fill: Optional[float] = None
    minutes_to_exit: Optional[float] = None
    hits: int = 0               # 觸及第幾檔止盈
    ties: int = 0               # 同一根同時觸及 TP 與 SL 的次數

    @property
    def offset(self) -> float:
        """進場價相對發單當下市價的偏離；正=掛在市價上方。"""
        return self.entry - self.market_at_signal

    @property
    def settled(self) -> bool:
        """還在跑的單不能拿去統計，否則會把未實現當成已實現。"""
        return self.outcome not in ("持倉中", "資料結束")


def slice_from(bars: Sequence[dict], ts: float, hours: float) -> list[dict]:
    end = ts + hours * 3600
    return [b for b in bars if ts <= b["t"] <= end]


def price_at(bars: Sequence[dict], ts: float) -> Optional[float]:
    prev = [b for b in bars if b["t"] <= ts]
    return prev[-1]["c"] if prev else None


def simulate(
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: Iterable[float],
    ts: float,
    bars: Sequence[dict],
    rules: Optional[Rules] = None,
    *,
    timeframe: str = "",
) -> Optional[Trade]:
    """重放一筆報單。bars 要涵蓋 ts 之後至少 pending+max_hold 小時才算得完。

    回傳 None 代表連發單當下的市價都取不到（bars 沒涵蓋到 ts），無法評估。
    """
    rules = rules or Rules()
    mkt = price_at(bars, ts)
    if mkt is None:
        return None

    entry = float(entry_price)
    is_buy = direction == "buy"
    sign = 1 if is_buy else -1

    # 依倍率把原始 TP/SL 距離放大縮小（測「停損放寬會不會比較好」用）
    stop_px = entry - sign * abs(entry - float(stop_loss)) * rules.stop_mult
    targets = [entry + sign * abs(float(x) - entry) * rules.tp_mult
               for x in (take_profit or [])]

    trade = Trade(when=datetime.fromtimestamp(ts), direction=direction,
                  entry=entry, stop=stop_px, targets=targets,
                  market_at_signal=mkt, timeframe=timeframe)
    if not targets:
        return trade

    window = slice_from(bars, ts, rules.pending_hours + rules.max_hold_hours)
    if not window:
        return trade

    stop = stop_px
    remaining = 1.0
    realized = 0.0
    fill_ts: Optional[float] = None
    hit_index = 0

    for bar in window:
        # ── 尚未成交：等價格觸及掛單價 ──────────────────────────────
        if fill_ts is None:
            if bar["t"] - ts > rules.pending_hours * 3600:
                break                                   # 逾時未成交 → 撤單
            if bar["l"] <= entry <= bar["h"]:
                fill_ts = bar["t"]
                trade.filled = True
                trade.minutes_to_fill = (fill_ts - ts) / 60
            else:
                continue

        # ── 已成交：更新順逆行，再判斷出場 ──────────────────────────
        adverse = (entry - bar["l"]) if is_buy else (bar["h"] - entry)
        favour = (bar["h"] - entry) if is_buy else (entry - bar["l"])
        trade.mae = max(trade.mae, adverse)
        trade.mfe = max(trade.mfe, favour)

        hit_stop = (bar["l"] <= stop) if is_buy else (bar["h"] >= stop)
        nxt = targets[hit_index] if hit_index < len(targets) else None
        hit_tp = nxt is not None and ((bar["h"] >= nxt) if is_buy else (bar["l"] <= nxt))

        # 假設 1：同一根同時觸及 → 預設保守地當作先觸及停損。
        if hit_stop and hit_tp:
            trade.ties += 1
            if rules.optimistic:
                hit_stop = False
        if hit_stop:
            realized += remaining * (stop - entry) * sign
            trade.outcome = "保本" if hit_index > 0 else "止損"
            trade.minutes_to_exit = (bar["t"] - fill_ts) / 60
            break

        if hit_tp:
            # 最後一檔止盈要把剩餘部位全部平掉。單一止盈檔的訊號（中頻就是）
            # 若照分批表只平 50%，獲利會被硬生生砍半 —— 這裡必須用 remaining。
            is_last = hit_index == len(targets) - 1
            ratio = remaining if is_last else min(
                rules.partials[hit_index] if hit_index < len(rules.partials) else remaining,
                remaining)
            realized += ratio * (nxt - entry) * sign
            remaining -= ratio
            hit_index += 1
            trade.hits = hit_index
            if hit_index == 1 and rules.breakeven:
                stop = entry                            # 假設 4：保本移損
            if remaining <= 1e-9 or hit_index >= len(targets):
                trade.outcome = "全部止盈" if hit_index >= len(targets) else "部分止盈"
                trade.minutes_to_exit = (bar["t"] - fill_ts) / 60
                break

        if hit_index >= 1 and rules.trail_after_tp1 > 0:
            trail = bar["c"] - sign * rules.trail_after_tp1
            stop = max(stop, trail) if is_buy else min(stop, trail)

        if bar["t"] - fill_ts > rules.max_hold_hours * 3600:
            realized += remaining * (bar["c"] - entry) * sign
            trade.outcome = "逾時平倉"
            trade.minutes_to_exit = (bar["t"] - fill_ts) / 60
            break
    else:
        # 走完手上的 K 線還沒出場 —— 資料不夠，不是結果。
        if fill_ts is not None and remaining > 0:
            realized += remaining * (window[-1]["c"] - entry) * sign
            trade.outcome = "資料結束"

    if trade.filled and trade.outcome == "未成交":
        trade.outcome = "持倉中"
    trade.profit = realized * CONTRACT_SIZE
    return trade
