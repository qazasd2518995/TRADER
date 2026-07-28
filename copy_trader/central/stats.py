"""會員績效統計：把 EA 與 TradeManager 已經在寫的檔案彙整成前端要的數字。

這個模組只讀不寫。資料來源：

  <MT5 Files>/closed_trades.json    已平倉成交，含實際損益（EA 寫入）
  <MT5 Files>/positions.json        目前持倉與浮動損益
  <MT5 Files>/account_info.json     帳戶餘額、淨值、保證金
  <MT5 Files>/martingale_state.json 目前馬丁層級
  <MT5 Files>/signal_sources.json   signal_id -> 訊號來源名稱
  <DATA_DIR>/trade_journal.txt      跟單事件日誌

MT5 沒連線時每個區塊各自降級（回 None 或空陣列），不會讓整包統計失敗——
前端靠 connected 旗標決定顯示「等待 MT5 連線」還是真實數字。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from copy_trader.config import DATA_DIR
except Exception:  # pragma: no cover - 打包環境下的保險
    DATA_DIR = Path.cwd()

# 事件日誌的標題行：[2026-07-08 17:54:19] ORDER_FILLED
_JOURNAL_HEAD = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+([A-Z_]+)\s*$")

# account_info.json 的 timestamp 超過這個秒數沒更新，就當作 EA 沒在寫檔
STALE_AFTER_SECONDS = 120

# 日誌解析結果快取：路徑 -> (mtime, size, 事件串列)
_journal_cache: Dict[str, Tuple[float, int, List[Dict[str, Any]]]] = {}


# --------------------------------------------------------------------------
# 檔案位置
# --------------------------------------------------------------------------

def resolve_mt5_dir(configured: str = "") -> Path:
    """設定裡填了就用填的，否則沿用 TradeManager 的自動偵測順序。"""
    configured = (configured or "").strip().strip('"')
    if configured:
        return Path(configured)

    try:
        from copy_trader.platform import PlatformConfig

        path = PlatformConfig().get_mt5_files_path()
        if path and Path(path).is_dir():
            return Path(path)
    except Exception:
        pass

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library" / "Application Support"
            / "net.metaquotes.wine.metatrader5" / "drive_c"
            / "Program Files" / "MetaTrader 5" / "MQL5" / "Files"
        )

    for candidate in (
        r"C:\Program Files\MetaTrader 5\MQL5\Files",
        r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files",
    ):
        if os.path.isdir(candidate):
            return Path(candidate)
    return Path(r"C:\Program Files\MetaTrader 5\MQL5\Files")


def journal_path() -> Path:
    candidate = Path(DATA_DIR) / "trade_journal.txt"
    if candidate.exists():
        return candidate
    return Path("trade_journal.txt")


def _read_json(path: Path, retries: int = 3) -> Optional[Any]:
    """EA 隨時可能正在覆寫，讀到半截的 JSON 就重試。"""
    for attempt in range(retries):
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(0.05)
    return None


# --------------------------------------------------------------------------
# 事件日誌
# --------------------------------------------------------------------------

def _split_details(line: str) -> Dict[str, str]:
    """拆 `a=1 | b=2` 這種明細行。

    信號欄位本身含有 ` | SL: ...` 這種沒有等號的片段，所以不含 `=` 的片段
    一律併回前一個欄位，而不是當成新欄位。
    """
    fields: Dict[str, str] = {}
    key: Optional[str] = None
    for chunk in line.split(" | "):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            name, _, value = chunk.partition("=")
            key = name.strip()
            fields[key] = value.strip()
        elif key is not None:
            fields[key] = f"{fields[key]} | {chunk}"
    return fields


def read_journal(limit: int = 500) -> List[Dict[str, Any]]:
    """解析事件日誌，回傳由新到舊的事件。用 mtime + size 做快取。"""
    path = journal_path()
    try:
        stat = path.stat()
    except OSError:
        return []

    cache_key = str(path)
    cached = _journal_cache.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        events = cached[2]
        return events[:limit]

    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            pending: Optional[Dict[str, Any]] = None
            for raw in f:
                line = raw.rstrip("\n")
                head = _JOURNAL_HEAD.match(line.strip())
                if head:
                    if pending:
                        events.append(pending)
                    pending = {
                        "time": head.group(1),
                        "event": head.group(2),
                        "fields": {},
                    }
                    continue
                if pending is not None and line.startswith("  ") and "=" in line:
                    pending["fields"] = _split_details(line.strip())
            if pending:
                events.append(pending)
    except OSError:
        return []

    events.reverse()
    _journal_cache[cache_key] = (stat.st_mtime, stat.st_size, events)
    return events[:limit]


def _journal_index(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """signal_id -> 該筆單在日誌裡出現過的欄位（新的不覆蓋舊的關鍵欄位）。"""
    index: Dict[str, Dict[str, str]] = {}
    for event in events:
        signal_id = event["fields"].get("signal_id") or ""
        if not signal_id or signal_id == "None":
            continue
        bucket = index.setdefault(signal_id, {})
        for key, value in event["fields"].items():
            if key not in bucket and value and value != "None":
                bucket[key] = value
    return index


# --------------------------------------------------------------------------
# 馬丁階梯
# --------------------------------------------------------------------------

def _float(value: Any, fallback: float) -> float:
    try:
        result = float(str(value).strip())
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError):
        return fallback


def _int(value: Any, fallback: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback


def build_ladder(settings: Dict[str, Any]) -> List[float]:
    """依設定算出每一層的手數：優先用自訂手數，否則 base × 倍數^層。"""
    base = _float(settings.get("default_lot_size"), 0.01)
    multiplier = _float(settings.get("martingale_multiplier"), 2.0)
    max_level = max(1, min(_int(settings.get("martingale_max_level"), 5), 12))

    custom_raw = str(settings.get("martingale_lots") or "").strip()
    if custom_raw:
        lots = [_float(part, 0.0) for part in custom_raw.split(",") if part.strip()]
        lots = [lot for lot in lots if lot > 0]
        if lots:
            return lots[:12]

    return [round(base * (multiplier ** level), 4) for level in range(max_level)]


def _level_for_volume(volume: float, ladder: List[float]) -> int:
    """用手數回推這筆單下在第幾層——比日誌可靠，因為 closed_trades 一定有手數。"""
    if not ladder or volume <= 0:
        return 0
    best_index, best_gap = 0, abs(volume - ladder[0])
    for index, lot in enumerate(ladder):
        gap = abs(volume - lot)
        if gap < best_gap:
            best_index, best_gap = index, gap
    return best_index


# --------------------------------------------------------------------------
# 彙總
# --------------------------------------------------------------------------

def _summarise(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """單筆維度的績效。空清單回傳全零，前端不用另外判斷。"""
    total = len(trades)
    wins = [t for t in trades if t["profit"] >= 0]
    losses = [t for t in trades if t["profit"] < 0]
    gross_win = sum(t["profit"] for t in wins)
    gross_loss = sum(t["profit"] for t in losses)

    cumulative, peak, max_drawdown = 0.0, 0.0, 0.0
    win_streak = loss_streak = best_win_streak = worst_loss_streak = 0
    for trade in trades:
        cumulative += trade["profit"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        if trade["profit"] >= 0:
            win_streak, loss_streak = win_streak + 1, 0
            best_win_streak = max(best_win_streak, win_streak)
        else:
            loss_streak, win_streak = loss_streak + 1, 0
            worst_loss_streak = max(worst_loss_streak, loss_streak)

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / total * 100) if total else 0.0,
        "net_profit": gross_win + gross_loss,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": (gross_win / abs(gross_loss)) if gross_loss else None,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "best": max((t["profit"] for t in trades), default=0.0),
        "worst": min((t["profit"] for t in trades), default=0.0),
        "max_win_streak": best_win_streak,
        "max_loss_streak": worst_loss_streak,
        "max_drawdown": max_drawdown,
        "total_volume": sum(t["volume"] for t in trades),
    }


def _cycles(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """馬丁回合：連續虧損直到一次獲利才算走完一輪，這才是這套系統真正的損益單位。"""
    completed: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    for trade in trades:
        current.append(trade)
        if trade["profit"] >= 0:
            completed.append({
                "trades": len(current),
                "profit": sum(t["profit"] for t in current),
                "ended": trade["close_time"],
            })
            current = []

    profitable = [c for c in completed if c["profit"] >= 0]
    return {
        "completed": len(completed),
        "profitable": len(profitable),
        "rate": (len(profitable) / len(completed) * 100) if completed else 0.0,
        "avg_length": (sum(c["trades"] for c in completed) / len(completed)) if completed else 0.0,
        "longest": max((c["trades"] for c in completed), default=0),
        "open_trades": len(current),
        "open_profit": sum(t["profit"] for t in current),
    }


def _by_source(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for trade in trades:
        name = trade["source"] or "未標記來源"
        bucket = buckets.setdefault(name, {"source": name, "wins": 0, "losses": 0, "profit": 0.0})
        bucket["profit"] += trade["profit"]
        if trade["profit"] >= 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    rows = []
    for bucket in buckets.values():
        total = bucket["wins"] + bucket["losses"]
        bucket["total"] = total
        bucket["win_rate"] = (bucket["wins"] / total * 100) if total else 0.0
        rows.append(bucket)
    rows.sort(key=lambda row: row["profit"], reverse=True)
    return rows


def _by_day(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for trade in trades:
        day = (trade["close_time"] or "")[:10].replace(".", "-")
        if not day:
            continue
        bucket = buckets.setdefault(day, {"date": day, "profit": 0.0, "trades": 0, "wins": 0})
        bucket["profit"] += trade["profit"]
        bucket["trades"] += 1
        if trade["profit"] >= 0:
            bucket["wins"] += 1
    return [buckets[key] for key in sorted(buckets)]


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def pending_orders(trade_manager: Any) -> List[Dict[str, Any]]:
    """還沒進場的掛單，附上距離自動刪單還剩幾秒。

    這份資料只存在跑著的 TradeManager 記憶體裡（MT5 的 orders.json 沒有「何時送出」，
    也就算不出倒數），所以服務沒啟動時回空陣列。
    """
    if trade_manager is None:
        return []
    try:
        from copy_trader.trade_manager.manager import OrderStatus

        waiting = {OrderStatus.PENDING, OrderStatus.SENT}
        now = time.time()
        rows = []
        for order in trade_manager.get_all_orders():
            if order.status not in waiting:
                continue
            signal = order.signal
            limit = order.cancel_after_seconds or 0
            elapsed = now - order.created_at
            rows.append({
                "signal_id": order.signal_id,
                "ticket": order.ticket,
                "status": order.status.value,
                "side": str(getattr(signal, "direction", "") or "").lower(),
                "symbol": getattr(signal, "symbol", "") or "",
                "entry_price": _float(getattr(signal, "entry_price", 0), 0.0),
                "sl": _float(getattr(signal, "stop_loss", 0), 0.0),
                "tp": (getattr(signal, "take_profit", None) or [None])[0],
                "source": order.source_window or "",
                "created_at": order.created_at,
                "elapsed_seconds": int(elapsed),
                "cancel_after_seconds": int(limit),
                # limit 為 0 = 不因逾時刪單，倒數就沒有意義
                "remaining_seconds": int(max(0, limit - elapsed)) if limit else None,
                "cancel_if_price_beyond": order.cancel_if_price_beyond,
            })
        rows.sort(key=lambda r: r["created_at"])
        return rows
    except Exception:
        return []


def build_stats(settings: Dict[str, Any], trade_manager: Any = None) -> Dict[str, Any]:
    mt5_dir = resolve_mt5_dir(str(settings.get("mt5_files_dir") or ""))
    now = time.time()

    account_raw = _read_json(mt5_dir / "account_info.json") or {}
    closed_raw = _read_json(mt5_dir / "closed_trades.json") or {}
    positions_raw = _read_json(mt5_dir / "positions.json") or {}
    martingale_raw = _read_json(mt5_dir / "martingale_state.json") or {}
    sources_raw = _read_json(mt5_dir / "signal_sources.json") or {}

    account_stamp = _float(account_raw.get("timestamp"), 0.0)
    stale_seconds = int(now - account_stamp) if account_stamp else None
    connected = bool(
        account_raw
        and account_raw.get("terminal_connected")
        and stale_seconds is not None
        and stale_seconds <= STALE_AFTER_SECONDS
    )

    events = read_journal()
    index = _journal_index(events)
    ladder = build_ladder(settings)

    trades: List[Dict[str, Any]] = []
    for raw in (closed_raw.get("trades") or []):
        if not isinstance(raw, dict):
            continue
        # closed_trades 的 comment 是 "copy_" + signal_id
        comment = str(raw.get("comment") or "")
        signal_id = comment[5:] if comment.startswith("copy_copy_") else comment
        fields = index.get(signal_id, {})
        volume = _float(raw.get("volume"), 0.0)
        profit = _float(raw.get("profit"), 0.0)

        trades.append({
            "ticket": raw.get("ticket"),
            "signal_id": signal_id,
            "symbol": raw.get("symbol") or "",
            "side": str(raw.get("type") or "").lower(),
            "volume": volume,
            "entry_price": _float(raw.get("entry_price"), 0.0),
            "exit_price": _float(raw.get("exit_price"), 0.0),
            "sl": _float(raw.get("sl"), 0.0),
            "tp": _float(raw.get("tp"), 0.0),
            "profit": profit,
            "is_win": profit >= 0,
            "change_percent": _float(raw.get("change_percent"), 0.0),
            "open_timestamp": _int(raw.get("open_timestamp"), 0),
            "close_timestamp": _int(raw.get("close_timestamp"), 0),
            "close_time": str(raw.get("close_time") or ""),
            "source": sources_raw.get(signal_id) or fields.get("來源") or "",
            "level": _level_for_volume(volume, ladder),
        })

    trades.sort(key=lambda t: (t["close_timestamp"], t["ticket"] or 0))

    cumulative = 0.0
    for trade in trades:
        cumulative += trade["profit"]
        trade["cumulative"] = round(cumulative, 2)

    positions: List[Dict[str, Any]] = []
    for raw in (positions_raw.get("positions") or []):
        if not isinstance(raw, dict):
            continue
        signal_id = str(raw.get("comment") or "")
        if signal_id.startswith("copy_copy_"):
            signal_id = signal_id[5:]
        positions.append({
            "ticket": raw.get("ticket"),
            "symbol": raw.get("symbol") or "",
            "side": str(raw.get("type") or "").lower(),
            "volume": _float(raw.get("volume"), 0.0),
            "entry_price": _float(raw.get("entry_price") or raw.get("price_open"), 0.0),
            "current_price": _float(raw.get("current_price") or raw.get("price_current"), 0.0),
            "sl": _float(raw.get("sl"), 0.0),
            "tp": _float(raw.get("tp"), 0.0),
            "profit": _float(raw.get("profit"), 0.0),
            "open_timestamp": _int(raw.get("open_timestamp") or raw.get("time"), 0),
            "source": sources_raw.get(signal_id) or "",
        })

    level = _int(martingale_raw.get("level"), 0)
    next_lot = ladder[level] if 0 <= level < len(ladder) else (ladder[-1] if ladder else 0.0)

    return {
        "connected": connected,
        "mt5_dir": str(mt5_dir),
        "stale_seconds": stale_seconds,
        "generated_at": now,
        "account": {
            "login": account_raw.get("login"),
            "name": account_raw.get("name") or "",
            "server": account_raw.get("server") or "",
            "currency": account_raw.get("currency") or "USD",
            "balance": _float(account_raw.get("balance"), 0.0),
            "equity": _float(account_raw.get("equity"), 0.0),
            "margin": _float(account_raw.get("margin"), 0.0),
            "free_margin": _float(account_raw.get("free_margin"), 0.0),
            "floating": _float(account_raw.get("profit"), 0.0),
            "leverage": account_raw.get("leverage"),
            "terminal_connected": bool(account_raw.get("terminal_connected")),
            "server_time": account_raw.get("server_time") or "",
            # EA 寫的 close_timestamp 用 UTC 讀出來就是券商牆上時間，前端算「今日／本週」
            # 要用同一個時區，不然篩選結果會跟表格顯示的日期對不起來
            "gmt_offset": _int(account_raw.get("gmt_offset"), 0),
        } if account_raw else None,
        "summary": _summarise(trades),
        "cycles": _cycles(trades),
        "trades": trades,
        "positions": positions,
        "pending": pending_orders(trade_manager),
        "cancel_rules": {
            "after_seconds": _int(settings.get("cancel_pending_after_seconds"), 10800),
            "price_beyond_percent": _float(settings.get("cancel_if_price_beyond_percent"), 1.0),
        },
        "sources": _by_source(trades),
        "daily": _by_day(trades),
        "martingale": {
            "level": level,
            "consecutive_losses": _int(martingale_raw.get("consecutive_losses"), 0),
            "enabled": str(settings.get("use_martingale", "")).lower() in ("true", "1", "yes", "on"),
            "multiplier": _float(settings.get("martingale_multiplier"), 2.0),
            "ladder": ladder,
            "next_lot": next_lot,
            "updated": martingale_raw.get("updated") or "",
        },
        "journal": [
            {"time": e["time"], "event": e["event"], "fields": e["fields"]}
            for e in events[:60]
        ],
    }
