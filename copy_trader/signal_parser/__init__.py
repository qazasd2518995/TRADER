# 一律可用（純 Python、無外部相依）
from .keyword_filter import is_potential_signal, extract_quick_info
from .regex_parser import RegexSignalParser, ParsedSignal, quick_parse

# 重量級 / 選用的 LLM 解析器 — 延遲容錯載入。
# regex-only 部署（中央 collector、會員端 client）不需要 anthropic / groq /
# google.genai；缺套件時設為 None，不讓整個 package 匯入失敗。
try:
    from .prompts import SIGNAL_EXTRACTION_PROMPT
except Exception:
    SIGNAL_EXTRACTION_PROMPT = None
try:
    from .parser import SignalParser
except Exception:
    SignalParser = None
try:
    from .groq_parser import GroqSignalParser
except Exception:
    GroqSignalParser = None
try:
    from .groq_vision_parser import GroqVisionParser
except Exception:
    GroqVisionParser = None
try:
    from .gemini_vision_parser import GeminiVisionParser
except Exception:
    GeminiVisionParser = None

__all__ = [
    "SignalParser",
    "ParsedSignal",
    "SIGNAL_EXTRACTION_PROMPT",
    "GroqSignalParser",
    "GroqVisionParser",
    "GeminiVisionParser",
    "RegexSignalParser",
    "is_potential_signal",
    "extract_quick_info",
    "quick_parse",
]
