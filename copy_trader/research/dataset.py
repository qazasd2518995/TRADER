"""把每一筆提供者訊號接上發單當下的行情，產出「特徵 + 結果」的研究資料集。

每一條設計都是踩過、或文獻明確點名的坑：

1. **時間基準用 published_at**（Hub 發布的時刻）。那是我們真正能動手的時刻；
   用提供者打字的時間會讓特徵偷看到我們當時還不知道的幾秒～幾分鐘。舊的 OCR
   時代訊號只存了「18:40」這種沒日期的字串，published_at 反而每筆都有。
2. **特徵只用發單前「已收完」的 K 線。** 形成中的那根含有發單之後的價格。
3. **結果一律用 backtest.engine.simulate** —— 跟線上影子記帳同一份出場模擬。
   各寫一份就會分歧，研究結論跟線上對照就對不起來。
4. **提供者在成交前撤單 = 沒成交。** 不處理的話成交率會被高估，而且被高估的
   正好是提供者自己都覺得不該進的那些單。
5. **單位用 R**（淨損益 / 停損距離），扣 20 USD/手 來回成本。不同停損寬度的
   單才能放在一起比，也才是 meta-labeling 該學的東西。
6. Exness 伺服器時間是 GMT+0（2026-10-09 實測偏移 0），K 線 t 就是真實 UTC。
"""
from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from copy_trader.backtest.engine import Rules, simulate, CONTRACT_SIZE

COST_PER_LOT = 20.0                     # 點差 + 手續費，來回，每 1 手
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "market_history"
LINE_SOURCES = ("黃金報單🈲言群", "焦點利潤(yuyu)")
MARKOUT_MINUTES = (5, 15, 60, 240)


# ── K 線 ─────────────────────────────────────────────────────────────────

class Bars:
    """依時間排序的 K 線，用二分搜尋取窗。engine 內建的線性掃描對 10 萬根太慢。"""

    def __init__(self, bars: Sequence[dict], seconds: int):
        self.bars = list(bars)
        self.t = [int(b["t"]) for b in self.bars]
        self.sec = int(seconds)

    @property
    def start(self) -> int:
        return self.t[0] if self.t else 0

    @property
    def end(self) -> int:
        return self.t[-1] if self.t else 0

    def closed_before(self, ts: float, n: int) -> List[dict]:
        """ts 當下「已經收完」的最後 n 根。開盤時間 + 週期 <= ts 才算收完。"""
        i = bisect_right(self.t, ts - self.sec)
        return self.bars[max(0, i - n):i]

    def window(self, ts: float, hours: float) -> List[dict]:
        i = bisect_left(self.t, ts)
        j = bisect_right(self.t, ts + hours * 3600)
        return self.bars[i:j]

    def close_at(self, ts: float) -> Optional[float]:
        """ts 當下最新一根已收完 K 線的收盤價。"""
        got = self.closed_before(ts, 1)
        return got[-1]["c"] if got else None


def load_bars(name: str, seconds: int) -> Bars:
    raw = json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    return Bars(sorted(raw, key=lambda b: b["t"]), seconds)


# ── 指標（全部只吃已收完的 K 線）─────────────────────────────────────────

def atr(bars: Sequence[dict], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-n - 1:-1], bars[-n:]):
        trs.append(max(cur["h"] - cur["l"], abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    return sum(trs) / len(trs)


def ema(values: Sequence[float], n: int) -> List[float]:
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def session_of(hour_utc: int) -> str:
    """黃金的交易時段（UTC）。倫敦開盤 07、紐約開盤約 13（夏令）。"""
    if 0 <= hour_utc < 7:
        return "asia"
    if 7 <= hour_utc < 12:
        return "london"
    if 12 <= hour_utc < 16:
        return "overlap"
    if 16 <= hour_utc < 21:
        return "ny"
    return "late"


# ── 訊號 ─────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    seq: int
    source: str
    exec_id: str
    ts: float                     # published_at
    direction: str
    entry: float
    stop: float
    targets: List[float]
    cancel_ts: Optional[float] = None


def load_signals(path: Path = DATA_DIR / "hub_signals.json",
                 sources: Iterable[str] = LINE_SOURCES) -> List[Signal]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    wanted = set(sources)

    # 撤單事件：exec_id -> 最早的撤單時刻
    cancels: Dict[str, float] = {}
    for r in rows:
        if r.get("type") != "cancel_signal":
            continue
        ts = r.get("published_at")
        for eid in r.get("target_execution_ids") or []:
            if ts and (eid not in cancels or ts < cancels[eid]):
                cancels[eid] = float(ts)

    out: List[Signal] = []
    seen = set()
    recent: Dict[tuple, float] = {}           # (來源, 方向, 進場, 停損, TP1) -> 上次發布時刻
    for r in rows:
        if r.get("type") != "trade_signal" or r.get("source") not in wanted:
            continue
        # execution_id 是改用 LINE 資料庫之後（2026-08 底）才有的欄位。之前 OCR
        # 時代的 217 筆只有 raw_message_key / id —— 只認 execution_id 會把中頻
        # 四個月的歷史整段丟掉，而且丟得無聲無息（剩下的全部是 M1 時期）。
        eid = str(r.get("execution_id") or r.get("raw_message_key")
                  or r.get("id") or f"seq-{r.get('seq')}")
        if eid in seen:                       # 同一筆的修訂版只算第一次發布
            continue
        sig = r.get("signal") or {}
        try:
            entry = float(sig.get("entry_price"))
            stop = float(sig.get("stop_loss"))
            tps = [float(x) for x in (sig.get("take_profit") or [])]
            ts = float(r.get("published_at"))
        except (TypeError, ValueError):
            continue                          # 不完整的訊號系統本來就不會下單
        direction = str(sig.get("direction") or "").lower()
        if direction not in ("buy", "sell") or not tps:
            continue
        # OCR 會把同一則訊息重讀成兩筆（記錄在 signals-always-limit）。內容完全
        # 相同、6 小時內再出現的，當成同一筆 —— 提供者不會在 6 小時內掛兩張
        # 一模一樣價位的單。
        key = (r["source"], direction, entry, stop, tps[0])
        if key in recent and ts - recent[key] < 6 * 3600:
            continue
        recent[key] = ts
        seen.add(eid)
        out.append(Signal(int(r.get("seq") or 0), str(r["source"]), eid, ts,
                          direction, entry, stop, tps, cancels.get(eid)))
    return sorted(out, key=lambda s: s.ts)


# ── 特徵 ─────────────────────────────────────────────────────────────────

def features(sig: Signal, m1: Bars, m5: Bars, h1: Bars) -> Optional[Dict[str, Any]]:
    """只用 sig.ts 當下已收完的 K 線。取不到足夠歷史就回 None。"""
    fine = m1 if sig.ts >= m1.start + 86400 else m5
    price = fine.close_at(sig.ts)
    m5b = m5.closed_before(sig.ts, 300)          # 25 小時
    h1b = h1.closed_before(sig.ts, 80)
    if price is None or len(m5b) < 50 or len(h1b) < 30:
        return None

    a5 = atr(m5b, 14)
    a60 = atr(h1b, 14)
    if not a5 or not a60:
        return None

    sign = 1 if sig.direction == "buy" else -1
    closes_h1 = [b["c"] for b in h1b]
    e20 = ema(closes_h1, 20)
    e50 = ema(closes_h1, 50)
    slope = (e20[-1] - e20[-6]) / a60                # 近 5 小時 EMA20 斜率（ATR 單位）

    def ret_hours(h: int) -> float:
        need = int(h * 12)
        if len(m5b) <= need:
            return 0.0
        return (price - m5b[-need - 1]["c"]) / a60

    def range_pos(hours: float) -> float:
        seg = m5b[-int(hours * 12):]
        hi, lo = max(b["h"] for b in seg), min(b["l"] for b in seg)
        return (price - lo) / (hi - lo) if hi > lo else 0.5

    sl_dist = abs(sig.entry - sig.stop)
    tp1_dist = abs(sig.targets[0] - sig.entry)
    # 要等價格「往不利方向」走多遠才會成交。正 = 正常限價單（等回檔），
    # 負 = 掛單價已經穿過市價，送出去就會立刻成交。
    wait = (price - sig.entry) * sign
    hour = int((sig.ts % 86400) // 3600)
    dow = int(((sig.ts // 86400) + 3) % 7)          # 1970-01-01 是星期四 → 0=週一

    return {
        "price": price,
        "res": "M1" if fine is m1 else "M5",
        "atr_m5": a5,
        "atr_h1": a60,
        "wait_usd": wait,
        "wait_atr": wait / a5,
        "sl_usd": sl_dist,
        "sl_atr": sl_dist / a5,
        "tp1_usd": tp1_dist,
        "rr1": tp1_dist / sl_dist if sl_dist else 0.0,
        "n_tps": len(sig.targets),
        "ema_slope": slope,
        "with_trend": sign * slope,                  # >0 = 順勢
        "dist_ema20": (price - e20[-1]) / a60 * sign, # >0 = 價格在自己方向那一側
        "dist_ema50": (price - e50[-1]) / a60 * sign,
        "ret_1h": ret_hours(1) * sign,               # >0 = 剛剛已經往自己方向走了
        "ret_4h": ret_hours(4) * sign,
        "ret_24h": ret_hours(24) * sign,
        "range_pos_2h": range_pos(2) if sign > 0 else 1 - range_pos(2),
        "range_pos_24h": range_pos(24) if sign > 0 else 1 - range_pos(24),
        "hour_utc": hour,
        "dow": dow,
        "session": session_of(hour),
        "is_buy": 1 if sign > 0 else 0,
    }


# ── 結果 ─────────────────────────────────────────────────────────────────

def outcome(sig: Signal, m1: Bars, m5: Bars, rules: Optional[Rules] = None) -> Dict[str, Any]:
    rules = rules or Rules()
    fine = m1 if sig.ts >= m1.start + 86400 else m5
    # 撤單：把掛單有效期縮到撤單那一刻。已成交的單撤單不碰（系統規則）。
    if sig.cancel_ts and sig.cancel_ts > sig.ts:
        rules = Rules(**{**asdict(rules),
                         "pending_hours": min(rules.pending_hours, (sig.cancel_ts - sig.ts) / 3600)})
    span = rules.pending_hours + rules.max_hold_hours + 1
    win = fine.window(sig.ts, span)
    lead = fine.closed_before(sig.ts, 2)             # engine 要拿得到發單當下的市價
    tr = simulate(sig.direction, sig.entry, sig.stop, sig.targets, sig.ts, lead + win, rules)
    if tr is None:
        return {"settled": False}

    sl_dist = abs(sig.entry - sig.stop) or 1e-9
    net = tr.profit - (COST_PER_LOT if tr.filled else 0.0)
    res = {
        "settled": tr.settled,
        "filled": tr.filled,
        "cancelled": bool(sig.cancel_ts and not tr.filled),
        "outcome": tr.outcome,
        "min_to_fill": tr.minutes_to_fill,
        "min_to_exit": tr.minutes_to_exit,
        "gross_usd": tr.profit,
        "net_usd": net if tr.filled else 0.0,
        "R": (net / (sl_dist * CONTRACT_SIZE)) if tr.filled else 0.0,
        "mae_R": tr.mae / sl_dist,
        "mfe_R": tr.mfe / sl_dist,
        "ties": tr.ties,
        "win": bool(tr.filled and net > 0),
    }

    # 成交後的 markout：成交之後 N 分鐘的價格相對進場價（順向為正，R 單位）。
    # 這是限價單「逆選擇」的直接量測 —— 成交的單如果系統性地繼續往不利方向走，
    # 代表我們成交的正是不該成交的那些。
    if tr.filled and tr.minutes_to_fill is not None:
        fill_ts = sig.ts + tr.minutes_to_fill * 60
        sign = 1 if sig.direction == "buy" else -1
        for m in MARKOUT_MINUTES:
            px = fine.close_at(fill_ts + m * 60 + fine.sec)
            res[f"mk_{m}"] = ((px - sig.entry) * sign / sl_dist) if px is not None else None
    return res


# ── 組裝 ─────────────────────────────────────────────────────────────────

def build(rules: Optional[Rules] = None) -> List[Dict[str, Any]]:
    m1 = load_bars("XAUUSD247m_M1_full.json", 60)
    m5 = load_bars("XAUUSD247m_M5_full.json", 300)
    h1 = load_bars("XAUUSD247m_H1_full.json", 3600)
    rows: List[Dict[str, Any]] = []
    for sig in load_signals():
        f = features(sig, m1, m5, h1)
        if f is None:
            continue
        o = outcome(sig, m1, m5, rules)
        rows.append({"seq": sig.seq, "source": sig.source, "exec_id": sig.exec_id,
                     "ts": sig.ts, "direction": sig.direction, "entry": sig.entry,
                     "stop": sig.stop, "tp1": sig.targets[0], **f, **o})
    _add_provider_state(rows)
    return rows


def _add_provider_state(rows: List[Dict[str, Any]]) -> None:
    """提供者「近況」特徵 —— 只算發單當下**已經結算**的單。

    用還沒出場的單去算近況就是偷看未來。出場時刻 = 發單 + 成交分鐘 + 持有分鐘。
    """
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_src.setdefault(r["source"], []).append(r)
    for src, rs in by_src.items():
        done = []
        for r in rs:
            if r.get("filled") and r.get("settled") and r.get("min_to_fill") is not None \
                    and r.get("min_to_exit") is not None:
                done.append((r["ts"] + (r["min_to_fill"] + r["min_to_exit"]) * 60, r["R"]))
        done.sort()
        for r in rs:
            known = [R for t, R in done if t < r["ts"]][-10:]
            r["prov_last10_R"] = sum(known) / len(known) if known else 0.0
            r["prov_n_known"] = len(known)
            r["prov_24h_count"] = sum(1 for x in rs if r["ts"] - 86400 <= x["ts"] < r["ts"])
