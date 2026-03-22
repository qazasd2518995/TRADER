"""
LLM Prompts for Trading Signal Extraction
"""

SIGNAL_EXTRACTION_PROMPT = """You are a trading signal parser. Extract structured trading information from the given text.

Input text may contain trading signals in various formats. Common patterns include:
- Direction: Buy/Sell/Long/Short or Chinese equivalents (做多/做空/買入/賣出/乘)
- Symbol: XAUUSD, 黃金, Gold, etc.
- Entry price: Numbers near keywords like "入場", "Entry", "價格", or after direction
- Stop Loss (SL): Numbers near "止損", "SL", "Stop", "損"
- Take Profit (TP): Numbers near "止盈", "TP", "Target", "盈"
- May have multiple TP levels (TP1, TP2, TP3 or 止盈1, 止盈2)

Important rules:
1. If no explicit entry price, return entry_price as null (means market order)
2. XAUUSD prices should be around 2000-3000 range (if you see 4xxx or 5xxx, it's likely already correct)
3. If direction is ambiguous, return null
4. Handle both Chinese and English text
5. Multiple take-profit levels should be returned as an array
6. "乘" before a symbol usually means the signal is starting (not a direction)
7. Ignore disclaimers like "純粹個人投資分享"

Return ONLY a valid JSON object (no markdown, no explanation) with this structure:
{
    "is_valid_signal": boolean,
    "direction": "buy" | "sell" | null,
    "symbol": string | null,
    "entry_price": number | null,
    "stop_loss": number | null,
    "take_profit": [number] | null,
    "lot_size": number | null,
    "confidence": number (0-1),
    "raw_text_summary": string
}

Examples:

Input: "乘XAUUSD 黃金
Sell ：4903
止損：4915
止盈 : 4885
（純粹個人投資分享）"

Output: {"is_valid_signal": true, "direction": "sell", "symbol": "XAUUSD", "entry_price": 4903, "stop_loss": 4915, "take_profit": [4885], "lot_size": null, "confidence": 0.95, "raw_text_summary": "XAUUSD Sell@4903 SL:4915 TP:4885"}

Input: "黃金 做多
進場: 2850
止損: 2840
止盈1: 2865
止盈2: 2880"

Output: {"is_valid_signal": true, "direction": "buy", "symbol": "XAUUSD", "entry_price": 2850, "stop_loss": 2840, "take_profit": [2865, 2880], "lot_size": null, "confidence": 0.95, "raw_text_summary": "XAUUSD Buy@2850 SL:2840 TP1:2865 TP2:2880"}

Input: "今天天氣很好"

Output: {"is_valid_signal": false, "direction": null, "symbol": null, "entry_price": null, "stop_loss": null, "take_profit": null, "lot_size": null, "confidence": 0.0, "raw_text_summary": "Not a trading signal"}

Now parse this text:
{input_text}
"""

# Simplified prompt for Groq (faster, more direct)
SIGNAL_PARSER_PROMPT = """Parse this trading signal into JSON format.

Rules:
- direction: "buy" (多/long/buy) or "sell" (空/short/sell)
- entry_price: null for market orders, number for pending orders
- stop_loss: the stop loss price
- take_profit: array of take profit prices (can be multiple)
- symbol: usually "XAUUSD" for gold
- is_valid: true if this is a valid trading signal
- confidence: 0.0 to 1.0

For "市價" (market price), entry_price should be null.
Multiple TPs like "Tp 4889 4894 4899" should become [4889, 4894, 4899].
Price range like "4884-4885" means entry around that level.

Return ONLY valid JSON:
{
  "is_valid": boolean,
  "direction": "buy" | "sell",
  "symbol": "XAUUSD",
  "entry_price": number | null,
  "stop_loss": number,
  "take_profit": [number],
  "confidence": number
}"""

SIGNAL_VALIDATION_PROMPT = """Validate if this parsed trading signal makes sense for execution.

Parsed Signal: {signal_json}
Current Market Price: {current_price}
Symbol: {symbol}

Check these conditions:
1. For SELL: stop_loss should be ABOVE entry_price, take_profit should be BELOW entry_price
2. For BUY: stop_loss should be BELOW entry_price, take_profit should be ABOVE entry_price
3. Entry price should be within 2% of current market price (for market orders, this is less strict)
4. Risk/reward ratio should ideally be >= 1:1

Return JSON:
{
    "is_valid": boolean,
    "issues": [string],
    "suggested_corrections": {} | null
}
"""
