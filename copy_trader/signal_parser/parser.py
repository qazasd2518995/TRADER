"""
Signal Parser using Claude API
Parses trading signals from OCR text.
"""
import json
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List
import time

from anthropic import Anthropic

from .prompts import SIGNAL_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class ParsedSignal:
    """Parsed trading signal structure."""
    is_valid: bool
    direction: Optional[str]  # 'buy' or 'sell'
    symbol: str
    entry_price: Optional[float]  # None = market order
    stop_loss: Optional[float]
    take_profit: Optional[List[float]]  # Multiple TP levels
    lot_size: Optional[float]
    confidence: float
    raw_text: str
    raw_text_summary: str
    timestamp: float = field(default_factory=time.time)

    def __str__(self):
        if not self.is_valid:
            return f"Invalid Signal: {self.raw_text_summary}"

        tps = ", ".join([str(tp) for tp in (self.take_profit or [])])
        return (
            f"{self.direction.upper()} {self.symbol} "
            f"@ {self.entry_price or 'MARKET'} "
            f"SL: {self.stop_loss} TP: [{tps}] "
            f"(conf: {self.confidence:.0%})"
        )


class SignalParser:
    """Parse trading signals using Claude API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        """
        Initialize signal parser.

        Args:
            api_key: Anthropic API key
            model: Claude model to use
        """
        self.client = Anthropic(api_key=api_key)
        self.model = model
        logger.info(f"SignalParser initialized with model: {model}")

    def parse(self, raw_text: str) -> ParsedSignal:
        """
        Parse raw OCR text into structured signal.

        Args:
            raw_text: Text extracted from screenshot

        Returns:
            ParsedSignal object
        """
        # Pre-process text
        normalized = self._normalize_text(raw_text)

        if len(normalized.strip()) < 5:
            return self._empty_signal(raw_text, "Text too short")

        try:
            # Call Claude API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": SIGNAL_EXTRACTION_PROMPT.format(input_text=normalized)
                }]
            )

            result_text = response.content[0].text
            logger.debug(f"LLM response: {result_text}")

            # Extract JSON from response
            data = self._extract_json(result_text)
            if data is None:
                return self._empty_signal(raw_text, "Failed to extract JSON")

            return self._build_signal(data, raw_text)

        except Exception as e:
            logger.error(f"Error parsing signal: {e}")
            return self._empty_signal(raw_text, f"Parse error: {e}")

    def _normalize_text(self, text: str) -> str:
        """Normalize text for better parsing."""
        # Common substitutions
        replacements = {
            '：': ':',
            '，': ',',
            '　': ' ',  # Full-width space
            '\u3000': ' ',  # Ideographic space
            '做多': 'Buy',
            '做空': 'Sell',
            '買入': 'Buy',
            '賣出': 'Sell',
            '多單': 'Buy',
            '空單': 'Sell',
        }

        result = text
        for old, new in replacements.items():
            result = result.replace(old, new)

        # Remove extra whitespace
        result = re.sub(r'\s+', ' ', result)

        return result.strip()

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON object from LLM response."""
        # Try to find JSON in the response
        # Handle cases where response might include markdown code blocks
        json_patterns = [
            r'```json\s*([\s\S]*?)\s*```',  # Markdown code block
            r'```\s*([\s\S]*?)\s*```',       # Generic code block
            r'(\{[\s\S]*\})',                 # Raw JSON object
        ]

        for pattern in json_patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue

        # Last resort: try parsing the whole text as JSON
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return None

    def _build_signal(self, data: dict, raw_text: str) -> ParsedSignal:
        """Build ParsedSignal from LLM response data."""
        # Validate and normalize direction
        direction = data.get('direction')
        if direction:
            direction = direction.lower()
            if direction not in ['buy', 'sell']:
                direction = None

        # Handle take_profit as list
        tp = data.get('take_profit')
        if tp is not None:
            if isinstance(tp, (int, float)):
                tp = [tp]
            elif not isinstance(tp, list):
                tp = None

        return ParsedSignal(
            is_valid=data.get('is_valid_signal', False),
            direction=direction,
            symbol=data.get('symbol', 'XAUUSD'),
            entry_price=data.get('entry_price'),
            stop_loss=data.get('stop_loss'),
            take_profit=tp,
            lot_size=data.get('lot_size'),
            confidence=data.get('confidence', 0.0),
            raw_text=raw_text,
            raw_text_summary=data.get('raw_text_summary', '')
        )

    def _empty_signal(self, raw_text: str, reason: str) -> ParsedSignal:
        """Create an empty/invalid signal."""
        return ParsedSignal(
            is_valid=False,
            direction=None,
            symbol="",
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            lot_size=None,
            confidence=0.0,
            raw_text=raw_text,
            raw_text_summary=reason
        )


def test_parser(api_key: str):
    """Test the signal parser with sample inputs."""
    parser = SignalParser(api_key)

    test_cases = [
        """乘XAUUSD 黃金
Sell ：4903
止損：4915
止盈 : 4885
（純粹個人投資分享）""",

        """黃金 做多
進場: 2850
止損: 2840
止盈1: 2865
止盈2: 2880""",

        "今天天氣很好，適合出門",

        """XAUUSD BUY
Entry: 2855.50
SL: 2845
TP1: 2870
TP2: 2890
TP3: 2920"""
    ]

    for i, text in enumerate(test_cases, 1):
        print(f"\n{'='*50}")
        print(f"Test Case {i}:")
        print(f"Input: {text[:50]}...")
        print("-" * 50)

        signal = parser.parse(text)
        print(f"Result: {signal}")
        print(f"Valid: {signal.is_valid}")
        print(f"Confidence: {signal.confidence:.0%}")


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.DEBUG)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Please set ANTHROPIC_API_KEY environment variable")
    else:
        test_parser(api_key)
