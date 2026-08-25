"""行情資料層：把 EA 寫出的報價與 K 線整理成前端畫圖要的形狀。

這個模組只讀不寫。資料來源全部來自會員自己那台 MT5，不碰外部 API——
圖上看到的價格就是他下單的那個券商的價格，不會出現「圖表跟成交價對不上」
這種事。

  <MT5 Files>/rates_M1.json    M1 K 線（舊版 EA 也有）
  <MT5 Files>/rates_M5.json    M5 / M15 / H1 / H4 / D1（v4.2 起）
  <MT5 Files>/watchlist.json   市場總覽的自選商品報價（v4.2 起）
  <MT5 Files>/<SYMBOL>_price.json  主商品即時 bid/ask

舊版 EA 只寫 M1。這種情況不擺爛也不報錯，直接拿 M1 聚合出大週期——
K 線聚合是純算術（開盤取第一根、收盤取最後一根、高低取極值、量相加），
結果跟券商自己算的一模一樣，只是根數受限於 M1 的存量。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from copy_trader.central.stats import _read_json, resolve_mt5_dir

# 前端可以切的週期，以及每根幾秒。順序就是 UI 上的排列順序。
TIMEFRAMES: Dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

# 超過這個秒數沒更新，就當作 EA 沒在寫了
STALE_AFTER_SECONDS = 180

# 前端最多畫這麼多根。再多也只是把蠟燭壓成一片糊。
MAX_BARS = 400


def _bars_from(path: Path) -> Optional[Dict[str, Any]]:
    """讀一個 rates 檔。檔案不在或內容壞掉都回 None，交給呼叫端降級。"""
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    bars = data.get("bars")
    if not isinstance(bars, list) or not bars:
        return None

    clean: List[Dict[str, Any]] = []
    for b in bars:
        try:
            row = {
                "t": int(b["t"]),
                "o": float(b["o"]),
                "h": float(b["h"]),
                "l": float(b["l"]),
                "c": float(b["c"]),
                "v": int(b.get("tv") or 0),
            }
        except (KeyError, TypeError, ValueError):
            continue
        # 高低必須包住開收，不然畫出來會是穿幫的蠟燭
        row["h"] = max(row["h"], row["o"], row["c"])
        row["l"] = min(row["l"], row["o"], row["c"])
        clean.append(row)

    if not clean:
        return None
    clean.sort(key=lambda r: r["t"])
    return {
        "symbol": str(data.get("symbol") or ""),
        "timeframe": str(data.get("timeframe") or ""),
        "digits": int(data.get("digits") or 2),
        "bars": clean[-MAX_BARS:],
    }


def aggregate(bars: List[Dict[str, Any]], seconds: int) -> List[Dict[str, Any]]:
    """把小週期的 K 線併成大週期。

    以 epoch 對齊切桶（跟 MT5 的分桶一致），每桶開盤取第一根的 o、收盤取
    最後一根的 c、高低取極值、量相加。
    """
    if seconds <= 0 or not bars:
        return list(bars)

    out: List[Dict[str, Any]] = []
    for b in bars:
        bucket = b["t"] - (b["t"] % seconds)
        if out and out[-1]["t"] == bucket:
            cur = out[-1]
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b["v"]
        else:
            out.append({"t": bucket, "o": b["o"], "h": b["h"],
                        "l": b["l"], "c": b["c"], "v": b["v"]})
    return out


def _live_price(mt5_dir: Path, symbol: str) -> Optional[Dict[str, Any]]:
    """主商品的即時報價。EA 每秒寫一次。"""
    names = []
    if symbol:
        names.append(f"{symbol}_price.json")
    names.append("XAUUSD_price.json")

    for name in names:
        data = _read_json(mt5_dir / name)
        if not isinstance(data, dict):
            continue
        try:
            bid = float(data.get("bid") or 0)
            ask = float(data.get("ask") or 0)
        except (TypeError, ValueError):
            continue
        if bid <= 0:
            continue
        ts = int(data.get("timestamp") or data.get("time") or 0)
        return {
            "symbol": str(data.get("symbol") or symbol or "XAUUSD"),
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 5) if ask > 0 else None,
            "timestamp": ts,
            "stale": bool(ts and (time.time() - ts) > STALE_AFTER_SECONDS),
        }
    return None


def _watchlist(mt5_dir: Path) -> List[Dict[str, Any]]:
    """市場總覽。舊版 EA 沒這個檔，回空陣列讓前端整塊收起來。"""
    data = _read_json(mt5_dir / "watchlist.json")
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []

    rows: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            bid = float(it.get("bid") or 0)
        except (TypeError, ValueError):
            continue
        if bid <= 0:
            continue
        try:
            pct = float(it.get("change_pct") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        rows.append({
            "symbol": str(it.get("symbol") or ""),
            "bid": bid,
            "digits": int(it.get("digits") or 2),
            "change_pct": pct,
        })
    return rows


def build_market(settings: Dict[str, Any], timeframe: str = "M15") -> Dict[str, Any]:
    """組出 /api/market 的回應。

    MT5 沒開的時候每一塊各自降級（回 None 或空陣列），不會整包失敗——
    前端靠 connected 決定顯示「等待 MT5 連線」還是真的畫圖。
    """
    timeframe = timeframe if timeframe in TIMEFRAMES else "M15"
    mt5_dir = resolve_mt5_dir(str(settings.get("mt5_files_dir") or ""))
    symbol = str(settings.get("symbol") or settings.get("trading_symbol") or "XAUUSD")

    result: Dict[str, Any] = {
        "connected": False,
        "symbol": symbol,
        "timeframe": timeframe,
        "digits": 2,
        "bars": [],
        "source": "none",
        "available": [],
        "price": None,
        "watchlist": [],
        "server_time": int(time.time()),
    }

    if not mt5_dir.is_dir():
        return result

    # EA 直接寫出來的週期優先；只有 M1 的舊版 EA 才走聚合
    native = {tf for tf in TIMEFRAMES if (mt5_dir / f"rates_{tf}.json").is_file()}
    m1 = _bars_from(mt5_dir / "rates_M1.json")

    payload = None
    if timeframe in native:
        payload = _bars_from(mt5_dir / f"rates_{timeframe}.json")
        if payload:
            result["source"] = "native"

    if payload is None and m1 is not None:
        # 降級：拿 M1 聚合。M1 只有 400 根 ≈ 6.7 小時，所以 H4/D1 聚合出來
        # 可能只有一兩根——available 會照實反映，前端不給選。
        agg = aggregate(m1["bars"], TIMEFRAMES[timeframe])
        if agg:
            payload = {"symbol": m1["symbol"], "timeframe": timeframe,
                       "digits": m1["digits"], "bars": agg}
            result["source"] = "aggregated"

    if payload:
        result["connected"] = True
        result["bars"] = payload["bars"]
        result["digits"] = payload["digits"]
        if payload["symbol"]:
            result["symbol"] = payload["symbol"]

    # 哪些週期真的有足夠的根數可以畫（至少 8 根才不會是一片空白）
    available = []
    for tf, secs in TIMEFRAMES.items():
        if tf in native:
            available.append(tf)
        elif m1 is not None and len(aggregate(m1["bars"], secs)) >= 8:
            available.append(tf)
    result["available"] = available

    result["price"] = _live_price(mt5_dir, result["symbol"])
    result["watchlist"] = _watchlist(mt5_dir)
    return result


def live_tick(settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """只讀即時報價那一個小檔，給每秒一次的 /api/status 搭順風車用。

    刻意不重讀 K 線——那是幾百 KB，每秒讀一次沒必要。前端拿這個價去更新
    「形成中的那根」，蠟燭的骨架仍然由 /api/market 每 5 秒帶回來。
    """
    mt5_dir = resolve_mt5_dir(str(settings.get("mt5_files_dir") or ""))
    if not mt5_dir.is_dir():
        return None
    symbol = str(settings.get("symbol") or settings.get("trading_symbol") or "XAUUSD")
    return _live_price(mt5_dir, symbol)
