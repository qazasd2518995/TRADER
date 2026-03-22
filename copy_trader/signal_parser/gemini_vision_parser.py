"""
Gemini Vision Signal Parser
Uses Google Gemini Flash as primary vision model for signal extraction.
Free tier: 250 RPD, no image token cost.
"""
import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Reuse the same prompt from groq_vision_parser
from .groq_vision_parser import VISION_PROMPT

RATE_LIMIT_RPM = 10  # Gemini free: 10 RPM
RATE_LIMIT_WINDOW = 60


@dataclass
class ParsedSignal:
    """Parsed trading signal from vision model."""
    is_valid: bool = False
    symbol: str = "XAUUSD"
    direction: str = ""
    entry_price: Optional[float] = None
    is_market_order: bool = False
    stop_loss: Optional[float] = None
    take_profit: list = field(default_factory=list)
    lot_size: Optional[float] = None
    confidence: float = 0.0
    raw_text: str = ""
    raw_text_summary: str = ""
    parse_method: str = "gemini_vision"
    error: str = ""

    def __str__(self):
        tp_str = ", ".join([str(tp) for tp in self.take_profit]) if self.take_profit else "None"
        entry_str = f"@ {self.entry_price}" if self.entry_price else "@ Market"
        return f"{self.direction.upper()} {self.symbol} {entry_str} | SL: {self.stop_loss} | TP: [{tp_str}] | Conf: {self.confidence:.0%}"


class GeminiVisionParser:
    """Signal parser using Google Gemini Vision API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self._request_times: list = []
        self._daily_limit_hit = False
        self._daily_limit_reset: float = 0

        try:
            from google import genai
            self.client = genai.Client(api_key=api_key)
            logger.info(f"GeminiVisionParser initialized with model: {model}")
        except ImportError:
            raise ImportError("Please install google-genai: pip install google-genai")

    @property
    def is_available(self) -> bool:
        """False if daily limit was hit (cooldown 10 minutes)."""
        if self._daily_limit_hit:
            if time.time() > self._daily_limit_reset:
                self._daily_limit_hit = False
                logger.info("Gemini daily limit cooldown expired, retrying...")
                return True
            return False
        return True

    def _check_rate_limit(self) -> bool:
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < RATE_LIMIT_WINDOW]
        if len(self._request_times) >= RATE_LIMIT_RPM:
            return False
        return True

    def _record_request(self):
        self._request_times.append(time.time())

    def _load_image_bytes(self, image_path: str) -> Optional[bytes]:
        """Read image, resize to save bandwidth, return JPEG bytes."""
        try:
            from PIL import Image
            import io

            path = Path(image_path)
            if not path.exists():
                logger.error(f"Image not found: {image_path}")
                return None

            img = Image.open(path)
            max_w = 800
            if img.width > max_w:
                ratio = max_w / img.width
                img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)

            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return None

    def parse_image(self, image_path: str) -> ParsedSignal:
        """Parse trading signal from screenshot using Gemini Vision."""
        if not self.is_available:
            return ParsedSignal(is_valid=False, error="Daily limit — waiting for cooldown")

        if not self._check_rate_limit():
            return ParsedSignal(is_valid=False, error="Rate limit exceeded")

        img_bytes = self._load_image_bytes(image_path)
        if not img_bytes:
            return ParsedSignal(is_valid=False, error="Failed to read image")

        try:
            from google.genai import types

            self._record_request()
            t_start = time.time()

            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    VISION_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )

            elapsed = (time.time() - t_start) * 1000
            content = response.text.strip() if response.text else ""
            logger.info(f"Gemini Vision response ({elapsed:.0f}ms): {content[:200]}")

            return self._parse_response(content, image_path)

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                self._daily_limit_hit = True
                self._daily_limit_reset = time.time() + 600
                logger.error("Gemini 每日額度用完，10 分鐘後重試，暫時降級到 Groq/regex")
            else:
                logger.error(f"Gemini Vision API error: {e}")
            return ParsedSignal(is_valid=False, error=error_str)

    def _parse_response(self, content: str, image_path: str) -> ParsedSignal:
        """Parse LLM JSON response into ParsedSignal."""
        try:
            if "```" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    content = content[start:end]

            data = json.loads(content)

            take_profits = data.get("take_profit", [])
            if isinstance(take_profits, (int, float)):
                take_profits = [take_profits]
            elif take_profits is None:
                take_profits = []
            take_profits = [float(tp) for tp in take_profits if tp is not None]

            entry_price = float(data["entry_price"]) if data.get("entry_price") else None
            is_market = data.get("is_market_order", False) or (entry_price is None and data.get("is_valid", False))

            signal = ParsedSignal(
                is_valid=data.get("is_valid", False),
                symbol=data.get("symbol", "XAUUSD"),
                direction=data.get("direction", "").lower(),
                entry_price=entry_price,
                is_market_order=is_market,
                stop_loss=float(data["stop_loss"]) if data.get("stop_loss") else None,
                take_profit=take_profits,
                confidence=float(data.get("confidence", 0)),
                raw_text_summary=f"gemini:{Path(image_path).name}",
                parse_method="gemini_vision",
            )

            if signal.direction not in ["buy", "sell"]:
                signal.is_valid = False
                signal.error = f"Invalid direction: {signal.direction}"

            return signal

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON: {e}\nContent: {content}")
            return ParsedSignal(is_valid=False, error=f"JSON parse error: {e}")
        except Exception as e:
            logger.error(f"Gemini parse error: {e}")
            return ParsedSignal(is_valid=False, error=str(e))
