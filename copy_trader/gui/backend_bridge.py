"""
黃金跟單系統 - 後端橋接層
將 CopyTrader async 後端與 Qt GUI 連接
"""
import asyncio
import logging
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class BackendBridge(QObject):
    """CopyTrader 後端 ↔ Qt GUI 橋接"""

    # 信號 → GUI
    status_changed = Signal(str)              # "running" / "stopped" / "error"
    signal_detected = Signal(dict)            # 偵測到的訊號資訊
    trade_submitted = Signal(str, dict)       # (signal_id, signal_data)
    martingale_updated = Signal(int, float)   # (level, lot_size)
    stats_updated = Signal(dict)              # 統計資料

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.loop = loop
        self.trader = None
        self._task = None

    def _event_callback(self, event_type: str, data: dict):
        """接收後端事件並轉為 Qt Signal"""
        try:
            if event_type == "signal_detected":
                self.signal_detected.emit(data)
            elif event_type == "trade_submitted":
                self.trade_submitted.emit(data.get("signal_id", ""), data)
            elif event_type == "martingale_updated":
                self.martingale_updated.emit(
                    data.get("level", 0),
                    data.get("lot_size", 0.01)
                )
            elif event_type == "stats_updated":
                self.stats_updated.emit(data)
        except Exception as e:
            logger.error(f"Event callback error: {e}")

    async def start_trading(self, config):
        """啟動交易（async）"""
        try:
            # 延遲匯入避免循環依賴
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from app import CopyTrader

            self.trader = CopyTrader(config, event_callback=self._event_callback)
            self.status_changed.emit("running")

            logger.info("Backend starting...")
            self._task = asyncio.ensure_future(self.trader.start())

            # 定期發送馬丁格爾和統計更新
            asyncio.ensure_future(self._periodic_updates())

        except Exception as e:
            logger.error(f"Failed to start trading: {e}", exc_info=True)
            self.status_changed.emit("error")

    async def _periodic_updates(self):
        """定期發送統計更新到 GUI"""
        while self.trader and self.trader._running:
            try:
                # 馬丁格爾狀態
                tm = self.trader.trade_manager
                self.martingale_updated.emit(
                    tm.current_martingale_level,
                    tm.get_martingale_lot_size()
                )

                # 統計
                self.stats_updated.emit({
                    "daily_trades": self.trader._daily_trades,
                    "daily_loss": self.trader._daily_loss,
                    "api_calls": self.trader._api_calls_today,
                    "filtered": self.trader._signals_filtered,
                })

            except Exception as e:
                logger.debug(f"Periodic update error: {e}")

            await asyncio.sleep(2)

    def stop_trading(self):
        """停止交易"""
        if self.trader:
            self.trader.stop()
            logger.info("Backend stopped")
        self.trader = None
        self.status_changed.emit("stopped")

    def reset_martingale(self):
        """重置馬丁格爾"""
        if self.trader:
            self.trader.trade_manager.reset_martingale()
            self.martingale_updated.emit(0, self.trader.trade_manager.default_lot_size)
            logger.info("Martingale reset from GUI")
