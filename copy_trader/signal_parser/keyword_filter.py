"""
Keyword Filter for Trading Signals
Pre-filters OCR text to avoid unnecessary LLM API calls.
"""
import re
from typing import Tuple

# Signal keywords - must contain at least one to be considered a potential signal
SIGNAL_KEYWORDS = [
    # Direction
    "buy", "sell", "多", "空", "做多", "做空", "long", "short",
    # Price action
    "市價", "掛單", "進場", "入場", "開倉",
    # Stop loss / Take profit
    "止損", "止盈", "sl", "tp", "stop", "stoploss", "takeprofit",
    # Symbol indicators
    "xauusd", "黃金", "gold",
]

# Pattern for price-like numbers (4 digits for XAUUSD ~2800-9999 range)
# Removed \b word boundary to handle cases like "4810/4835"
PRICE_PATTERN = re.compile(r'(?<![.\d])\d{4}(?:\.\d{1,2})?(?![.\d])')

# Pattern for signal structure (direction + price)
SIGNAL_STRUCTURE_PATTERNS = [
    # "Sell：5110" or "Buy: 4852"
    re.compile(r'(?:buy|sell|多|空)\s*[：:]\s*\d{4,5}', re.IGNORECASE),
    # "黃金4884-4885多" or "黃金4884多"
    re.compile(r'黃金\s*\d{4,5}(?:\s*[-~]\s*\d{4,5})?\s*(?:多|空)'),
    # "5180多" or "5180空" (price + direction, no 黃金 prefix)
    re.compile(r'\d{4,5}(?:\.\d+)?(?:\s*[-~]\s*\d{4,5}(?:\.\d+)?)?\s*(?:多|空)'),
    # "止損：5120" or "SL 4879"
    re.compile(r'(?:止損|止损|止盈|止赢|sl|tp)\s*[：:=]?\s*\d{4,5}', re.IGNORECASE),
    # "Tp 4889 4894 4899" (multiple TPs)
    re.compile(r'(?:tp|止盈)\s*\d{4,5}(?:\s+\d{4,5})+', re.IGNORECASE),
    # "市價 止損4810/止盈4835"
    re.compile(r'市價.*(?:止損|止盈)', re.IGNORECASE),
]


def is_potential_signal(text: str) -> Tuple[bool, str]:
    """
    Check if text potentially contains a trading signal.

    Args:
        text: OCR extracted text

    Returns:
        Tuple of (is_potential_signal, reason)
    """
    if not text or len(text.strip()) < 10:
        return False, "Text too short"

    text_lower = text.lower()

    # Check for signal keywords
    found_keywords = []
    for keyword in SIGNAL_KEYWORDS:
        if keyword.lower() in text_lower:
            found_keywords.append(keyword)

    if not found_keywords:
        return False, "No signal keywords found"

    # Check for price-like numbers (XAUUSD typically 4xxx-5xxx range)
    prices = PRICE_PATTERN.findall(text)
    if len(prices) < 2:  # Need at least entry + SL or TP
        return False, f"Not enough price values (found {len(prices)})"

    # Check for signal structure patterns
    for pattern in SIGNAL_STRUCTURE_PATTERNS:
        if pattern.search(text):
            return True, f"Matched pattern, keywords: {found_keywords}, prices: {prices[:5]}"

    # If we have keywords and multiple prices, it's probably a signal
    if len(found_keywords) >= 2 and len(prices) >= 2:
        return True, f"Keywords: {found_keywords}, prices: {prices[:5]}"

    return False, f"Keywords found but no signal structure: {found_keywords}"


def extract_quick_info(text: str) -> dict:
    """
    Quick extraction without LLM for logging purposes.

    Returns:
        Dict with basic extracted info
    """
    info = {
        "has_buy": any(kw in text.lower() for kw in ["buy", "多", "做多", "long"]),
        "has_sell": any(kw in text.lower() for kw in ["sell", "空", "做空", "short"]),
        "prices": PRICE_PATTERN.findall(text)[:10],
        "has_sl": any(kw in text.lower() for kw in ["止損", "sl", "stoploss"]),
        "has_tp": any(kw in text.lower() for kw in ["止盈", "tp", "takeprofit"]),
    }
    return info


if __name__ == "__main__":
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

        """大家早安！今天天氣不錯""",

        """黃金現在4900，觀望中""",
    ]

    print("=== Keyword Filter Test ===\n")
    for text in test_texts:
        is_signal, reason = is_potential_signal(text)
        preview = text[:50].replace('\n', ' ')
        print(f"Text: {preview}...")
        print(f"  Is Signal: {is_signal}")
        print(f"  Reason: {reason}")
        if is_signal:
            info = extract_quick_info(text)
            print(f"  Quick Info: {info}")
        print()
