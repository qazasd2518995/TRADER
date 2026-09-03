"""滾動累積 M1 K 線。

為什麼需要
  EA 匯出的 rates_M1.json 只留 400 根（約 6.7 小時），但一筆報單的評估窗是
  掛單 4h + 持有 24h = 28 小時。只讀當下那份檔案永遠算不完一筆單。
  這裡每次輪詢把新的 K 線併進來，時間一長就有連續的歷史。

  只要輪詢間隔遠小於 400 分鐘就不會漏。輪詢停掉再開會產生缺口，缺口期間的
  訊號評不出結果 —— 那是預期行為，寧可沒有數字，也不要拿有洞的資料算績效。

存法
  記憶體一份 dict[時間戳] = K 線，寫檔用 tmp + replace，中途當掉不會留半份。
  正在形成中的那根 K 線每次輪詢都會被新版覆蓋（後到的贏），收線後就定型。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

RETENTION_DAYS = 45.0        # 留多久的歷史
FLUSH_INTERVAL = 300.0       # 最短寫檔間隔，避免每次輪詢都重寫幾 MB


class BarStore:
    """一個商品、一個週期的滾動 K 線倉庫。"""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: float = RETENTION_DAYS,
        flush_interval: float = FLUSH_INTERVAL,
        clock=time.time,
    ):
        self.path = Path(path)
        self.retention_days = float(retention_days)
        self.flush_interval = float(flush_interval)
        self.clock = clock
        self._bars: Dict[int, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._flushed_at = 0.0
        self.symbol = ""
        self._load()

    # ── 讀寫 ────────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # 壞掉的倉庫不能讓訊號中心起不來；就當作空的重新累積。
            logger.warning("K 線倉庫讀取失敗，改從空的開始：%s", exc)
            return
        self.symbol = str(data.get("symbol") or "")
        for b in data.get("bars") or []:
            row = _clean(b)
            if row:
                self._bars[row["t"]] = row

    def flush(self, force: bool = False) -> bool:
        """寫回磁碟。回傳是否真的寫了。"""
        with self._lock:
            now = self.clock()
            if not self._dirty:
                return False
            if not force and now - self._flushed_at < self.flush_interval:
                return False
            bars = sorted(self._bars.values(), key=lambda r: r["t"])
            payload = {"symbol": self.symbol, "timeframe": "M1", "bars": bars}
            self._dirty = False
            self._flushed_at = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("K 線倉庫寫入失敗，資料仍在記憶體：%s", exc)
            with self._lock:
                self._dirty = True
            return False
        return True

    # ── 併入與查詢 ──────────────────────────────────────────────────
    def ingest(self, bars: Sequence[dict], symbol: str = "") -> int:
        """併入一批 K 線，回傳新增（不含更新）的根數。"""
        added = 0
        with self._lock:
            if symbol:
                self.symbol = symbol
            for b in bars or []:
                row = _clean(b)
                if not row:
                    continue
                if row["t"] not in self._bars:
                    added += 1
                # 形成中的 K 線會被後到的版本覆蓋，這是刻意的
                self._bars[row["t"]] = row
                self._dirty = True
            self._prune_locked()
        return added

    def _prune_locked(self) -> None:
        if self.retention_days <= 0 or not self._bars:
            return
        cutoff = max(self._bars) - self.retention_days * 86400
        stale = [t for t in self._bars if t < cutoff]
        for t in stale:
            del self._bars[t]

    def bars_since(self, ts: float, until: Optional[float] = None) -> List[dict]:
        with self._lock:
            rows = [b for b in self._bars.values() if b["t"] >= ts
                    and (until is None or b["t"] <= until)]
        rows.sort(key=lambda r: r["t"])
        return rows

    def covers(self, ts: float, hours: float) -> bool:
        """從 ts 起算 hours 小時的資料是否已經到齊。沒到齊就別急著下結論。"""
        with self._lock:
            if not self._bars:
                return False
            newest = max(self._bars)
        return newest >= ts + hours * 3600

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._bars)

    @property
    def span(self) -> tuple[Optional[int], Optional[int]]:
        with self._lock:
            if not self._bars:
                return None, None
            return min(self._bars), max(self._bars)


def _clean(b: Any) -> Optional[dict]:
    try:
        row = {"t": int(b["t"]), "o": float(b["o"]), "h": float(b["h"]),
               "l": float(b["l"]), "c": float(b["c"])}
    except (KeyError, TypeError, ValueError):
        return None
    # 高低必須包住開收，不然出場判斷會少觸發
    row["h"] = max(row["h"], row["o"], row["c"])
    row["l"] = min(row["l"], row["o"], row["c"])
    return row
