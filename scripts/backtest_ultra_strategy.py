#!/usr/bin/env python3
"""No-lookahead backtest for ``ultra_confluence_v1``.

Examples:

  python scripts/backtest_ultra_strategy.py --input rates_M1.json
  python scripts/backtest_ultra_strategy.py --dukascopy 2026-01-01 2026-08-26

Dukascopy input is BID candles.  ``--spread`` is therefore added for buy-limit
fills and sell-position exits.  The simulation is deliberately conservative:
when stop and target are both inside one M1 candle, the stop is evaluated first.
No result from this script is a promise of future performance.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import json
import lzma
from pathlib import Path
import struct
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from copy_trader.central.market import aggregate  # noqa: E402
from copy_trader.central.ultra_strategy import (  # noqa: E402
    UltraStrategyConfig,
    evaluate_market,
)


def _clean_bar(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return {
            "t": int(raw["t"]),
            "o": float(raw["o"]),
            "h": float(raw["h"]),
            "l": float(raw["l"]),
            "c": float(raw["c"]),
            "v": int(raw.get("tv") or raw.get("v") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def load_json(path: Path) -> List[Dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    raw_bars = value.get("bars") if isinstance(value, dict) else value
    bars = [_clean_bar(item) for item in (raw_bars or []) if isinstance(item, dict)]
    return sorted((item for item in bars if item), key=lambda item: item["t"])


def _dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _dukascopy_day(day: date, cache_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    # Dukascopy's archive month is zero based.  Candle rows are
    # seconds-from-day, OHLC integers and float volume in big-endian order.
    url = (
        "https://datafeed.dukascopy.com/datafeed/XAUUSD/"
        f"{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/BID_candles_min_1.bi5"
    )
    try:
        cache_path = cache_dir / f"{day.isoformat()}.bi5" if cache_dir else None
        if cache_path and cache_path.exists():
            compressed = cache_path.read_bytes()
        else:
            compressed = b""
            for attempt in range(5):
                try:
                    request = Request(url, headers={"User-Agent": "TRADER research backtest/1.0"})
                    with urlopen(request, timeout=40) as response:
                        compressed = response.read()
                    break
                except (HTTPError, URLError, TimeoutError):
                    if attempt == 4:
                        raise
                    time.sleep(1.5 * (attempt + 1))
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(compressed)
        raw = lzma.decompress(compressed)
    except (HTTPError, URLError, TimeoutError, lzma.LZMAError):
        return []
    if len(raw) % 24:
        return []
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    bars = []
    for offset in range(0, len(raw), 24):
        second, open_i, close_i, low_i, high_i, volume = struct.unpack(">5If", raw[offset:offset + 24])
        if not any((open_i, close_i, low_i, high_i)):
            continue
        bars.append({
            "t": midnight + int(second),
            "o": open_i / 1000.0,
            "h": high_i / 1000.0,
            "l": low_i / 1000.0,
            "c": close_i / 1000.0,
            "v": int(max(0.0, volume) * 1000),
        })
    return bars


def download_dukascopy(
    start: date,
    end: date,
    workers: int = 4,
    cache_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    days = list(_dates(start, end))
    by_day: Dict[date, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 24))) as pool:
        futures = {pool.submit(_dukascopy_day, day, cache_dir): day for day in days}
        for future in as_completed(futures):
            by_day[futures[future]] = future.result()
    missing = [day.isoformat() for day in days if len(by_day.get(day, [])) < 360]
    if missing:
        preview = ", ".join(missing[:8])
        raise RuntimeError(
            f"historical data incomplete: {len(missing)}/{len(days)} days missing ({preview})"
        )
    return [bar for day in days for bar in by_day.get(day, [])]


def _completed(bars: List[Dict[str, Any]], pointer: int, end_time: int, seconds: int) -> int:
    while pointer < len(bars) and int(bars[pointer]["t"]) + seconds <= end_time:
        pointer += 1
    return pointer


def _exit_position(position: Dict[str, Any], bar: Dict[str, Any], spread: float) -> Optional[float]:
    decision = position["decision"]
    risk = abs(decision.entry_price - decision.stop_loss)
    if decision.direction == "buy":
        adverse_high, adverse_low = float(bar["h"]), float(bar["l"])
    else:
        # A sell closes on ASK while the source file contains BID candles.
        adverse_high = float(bar["h"]) + spread
        adverse_low = float(bar["l"]) + spread

    # Conservative intrabar ordering: existing stop first, then favourable
    # targets.  Newly trailed stops only become active from the next M1 bar.
    current_sl = float(position["current_sl"])
    if decision.direction == "buy" and adverse_low <= current_sl:
        return (current_sl - decision.entry_price) / risk
    if decision.direction == "sell" and adverse_high >= current_sl:
        return (decision.entry_price - current_sl) / risk

    reached = int(position["reached"])
    for index, target in enumerate(decision.take_profit):
        if index < reached:
            continue
        hit = adverse_high >= target if decision.direction == "buy" else adverse_low <= target
        if not hit:
            break
        reached = index + 1
        if reached == len(decision.take_profit):
            return 3.0
    position["reached"] = reached
    if reached == 1:
        position["current_sl"] = decision.entry_price
    elif reached >= 2:
        position["current_sl"] = decision.take_profit[reached - 2]
    return None


def run_backtest(
    bars: List[Dict[str, Any]],
    spread: float,
    config: UltraStrategyConfig,
    *,
    include_trades: bool = False,
) -> Dict[str, Any]:
    m15 = aggregate(bars, 900)
    h1 = aggregate(bars, 3600)
    m15_pointer = h1_pointer = 0
    pending: Optional[Dict[str, Any]] = None
    position: Optional[Dict[str, Any]] = None
    central_active_until = 0
    last_signal_at = 0
    daily_counts: Dict[str, int] = defaultdict(int)
    results: List[Dict[str, Any]] = []
    counts: Dict[str, int] = defaultdict(int)
    equity = peak = max_drawdown = 0.0

    for index, current in enumerate(bars):
        end_time = int(current["t"]) + 60
        m15_pointer = _completed(m15, m15_pointer, end_time, 900)
        h1_pointer = _completed(h1, h1_pointer, end_time, 3600)

        if pending is not None and index > pending["created_index"]:
            decision = pending["decision"]
            filled = (
                float(current["l"]) + spread <= decision.entry_price
                if decision.direction == "buy"
                else float(current["h"]) >= decision.entry_price
            )
            if filled:
                position = {
                    "decision": decision,
                    "current_sl": decision.stop_loss,
                    "reached": 0,
                    "filled_index": index,
                }
                pending = None
                counts["filled"] += 1
            elif end_time >= pending["expires_at"]:
                pending = None
                counts["unfilled"] += 1

        if position is not None and index > position["filled_index"]:
            outcome = _exit_position(position, current, spread)
            if outcome is not None:
                decision = position["decision"]
                results.append({
                    "r": outcome,
                    "setup": decision.setup,
                    "direction": decision.direction,
                    "signal_time": decision.bar_time,
                    **decision.feature_payload(),
                })
                equity += outcome
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, peak - equity)
                position = None

        if end_time >= central_active_until:
            central_active_until = 0
        if central_active_until or end_time - last_signal_at < config.cooldown_seconds:
            continue
        if index < 400 or m15_pointer < 30 or h1_pointer < 30:
            continue
        day = datetime.fromtimestamp(end_time, timezone.utc).strftime("%Y-%m-%d")
        if daily_counts[day] >= config.max_signals_per_day:
            continue

        decision = evaluate_market(
            bars[max(0, index - 399):index + 1],
            m15[max(0, m15_pointer - 400):m15_pointer],
            h1[max(0, h1_pointer - 400):h1_pointer],
            {"bid": float(current["c"]), "ask": float(current["c"]) + spread},
            config,
        )
        if decision is None:
            continue
        counts["generated"] += 1
        counts[f"generated_{decision.setup}"] += 1
        daily_counts[day] += 1
        last_signal_at = end_time
        central_active_until = end_time + config.pending_expiry_seconds

        # Member-side max_active_orders=1: central may publish while an older
        # position is open, but that member correctly declines the new event.
        if pending is not None or position is not None:
            counts["blocked_active"] += 1
            continue
        pending = {
            "decision": decision,
            "created_index": index,
            "expires_at": central_active_until,
        }
        counts["accepted"] += 1

    wins = [row for row in results if row["r"] > 0]
    losses = [row for row in results if row["r"] < 0]
    breakeven = [row for row in results if row["r"] == 0]
    setup_stats = {}
    for setup in sorted({row["setup"] for row in results}):
        selected = [row for row in results if row["setup"] == setup]
        setup_stats[setup] = {
            "closed": len(selected),
            "win_rate_percent": (
                round(100 * sum(row["r"] > 0 for row in selected) / len(selected), 2)
                if selected else 0.0
            ),
            "net_r": round(sum(row["r"] for row in selected), 2),
        }
    gross_win = sum(row["r"] for row in wins)
    gross_loss = abs(sum(row["r"] for row in losses))
    summary = {
        "bars": len(bars),
        "from": datetime.fromtimestamp(bars[0]["t"], timezone.utc).isoformat() if bars else "",
        "to": datetime.fromtimestamp(bars[-1]["t"], timezone.utc).isoformat() if bars else "",
        **dict(counts),
        "open_at_end": int(position is not None),
        "pending_at_end": int(pending is not None),
        "closed": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate_percent": round(100 * len(wins) / len(results), 2) if results else 0.0,
        "net_r": round(sum(row["r"] for row in results), 2),
        "average_r": round(sum(row["r"] for row in results) / len(results), 3) if results else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_r": round(max_drawdown, 2),
        "by_setup": setup_stats,
    }
    if include_trades:
        summary["_trades"] = results
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path, help="MT5 rates_M1.json")
    group.add_argument("--dukascopy", nargs=2, metavar=("START", "END"), help="inclusive YYYY-MM-DD range")
    parser.add_argument("--spread", type=float, default=0.40, help="fixed USD bid/ask spread")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cache-dir", type=Path, help="optional directory for downloaded .bi5 files")
    parser.add_argument("--trades-output", type=Path, help="optional JSON file with per-trade features/outcome")
    args = parser.parse_args()

    if args.input:
        bars = load_json(args.input)
    else:
        start, end = (date.fromisoformat(value) for value in args.dukascopy)
        bars = download_dukascopy(start, end, args.workers, args.cache_dir)
    config = UltraStrategyConfig(enabled=True)
    summary = run_backtest(
        bars,
        max(0.0, args.spread),
        config,
        include_trades=bool(args.trades_output),
    )
    trades = summary.pop("_trades", [])
    if args.trades_output:
        args.trades_output.parent.mkdir(parents=True, exist_ok=True)
        args.trades_output.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
