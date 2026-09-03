#!/usr/bin/env python3
"""訊號源回測引擎 —— 用歷史 K 線逐根重放每一筆報單，算出真實績效。

    python scripts/backtest_signals.py                    # 全部來源
    python scripts/backtest_signals.py --source yuyu      # 只看高頻
    python scripts/backtest_signals.py --detail           # 逐筆列出

資料來源
  訊號：LINE 資料庫（跟訊號中心同一份解析器，不另外寫 parser）
  行情：data/market_history/XAUUSD247m_<TF>.json（由 EA 匯出，見 README）

為什麼要逐根推進
  只看「區間內最高/最低有沒有碰到 TP/SL」會同時判定兩者都觸及，勝率完全失真。
  必須按時間順序走，先碰到哪個就是哪個。

明確列出的假設（會影響數字，看報告時要記得）
  1. 同一根 K 線同時觸及 TP 與 SL —— 保守假設「先觸及 SL」。K 線不含路徑資訊，
     這是回測的固有限制；寧可低估勝率，不要高估。
  2. 掛單 PENDING_HOURS 小時內沒成交就撤單（對齊系統的逾時撤單行為）。
  3. 成交後最多持有 MAX_HOLD_HOURS，之後以當時價格平倉。
  4. 多檔止盈按 PARTIAL_RATIOS 分批，觸及第一檔後停損移到進場價（保本移損）
     —— 這是系統對 yuyu 實際採用的設定，不模擬就會嚴重低估他的績效。
  5. 不計點差與手續費。實際成交會略差，比較「來源之間」時影響不大。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 用 reconfigure 而不是包一層 TextIOWrapper：包一層的話，被匯入時
# 前一個 wrapper 會被回收並把底層 buffer 關掉，另一支就印不出東西。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from copy_trader.line_db.keys import default_key_provider          # noqa: E402
from copy_trader.line_db.sqlite_provider import SQLiteLineDatabaseProvider  # noqa: E402
from copy_trader.signal_parser.strict_parser import parse_strict_signal     # noqa: E402

LINE_DB = r"C:\Users\zheng\AppData\Local\LINE\Data\db\qw7c98cf7ff86b6578e378b88a249d8e.edb"
HISTORY = ROOT / "data" / "market_history"

PENDING_HOURS = 4.0      # 掛單多久沒成交就撤
MAX_HOLD_HOURS = 24.0    # 成交後最長持有
PARTIAL_RATIOS = [0.5, 0.3, 0.2]

SOURCES = {
    "mid": {
        "label": "中頻（乘／陳昊）",
        "chats": ["m53ba41be660f343281b84498c3c2c700",
                  "m9cdfc4c07adf0a5990f1f167316482fb"],
        "senders": ["pdd941ce0c96273b174ad9558eb3c2f6f",
                    "p5f4220170c31904be8937ea50f66d581"],
        "profile": "mid_frequency_v1",
    },
    "yuyu": {
        "label": "高頻（yuyu）",
        "chats": ["mabc8efb19a26843fd7b9782236f782a6"],
        "senders": None,
        "profile": "yuyu_range_v1",
    },
}


# ── 行情 ────────────────────────────────────────────────────────────────
class Market:
    """多週期 K 線。優先用 M1（精度最高），訊號早於 M1 範圍時自動退到 M5/M15。"""

    ORDER = ["M1", "M5", "M15"]

    def __init__(self):
        self.frames: dict[str, list[dict]] = {}
        for tf in self.ORDER:
            path = HISTORY / f"XAUUSD247m_{tf}.json"
            if not path.is_file():
                continue
            bars = json.loads(path.read_text(encoding="utf-8")).get("bars") or []
            if bars:
                self.frames[tf] = bars

    def pick(self, ts: float) -> tuple[str, list[dict]] | tuple[None, None]:
        """挑一個涵蓋這個時間點的週期，精度優先。"""
        for tf in self.ORDER:
            bars = self.frames.get(tf)
            if bars and bars[0]["t"] <= ts <= bars[-1]["t"]:
                return tf, bars
        return None, None

    @staticmethod
    def slice_from(bars: list[dict], ts: float, hours: float) -> list[dict]:
        end = ts + hours * 3600
        return [b for b in bars if ts <= b["t"] <= end]

    @staticmethod
    def price_at(bars: list[dict], ts: float) -> float | None:
        prev = [b for b in bars if b["t"] <= ts]
        return prev[-1]["c"] if prev else None


# ── 規則（要優化的旋鈕都放這，引擎本身不用複製一份）─────────────────────
@dataclass
class Rules:
    breakeven: bool = True       # 觸及首檔止盈後把停損移到進場價
    stop_mult: float = 1.0       # 停損距離倍率（1.5 = 放寬到 1.5 倍）
    tp_mult: float = 1.0         # 止盈距離倍率
    optimistic: bool = False     # 同根 tie 判 TP
    pending_hours: float = PENDING_HOURS
    max_hold_hours: float = MAX_HOLD_HOURS
    trail_after_tp1: float = 0.0  # >0 = 首檔止盈後改用這個距離的移動停損（取代保本）
    partials: tuple[float, ...] = tuple(PARTIAL_RATIOS)  # 各檔止盈平掉的比例


# ── 單筆結果 ─────────────────────────────────────────────────────────────
@dataclass
class Trade:
    when: datetime
    direction: str
    entry: float
    stop: float
    targets: list[float]
    market_at_signal: float
    timeframe: str
    filled: bool = False
    outcome: str = "未成交"        # 未成交 / 止損 / 保本 / 部分止盈 / 全部止盈 / 逾時平倉
    profit: float = 0.0            # 每 1 手的美元損益（1 手 = 100 盎司，1 美元價差 = 100 USD）
    mae: float = 0.0               # 最大逆行（美元）
    mfe: float = 0.0               # 最大順行（美元）
    minutes_to_fill: float | None = None
    minutes_to_exit: float | None = None
    hits: int = 0                  # 觸及第幾檔止盈
    ties: int = 0                  # 同一根同時觸及 TP 與 SL 的次數（假設 1 的影響面）

    @property
    def offset(self) -> float:
        """進場價相對發單當下市價的偏離；正=掛在市價上方。"""
        return self.entry - self.market_at_signal


def simulate(sig, ts: float, market: Market, rules: Rules | None = None) -> Trade | None:
    rules = rules or Rules()
    tf, bars = market.pick(ts)
    if not bars:
        return None
    mkt = Market.price_at(bars, ts)
    if mkt is None:
        return None

    entry = float(sig.entry_price)
    is_buy_ = sig.direction == "buy"
    sign = 1 if is_buy_ else -1
    # 依倍率把原始 TP/SL 距離放大縮小（測「停損放寬會不會比較好」用）
    raw_stop = float(sig.stop_loss)
    stop_px = entry - sign * abs(entry - raw_stop) * rules.stop_mult
    targets = [entry + sign * abs(float(x) - entry) * rules.tp_mult
               for x in (sig.take_profit or [])]
    trade = Trade(when=datetime.fromtimestamp(ts), direction=sig.direction,
                  entry=entry, stop=stop_px,
                  targets=targets, market_at_signal=mkt, timeframe=tf)
    if not targets:
        return trade

    is_buy = sig.direction == "buy"
    window = Market.slice_from(bars, ts, PENDING_HOURS + MAX_HOLD_HOURS)
    if not window:
        return trade

    stop = trade.stop
    remaining = 1.0
    realized = 0.0
    fill_ts = None
    hit_index = 0

    for bar in window:
        # ── 尚未成交：等價格觸及掛單價 ──────────────────────────────
        if fill_ts is None:
            if bar["t"] - ts > rules.pending_hours * 3600:
                break                                   # 逾時未成交 → 撤單
            if bar["l"] <= trade.entry <= bar["h"]:
                fill_ts = bar["t"]
                trade.filled = True
                trade.minutes_to_fill = (fill_ts - ts) / 60
            else:
                continue

        # ── 已成交：更新順逆行，再判斷出場 ──────────────────────────
        adverse = (trade.entry - bar["l"]) if is_buy else (bar["h"] - trade.entry)
        favour = (bar["h"] - trade.entry) if is_buy else (trade.entry - bar["l"])
        trade.mae = max(trade.mae, adverse)
        trade.mfe = max(trade.mfe, favour)

        hit_stop = (bar["l"] <= stop) if is_buy else (bar["h"] >= stop)
        nxt = targets[hit_index] if hit_index < len(targets) else None
        hit_tp = nxt is not None and ((bar["h"] >= nxt) if is_buy else (bar["l"] <= nxt))

        # 假設 1：同一根同時觸及 → 預設保守地當作先觸及停損。
        # optimistic=True 時反過來假設先觸及止盈，兩者夾出真實績效的區間。
        if hit_stop and hit_tp:
            trade.ties += 1
            if rules.optimistic:
                hit_stop = False
        if hit_stop:
            realized += remaining * (stop - trade.entry) * (1 if is_buy else -1)
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
            realized += ratio * (nxt - trade.entry) * (1 if is_buy else -1)
            remaining -= ratio
            hit_index += 1
            trade.hits = hit_index
            if hit_index == 1 and rules.breakeven:
                stop = trade.entry          # 假設 4：保本移損
            if remaining <= 1e-9 or hit_index >= len(targets):
                trade.outcome = "全部止盈" if hit_index >= len(targets) else "部分止盈"
                trade.minutes_to_exit = (bar["t"] - fill_ts) / 60
                break

        if hit_index >= 1 and rules.trail_after_tp1 > 0:
            trail = bar["c"] - sign * rules.trail_after_tp1
            stop = max(stop, trail) if is_buy else min(stop, trail)

        if fill_ts and bar["t"] - fill_ts > rules.max_hold_hours * 3600:
            realized += remaining * (bar["c"] - trade.entry) * (1 if is_buy else -1)
            trade.outcome = "逾時平倉"
            trade.minutes_to_exit = (bar["t"] - fill_ts) / 60
            break
    else:
        if fill_ts is not None and remaining > 0:
            realized += remaining * (window[-1]["c"] - trade.entry) * (1 if is_buy else -1)
            trade.outcome = "資料結束"

    if trade.filled and trade.outcome == "未成交":
        trade.outcome = "持倉中"
    trade.profit = realized * 100          # 1 手 = 100 盎司
    return trade


# ── 訊號 ────────────────────────────────────────────────────────────────
def load_signals(key: str) -> list[tuple[float, object]]:
    cfg = SOURCES[key]
    provider = SQLiteLineDatabaseProvider(LINE_DB, default_key_provider("line-db-research"))
    conn = provider.connect()
    out = []
    for chat in cfg["chats"]:
        if cfg["senders"]:
            marks = ",".join("?" * len(cfg["senders"]))
            rows = conn.execute(
                f"SELECT _createdTime,_text FROM _message WHERE _chatId=? "
                f"AND _from IN ({marks}) AND COALESCE(_text,'')<>''",
                (chat, *cfg["senders"])).fetchall()
        else:
            rows = conn.execute(
                "SELECT _createdTime,_text FROM _message WHERE _chatId=? "
                "AND COALESCE(_text,'')<>''", (chat,)).fetchall()
        for ts, text in rows:
            res = parse_strict_signal(text or "", cfg["profile"])
            if res.status == "accepted" and res.signal \
                    and res.signal.entry_price and res.signal.stop_loss:
                out.append((ts / 1000, res.signal))
    provider.close()
    out.sort()
    return out


# ── 報告 ────────────────────────────────────────────────────────────────
def report(label: str, trades: list[Trade], detail: bool) -> None:
    usable = [t for t in trades if t.outcome not in ("持倉中", "資料結束")]
    filled = [t for t in usable if t.filled]
    print(f"\n╔══ {label}")
    print(f"║ 訊號 {len(trades)} 筆 · 可評估 {len(usable)} 筆 · 成交 {len(filled)} 筆", end="")
    if usable:
        print(f"（成交率 {len(filled)/len(usable)*100:.0f}%）")
    else:
        print()
    if not filled:
        print("╚" + "═" * 58)
        return

    wins = [t for t in filled if t.profit > 0]
    losses = [t for t in filled if t.profit < 0]
    flat = [t for t in filled if t.profit == 0]
    total = sum(t.profit for t in filled)
    print(f"║ 勝率 {len(wins)/len(filled)*100:.1f}%  "
          f"（賺 {len(wins)} / 賠 {len(losses)} / 平手 {len(flat)}）")
    print(f"║ 每手總損益 {total:+,.0f} USD    每筆期望值 {total/len(filled):+,.1f} USD")
    if wins:
        print(f"║ 平均獲利 {st.mean(t.profit for t in wins):+,.0f}", end="")
    if losses:
        print(f"    平均虧損 {st.mean(t.profit for t in losses):+,.0f}", end="")
    if wins and losses:
        pf = sum(t.profit for t in wins) / abs(sum(t.profit for t in losses))
        print(f"    獲利因子 {pf:.2f}")
    else:
        print()

    tie_trades = [t for t in filled if t.ties]
    if tie_trades:
        print(f"║ ⚠ 有 {len(tie_trades)} 筆出現「同根同時觸及 TP 與 SL」"
              f"（占成交 {len(tie_trades)/len(filled)*100:.0f}%）—— 這些單的判定受假設影響")
    outcomes: dict[str, int] = {}
    for t in filled:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    print("║ 出場方式：" + "  ".join(f"{k}×{v}" for k, v in
                                sorted(outcomes.items(), key=lambda x: -x[1])))

    rr = []
    for t in filled:
        risk = abs(t.entry - t.stop)
        if risk > 0 and t.targets:
            rr.append((abs(t.targets[-1] - t.entry) / risk, risk,
                       abs(t.targets[0] - t.entry)))
    if rr:
        print(f"║ 訊號幾何：停損中位 {st.median(x[1] for x in rr):.1f} 美元  "
              f"首檔止盈 {st.median(x[2] for x in rr):.1f}  "
              f"末檔止盈 {st.median(x[0]*x[1] for x in rr):.1f}")
        med_rr = st.median(x[0] for x in rr)
        need = 100 / (1 + med_rr)
        print(f"║   盈虧比(末檔) {med_rr:.2f} → 要打平至少需要 {need:.0f}% 勝率"
              f"（實際 {len(wins)/len(filled)*100:.1f}%）"
              f"{'  ✅' if len(wins)/len(filled)*100 >= need else '  ❌ 數學上必虧'}")

    mae = [t.mae for t in filled]
    print(f"║ 最大逆行(MAE)：中位 {st.median(mae):.1f}  "
          f"90分位 {sorted(mae)[int(len(mae)*0.9)-1]:.1f}  最大 {max(mae):.1f} 美元")
    win_mae = [t.mae for t in wins]
    if win_mae:
        print(f"║   其中「最後有賺的單」逆行中位 {st.median(win_mae):.1f}、"
              f"最大 {max(win_mae):.1f} 美元 ← 停損至少要留這麼寬")
    fills = [t.minutes_to_fill for t in filled if t.minutes_to_fill is not None]
    if fills:
        print(f"║ 掛單到成交：中位 {st.median(fills):.0f} 分鐘")
    exits = [t.minutes_to_exit for t in filled if t.minutes_to_exit is not None]
    if exits:
        print(f"║ 持倉時間：中位 {st.median(exits):.0f} 分鐘")
    offs = [t.offset for t in trades]
    if offs:
        print(f"║ 掛單偏離市價：中位 {st.median(offs):+.1f} 美元"
              f"（正=掛上方，SELL 等回彈；負=掛下方，BUY 等回檔）")

    for name, group in (("買", [t for t in filled if t.direction == "buy"]),
                        ("賣", [t for t in filled if t.direction == "sell"])):
        if group:
            w = sum(1 for t in group if t.profit > 0)
            print(f"║ {name}方 {len(group):3} 筆  勝率 {w/len(group)*100:5.1f}%  "
                  f"每筆 {sum(t.profit for t in group)/len(group):+7.1f} USD")

    buckets: dict[int, list[Trade]] = {}
    for t in filled:
        buckets.setdefault(t.when.hour, []).append(t)
    rows = [(h, len(g), sum(x.profit for x in g) / len(g)) for h, g in buckets.items() if len(g) >= 3]
    if rows:
        rows.sort(key=lambda r: -r[2])
        print("║ 時段表現（樣本≥3）：" + "  ".join(f"{h:02d}點 {n}筆 {p:+.0f}" for h, n, p in rows[:6]))
    print("╚" + "═" * 58)

    if detail:
        print("  時間              方向 進場     停損   結果      損益/手   逆行  成交(分)")
        for t in trades:
            print(f"  {t.when:%m-%d %H:%M}  {t.direction.upper():4} {t.entry:8.1f} "
                  f"{t.stop:7.1f}  {t.outcome:8} {t.profit:+8.0f} {t.mae:6.1f} "
                  f"{('%.0f' % t.minutes_to_fill) if t.minutes_to_fill is not None else '  -':>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description="訊號源回測引擎")
    ap.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ap.add_argument("--detail", action="store_true", help="逐筆列出")
    ap.add_argument("--optimistic", action="store_true",
                    help="同根同時觸及 TP/SL 時改判為先觸及 TP（與預設的保守假設夾出區間）")
    args = ap.parse_args()

    market = Market()
    if not market.frames:
        print(f"找不到行情資料。請先把 EA 匯出的 rates_*.json 放到 {HISTORY}")
        return 2
    print("行情資料：" + "  ".join(
        f"{tf} {len(b)} 根（{datetime.fromtimestamp(b[0]['t']):%Y-%m-%d} 起）"
        for tf, b in market.frames.items()))
    print(f"假設：掛單 {PENDING_HOURS:.0f}h 未成交撤單 · 最長持有 {MAX_HOLD_HOURS:.0f}h · "
          f"分批 {PARTIAL_RATIOS} · 觸及首檔止盈後保本 · 同根同時觸及視為止損 · 不計點差")

    keys = list(SOURCES) if args.source == "all" else [args.source]
    for key in keys:
        signals = load_signals(key)
        trades = [t for t in (simulate(s, ts, market, Rules(optimistic=args.optimistic)) for ts, s in signals) if t]
        report(SOURCES[key]["label"], trades, args.detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
