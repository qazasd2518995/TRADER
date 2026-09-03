"""出場模擬引擎。

離線回測（scripts/backtest_signals.py）和線上影子對照（central/exec_shadow.py）
共用這一份。兩邊各留一份的話會慢慢分歧，對照出來的數字就沒有意義了。
"""
from .engine import Rules, Trade, price_at, simulate, slice_from

__all__ = ["Rules", "Trade", "simulate", "slice_from", "price_at"]
