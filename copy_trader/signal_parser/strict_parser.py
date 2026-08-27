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


# yuyu 的三個止盈永遠等距(~5 點)。相鄰間距超過這個上限,就不是他正常的排列,
# 而是某一格打錯位數(掉一個 0)造成的離群值。給到 20 是他真實間距的 4 倍裕度,
# 連偶爾 ±1 的區間端點抖動都遠在界內,只有掉位數的粗錯才會超過。
_YUYU_MAX_TP_STEP = 20.0


def _repair_take_profits(
    take_profits: list[float], direction: str
) -> tuple[list[float], bool]:
    """把打錯位數的單一止盈,用另外兩個等距的止盈外推回來。

    yuyu 的止盈永遠是「3 個、同方向、固定 ~5 點間距」,偶爾其中一個掉一位數
    (2026-08-27 17:56/18:15:`Tp 4585 4590 460`,第三個 4595 被打成 460),
    害整組被拒發、掉單。他 22:21 自己重貼修正成 `Tp 4586 4590 4595`,證實那格
    本來就是 4595。

    修法刻意保守,只有下面全部成立才動手,否則原封不動回傳讓上層照常判斷:
      * 恰好 3 個止盈(yuyu 的固定格式)
      * 其中「兩個」彼此等距、間距在合理範圍內(給得出可信的步長)
      * 剩下「那一個」離用該步長外推出來的值極遠(>間距上限),也就是明顯打錯的離群值
      * 補回來之後三點同方向嚴格單調遞增/遞減
    純用他自己填對的兩個數字外推,不套任何硬編碼價位;一次只補一格,補兩格以上
    (無法只靠單點外推)一律放棄,交給上層拒絕。
    """
    if len(take_profits) != 3:
        return take_profits, False

    for bad in range(3):
        good_idx = [i for i in range(3) if i != bad]
        i0, i1 = good_idx
        v0, v1 = take_profits[i0], take_profits[i1]
        step = (v1 - v0) / (i1 - i0)                 # 每格間距(含方向正負)
        if step == 0 or abs(step) > _YUYU_MAX_TP_STEP:
            continue

        rebuilt = round(v0 + step * (bad - i0), 2)
        # 只有原值離外推值「非常遠」(掉位數等級)才算壞;近的(含 ±1 抖動)不動它
        if abs(take_profits[bad] - rebuilt) <= _YUYU_MAX_TP_STEP:
            continue

        candidate = list(take_profits)
        candidate[bad] = rebuilt
        a, b, c = candidate
        monotonic = a < b < c if direction == "buy" else a > b > c
        if not monotonic:
            continue
        return candidate, True

    return take_profits, False


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

    # yuyu 的止盈是固定 ~5 點等距的三個點,偶爾其中一格打錯位數(掉一個 0)。
    # 只有他這個 profile 先試著把離群的那格用另外兩格外推補回來,再照常做幾何檢查。
    # 放在幾何檢查「之前」是因為賣單掉位數(如 456)仍在進場價下方、會騙過幾何,
    # 必須靠間距檢查才抓得到。修不動時原封回傳,行為與過去一致。
    tp_repaired = False
    if profile == "yuyu_range_v1":
        take_profits, tp_repaired = _repair_take_profits(take_profits, direction)

    signal = _build_signal(direction, entry, stop_loss, take_profits)
    if not _geometry_is_valid(signal):
        return StrictParseResult(
            "rejected_invalid_geometry",
            profile,
            signal=signal,
            reason="sl_tp_geometry",
        )
    reason = "tp_repaired_from_spacing" if tp_repaired else ""
    return StrictParseResult("accepted", profile, signal=signal, reason=reason)
