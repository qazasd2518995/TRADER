"""
Groq Vision Signal Parser
Sends screenshot directly to Groq's vision model (llama-4-scout)
to extract trading signals — bypasses local OCR entirely.
"""
import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Rate limiting for Groq free tier
RATE_LIMIT_RPM = 30
RATE_LIMIT_WINDOW = 60

VISION_PROMPT = """你是一個黃金交易信號解析器。請看這張 LINE 聊天截圖，找出最新的交易信號。

最重要的規則：
- 截圖中可能有多個信號（舊的在上面，新的在下面）
- 你必須只解析「最下面」（最新）的那一個信號
- 忽略上面的所有舊信號
- 信號可能分散在多則連續訊息中，同一個發訊者連續發的訊息要合併為一個信號

常見格式範例：

格式A（單則完整）：
  乘XAUUSD 黃金
  Sell：5105
  止損：5114
  止盈：5090

格式B（價格+方向）：
  黃金5080-5081多
  Tp 5090 5100 5110
  Sl 5070

格式C（現價+方向）：
  現價空（5086）
  止損5094
  止盈5070

格式D（簡短）：
  5187多
  止損：5164
  止盈：5212

格式E（市價，無入場價）：
  黃金 多
  止損5178
  止盈5195 5200

格式F（跨多則訊息）：
  訊息1: 黃金做多 5065多
  訊息2: 止損：5020
  訊息3: 5090 5120 5140
  → 這三則合起來是一個完整信號

解析規則：
- 找最新（最下面）的信號，多則訊息要合併
- direction: "buy"（多/long/buy/做多/買）或 "sell"（空/short/sell/做空/賣）
- entry_price: 入場價格，null 表示市價單
- "現價空（5086）" → entry=5086, direction=sell
- "5187多" → entry=5187, direction=buy
- "黃金 多" 沒有數字 → entry=null, is_market_order=true
- stop_loss: 止損/SL 價格
- take_profit: 止盈/TP 價格陣列（可多個）
- 單獨出現的數字列（如 "5090 5120 5140"）通常是止盈
- 價格範圍 "5080-5081" 取第一個數字（5080）
- 忽略免責聲明
- confidence: 信號完整（有方向+SL+TP）給 0.95，缺值給 0.5
- 如果截圖沒有任何信號 → is_valid: false

只回傳 JSON：
{
  "is_valid": boolean,
  "direction": "buy" | "sell",
  "symbol": "XAUUSD",
  "entry_price": number | null,
  "is_market_order": boolean,
  "stop_loss": number | null,
  "take_profit": [number] | [],
  "confidence": number
}"""


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
    parse_method: str = "groq_vision"
    error: str = ""

    def __str__(self):
        tp_str = ", ".join([str(tp) for tp in self.take_profit]) if self.take_profit else "None"
        entry_str = f"@ {self.entry_price}" if self.entry_price else "@ Market"
        return f"{self.direction.upper()} {self.symbol} {entry_str} | SL: {self.stop_loss} | TP: [{tp_str}] | Conf: {self.confidence:.0%}"


class GroqVisionParser:
    """Signal parser using Groq Vision API (llama-4-scout)."""

    def __init__(self, api_key: str, model: str = "meta-llama/llama-4-scout-17b-16e-instruct"):
        self.api_key = api_key
        self.model = model
        self._request_times: list = []
        self._daily_limit_hit = False  # True when 429 TPD received
        self._daily_limit_reset: float = 0  # timestamp when to retry

        try:
            from groq import Groq
            self.client = Groq(api_key=api_key)
            logger.info(f"GroqVisionParser initialized with model: {model}")
        except ImportError:
            raise ImportError("Please install groq: pip install groq")

    @property
    def is_available(self) -> bool:
        """False if daily token limit was hit (cooldown 10 minutes)."""
        if self._daily_limit_hit:
            if time.time() > self._daily_limit_reset:
                self._daily_limit_hit = False
                logger.info("Vision API daily limit cooldown expired, retrying...")
                return True
            return False
        return True

    def _check_rate_limit(self) -> bool:
        """Check if we're within rate limits."""
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < RATE_LIMIT_WINDOW]
        if len(self._request_times) >= RATE_LIMIT_RPM:
            wait_time = RATE_LIMIT_WINDOW - (now - self._request_times[0])
            logger.warning(f"Rate limit reached, need to wait {wait_time:.1f}s")
            return False
        return True

    def _record_request(self):
        self._request_times.append(time.time())

    def _encode_image(self, image_path: str) -> Optional[str]:
        """Read image, resize to save tokens, return base64 JPEG string."""
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

            # Convert to JPEG (smaller than PNG)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to encode image: {e}")
            return None

    def parse_image(self, image_path: str) -> ParsedSignal:
        """
        Parse trading signal directly from screenshot using vision model.

        Args:
            image_path: Path to the screenshot file

        Returns:
            ParsedSignal object
        """
        if not self.is_available:
            return ParsedSignal(is_valid=False, error="Daily token limit — waiting for cooldown")

        if not self._check_rate_limit():
            return ParsedSignal(is_valid=False, error="Rate limit exceeded")

        b64 = self._encode_image(image_path)
        if not b64:
            return ParsedSignal(is_valid=False, error="Failed to read image")

        try:
            self._record_request()
            t_start = time.time()

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=500,
            )

            elapsed = (time.time() - t_start) * 1000
            content = response.choices[0].message.content.strip()
            logger.info(f"Groq Vision response ({elapsed:.0f}ms): {content[:200]}")

            return self._parse_response(content, image_path)

        except Exception as e:
            error_str = str(e)
            # Detect any 429 rate limit (daily tokens, RPM, etc.)
            if "429" in error_str or "rate_limit" in error_str.lower():
                self._daily_limit_hit = True
                self._daily_limit_reset = time.time() + 600  # retry in 10 min
                logger.error("Groq Vision 額度限制，10 分鐘後重試，暫時使用 regex 解析")
            else:
                logger.error(f"Groq Vision API error: {e}")
            return ParsedSignal(is_valid=False, error=error_str)

    def _parse_response(self, content: str, image_path: str) -> ParsedSignal:
        """Parse LLM JSON response into ParsedSignal."""
        try:
            # Extract JSON from possible markdown wrapping
            if "```" in content:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    content = content[start:end]

            data = json.loads(content)

            # Handle take_profit
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
                raw_text_summary=f"vision:{Path(image_path).name}",
                parse_method="groq_vision",
            )

            if signal.direction not in ["buy", "sell"]:
                signal.is_valid = False
                signal.error = f"Invalid direction: {signal.direction}"

            return signal

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Vision JSON: {e}\nContent: {content}")
            return ParsedSignal(is_valid=False, error=f"JSON parse error: {e}")
        except Exception as e:
            logger.error(f"Vision parse error: {e}")
            return ParsedSignal(is_valid=False, error=str(e))
