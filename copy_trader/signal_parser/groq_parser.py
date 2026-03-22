"""
Groq LLM Signal Parser
Uses Groq API (free tier) for parsing trading signals.
"""
import json
import logging
import time
from typing import Optional
from dataclasses import dataclass, field
from .prompts import SIGNAL_PARSER_PROMPT

logger = logging.getLogger(__name__)

# Rate limiting for Groq free tier
RATE_LIMIT_RPM = 30  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds


@dataclass
class ParsedSignal:
    """Parsed trading signal."""
    is_valid: bool = False
    symbol: str = "XAUUSD"
    direction: str = ""  # "buy" or "sell"
    entry_price: Optional[float] = None  # None = market order
    is_market_order: bool = False  # True if explicitly a market order
    stop_loss: Optional[float] = None
    take_profit: list = field(default_factory=list)  # Support multiple TPs
    lot_size: Optional[float] = None
    confidence: float = 0.0
    raw_text: str = ""
    raw_text_summary: str = ""
    parse_method: str = "groq"
    error: str = ""

    def __str__(self):
        tp_str = ", ".join([str(tp) for tp in self.take_profit]) if self.take_profit else "None"
        entry_str = f"@ {self.entry_price}" if self.entry_price else "@ Market"
        return f"{self.direction.upper()} {self.symbol} {entry_str} | SL: {self.stop_loss} | TP: [{tp_str}] | Conf: {self.confidence:.0%}"


class GroqSignalParser:
    """Signal parser using Groq API."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        """
        Initialize Groq parser.

        Args:
            api_key: Groq API key
            model: Model to use (default: llama-3.3-70b-versatile)
        """
        self.api_key = api_key
        self.model = model
        self._request_times: list = []

        # Initialize Groq client
        try:
            from groq import Groq
            self.client = Groq(api_key=api_key)
            logger.info(f"GroqSignalParser initialized with model: {model}")
        except ImportError:
            raise ImportError("Please install groq: pip install groq")

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        # Remove old timestamps
        self._request_times = [t for t in self._request_times if now - t < RATE_LIMIT_WINDOW]

        if len(self._request_times) >= RATE_LIMIT_RPM:
            wait_time = RATE_LIMIT_WINDOW - (now - self._request_times[0])
            logger.warning(f"Rate limit reached, need to wait {wait_time:.1f}s")
            return False
        return True

    def _record_request(self):
        """Record a request for rate limiting."""
        self._request_times.append(time.time())

    def parse(self, text: str) -> ParsedSignal:
        """
        Parse trading signal from text using Groq LLM.

        Args:
            text: Raw text from OCR

        Returns:
            ParsedSignal object
        """
        # Check rate limit
        if not self._check_rate_limit():
            return ParsedSignal(
                is_valid=False,
                error="Rate limit exceeded, try again later"
            )

        try:
            # Build prompt
            prompt = f"""{SIGNAL_PARSER_PROMPT}

Now parse this signal:
```
{text}
```

Respond with ONLY the JSON object, no other text."""

            # Call Groq API
            self._record_request()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,  # Low temperature for consistent parsing
                max_tokens=500,
            )

            # Extract response
            content = response.choices[0].message.content.strip()
            logger.debug(f"Groq response: {content}")

            # Parse JSON from response
            return self._parse_response(content, text)

        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return ParsedSignal(
                is_valid=False,
                error=str(e),
                raw_text_summary=text[:100]
            )

    def _parse_response(self, content: str, original_text: str) -> ParsedSignal:
        """Parse LLM response into ParsedSignal."""
        try:
            # Try to extract JSON from response
            # Sometimes LLM wraps it in ```json ... ```
            if "```" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    content = content[start:end]

            data = json.loads(content)

            # Handle take_profit - can be single value or list
            take_profits = data.get("take_profit", [])
            if isinstance(take_profits, (int, float)):
                take_profits = [take_profits]
            elif take_profits is None:
                take_profits = []

            # Filter out None values and convert to float
            take_profits = [float(tp) for tp in take_profits if tp is not None]

            entry_price = float(data["entry_price"]) if data.get("entry_price") else None
            is_market = entry_price is None and data.get("is_market_order", False)

            signal = ParsedSignal(
                is_valid=data.get("is_valid", False),
                symbol=data.get("symbol", "XAUUSD"),
                direction=data.get("direction", "").lower(),
                entry_price=entry_price,
                is_market_order=is_market,
                stop_loss=float(data["stop_loss"]) if data.get("stop_loss") else None,
                take_profit=take_profits,
                confidence=float(data.get("confidence", 0)),
                raw_text_summary=original_text[:100]
            )

            # Validate direction
            if signal.direction not in ["buy", "sell"]:
                signal.is_valid = False
                signal.error = f"Invalid direction: {signal.direction}"

            return signal

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}\nContent: {content}")
            return ParsedSignal(
                is_valid=False,
                error=f"JSON parse error: {e}",
                raw_text_summary=original_text[:100]
            )
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return ParsedSignal(
                is_valid=False,
                error=str(e),
                raw_text_summary=original_text[:100]
            )


if __name__ == "__main__":
    import os

    # Test
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("Set GROQ_API_KEY environment variable to test")
        exit(1)

    parser = GroqSignalParser(api_key)

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
    ]

    print("=== Groq Parser Test ===\n")
    for text in test_texts:
        print(f"Input:\n{text}\n")
        signal = parser.parse(text)
        print(f"Output: {signal}")
        print(f"  Valid: {signal.is_valid}")
        print(f"  TPs: {signal.take_profit}")
        print()
