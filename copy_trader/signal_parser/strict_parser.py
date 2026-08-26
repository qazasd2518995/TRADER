"""Source-scoped parsers for exact LINE database text.

Unlike the legacy OCR parser, this module does not repair characters, infer a
direction from the last occurrence in a chat screenshot, or split one message
by distance heuristics. One LINE row produces at most one trade.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .regex_parser import ParsedSignal, order_take_profits


PRICE = r"(\d{3,5}(?:\.\d{1,2})?)"
_MID_ENTRY = re.compile(rf"(?<![A-Za-z])(buy|sell)\s*[：:]\s*{PRICE}", re.IGNORECASE)
_MID_NEARBY_ENTRY = re.compile(rf"(?:XAUUSD\s*)?(?:黃金)?\s*{PRICE}\s*附近\s*(多|空)", re.IGNORECASE)
_YUYU_ENTRY = re.compile(
    rf"(?:XAUUSD\s*)?黃金\s*{PRICE}\s*[-~～－—到至]\s*{PRICE}\s*(多|空)",
    re.IGNORECASE,
)
_YUYU_SINGLE_ENTRY = re.compile(
    rf"(?:XAUUSD\s*)?黃金\s*{PRICE}\s*(多|空)",
    re.IGNORECASE,
)
_DIRECTION_HINT = re.compile(r"(?<![A-Za-z])(buy|sell)(?![A-Za-z])|多|空", re.IGNORECASE)
_SL_LABEL = re.compile(r"(?<![A-Za-z])(?:SL|STOP\s*LOSS)(?![A-Za-z])|止損|止损", re.IGNORECASE)
_TP_LABEL = re.compile(
    r"(?<![A-Za-z])(?:TP(?:\s*[1-9](?!\d))?|TAKE\s*PROFIT)(?![A-Za-z])|止盈|止贏|止赢",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(PRICE)


@dataclass(frozen=True)
class StrictParseResult:
    status: str
    profile: str
    signal: ParsedSignal | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted" and self.signal is not None


def _first_price_after_label(text: str, label: re.Pattern[str]) -> float | None:
    match = label.search(text)
    if not match:
        return None
    value = _PRICE_RE.search(text, match.end())
    if not value:
        return None
    # Do not cross into a different labelled line looking for a number.
    between = text[match.end():value.start()]
    if "\n" in between or "\r" in between:
        return None
    return float(value.group(1))


def _tp_prices(text: str) -> list[float]:
    values: list[float] = []
    for line in text.splitlines():
        label = _TP_LABEL.search(line)
        if not label:
            continue
        for value in _PRICE_RE.finditer(line, label.end()):
            number = float(value.group(1))
            if number not in values:
                values.append(number)
    return values


def _build_signal(direction: str, entry: float, stop_loss: float, take_profits: list[float]) -> ParsedSignal:
    ordered = order_take_profits(direction, take_profits)
    return ParsedSignal(
        is_valid=True,
        symbol="XAUUSD",
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=ordered,
        confidence=0.0,
        raw_text_summary=(
            f"XAUUSD {direction.upper()} {entry:g} "
            f"SL {stop_loss:g} TP {'/'.join(f'{value:g}' for value in ordered)}"
        ),
    )


def _geometry_is_valid(signal: ParsedSignal) -> bool:
    entry = float(signal.entry_price)
    stop_loss = float(signal.stop_loss)
    take_profits = [float(value) for value in signal.take_profit]
    if signal.direction == "buy":
        return stop_loss < entry < min(take_profits)
    return max(take_profits) < entry < stop_loss


def parse_strict_signal(text: str, profile: str) -> StrictParseResult:
    body = (text or "").strip()
    profile = (profile or "strict_gold_v1").strip()
    if not body:
        return StrictParseResult("rejected_unknown_format", profile, reason="empty_text")

    entries: list[tuple[str, float]] = []
    if profile == "yuyu_range_v1":
        matches = list(_YUYU_ENTRY.finditer(body))
        entries = [
            ("buy" if match.group(3) == "多" else "sell", float(match.group(1)))
            for match in matches
        ]
        entries.extend(
            ("buy" if match.group(2) == "多" else "sell", float(match.group(1)))
            for match in _YUYU_SINGLE_ENTRY.finditer(body)
        )
    elif profile in {"mid_frequency_v1", "strict_gold_v1"}:
        english = list(_MID_ENTRY.finditer(body))
        nearby = list(_MID_NEARBY_ENTRY.finditer(body))
        entries.extend((match.group(1).casefold(), float(match.group(2))) for match in english)
        entries.extend(
            ("buy" if match.group(2) == "多" else "sell", float(match.group(1)))
            for match in nearby
        )
    else:
        return StrictParseResult("rejected_unknown_format", profile, reason="unknown_profile")

    if len(entries) > 1:
        return StrictParseResult("manual_review", profile, reason="multiple_entries")
    if not entries:
        status = "rejected_missing_entry" if _DIRECTION_HINT.search(body) else "rejected_unknown_format"
        return StrictParseResult(status, profile, reason="entry_not_found")

    stop_loss = _first_price_after_label(body, _SL_LABEL)
    take_profits = _tp_prices(body)
    if stop_loss is None or not take_profits:
        return StrictParseResult("rejected_unknown_format", profile, reason="missing_sl_or_tp")

    direction, entry = entries[0]
    signal = _build_signal(direction, entry, stop_loss, take_profits)
    if not _geometry_is_valid(signal):
        return StrictParseResult(
            "rejected_invalid_geometry",
            profile,
            signal=signal,
            reason="sl_tp_geometry",
        )
    return StrictParseResult("accepted", profile, signal=signal)
