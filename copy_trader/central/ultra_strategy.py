"""Deterministic market-data signal source for the third product.

This module never reads LINE and never impersonates a chat sender.  It reads
closed MT5 bars plus the broker's live bid/ask, evaluates one explicit setup,
and publishes ordinary Hub events under ``ULTRA_HIGH_FREQ``.  Every accepted
decision carries its feature values so live results can be audited by setup.

The strategy intentionally emits limit orders only.  An unfilled order expires
through an exact ``cancel_signal`` event; a filled position is never closed by
that expiry event because the member TradeManager's cancellation state machine
only deletes pending MT5 orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Optional, Protocol

from copy_trader.central.market import _bars_from, _live_price
from copy_trader.central.membership import ULTRA_HIGH_FREQ
from copy_trader.central.stats import _read_json, resolve_mt5_dir

logger = logging.getLogger(__name__)

STRATEGY_NAME = "ultra_confluence_v1"
MIN_M1_BARS = 360
MIN_M15_BARS = 30
MIN_H1_BARS = 30


class Publisher(Protocol):
    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class UltraStrategyConfig:
    enabled: bool = False
    max_signals_per_day: int = 12
    cooldown_seconds: int = 15 * 60
    pending_expiry_seconds: int = 20 * 60
    max_spread: float = 1.20
    min_h1_atr: float = 4.0
    max_h1_atr: float = 60.0
    max_market_age_seconds: int = 90

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "UltraStrategyConfig":
        def truthy(value: Any) -> bool:
            return str(value or "").strip().lower() in {"1", "true", "yes", "on", "啟用"}

        def number(key: str, fallback: float, low: float, high: float) -> float:
            try:
                value = float(settings.get(key, fallback))
            except (TypeError, ValueError):
                value = fallback
            return min(high, max(low, value))

        min_h1_atr = number("ultra_min_h1_atr", 4.0, 0.1, 100.0)
        max_h1_atr = number("ultra_max_h1_atr", 60.0, 1.0, 300.0)
        return cls(
            enabled=truthy(settings.get("ultra_strategy_enabled")),
            max_signals_per_day=int(number("ultra_max_signals_per_day", 12, 1, 96)),
            cooldown_seconds=int(number("ultra_cooldown_seconds", 900, 60, 6 * 3600)),
            pending_expiry_seconds=int(number("ultra_pending_expiry_seconds", 1200, 120, 6 * 3600)),
            max_spread=number("ultra_max_spread", 1.20, 0.05, 10.0),
            min_h1_atr=min_h1_atr,
            max_h1_atr=max(min_h1_atr, max_h1_atr),
            max_market_age_seconds=int(number("ultra_max_market_age_seconds", 90, 10, 600)),
        )


@dataclass(frozen=True)
class StrategyDecision:
    direction: str
    setup: str
    entry_price: float
    stop_loss: float
    take_profit: List[float]
    h1_atr: float
    grid_step: float
    retest_count: int
    h1_trend: str
    m15_trend: str
    displacement_15m: float
    recent_range_15m: float
    sweep_depth: float
    close_location: float
    spread: float
    bar_time: int

    def feature_payload(self) -> Dict[str, Any]:
        value = asdict(self)
        for key in ("entry_price", "stop_loss", "h1_atr", "grid_step",
                    "displacement_15m", "recent_range_15m", "sweep_depth",
                    "close_location", "spread"):
            value[key] = round(float(value[key]), 5)
        value["take_profit"] = [round(float(item), 5) for item in self.take_profit]
        return value


def _ema(values: List[float], period: int, end: Optional[int] = None) -> float:
    """EMA ending at ``end`` (exclusive); deterministic and dependency-free."""
    selected = values[:end] if end is not None else values
    if not selected:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = float(selected[0])
    for value in selected[1:]:
        result += alpha * (float(value) - result)
    return result


def _atr(bars: List[Dict[str, Any]], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    ranges: List[float] = []
    for index in range(max(1, len(bars) - period), len(bars)):
        bar = bars[index]
        previous_close = float(bars[index - 1]["c"])
        ranges.append(max(
            float(bar["h"]) - float(bar["l"]),
            abs(float(bar["h"]) - previous_close),
            abs(float(bar["l"]) - previous_close),
        ))
    return sum(ranges) / len(ranges) if ranges else 0.0


def _trend(bars: List[Dict[str, Any]], atr: float, *, slope_bars: int) -> str:
    closes = [float(bar["c"]) for bar in bars]
    fast = _ema(closes, 8)
    slow = _ema(closes, 21)
    prior_fast = _ema(closes, 8, max(1, len(closes) - slope_bars))
    gap = fast - slow
    slope = fast - prior_fast
    threshold = max(0.05 * atr, 0.05)
    if gap > threshold and slope > 0:
        return "up"
    if gap < -threshold and slope < 0:
        return "down"
    return "neutral"


def _round_price(value: float, digits: int) -> float:
    return round(float(value), max(0, min(int(digits), 8)))


def _touch_episodes(
    bars: Iterable[Dict[str, Any]],
    level: float,
    tolerance: float,
    *,
    separation_seconds: int = 8 * 60,
) -> int:
    count = 0
    last_touch = -10**18
    for bar in bars:
        touched = float(bar["l"]) <= level + tolerance and float(bar["h"]) >= level - tolerance
        if touched:
            timestamp = int(bar["t"])
            if timestamp - last_touch >= separation_seconds:
                count += 1
                last_touch = timestamp
    return count


def evaluate_market(
    m1: List[Dict[str, Any]],
    m15: List[Dict[str, Any]],
    h1: List[Dict[str, Any]],
    tick: Dict[str, Any],
    config: UltraStrategyConfig,
    *,
    digits: int = 2,
) -> Optional[StrategyDecision]:
    """Evaluate one newly closed M1 bar without using any future data."""
    if len(m1) < MIN_M1_BARS or len(m15) < MIN_M15_BARS or len(h1) < MIN_H1_BARS:
        return None

    bid = float(tick.get("bid") or 0)
    ask = float(tick.get("ask") or 0)
    if bid <= 0 or ask <= bid:
        return None
    spread = ask - bid
    if spread > config.max_spread:
        return None

    h1_atr = _atr(h1, 14)
    if not (config.min_h1_atr <= h1_atr <= config.max_h1_atr):
        return None

    # yuyu's observed template changes from a five-dollar to a ten-dollar grid
    # in the high-volatility regime.  The threshold is deliberately coarse so
    # it is stable across brokers and cannot overfit individual message prices.
    grid_step = 5.0 if h1_atr < 25.0 else 10.0
    tolerance = max(0.30, min(1.25, grid_step * 0.12))
    reclaim = max(0.35, grid_step * 0.07)
    last = m1[-1]
    close = float(last["c"])
    open_price = float(last["o"])
    low = float(last["l"])
    high = float(last["h"])

    h1_trend = _trend(h1, h1_atr, slope_bars=3)
    m15_trend = _trend(m15, h1_atr, slope_bars=2)
    displacement = close - float(m1[-16]["c"])
    recent = m1[-15:]
    recent_range = max(float(bar["h"]) for bar in recent) - min(float(bar["l"]) for bar in recent)
    if recent_range < 0.35 * h1_atr:
        return None

    buy_level = math.floor((close + 1e-9) / grid_step) * grid_step
    sell_level = math.ceil((close - 1e-9) / grid_step) * grid_step
    body = abs(close - open_price)
    candle_range = max(1e-9, high - low)
    close_location = (close - low) / candle_range
    buy_rejection = (
        low <= buy_level + tolerance
        and close >= buy_level + reclaim
        and (close > open_price or (min(open_price, close) - low) >= max(body * 0.35, 0.15))
    )
    sell_rejection = (
        high >= sell_level - tolerance
        and close <= sell_level - reclaim
        and (close < open_price or (high - max(open_price, close)) >= max(body * 0.35, 0.15))
    )

    direction = ""
    setup = ""
    entry = 0.0
    if buy_rejection and h1_trend == "up" and m15_trend == "up" and abs(displacement) < 0.35 * h1_atr:
        direction, setup, entry = "buy", "trend_pullback", buy_level
    elif sell_rejection and h1_trend == "down" and m15_trend == "down" and abs(displacement) < 0.35 * h1_atr:
        direction, setup, entry = "sell", "trend_pullback", sell_level
    else:
        return None

    # The reversal branch did not survive the chronological backtest and is not
    # present in production. A setup is accepted only when this exact level
    # appeared in at least two separated episodes during the preceding six
    # hours. The trigger bar is excluded; otherwise the current rejection would
    # count as its own history.
    retests = _touch_episodes(m1[-361:-1], entry, tolerance)
    if not (2 <= retests <= 4):
        return None

    gap = (ask - entry) if direction == "buy" else (entry - bid)
    min_gap = max(0.30, spread * 2.0)
    max_gap = grid_step * 0.55
    if not (min_gap <= gap <= max_gap):
        return None

    risk = max(grid_step, min(grid_step * 1.50, h1_atr * 0.35))
    risk = round(risk * 2.0) / 2.0
    if direction == "buy":
        sweep_depth = entry - low
        stop = entry - risk
        take_profit = [entry + risk, entry + 2 * risk, entry + 3 * risk]
    else:
        sweep_depth = high - entry
        stop = entry + risk
        take_profit = [entry - risk, entry - 2 * risk, entry - 3 * risk]

    return StrategyDecision(
        direction=direction,
        setup=setup,
        entry_price=_round_price(entry, digits),
        stop_loss=_round_price(stop, digits),
        take_profit=[_round_price(value, digits) for value in take_profit],
        h1_atr=h1_atr,
        grid_step=grid_step,
        retest_count=retests,
        h1_trend=h1_trend,
        m15_trend=m15_trend,
        displacement_15m=displacement,
        recent_range_15m=recent_range,
        sweep_depth=sweep_depth,
        close_location=close_location,
        spread=spread,
        bar_time=int(last["t"]),
    )


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = max(0, int(value))
    out = "0"
    if value:
        chars = []
        while value:
            value, remainder = divmod(value, 36)
            chars.append(alphabet[remainder])
        out = "".join(reversed(chars))
    return out


class UltraStrategyEngine:
    """Persistent one-minute evaluator and exact pending-order expiry owner."""

    def __init__(
        self,
        mt5_files_dir: str,
        publisher: Publisher,
        state_path: Path,
        config: UltraStrategyConfig,
        *,
        clock=time.time,
    ):
        self.mt5_dir = resolve_mt5_dir(mt5_files_dir)
        self.publisher = publisher
        self.state_path = Path(state_path)
        self.config = config
        self.clock = clock
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        data = _read_json(self.state_path)
        return data if isinstance(data, dict) else {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def _publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.publisher.publish(payload)
        if not response.get("ok"):
            raise RuntimeError(f"hub rejected ultra strategy event: {response}")
        return response

    def _cancel_expired(self, now: float) -> int:
        active = self.state.get("active")
        if not isinstance(active, dict) or not active.get("execution_id"):
            return 0
        if now < float(active.get("expires_at") or 0):
            return 0
        execution_id = str(active["execution_id"])
        event_id = f"ultra:{STRATEGY_NAME}:expire:{execution_id}"
        payload = {
            "event_id": event_id,
            "type": "cancel_signal",
            "source": ULTRA_HIGH_FREQ,
            "source_name": STRATEGY_NAME,
            "target_execution_ids": [execution_id],
            "target_signals": [active.get("signal") or {}],
            "cancel_reason": "strategy_expired",
            "message_time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        self._publish(payload)
        self.state["active"] = None
        self._save_state()
        logger.info("超高頻掛單逾時，發布精確撤單：%s", execution_id)
        return 1

    @staticmethod
    def _closed(payload: Dict[str, Any], seconds: int, reference: int) -> List[Dict[str, Any]]:
        return [bar for bar in (payload.get("bars") or []) if int(bar["t"]) + seconds <= reference]

    def _market_snapshot(self) -> Optional[tuple]:
        if not self.mt5_dir.is_dir():
            return None
        # File mtime uses the operating system's real clock, so it remains a
        # reliable freshness check even when the broker encodes server wall time
        # into its JSON timestamp.
        symbol_info = _read_json(self.mt5_dir / "symbol_info.json") or {}
        symbol = str(symbol_info.get("symbol") or "XAUUSD")
        price_path = self.mt5_dir / f"{symbol}_price.json"
        if not price_path.is_file() and symbol != "XAUUSD":
            price_path = self.mt5_dir / "XAUUSD_price.json"
        if not price_path.is_file():
            return None
        if self.clock() - price_path.stat().st_mtime > self.config.max_market_age_seconds:
            return None

        tick = _live_price(self.mt5_dir, symbol)
        m1_payload = _bars_from(self.mt5_dir / "rates_M1.json")
        m15_payload = _bars_from(self.mt5_dir / "rates_M15.json")
        h1_payload = _bars_from(self.mt5_dir / "rates_H1.json")
        if not tick or not m1_payload or not m15_payload or not h1_payload:
            return None
        reference = int(tick.get("timestamp") or 0)
        if reference <= 0:
            return None
        m1 = self._closed(m1_payload, 60, reference)
        m15 = self._closed(m15_payload, 900, reference)
        h1 = self._closed(h1_payload, 3600, reference)
        if not m1 or reference - (int(m1[-1]["t"]) + 60) > self.config.max_market_age_seconds:
            return None
        digits = int(symbol_info.get("digits") or m1_payload.get("digits") or 2)
        return symbol, digits, tick, m1, m15, h1

    def _today_count(self, now: float) -> int:
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        daily = self.state.setdefault("daily_counts", {})
        # Keep a tiny bounded state file.
        for key in list(daily):
            if key != day:
                daily.pop(key, None)
        return int(daily.get(day) or 0)

    def run_cycle(self) -> int:
        now = float(self.clock())
        published = self._cancel_expired(now)
        if not self.config.enabled or self.state.get("active"):
            return published

        snapshot = self._market_snapshot()
        if snapshot is None:
            return published
        symbol, digits, tick, m1, m15, h1 = snapshot
        bar_time = int(m1[-1]["t"])
        if bar_time <= int(self.state.get("last_evaluated_bar") or 0):
            return published
        self.state["last_evaluated_bar"] = bar_time
        self._save_state()

        last_signal_at = float(self.state.get("last_signal_at") or 0)
        if now - last_signal_at < self.config.cooldown_seconds:
            return published
        if self._today_count(now) >= self.config.max_signals_per_day:
            return published

        decision = evaluate_market(m1, m15, h1, tick, self.config, digits=digits)
        if decision is None:
            return published

        feature = decision.feature_payload()
        identity = f"{STRATEGY_NAME}|{decision.bar_time}|{decision.direction}|{decision.entry_price:.5f}"
        event_hash = hashlib.sha256(identity.encode("ascii")).hexdigest()[:16]
        execution_id = f"copy_uhf_{_base36(decision.bar_time)}{decision.direction[0]}"
        expires_at = now + self.config.pending_expiry_seconds
        signal = {
            "symbol": symbol or "XAUUSD",
            "direction": decision.direction,
            "entry_price": decision.entry_price,
            "is_market_order": False,
            "pending_order_type": "limit",
            "stop_loss": decision.stop_loss,
            "take_profit": list(decision.take_profit),
            "lot_size": None,
            "parse_status": "accepted",
            "parse_method": f"market_data+{STRATEGY_NAME}",
            "raw_text_summary": f"{decision.setup} / grid={decision.grid_step:g} / retest={decision.retest_count}",
            "error": "",
        }
        payload = {
            "event_id": f"ultra:{event_hash}",
            "execution_id": execution_id,
            "type": "trade_signal",
            "source": ULTRA_HIGH_FREQ,
            "source_name": STRATEGY_NAME,
            "signal": signal,
            "strategy": feature,
            "message_time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "market_bar_time": decision.bar_time,
            "expires_at": expires_at,
        }
        self._publish(payload)

        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        daily = self.state.setdefault("daily_counts", {})
        daily[day] = int(daily.get(day) or 0) + 1
        self.state["last_signal_at"] = now
        self.state["active"] = {
            "execution_id": execution_id,
            "expires_at": expires_at,
            "event_id": payload["event_id"],
            "signal": signal,
        }
        self._save_state()
        logger.info(
            "超高頻訊號已發布：%s %s @ %s SL=%s TP=%s setup=%s ATR=%.2f retest=%s",
            execution_id,
            decision.direction,
            decision.entry_price,
            decision.stop_loss,
            decision.take_profit,
            decision.setup,
            decision.h1_atr,
            decision.retest_count,
        )
        return published + 1
