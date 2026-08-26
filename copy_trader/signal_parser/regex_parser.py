"""
Regex-based Signal Parser
Fast parsing without LLM for common signal formats.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ParsedSignal:
    """Parsed trading signal."""
    is_valid: bool = False
    symbol: str = "XAUUSD"
    direction: str = ""  # "buy" or "sell"
    entry_price: Optional[float] = None  # None = market order
    is_market_order: bool = False  # True if explicitly a market order
    stop_loss: Optional[float] = None
    take_profit: List[float] = field(default_factory=list)
    lot_size: Optional[float] = None
    confidence: float = 0.0
    raw_text: str = ""
    raw_text_summary: str = ""
    parse_method: str = "regex"  # "regex" or "llm"
    error: str = ""

    def __str__(self):
        tp_str = ", ".join([str(tp) for tp in self.take_profit]) if self.take_profit else "None"
        entry_str = f"@ {self.entry_price}" if self.entry_price else "@ Market"
        return f"{self.direction.upper()} {self.symbol} {entry_str} | SL: {self.stop_loss} | TP: [{tp_str}]"


def order_take_profits(direction: str, take_profits: List[float]) -> List[float]:
    """把多個止盈點排成「由近到遠」——買單遞增、賣單遞減。

    這個順序是多 TP 分批平倉的前提：程式把 MT5 的 TP 設在 tps[-1] 當尾段安全網，
    中間關卡由 _check_partial_tp_hits 逐一處理。所以 tps[-1] 必須是「最遠」的目標。

    抽取階段一律遞增排序（那時還不知道方向），對買單剛好正確，但賣單完全相反：
    遞增後 tps[-1] 反而是最近的止盈，MT5 就會在第一個目標把整倉平掉，分批完全失效。
    """
    cleaned = sorted({float(tp) for tp in (take_profits or []) if tp})
    if str(direction or "").lower() == "sell":
        cleaned.reverse()
    return cleaned


class RegexSignalParser:
    """Fast regex-based signal parser."""

    # Direction patterns
    # NOTE: Do NOT use \b around Chinese characters — Python \b doesn't fire
    # between \w classes (digits and CJK are both \w), so "5180多" won't match \b多\b.
    BUY_PATTERNS = [
        r'\b(?:buy)\s+(?:limit|stop)\b',  # MT5: Buy Limit, Buy Stop
        r'\b(?:buy|long)\b',
        r'(?:做多|買入|买入)',
        r'(?<![a-zA-Z])(?:多|買)(?![a-zA-Z])',  # standalone 多/買 (not inside English words)
        r'(\d{4,5}(?:\.\d+)?)\s*[-~]\s*(\d{4,5}(?:\.\d+)?)\s*多',  # 4884-4885多
        r'多\s*(?:單|单)',
    ]

    SELL_PATTERNS = [
        r'\b(?:sell)\s+(?:limit|stop)\b',  # MT5: Sell Limit, Sell Stop
        r'\b(?:sell|short)\b',
        r'(?:做空|賣出|卖出)',
        r'(?<![a-zA-Z])(?:空|賣)(?![a-zA-Z])',  # standalone 空/賣 (not inside English words)
        r'(\d{4,5}(?:\.\d+)?)\s*[-~]\s*(\d{4,5}(?:\.\d+)?)\s*空',  # 4884-4885空
        r'空\s*(?:單|单)',
    ]

    # Price patterns
    ENTRY_PATTERNS = [
        r'(?:buy|sell|買|賣)\s*[：:\s]\s*(\d{4,5}(?:\.\d+)?)',  # Buy：5110 or Buy 5110
        r'(?:進場|入場|entry|價格|价格|price)\s*[-：:=]?\s*(\d{4,5}(?:\.\d+)?)',  # MT5: 價格 4458.86
        r'(\d{4,5}(?:\.\d+)?)\s*[-~]\s*(\d{4,5}(?:\.\d+)?)\s*(?:多|空)',  # Range entry: 4884-4885多
        r'(\d{4,5}(?:\.\d+)?)\s*(?:多|空)',  # 5180多 or 5180空 (price before direction)
        r'(\d{4,5}(?:\.\d+)?)\s*附近',  # wayne: 4430附近 (nearby price as entry)
        r'[（(]\s*(\d{4,5}(?:\.\d+)?)\s*[）)]',  # Noir: 輕倉空（4584）(parenthesized entry)
    ]
    SL_PATTERNS = [
        r'(?:止損|止损|止隕|止璗|止摃|止損|止撰|sl|si|stop\s*loss|損|隕|璗)\s*[：:=]?\s*(\d{4,5}(?:\.\d+)?)',
        r'(?:止損|止损|止隕|止璗|sl|si)\s*(\d{4,5}(?:\.\d+)?)',
    ]
    TP_PATTERNS = [
        # Multiple TPs: "Tp 4889 4894 4899" or "止盈 4889 4894 4899"
        r'(?:止盈|止赢|止贏|止嬴|止營|止瑩|獲利|覆利|获利|」\s*三|tp|take\s*profit|盈)\s*[：:=]?\s*((?:\d{4,5}(?:\.\d+)?\s*)+)',
        # TP with number: TP1 4920, TP2 4950
        r'(?:止盈|止赢|止贏|止營|止瑩|獲利|覆利|获利|」\s*三|tp)\s*\d\s*[：:=]?\s*(\d{4,5}(?:\.\d+)?)',
        # Single TP with label
        r'(?:止盈|止赢|止贏|止營|止瑩|獲利|覆利|获利|」\s*三|tp)\s*[：:=]?\s*(\d{4,5}(?:\.\d+)?)',
        # Fallback: number right after SL pattern on sell signals (lower price = TP for sell)
    ]
    MARKET_ORDER_PATTERNS = [
        r'市價|市价|market|現價|现价',
    ]

    def __init__(self):
        # Compile patterns for performance
        self._buy_re = [re.compile(p, re.IGNORECASE) for p in self.BUY_PATTERNS]
        self._sell_re = [re.compile(p, re.IGNORECASE) for p in self.SELL_PATTERNS]
        self._entry_re = [re.compile(p, re.IGNORECASE) for p in self.ENTRY_PATTERNS]
        self._sl_re = [re.compile(p, re.IGNORECASE) for p in self.SL_PATTERNS]
        self._tp_re = [re.compile(p, re.IGNORECASE) for p in self.TP_PATTERNS]
        self._market_re = [re.compile(p, re.IGNORECASE) for p in self.MARKET_ORDER_PATTERNS]

        logger.info("RegexSignalParser initialized")

    def _normalize_text(self, text: str) -> str:
        """Normalize exact LINE message text into parser-friendly tokens."""
        replacements = {
            "\r": " ",
            "\n": " ",
            "\u3000": " ",
            "：": ":",
            "，": ",",
            "（": " ",
            "）": " ",
            "止損": " SL ",
            "止损": " SL ",
            "止盈": " TP ",
            "止贏": " TP ",
            "止赢": " TP ",
            "獲利": " TP ",
            "获利": " TP ",
            "買入": " BUY ",
            "买入": " BUY ",
            "做多": " BUY ",
            "做空": " SELL ",
            "賣出": " SELL ",
            "卖出": " SELL ",
            "\U0001f233": " SELL ",  # 🈳 emoji (squared 空), used by wayne
            "市價": " MARKET ",
            "市价": " MARKET ",
        }

        normalized = text
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def parse_latest(self, text: str) -> ParsedSignal:
        """Parse one exact LINE database message."""
        return self.parse(text)

    def parse_all_latest(self, text: str) -> List[ParsedSignal]:
        """
        Parse all valid signals when one LINE message contains multiple orders.

        Returns:
            List of ParsedSignal objects (may be empty or have 1+ items)
        """
        sub_signals = self._split_block_by_directions(text.strip())
        if len(sub_signals) > 1:
            results = [self.parse(part) for part in sub_signals]
            results = [signal for signal in results if signal.is_valid]
            if results:
                return results
        sig = self.parse(text)
        return [sig] if sig.is_valid else []

    def _split_block_by_directions(self, text: str) -> List[str]:
        """
        Split a text block into sub-blocks at each direction keyword boundary.

        For example, when 乘 posts two signals at once:
        "乘XAUUSD黃金 Sell：4460 止損：4475 止盈：4440 ... 乘XAUUSD黃金 BUY：4425 止損：4416 止盈：4442"
        → ["Sell：4460 止損：4475 止盈：4440 ...", "BUY：4425 止損：4416 止盈：4442"]
        """
        # Find all direction keyword positions
        direction_patterns = [
            r'\bbuy\s+(?:limit|stop)\b', r'\bsell\s+(?:limit|stop)\b',
            r'\bbuy\b', r'\bsell\b', r'\blong\b', r'\bshort\b',
            r'(?<![a-zA-Z])多(?![a-zA-Z])', r'(?<![a-zA-Z])空(?![a-zA-Z])',
            r'(?<![a-zA-Z])買(?![a-zA-Z])', r'(?<![a-zA-Z])賣(?![a-zA-Z])',
        ]

        positions = []
        for pat in direction_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                positions.append(m.start())

        positions = sorted(set(positions))

        if len(positions) <= 1:
            return [text]

        # Check if these are truly separate signals (each has its own SL/TP area)
        # Only split if positions are far enough apart (>30 chars)
        split_points = [positions[0]]
        for pos in positions[1:]:
            if pos - split_points[-1] >= 30:
                split_points.append(pos)

        if len(split_points) <= 1:
            return [text]

        # Split text at these positions
        sub_blocks = []
        for i, start in enumerate(split_points):
            end = split_points[i + 1] if i + 1 < len(split_points) else len(text)
            sub_text = text[start:end].strip()
            if len(sub_text) >= 10:
                sub_blocks.append(sub_text)

        return sub_blocks if len(sub_blocks) > 1 else [text]

    def parse(self, text: str) -> ParsedSignal:
        """
        Parse trading signal from text using regex.

        Args:
            text: Exact LINE message text

        Returns:
            ParsedSignal object
        """
        if not text or len(text.strip()) < 5:
            return ParsedSignal(is_valid=False, error="Text too short")

        # Pre-normalization: extract parenthesized entry before （） get replaced by spaces
        # e.g. Noir: "輕倉空（4584）" → capture 4584 as candidate entry
        _paren_entry = None
        _paren_match = re.search(r'[（(]\s*(\d{4,5}(?:\.\d+)?)\s*[）)]', text)
        if _paren_match:
            try:
                _paren_entry = float(_paren_match.group(1))
            except Exception:
                pass

        # Normalize text
        text_clean = self._normalize_text(text)

        # Find the LAST direction keyword position — only search for SL/TP after it
        # This prevents picking up SL/TP from older signals above the latest one
        signal_text = self._text_from_last_direction(text_clean)

        # Extract only complete prices from the exact database text.
        stop_loss = self._extract_stop_loss(signal_text)
        take_profits = self._extract_take_profits(signal_text)
        entry_price, is_market = self._extract_entry(signal_text)

        # Fallback: use parenthesized entry captured before normalization
        # e.g. Noir "輕倉空（4584）" → entry=4584
        if entry_price is None and _paren_entry is not None:
            entry_price = _paren_entry
            is_market = False
            logger.info(f"Using parenthesized entry: {_paren_entry}")

        # Require an explicit direction; price geometry alone is not authority.
        direction = self._detect_direction(signal_text)

        if not direction:
            return ParsedSignal(
                is_valid=False,
                error="Could not detect direction",
                raw_text_summary=text[:50]
            )

        # 依方向排序止盈，讓 tps[0] 永遠是「最近的目標」、tps[-1] 永遠是「最遠的目標」。
        # buy 價格往上走 → 升冪；sell 價格往下走 → 降冪。
        #
        # 下游 trade_manager 的分批平倉直接依賴這個假設：
        #   manager.py:552  MT5 的 take_profit 設 tps[-1] 當安全網   → 必須是最遠的
        #   manager.py:1007 第 N 次分批平倉等 tps[N]                → 必須由近排到遠
        # 原本 _extract_take_profits 收尾是無條件 `take_profits.sort()` (永遠升冪)，
        # 對 buy 剛好正確，對 sell 完全相反。實測 yuyu 的 "黃金 4070-4071空 /
        # Tp 4065 4060 4055 / Sl 4076"：升冪排成 [4055,4060,4065] 後，MT5 的 TP 被設
        # 成最近的 4065 → 整單在第一個目標就全平，4060/4055 永遠用不到；而分批平倉
        # 在等 tps[0]=4055 這個最遠的價，倉位早就沒了 → 賣單等於完全沒有分批平倉。
        # 排序放在這裡而不是 _extract_take_profits 裡面，是因為 direction 到這行才定案
        # (只有到這裡才知道方向)。
        take_profits = order_take_profits(direction, take_profits)

        # 5. Validate
        if not stop_loss and not take_profits:
            return ParsedSignal(
                is_valid=False,
                direction=direction,
                error="No SL or TP found",
                raw_text_summary=text[:50]
            )

        # 6. Build result
        signal = ParsedSignal(
            is_valid=True,
            symbol="XAUUSD",
            direction=direction,
            entry_price=entry_price if not is_market else None,
            is_market_order=is_market,
            stop_loss=stop_loss,
            take_profit=take_profits,
            confidence=0.95,
            raw_text_summary=self._build_summary(direction, entry_price, stop_loss, take_profits),
            parse_method="regex"
        )

        # Validate SL/TP logic
        if not self._validate_sl_tp(signal):
            signal.confidence = 0.7
            signal.error = "SL/TP direction mismatch (may need review)"

        logger.info(f"Parsed signal: {signal}")
        return signal

    def _text_from_last_direction(self, text: str) -> str:
        """Find the last direction keyword and return text from that point onward,
        but also include preceding entry price if adjacent.

        When one LINE message contains multiple signals,
        the older signal's SL/TP can pollute the newer signal's parsing.
        By finding the LAST buy/sell keyword, we only search SL/TP after it.

        Example: "乘XAUUSD黃金 SL:5030 ... 乘XAUUSD黃金 BUY:4999 SL:4990 TP:5017"
        → returns: "BUY:4999 SL:4990 TP:5017"

        For "黃金 4695-4696 空 Tp ...", the entry price directly precedes the
        direction keyword, so we expand leftward to include it.
        """
        # Find all direction keyword positions
        direction_patterns = [
            r'\bbuy\b', r'\bsell\b', r'\blong\b', r'\bshort\b',
            r'(?<![a-zA-Z])多(?![a-zA-Z])', r'(?<![a-zA-Z])空(?![a-zA-Z])',
            r'(?<![a-zA-Z])買(?![a-zA-Z])', r'(?<![a-zA-Z])賣(?![a-zA-Z])',
        ]
        last_pos = -1
        for pat in direction_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                if m.start() > last_pos:
                    last_pos = m.start()

        if last_pos > 0:
            # Look backward from the direction keyword for an adjacent entry price.
            # e.g. "4695-4696 空" or "4695 空" — include the price(s) in the result.
            # Also handle "4430附近可進場多" and "輕倉空（4584）" where the price is
            # separated from the direction keyword by Chinese words or punctuation.
            prefix = text[:last_pos]

            # Try range first: "4695-4696 空"
            price_prefix_match = re.search(
                r'(\d{4,5}(?:\.\d+)?\s*[-~]\s*\d{4,5}(?:\.\d+)?\s*)$',
                prefix,
            )
            # Then direct: "4695 空"
            if not price_prefix_match:
                price_prefix_match = re.search(
                    r'(\d{4,5}(?:\.\d+)?\s*)$',
                    prefix,
                )
            # Then with Chinese filler: "4430附近可進場多", "4430 附近 多"
            if not price_prefix_match:
                price_prefix_match = re.search(
                    r'(\d{4,5}(?:\.\d+)?(?:\s*附近)?[\s\S]{0,10})$',
                    prefix,
                )
            if price_prefix_match:
                # Expand start to include the entry price
                return text[price_prefix_match.start():]

            # Also check AFTER the direction keyword for parenthesized entry:
            # "輕倉空（4584）sl4600" → direction at "空", entry "(4584)" is after
            suffix = text[last_pos:]
            return suffix
        return text

    def _detect_direction(self, text: str) -> Optional[str]:
        """Detect buy or sell direction.

        NOTE: This runs on NORMALIZED text where _normalize_text() already
        replaced 做多/買入 → BUY, 做空/賣出 → SELL, 止損 → SL, 止盈 → TP.
        So Chinese multi-char keywords are already English here.
        Only standalone 多/空/買/賣 survive normalization.
        """
        # Priority 1: English keywords (includes normalized Chinese → BUY/SELL)
        if re.search(r'\bsell\b|\bshort\b', text, re.IGNORECASE):
            return "sell"
        if re.search(r'\bbuy\b|\blong\b', text, re.IGNORECASE):
            return "buy"

        # Priority 2: Compiled patterns for surviving Chinese chars
        # (standalone 多/空/買/賣, "XXXX多/空", "多單/空單", etc.)
        for pattern in self._sell_re:
            if pattern.search(text):
                return "sell"

        for pattern in self._buy_re:
            if pattern.search(text):
                return "buy"

        return None

    def _extract_entry(self, text: str) -> Tuple[Optional[float], bool]:
        """
        Extract entry price.

        Returns:
            (entry_price, is_market_order)
        """
        # Check for market order
        for pattern in self._market_re:
            if pattern.search(text):
                return None, True

        # Match "BUY 5180" / "SELL 5180" but NOT "BUY SL 5165" (avoid grabbing SL/TP as entry)
        simple_match = re.search(
            r'(?:\bbuy\b|\bsell\b|\blong\b|\bshort\b|買入|买入|賣出|卖出|做多|做空)\s*(?!(?:sl|tp|SL|TP)\b)[^\d]{0,3}(\d{4,5}(?:\.\d+)?)',
            text,
            re.IGNORECASE,
        )
        if simple_match:
            try:
                return float(simple_match.group(1)), False
            except Exception:
                pass

        # "4695-4696多" / "4695-4696空" — range entry before direction keyword
        # Check range BEFORE single price to avoid matching only the second number
        range_before_dir = re.search(
            r'(\d{4,5}(?:\.\d+)?)\s*[-~]\s*(\d{4,5}(?:\.\d+)?)\s*(?:多|空)',
            text,
        )
        if range_before_dir:
            try:
                p1, p2 = float(range_before_dir.group(1)), float(range_before_dir.group(2))
                # Use the first price (signal author's primary reference)
                return p1, False
            except Exception:
                pass

        # "5180多" / "5180空" — single price before Chinese direction keyword
        price_before_dir = re.search(
            r'(\d{4,5}(?:\.\d+)?)\s*(?:多|空)',
            text,
        )
        if price_before_dir:
            try:
                return float(price_before_dir.group(1)), False
            except Exception:
                pass

        # Try full entry patterns (4-5 digits)
        for pattern in self._entry_re:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    # Range: use first price
                    try:
                        p1, p2 = float(groups[0]), float(groups[1])
                        return p1, False
                    except:
                        pass
                elif len(groups) == 1:
                    try:
                        return float(groups[0]), False
                    except:
                        pass

        # Missing entry is incomplete unless the message explicitly says market.
        return None, False

    def _extract_stop_loss(self, text: str) -> Optional[float]:
        """Extract a complete stop-loss price."""
        simple_match = re.search(
            r'(?:\bsl\b|stop\s*loss|\u6b62\u640d|\u6b62\u635f)\s*[^\d]{0,6}(\d{4,5}(?:\.\d+)?)',
            text,
            re.IGNORECASE,
        )
        if simple_match:
            try:
                return float(simple_match.group(1))
            except Exception:
                pass

        # Try full patterns first (4-5 digits)
        for pattern in self._sl_re:
            match = pattern.search(text)
            if match:
                try:
                    return float(match.group(1))
                except:
                    pass

        return None

    def _extract_take_profits(self, text: str) -> List[float]:
        """Extract one or more complete take-profit prices."""
        take_profits = []

        simple_matches = re.findall(
            r'(?:\btp\b\d*|take\s*profit|\u6b62\u76c8|\u6b62\u8d0f|\u6b62\u8d62|\u7372\u5229|\u8986\u5229|\u83b7\u5229)\s*[^\d]{0,6}(\d{4,5}(?:\.\d+)?)',
            text,
            re.IGNORECASE,
        )
        for tp_str in simple_matches:
            try:
                tp = float(tp_str)
                if tp not in take_profits:
                    take_profits.append(tp)
            except Exception:
                pass

        # First, try to find all TP patterns (full 4-5 digit prices)
        for pattern in self._tp_re:
            for match in pattern.finditer(text):
                tp_str = match.group(1)
                # Extract all numbers from the matched group
                numbers = re.findall(r'\d{4,5}(?:\.\d+)?', tp_str)
                for num in numbers:
                    try:
                        tp = float(num)
                        if tp not in take_profits:
                            take_profits.append(tp)
                    except:
                        pass

        # Also look for "TP1 XXXX TP2 XXXX" pattern
        tp_numbered = re.findall(r'(?:tp|止盈|止營|止瑩|獲利|覆利|获利)\s*\d?\s*[：:=]?\s*(\d{4,5}(?:\.\d+)?)', text, re.IGNORECASE)
        for tp_str in tp_numbered:
            try:
                tp = float(tp_str)
                if tp not in take_profits:
                    take_profits.append(tp)
            except:
                pass

        # 先做穩定的升冪排序（去重／比對用）。最終「由近到遠」的方向性排序在 parse()
        # 裡 direction 定案之後才套用 — 這裡還不知道方向（見 order_take_profits）。
        take_profits.sort()

        return take_profits

    def _validate_sl_tp(self, signal: ParsedSignal) -> bool:
        """Validate SL and TP make sense for the direction."""
        if not signal.stop_loss or not signal.take_profit:
            return True

        avg_tp = sum(signal.take_profit) / len(signal.take_profit)

        if signal.direction == "buy":
            # For BUY: SL < TP (stop below, profit above)
            return signal.stop_loss < avg_tp
        else:  # sell
            # For SELL: SL > TP (stop above, profit below)
            return signal.stop_loss > avg_tp

    def _build_summary(self, direction: str, entry: Optional[float],
                       sl: Optional[float], tps: List[float]) -> str:
        """Build human-readable summary."""
        entry_str = f"@{entry}" if entry else "@Market"
        sl_str = f"SL:{sl}" if sl else ""
        tp_str = f"TP:{','.join(str(int(t)) for t in tps)}" if tps else ""
        return f"XAUUSD {direction.upper()} {entry_str} {sl_str} {tp_str}".strip()


# Singleton instance for quick access
_parser_instance = None

def get_parser() -> RegexSignalParser:
    """Get singleton parser instance."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = RegexSignalParser()
    return _parser_instance


def quick_parse(text: str) -> ParsedSignal:
    """Quick parse helper function."""
    return get_parser().parse(text)


if __name__ == "__main__":
    import time

    # Test cases
    test_texts = [
        """乘XAUUSD 黃金
Sell ：5110
止損：5120
止盈 : 5095
（純粹個人投資分享）""",

        """黃金4884-4885多
Tp 4889 4894 4899
Sl 4879
個人建議不構成投資計畫✨""",

        """市價 止損4810/止盈4835""",

        """空單先撤掉 接不到了
市價 止損4810/止盈4835""",

        """XAUUSD Buy 4900
SL 4880
TP1 4920
TP2 4950""",

        """大家早安！今天天氣不錯""",
    ]

    parser = RegexSignalParser()

    print("=== Regex Parser Test ===\n")

    total_time = 0
    for i, text in enumerate(test_texts):
        print(f"--- Test {i+1} ---")
        print(f"Input: {text[:50].replace(chr(10), ' ')}...")

        start = time.time()
        signal = parser.parse(text)
        elapsed = (time.time() - start) * 1000
        total_time += elapsed

        print(f"Output: {signal}")
        print(f"  Valid: {signal.is_valid}")
        print(f"  Time: {elapsed:.2f}ms")
        if signal.error:
            print(f"  Error: {signal.error}")
        print()

    print(f"=== Total parse time: {total_time:.2f}ms for {len(test_texts)} texts ===")
    print(f"=== Average: {total_time/len(test_texts):.2f}ms per signal ===")
