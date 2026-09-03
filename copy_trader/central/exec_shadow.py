"""執行設定影子對照 —— 同一批訊號，在幾組出場設定下各自會是什麼結果。

要解決的問題
  離線回測說「TP1 全出 + 停損收緊」比現行設定好，但那是從同一份歷史挑出來的。
  唯一能證明它不是過擬合的方法，是**往前**累積：從今天起發布的每一筆訊號，
  各組設定並排記帳，一段時間後再看。這支就是幹這件事。

  注意這裡跟 signal_collector 的 shadow_mode 不是同一件事。那個是「乾跑不發布」；
  這個照常發布、照常下單，只是額外用歷史 K 線多算幾種假設的結果。
  它完全不碰真實下單，只讀 K 線。

怎麼算才誠實
  1. 只統計「已定案」的訊號。單子還在跑就納進來，等於把未實現當已實現。
  2. 每輪都從原始訊號重新模擬一次，不累積中間狀態 —— 中間狀態會漂移，
     而漂移過的對照組比沒有對照更糟。
  3. 扣掉來回成本（點差＋手續費）。yuyu 首檔止盈只有 5 美元，成本佔比很高，
     不扣的話會把一個打平的設定看成賺錢的設定。
  4. K 線倉庫還沒累積到整個評估窗，就讓訊號留在未定案，不硬算。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from copy_trader.backtest import Rules, simulate

logger = logging.getLogger(__name__)

# 1 手黃金來回成本（點差 0.2 美元 約 20 USD）。設定得保守一點，
# 寧可低估優勢，也不要把成本吃掉的優勢當成真的。
DEFAULT_COST_USD = 20.0

# 要並排比較的設定。第一個一定是現行，其他都是候選。
# 候選來自 2026-09-04 的離線回測：yuyu 的 TP2/TP3 幾乎不會到（只吃 TP3 勝率 11%），
# 分批把八成部位押在他抓不到的地方；停損倍率 0.7-0.9 是連續平原不是單點。
VARIANTS: Dict[str, Rules] = {
    "現行設定": Rules(),
    "TP1全出": Rules(partials=(1.0,)),
    "TP1全出+停損0.8": Rules(partials=(1.0,), stop_mult=0.8),
    "TP1全出+停損0.9": Rules(partials=(1.0,), stop_mult=0.9),
}

MAX_ENTRIES = 5000          # 記到這麼多就丟最舊的，檔案不會無限長

# 取 K 線時要往前多拿一點：引擎需要「發單當下」那根來算掛單相對市價的偏離，
# 只給 ts 之後的 K 線會取不到，整筆就評不出來。
LOOKBACK_SEC = 3600.0


class ExecutionShadow:
    def __init__(
        self,
        path: Path,
        bar_store,
        *,
        cost_usd: float = DEFAULT_COST_USD,
        variants: Optional[Dict[str, Rules]] = None,
        clock=time.time,
    ):
        self.path = Path(path)
        self.bars = bar_store
        self.cost_usd = float(cost_usd)
        self.variants = dict(variants or VARIANTS)
        self.clock = clock
        self._entries: List[dict] = []
        self._lock = threading.Lock()
        self.started_at: Optional[float] = None
        self._load()

    # ── 持久化 ──────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("影子對照帳本讀取失敗，改從空的開始：%s", exc)
            return
        self._entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
        self.started_at = data.get("started_at")

    def _save_locked(self) -> None:
        payload = {"started_at": self.started_at, "entries": self._entries}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("影子對照帳本寫入失敗：%s", exc)

    # ── 記錄 ────────────────────────────────────────────────────────
    def record(self, payload: dict) -> bool:
        """從發布流程掛勾進來。只收有完整進場／停損／止盈的實單訊號。

        絕不能讓這裡的例外影響發布 —— 呼叫端要包 try/except。
        """
        if (payload or {}).get("type") != "trade_signal":
            return False
        sig = payload.get("signal") or {}
        entry, stop = sig.get("entry_price"), sig.get("stop_loss")
        tps = [x for x in (sig.get("take_profit") or []) if x]
        if not entry or not stop or not tps:
            return False
        direction = sig.get("direction")
        if direction not in ("buy", "sell"):
            return False

        now = self.clock()
        row = {
            "id": str(payload.get("execution_id") or payload.get("event_id") or now),
            "ts": now,
            "source": str(payload.get("source_name") or payload.get("source") or ""),
            "label": str(payload.get("source") or ""),
            "direction": direction,
            "entry": float(entry),
            "stop": float(stop),
            "targets": [float(x) for x in tps],
            "settled": False,
            "results": {},
        }
        with self._lock:
            if self.started_at is None:
                self.started_at = now
            if any(e.get("id") == row["id"] for e in self._entries):
                return False                      # 重送不重複記
            self._entries.append(row)
            del self._entries[:-MAX_ENTRIES]
            self._save_locked()
        return True

    # ── 評估 ────────────────────────────────────────────────────────
    def evaluate(self) -> int:
        """重算所有未定案的訊號，回傳這輪新定案的筆數。"""
        with self._lock:
            pending = [e for e in self._entries if not e.get("settled")]
        if not pending:
            return 0
        window = max(r.pending_hours + r.max_hold_hours for r in self.variants.values())

        newly = 0
        for entry in pending:
            ts = float(entry["ts"])
            bars = self.bars.bars_since(ts - LOOKBACK_SEC)
            if not bars:
                continue
            results: Dict[str, dict] = {}
            done = True
            for name, rules in self.variants.items():
                trade = simulate(entry["direction"], entry["entry"], entry["stop"],
                                 entry["targets"], ts, bars, rules)
                if trade is None:
                    done = False
                    break
                cost = self.cost_usd if trade.filled else 0.0
                results[name] = {
                    "outcome": trade.outcome,
                    "filled": trade.filled,
                    "profit": round(trade.profit - cost, 2),
                    "mae": round(trade.mae, 2),
                    "minutes": round(trade.minutes_to_exit or 0.0, 1),
                }
                if not trade.settled:
                    done = False
            # 窗口還沒走完就先不定案；已定案的結果之後不會再變。
            entry["results"] = results
            complete = len(results) == len(self.variants)
            if complete and done:
                entry["settled"] = True
                newly += 1
            elif self.bars.covers(ts, window):
                # 窗口過了卻算不完 —— K 線在這段時間有缺口（輪詢停過）。
                # 標記出來讓人看得到，絕不能悄悄當成 0 混進統計。
                entry["settled"] = True
                entry["unevaluable"] = not complete
                newly += 1

        if newly:
            with self._lock:
                self._save_locked()
        return newly

    # ── 統計 ────────────────────────────────────────────────────────
    def summary(self, source: str = "") -> dict:
        with self._lock:
            settled = [e for e in self._entries
                       if e.get("settled") and (not source or e.get("source") == source)]
            entries = [e for e in settled if not e.get("unevaluable")]
            unevaluable = len(settled) - len(entries)
            pending = sum(1 for e in self._entries if not e.get("settled"))
            started = self.started_at
        rows = []
        for name in self.variants:
            trades = [e["results"][name] for e in entries
                      if name in (e.get("results") or {})]
            rows.append(_stats(name, [t for t in trades if t.get("filled")]))
        base = rows[0]["ev"] if rows else 0.0
        for r in rows:
            r["delta"] = None if r["n"] == 0 else round(r["ev"] - base, 1)
        first, last = self.bars.span
        return {
            "started_at": started,
            "settled": len(entries),
            "pending": pending,
            "unevaluable": unevaluable,
            "cost_usd": self.cost_usd,
            "variants": rows,
            "sources": sorted({e.get("source") for e in entries if e.get("source")}),
            "bars": {"count": self.bars.count, "first": first, "last": last},
        }


def _stats(name: str, trades: List[dict]) -> dict:
    if not trades:
        return {"name": name, "n": 0, "wins": 0, "win_rate": 0.0,
                "ev": 0.0, "total": 0.0, "pf": None}
    profits = [float(t.get("profit") or 0.0) for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    total = sum(profits)
    return {
        "name": name,
        "n": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "ev": round(total / len(trades), 1),
        "total": round(total, 1),
        "pf": (round(sum(wins) / abs(sum(losses)), 2) if losses else None),
    }
