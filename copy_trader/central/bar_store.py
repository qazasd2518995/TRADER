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
    def ingest(self, bars: Sequence[dict], symbol: str = "",
               offset_sec: float = 0.0) -> int:
        """併入一批 K 線，回傳新增（不含更新）的根數。

        offset_sec 是這個終端的伺服器時間偏移（見 detect_server_offset）。
        併進來之前一律換算成真實時間，倉庫裡就不會混著兩種時基 —— 訊號
        時間是真實 epoch，對不上的話整個評估會靜靜地錯開好幾小時。
        """
        added = 0
        shift = float(offset_sec or 0.0)
        with self._lock:
            if symbol:
                self.symbol = symbol
            for b in bars or []:
                row = _clean(b)
                if not row:
                    continue
                if shift:
                    row["t"] = int(row["t"] - shift)
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


# ── 挑一個「活的」MT5 ────────────────────────────────────────────────
STALE_AFTER_SEC = 3600.0        # 超過這麼久沒更新就算停了


def _m1_mtime(files_dir: Path) -> Optional[float]:
    f = Path(files_dir) / "rates_M1.json"
    try:
        return f.stat().st_mtime
    except OSError:
        return None


def _instance_roots() -> List[Path]:
    """可能放著 instance_* 設定的地方。

    開發時 DATA_DIR 是原始碼目錄，打包後才是 APPDATA —— 兩邊都要找，
    否則在開發機上測不到真實候選。
    """
    roots: List[Path] = []
    try:
        from copy_trader.config import DATA_DIR

        roots.extend([Path(DATA_DIR), Path(DATA_DIR).parent])
    except Exception:                                         # noqa: BLE001
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "黃金跟單系統")
    return roots


def _candidate_dirs(configured: str = "") -> List[Path]:
    """所有可能在匯出 K 線的 MT5。

    不寫死路徑：本機每個會員實例的設定裡就記著自己那台 MT5 的位置，
    拿它們當候選最準 —— 可攜版裝在非標準路徑時，標準偵測找不到。
    """
    out: List[Path] = []
    if configured:
        out.append(Path(configured))
    for root in _instance_roots():
        try:
            found = sorted(root.glob("instance_*/client_web_launcher_settings.json"))
        except OSError:
            continue
        for settings in found:
            try:
                raw = json.loads(settings.read_text(encoding="utf-8")).get("mt5_files_dir")
            except (OSError, ValueError):
                continue
            if raw:
                out.append(Path(str(raw)))
    try:
        from copy_trader.config import _find_mt5_files_dir

        detected = _find_mt5_files_dir()
        if detected:
            out.append(Path(detected))
    except Exception as exc:                                  # noqa: BLE001
        logger.debug("標準 MT5 偵測失敗：%s", exc)

    seen, unique = set(), []
    for path in out:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def pick_live_mt5_dir(
    configured: str = "",
    current: Optional[Path] = None,
    *,
    clock=time.time,
) -> Optional[Path]:
    """挑一個還在匯出 M1 的 MT5 目錄。

    黏著性是刻意的：不同終端可能接不同券商，價格會有些微差異，一直換來換去
    等於把兩份行情混在一起。所以只有在目前這台明顯停掉、而且有別台是活的
    時候才換。全部都停（例如週末休市）就沿用原本那台，反正也沒有新 K 線。
    """
    now = clock()
    scored = []
    for path in _candidate_dirs(configured):
        mtime = _m1_mtime(path)
        if mtime is not None:
            scored.append((now - mtime, path))
    if not scored:
        return current
    scored.sort()
    freshest_age, freshest = scored[0]

    if current is not None:
        current_age = _m1_mtime(current)
        current_age = None if current_age is None else now - current_age
        if current_age is not None and current_age <= STALE_AFTER_SEC:
            return current                       # 目前這台還活著就別動
        if freshest_age > STALE_AFTER_SEC:
            return current                       # 沒有更好的選擇
    return freshest


def detect_server_offset(files_dir: Path) -> Optional[float]:
    """量出這個終端的 K 線時間戳比真實時間快多少秒。

    MT5 寫進 rates 檔的是**伺服器時間**當成整數，不同券商差好幾個小時
    （這台機器上 Exness 三台是 +3 小時，另一家是 0）。訊號時間是真實
    epoch，兩者不換算就會錯開整整三小時 —— 而且不會有任何徵兆，只是
    成交率和勝率變成隨機數字。

    量法：最新那根 K 線是「正在形成中」的，它的時間就是伺服器當下的分鐘
    起點；檔案的 mtime 則是真實當下。兩者相減就是偏移。伺服器時區都是
    整點或半點，所以取整到 15 分鐘來吃掉那不到一分鐘的誤差。

    回傳 None 代表量不出來（檔案不在或內容壞掉）—— 這時寧可不要併入，
    也不要用一個猜的偏移把倉庫弄髒。
    """
    path = Path(files_dir) / "rates_M1.json"
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bars = data.get("bars")
    if not isinstance(bars, list) or not bars:
        return None
    try:
        newest = max(int(b["t"]) for b in bars)
    except (KeyError, TypeError, ValueError):
        return None
    return round((newest - mtime) / 900.0) * 900.0
