"""Compatibility types and entry point for deterministic LINE text parsing.

The production collector selects a source-specific profile directly through
``strict_parser``. ``RegexSignalParser`` remains only for older imports and no
longer contains OCR typo repair, last-direction slicing, distance splitting or
invented confidence scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedSignal:
    """Normalized order fields shared by the Hub and MT5 client."""

    is_valid: bool = False
    symbol: str = "XAUUSD"
    direction: str = ""
    entry_price: Optional[float] = None
    is_market_order: bool = False
    # 空字串沿用 EA 依價格判斷的舊行為；"limit" 明確禁止被轉成 stop 單。
    # 第三來源只做回踩掛單，因此必須把訂單語意一路帶到 MT5。
    pending_order_type: str = ""
    stop_loss: Optional[float] = None
    take_profit: List[float] = field(default_factory=list)
    lot_size: Optional[float] = None
    confidence: float = 0.0
    raw_text: str = ""
    raw_text_summary: str = ""
    parse_method: str = "strict"
    error: str = ""

    def __str__(self) -> str:
        take_profits = ", ".join(str(value) for value in self.take_profit) or "None"
        entry = f"@ {self.entry_price}" if self.entry_price is not None else "@ Market"
        return (
            f"{self.direction.upper()} {self.symbol} {entry} | "
            f"SL: {self.stop_loss} | TP: [{take_profits}]"
        )


def order_take_profits(direction: str, take_profits: List[float]) -> List[float]:
    """Order targets from nearest to farthest for staged MT5 handling."""
    cleaned = sorted({float(value) for value in (take_profits or []) if value is not None})
    if str(direction or "").casefold() == "sell":
        cleaned.reverse()
    return cleaned


class RegexSignalParser:
    """Backward-compatible facade over one explicit strict parser profile."""

    def __init__(self, profile: str = "mid_frequency_v1"):
        self.profile = profile

    def parse(self, text: str) -> ParsedSignal:
        # Deferred import avoids a module cycle because strict_parser owns the
        # grammar while this module owns the shared ParsedSignal data class.
        from .strict_parser import parse_strict_signal

        result = parse_strict_signal(text, self.profile)
        if result.accepted and result.signal is not None:
            result.signal.parse_method = f"line_db+{result.profile}"
            result.signal.error = result.reason
            return result.signal
        return ParsedSignal(
            is_valid=False,
            parse_method=f"line_db+{result.profile}",
            error=result.status if not result.reason else f"{result.status}:{result.reason}",
        )

    def parse_latest(self, text: str) -> ParsedSignal:
        return self.parse(text)

    def parse_all_latest(self, text: str) -> List[ParsedSignal]:
        """One exact LINE row may produce at most one order."""
        signal = self.parse(text)
        return [signal] if signal.is_valid else []


_parser_instance: RegexSignalParser | None = None


def get_parser() -> RegexSignalParser:
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = RegexSignalParser()
    return _parser_instance


def quick_parse(text: str) -> ParsedSignal:
    return get_parser().parse(text)
