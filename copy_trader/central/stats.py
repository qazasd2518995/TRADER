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

def pending_orders(trade_manager: Any, mt5_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """還沒進場的掛單，附上距離自動刪單還剩幾秒。

    以 TradeManager 追蹤中的單為主（只有它知道倒數）。另外把 MT5 上存在、但沒被
    追蹤到的本系統掛單也一併列出並標記 tracked=False —— 面板叫「待成交掛單」，
    就該反映 MT5 的實況，不能因為程式沒追蹤到就假裝沒有。
    """
    rows: List[Dict[str, Any]] = []
    tracked_tickets = set()
    rows.extend(_tracked_pending(trade_manager, tracked_tickets))
    rows.extend(_untracked_pending(mt5_dir, tracked_tickets))
    rows.sort(key=lambda r: r["created_at"])
    return rows


def _untracked_pending(mt5_dir: Optional[Path], tracked: set) -> List[Dict[str, Any]]:
    """MT5 上有、但 TradeManager 沒在追的本系統掛單（例如重啟後沒認領成功的）。"""
    if mt5_dir is None:
        return []
    data = _read_json(Path(mt5_dir) / "orders.json") or {}
    out = []
    for raw in (data.get("orders") or []):
        if not isinstance(raw, dict):
            continue
        ticket = raw.get("ticket")
        if ticket in tracked:
            continue
        try:
            side = "sell" if int(raw.get("type") or 0) % 2 else "buy"
        except (TypeError, ValueError):
            side = ""
        out.append({
            "signal_id": str(raw.get("comment") or ""),
            "ticket": ticket,
            "status": "untracked",
            "tracked": False,
            "side": side,
            "symbol": raw.get("symbol") or "",
            "entry_price": _float(raw.get("price"), 0.0),
            "sl": _float(raw.get("sl"), 0.0),
            "tp": _float(raw.get("tp"), 0.0),
            "source": "",
            "created_at": 0.0,
            "elapsed_seconds": None,
            "cancel_after_seconds": 0,
            "remaining_seconds": None,
            "cancel_if_price_beyond": None,
            "setup_time": raw.get("time_setup") or "",
        })
    return out


def _tracked_pending(trade_manager: Any, tracked: set) -> List[Dict[str, Any]]:
    if trade_manager is None:
        return []
    try:
        from copy_trader.trade_manager.manager import OrderStatus

        waiting = {OrderStatus.PENDING, OrderStatus.SENT}
        now = time.time()
        try:
            current_price = trade_manager._get_current_price()
        except Exception:
            current_price = None
        rows = []
        for order in trade_manager.get_all_orders():
            if order.status not in waiting:
                continue
            signal = order.signal
            limit = order.cancel_after_seconds or 0
            elapsed = now - order.created_at
            if order.ticket is not None:
                tracked.add(order.ticket)
            rows.append({
                "signal_id": order.signal_id,
                "ticket": order.ticket,
                "status": order.status.value,
                "tracked": True,
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
                # 離成交還差多少，以及掛單期間最接近過的價位
                "current_price": current_price,
                "distance": (abs(current_price - float(getattr(signal, "entry_price", 0) or 0))
                             if current_price and getattr(signal, "entry_price", None) else None),
                "closest_price": order.closest_price,
                "closest_gap": order.closest_gap,
            })
        return rows
    except Exception:
        return []


def source_settings(
    settings: Dict[str, Any],
    trades: List[Dict[str, Any]],
    sources_raw: Dict[str, Any],
    martingale_raw: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """每個訊號來源的下單設定 + 目前馬丁層級。

    來源清單是「自動發現」的：把設定裡有的、歷史成交出現過的、signal_sources.json
    收過的全部聯集起來。這樣使用者不必手打群組名——打錯字會靜默套用全域設定，
    是這種以字串當 key 的設計最容易踩的坑。
    """
    try:
        profiles = json.loads(str(settings.get("source_profiles") or "{}"))
        if not isinstance(profiles, dict):
            profiles = {}
    except (TypeError, ValueError):
        profiles = {}

    known: List[str] = []
    for name in list(profiles.keys()) + [t["source"] for t in trades] + list(sources_raw.values()):
        name = str(name or "").strip()
        if name and name not in known:
            known.append(name)

    per_source = martingale_raw.get("per_source") or {}
    base_lot = _float(settings.get("default_lot_size"), 0.01)
    multiplier = _float(settings.get("martingale_multiplier"), 2.0)
    max_level = _int(settings.get("martingale_max_level"), 5)
    global_martingale = str(settings.get("use_martingale", "")).lower() in ("true", "1", "yes", "on")

    rows = []
    for name in known:
        raw = profiles.get(name) or {}
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in ("martingale", "flat"):
            mode = "martingale" if global_martingale else "flat"
        tp_mode = str(raw.get("tp_mode") or "").strip().lower()
        if tp_mode not in ("partial", "breakeven"):
            tp_mode = "partial"
        state = per_source.get(name) or {}
        rows.append({
            "source": name,
            "configured": bool(raw),
            "enabled": bool(raw.get("enabled", True)),
            "mode": mode,
            "tp_mode": tp_mode,
            "base_lot": _float(raw.get("base_lot"), base_lot),
            "multiplier": _float(raw.get("multiplier"), multiplier),
            "max_level": _int(raw.get("max_level"), max_level),
            "level": _int(state.get("level"), 0),
            "losses": _int(state.get("losses"), 0),
            "trades": sum(1 for t in trades if t["source"] == name),
        })
    return rows


def _position_side(close_deal_type: Any) -> str:
    """從「平倉成交的方向」還原出當初持倉的方向。

    MT5 的歷史成交記的是成交本身的方向：平掉一張買單是靠「賣出」成交完成的，
    所以 closed_trades.json 裡 type=sell 的那筆，當初下的其實是買單。
    直接拿 type 當方向顯示，每一筆都會是相反的。
    """
    raw = str(close_deal_type or "").strip().lower()
    if raw in ("buy", "0"):
        return "sell"
    if raw in ("sell", "1"):
        return "buy"
    return ""


def _merge_partial_closes(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把同一個 position 的多筆分批平倉合併成一筆。

    多 TP 分批平倉會讓一個 position 產生好幾筆成交紀錄，而 EA 把「position 的
    淨損益」寫進其中每一筆。直接加總會把同一筆損益重複計算好幾次。

    實例 (2026-07-31)：0.5 手在 TP1 平、剩餘 0.5 手停損，兩筆紀錄都寫 -4.50。
    加總得 -9.00，但帳戶餘額顯示這個 position 實際只賠了 4.50。

    合併規則：損益取一次（每筆都是淨額）、手數加總、出場價與時間取最後一筆。
    """
    if not trades:
        return trades

    grouped: Dict[Any, List[Dict[str, Any]]] = {}
    order: List[Any] = []
    for trade in trades:
        key = trade.get("position_id") or ("ticket", trade.get("ticket"))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(trade)

    merged: List[Dict[str, Any]] = []
    for key in order:
        parts = sorted(grouped[key], key=lambda t: t["close_timestamp"])
        head = dict(parts[-1])              # 出場價 / 平倉時間取最後一段
        head["volume"] = round(sum(p["volume"] for p in parts), 2)
        head["parts"] = len(parts)          # 分幾段平掉，前端可顯示
        head["ticket"] = parts[0]["ticket"]
        head["entry_price"] = parts[0]["entry_price"]
        head["open_timestamp"] = parts[0]["open_timestamp"]
        merged.append(head)
    return merged


def build_stats(settings: Dict[str, Any], trade_manager: Any = None) -> Dict[str, Any]:
    mt5_dir = resolve_mt5_dir(str(settings.get("mt5_files_dir") or ""))
    now = time.time()

    account_raw = _read_json(mt5_dir / "account_info.json") or {}
    closed_raw = _read_json(mt5_dir / "closed_trades.json") or {}
    positions_raw = _read_json(mt5_dir / "positions.json") or {}
    martingale_raw = _read_json(mt5_dir / "martingale_state.json") or {}
    sources_raw = _read_json(mt5_dir / "signal_sources.json") or {}

    # EA 寫的 timestamp 是「券商牆上時間」當成 epoch（GMT+3 就會比 UTC 快 3 小時），
    # 不扣掉時差的話，停擺 3 小時內的檔案都會被誤判成「連線中」。
    account_stamp = _float(account_raw.get("timestamp"), 0.0)
    broker_offset = _int(account_raw.get("gmt_offset"), 0)
    stale_seconds = int(now + broker_offset - account_stamp) if account_stamp else None
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
            # 分批平倉的每一段都是獨立成交紀錄，但共用同一個 position_id，
            # 用它把同一張單的多段合併回一筆（見 _merge_partial_closes）
            "position_id": raw.get("position_id"),
            "signal_id": signal_id,
            "symbol": raw.get("symbol") or "",
            # closed_trades 的 type 是「平倉成交」的方向，跟持倉方向相反
            # （平掉買單靠賣出成交）。要顯示成當初下的單，必須反過來。
            "side": _position_side(raw.get("type")),
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

    # 均注來源沒有關卡可言，標記模式讓前端別顯示「第 N 關」
    try:
        profiles = json.loads(str(settings.get("source_profiles") or "{}"))
    except (TypeError, ValueError):
        profiles = {}
    for trade in trades:
        profile = profiles.get(trade["source"]) if isinstance(profiles, dict) else None
        mode = str((profile or {}).get("mode") or "").lower()
        trade["mode"] = mode if mode in ("flat", "martingale") else ""

    trades = _merge_partial_closes(trades)
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
        "pending": pending_orders(trade_manager, mt5_dir),
        "source_settings": source_settings(settings, trades, sources_raw, martingale_raw),
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
