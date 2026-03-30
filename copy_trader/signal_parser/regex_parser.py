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
    # Fallback: OCR sometimes truncates entry price to 2-3 digits (e.g. "5080" → "50")
    ENTRY_PARTIAL_PATTERNS = [
        r'(?:buy|sell|買|賣)\s*[：:]\s*(\d{2,3})(?:\s|$|\D)',  # Buy：50 (truncated)
    ]

    SL_PATTERNS = [
        r'(?:止損|止损|止隕|止璗|止摃|止損|止撰|sl|si|stop\s*loss|損|隕|璗)\s*[：:=]?\s*(\d{4,5}(?:\.\d+)?)',
        r'(?:止損|止损|止隕|止璗|sl|si)\s*(\d{4,5}(?:\.\d+)?)',
    ]
    # Fallback SL: OCR truncates to 2-3 digits (e.g. "止損 496" instead of "止損 4963")
    SL_PARTIAL_PATTERNS = [
        r'(?:止損|止损|止隕|止璗|止摃|止撰|sl|si|stop\s*loss|損|隕|璗)\s*[：:=]?\s*(\d{2,3})(?:\s|$|\D)',
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
    # Fallback TP: OCR truncates to 2-3 digits (e.g. "止盈 49 昍" instead of "止盈 4983")
    TP_PARTIAL_PATTERNS = [
        r'(?:止盈|止赢|止贏|止嬴|止營|止瑩|獲利|覆利|获利|」\s*三|tp|take\s*profit|盈)\s*[：:=]?\s*(\d{2,3})(?:\s|$|\D)',
    ]

    MARKET_ORDER_PATTERNS = [
        r'市價|市价|market|現價|现价',
    ]

    # LINE chat timestamp patterns (OCR adds spaces between chars)
    # Matches: "下 午 1 30", "上 午 10 15", "下午2:30", etc.
    LINE_TIMESTAMP_PATTERN = r'[下上]\s*午\s*\d{1,2}\s*[:.：]?\s*\d{2}'
    # LINE date header patterns: "2月13日", "今天", "昨天", "星期一"
    LINE_DATE_PATTERN = r'\d{1,2}\s*月\s*\d{1,2}\s*[日E]'

    def __init__(self):
        # Compile patterns for performance
        self._buy_re = [re.compile(p, re.IGNORECASE) for p in self.BUY_PATTERNS]
        self._sell_re = [re.compile(p, re.IGNORECASE) for p in self.SELL_PATTERNS]
        self._entry_re = [re.compile(p, re.IGNORECASE) for p in self.ENTRY_PATTERNS]
        self._entry_partial_re = [re.compile(p, re.IGNORECASE) for p in self.ENTRY_PARTIAL_PATTERNS]
        self._sl_re = [re.compile(p, re.IGNORECASE) for p in self.SL_PATTERNS]
        self._sl_partial_re = [re.compile(p, re.IGNORECASE) for p in self.SL_PARTIAL_PATTERNS]
        self._tp_re = [re.compile(p, re.IGNORECASE) for p in self.TP_PATTERNS]
        self._tp_partial_re = [re.compile(p, re.IGNORECASE) for p in self.TP_PARTIAL_PATTERNS]
        self._market_re = [re.compile(p, re.IGNORECASE) for p in self.MARKET_ORDER_PATTERNS]

        logger.info("RegexSignalParser initialized")

    def _normalize_text(self, text: str) -> str:
        """Normalize OCR text into parser-friendly ASCII tokens."""
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
            "覆利": " TP ",  # OCR misread of 獲利
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
        """
        Parse only the LATEST signal from multi-message OCR text.

        Splits by LINE timestamps, tries parsing each block from newest
        (bottom of chat) to oldest. Returns the first valid signal found.

        Priority: blocks with explicit direction (buy/sell keyword) are preferred
        over blocks where direction was inferred from SL/TP positions, to avoid
        false positives from random numbers in chat.

        Args:
            text: Raw text from OCR (may contain multiple messages)

        Returns:
            ParsedSignal object from the newest message
        """
        blocks = self._split_by_timestamps(text)

        if len(blocks) <= 1:
            # Only one block, parse normally
            return self.parse(text)

        logger.debug(f"Split OCR text into {len(blocks)} blocks by timestamps")

        # Pass 1: Try newest to oldest, only accept blocks with EXPLICIT direction
        # (buy/sell keyword present). This avoids false positives from random numbers.
        best_inferred = None
        best_inferred_idx = -1

        for i, block in enumerate(reversed(blocks)):
            block_text = block.strip()
            if len(block_text) < 10:
                continue

            signal = self.parse(block_text)
            if signal.is_valid:
                block_idx = len(blocks) - 1 - i
                # Check if this block has an explicit direction keyword
                if self._has_explicit_direction(block_text):
                    logger.info(f"Using signal from block {block_idx + 1}/{len(blocks)} (newest with explicit direction)")
                    return signal
                elif best_inferred is None:
                    # Save the first (newest) inferred signal as fallback
                    best_inferred = signal
                    best_inferred_idx = block_idx

        # Pass 2: If no block had explicit direction, use the newest inferred signal
        if best_inferred:
            logger.info(f"Using inferred signal from block {best_inferred_idx + 1}/{len(blocks)} (no explicit direction found)")
            return best_inferred

        # Fallback: parse entire text (in case splitting broke a signal)
        logger.debug("No valid signal in individual blocks, parsing full text")
        return self.parse(text)

    def parse_all_latest(self, text: str) -> List[ParsedSignal]:
        """
        Parse ALL valid signals from the latest timestamp block.

        When a single OCR capture contains multiple signals (e.g. 乘 posts
        Sell 4460 and BUY 4425 at the same time), this returns all of them.

        Returns:
            List of ParsedSignal objects (may be empty or have 1+ items)
        """
        blocks = self._split_by_timestamps(text)

        if not blocks:
            return [self.parse(text)] if self.parse(text).is_valid else []

        # Find the newest non-trivial block(s) with signals
        results = []

        for block in reversed(blocks):
            block_text = block.strip()
            if len(block_text) < 10:
                continue

            # Try to split this block into multiple signals by direction keywords
            sub_signals = self._split_block_by_directions(block_text)

            if len(sub_signals) > 1:
                # Multiple signals found in one block
                for sub_text in sub_signals:
                    sig = self.parse(sub_text)
                    if sig.is_valid:
                        results.append(sig)
                if results:
                    logger.info(f"Found {len(results)} signals in same block")
                    return results

            # Single signal in this block
            sig = self.parse(block_text)
            if sig.is_valid:
                return [sig]

        # Fallback: parse entire text
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

    def _has_explicit_direction(self, text: str) -> bool:
        """Check if text contains an explicit buy/sell direction keyword."""
        # Quick check for common keywords first (avoid compiled pattern overhead)
        if re.search(r'\b(?:buy|sell|long|short)\b', text, re.IGNORECASE):
            return True
        if re.search(r'做多|做空|買入|买入|賣出|卖出|多單|多单|空單|空单', text):
            return True
        # Single-char direction adjacent to price: "5180多", "5180空"
        if re.search(r'\d{4,5}(?:\.\d+)?\s*(?:多|空)', text):
            return True
        for pattern in self._buy_re:
            if pattern.search(text):
                return True
        for pattern in self._sell_re:
            if pattern.search(text):
                return True
        return False

    def _split_by_timestamps(self, text: str) -> List[str]:
        """
        Split OCR text into message blocks using LINE timestamps, date headers,
        and Y-position gap markers (|MSG|) from BubbleDetector.

        LINE timestamps appear AFTER each message: "...message content... 下午 2:30"
        |MSG| markers are inserted by BubbleDetector when a Y-position gap indicates
        a different chat bubble (different sender/message).
        """
        # Combined pattern: timestamps OR date headers OR message boundary marker
        split_pattern = f'(?:{self.LINE_TIMESTAMP_PATTERN}|{self.LINE_DATE_PATTERN}|\\|MSG\\|)'
        parts = re.split(split_pattern, text)

        # Filter out empty/whitespace-only blocks.
        # Keep blocks >= 1 char so short cancel keywords ("撤", "SL") survive the split.
        # Signal parsing has its own length check in parse() so this is safe.
        blocks = [p for p in parts if p and len(p.strip()) >= 1]
        return blocks

    def parse(self, text: str) -> ParsedSignal:
        """
        Parse trading signal from text using regex.

        Args:
            text: Raw text from OCR

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

        # 1. Extract SL/TP first (full-digit pass), then retry with truncated OCR fallback
        stop_loss = self._extract_stop_loss(signal_text)
        take_profits = self._extract_take_profits(signal_text)

        # Second pass: use found prices as reference to expand truncated OCR numbers
        ref_prices = [p for p in [stop_loss] + (take_profits or []) if p]
        if not stop_loss and ref_prices:
            stop_loss = self._extract_stop_loss(signal_text, ref_prices=ref_prices)
            if stop_loss:
                ref_prices = [p for p in [stop_loss] + (take_profits or []) if p]
        if not take_profits and ref_prices:
            take_profits = self._extract_take_profits(signal_text, ref_prices=ref_prices)
            if take_profits:
                ref_prices = [p for p in [stop_loss] + (take_profits or []) if p]

        entry_price, is_market = self._extract_entry(signal_text, ref_prices=ref_prices)

        # Fallback: use parenthesized entry captured before normalization
        # e.g. Noir "輕倉空（4584）" → entry=4584
        if entry_price is None and _paren_entry is not None:
            entry_price = _paren_entry
            is_market = False
            logger.info(f"Using parenthesized entry: {_paren_entry}")

        # 2. Detect direction (can infer from SL/TP if not explicit)
        direction = self._detect_direction(signal_text)

        # If no explicit direction, try to infer from SL/TP
        if not direction and stop_loss and take_profits:
            direction = self._infer_direction_from_sltp(stop_loss, take_profits)
            if direction:
                logger.info(f"Inferred direction '{direction}' from SL/TP positions")

        if not direction:
            return ParsedSignal(
                is_valid=False,
                error="Could not detect direction",
                raw_text_summary=text[:50]
            )

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

        When OCR captures multiple signals in one block (no timestamp between them),
        the older signal's SL/TP can pollute the newer signal's parsing.
        By finding the LAST buy/sell keyword, we only search SL/TP after it.

        Example OCR: "乘XAUUSD黃金 SL:5030 ... 乘XAUUSD黃金 BUY:4999 SL:4990 TP:5017"
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

    def _infer_direction_from_sltp(self, sl: float, tps: List[float]) -> Optional[str]:
        """
        Infer direction from SL and TP positions.

        For BUY: SL < TP (stop below, profit above)
        For SELL: SL > TP (stop above, profit below)
        """
        if not tps:
            return None

        avg_tp = sum(tps) / len(tps)

        if sl < avg_tp:
            return "buy"
        elif sl > avg_tp:
            return "sell"

        return None

    def _extract_entry(self, text: str, ref_prices: list = None) -> Tuple[Optional[float], bool]:
        """
        Extract entry price.

        Args:
            ref_prices: reference prices (SL, TP) for expanding truncated OCR numbers

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

        # Fallback: try partial entry patterns (2-3 digits, OCR truncation)
        if ref_prices:
            for pattern in self._entry_partial_re:
                match = pattern.search(text)
                if match:
                    try:
                        partial = match.group(1)
                        expanded = self._expand_truncated_price(partial, ref_prices)
                        if expanded:
                            logger.info(f"Expanded truncated entry '{partial}' -> {expanded} (using SL/TP as reference)")
                            return expanded, False
                    except:
                        pass

        # No explicit entry = market order
        return None, True

    def _expand_truncated_price(self, partial: str, ref_prices: list) -> Optional[float]:
        """
        Try to expand a truncated price (e.g. '49' -> 4963.0) using reference prices.

        Strategy:
        1. If partial is a prefix of a reference price, use ref's trailing digits
        2. Otherwise, pad with zeros to match the reference's digit count
           (e.g. '498' with ref 4963 -> 4980)

        This handles OCR truncation like "止盈 49 昍" (should be "止盈 4983").
        """
        valid_refs = [p for p in ref_prices if p and p >= 1000]
        if not valid_refs:
            return None

        partial_len = len(partial)
        best_expanded = None
        best_distance = float('inf')

        for ref in valid_refs:
            ref_str = str(int(ref))
            ref_len = len(ref_str)

            # Partial must be shorter than reference
            if partial_len >= ref_len:
                continue

            if ref_str[:partial_len] == partial:
                # Prefix match: use ref's trailing digits for best estimate
                expanded = int(partial + ref_str[partial_len:])
            else:
                # No prefix match: pad with zeros (e.g. '498' -> '4980')
                expanded = int(partial + "0" * (ref_len - partial_len))

            distance = abs(expanded - ref)
            # Only accept if within reasonable range (< 5% of ref price)
            if distance < ref * 0.05 and distance < best_distance:
                best_distance = distance
                best_expanded = float(expanded)

        if best_expanded:
            logger.debug(f"Expanded '{partial}' -> {best_expanded} (ref: {valid_refs})")

        return best_expanded

    def _extract_stop_loss(self, text: str, ref_prices: list = None) -> Optional[float]:
        """Extract stop loss price, with fallback for truncated OCR."""
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

        # Fallback: try partial patterns (2-3 digits, OCR truncation)
        if ref_prices:
            for pattern in self._sl_partial_re:
                match = pattern.search(text)
                if match:
                    try:
                        partial = match.group(1)
                        expanded = self._expand_truncated_price(partial, ref_prices)
                        if expanded:
                            logger.info(f"Expanded truncated SL '{partial}' -> {expanded}")
                            return expanded
                    except:
                        pass

        return None

    def _extract_take_profits(self, text: str, ref_prices: list = None) -> List[float]:
        """Extract take profit prices (supports multiple), with fallback for truncated OCR."""
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

        # Fallback: try partial patterns (2-3 digits, OCR truncation like "止盈 49 昍")
        if not take_profits and ref_prices:
            for pattern in self._tp_partial_re:
                match = pattern.search(text)
                if match:
                    try:
                        partial = match.group(1)
                        expanded = self._expand_truncated_price(partial, ref_prices)
                        if expanded:
                            logger.info(f"Expanded truncated TP '{partial}' -> {expanded}")
                            take_profits.append(expanded)
                    except:
                        pass

        # Sort TPs
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
