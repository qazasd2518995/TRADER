"""自有策略。

跟 copy_trader/backtest 的分工：backtest 負責「一張單進場之後會怎樣」，
這裡負責「什麼時候該掛單、掛在哪裡」。出場一律交給 backtest 的引擎，
自有策略和跟單訊號才會在同一套規則下比較。
"""
from .range_fade import RangeFadeParams, generate_orders

__all__ = ["RangeFadeParams", "generate_orders"]
