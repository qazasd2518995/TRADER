#!/usr/bin/env python3
"""訊號源回測引擎 —— 用歷史 K 線逐根重放每一筆報單，算出真實績效。

    python scripts/backtest_signals.py                    # 全部來源
    python scripts/backtest_signals.py --source yuyu      # 只看高頻
    python scripts/backtest_signals.py --detail           # 逐筆列出

資料來源
  訊號：LINE 資料庫（跟訊號中心同一份解析器，不另外寫 parser）
  行情：data/market_history/XAUUSD247m_<TF>.json（由 EA 匯出，見 README）

出場模擬在 copy_trader/backtest/engine.py —— 線上的影子對照共用同一份，
假設也寫在那裡。這支只負責「挑訊號、挑週期、算統計」。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
import sys
from pathlib import Path

# 用 reconfigure 而不是包一層 TextIOWrapper：包一層的話，被匯入時
# 前一個 wrapper 會被回收並把底層 buffer 關掉，另一支就印不出東西。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from copy_trader.backtest import Rules, Trade                      # noqa: E402
from copy_trader.backtest.engine import (                          # noqa: E402
    MAX_HOLD_HOURS, PARTIAL_RATIOS, PENDING_HOURS,
)
from copy_trader.backtest.engine import simulate as engine_simulate  # noqa: E402
from copy_trader.line_db.keys import default_key_provider          # noqa: E402
from copy_trader.line_db.sqlite_provider import SQLiteLineDatabaseProvider  # noqa: E402
from copy_trader.signal_parser.strict_parser import parse_strict_signal     # noqa: E402

LINE_DB = r"C:\Users\zheng\AppData\Local\LINE\Data\db\qw7c98cf7ff86b6578e378b88a249d8e.edb"
HISTORY = ROOT / "data" / "market_history"

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


# ── 模擬 ────────────────────────────────────────────────────────────────
def simulate(sig, ts: float, market: Market, rules: Rules | None = None) -> Trade | None:
    """把一筆 LINE 訊號丟進共用引擎。挑週期是這裡的事，出場邏輯不在這。"""
    tf, bars = market.pick(ts)
    if not bars:
        return None
    return engine_simulate(
        sig.direction, float(sig.entry_price), float(sig.stop_loss),
        sig.take_profit or [], ts, bars, rules, timeframe=tf,
    )


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
