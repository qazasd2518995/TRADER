"""
黃金跟單系統 - MT5 JSON 資料讀取器
使用 QThread + Worker 模式，將檔案 I/O 移到背景線程避免阻塞 GUI
"""
import json
import time
import logging
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QThread, Signal, Slot

logger = logging.getLogger(__name__)


class _MT5Worker(QObject):
    """背景線程 Worker - 負責實際的 JSON 檔案讀取"""

    # 讀取完畢後回傳資料的信號
    price_ready = Signal(float, float, float)  # (bid, ask, file_age)
    account_ready = Signal(dict)
    positions_ready = Signal(list)
    orders_ready = Signal(list)
    trades_ready = Signal(list)

    def __init__(self):
        super().__init__()
        self._mt5_dir = Path(".")

    def set_mt5_dir(self, mt5_dir: str):
        self._mt5_dir = Path(mt5_dir)

    def _read_json(self, filename: str) -> dict:
        """安全讀取 JSON 檔案（在背景線程中執行）"""
        filepath = self._mt5_dir / filename
        try:
            if filepath.exists():
                age = time.time() - filepath.stat().st_mtime
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['_file_age'] = age
                return data
        except (json.JSONDecodeError, PermissionError, OSError) as e:
            logger.debug(f"Failed to read {filename}: {e}")
        return {}

    @Slot()
    def do_poll_fast(self):
        """讀取價格（由主線程 signal 觸發）"""
        data = self._read_json("XAUUSD_price.json")
        if data:
            bid = data.get('bid', 0)
            ask = data.get('ask', 0)
            age = data.get('_file_age', 999)
            if bid > 0 and ask > 0:
                self.price_ready.emit(bid, ask, age)

    @Slot()
    def do_poll_slow(self):
        """讀取帳戶/持倉/掛單"""
        account = self._read_json("account_info.json")
        if account:
            self.account_ready.emit(account)

        positions_data = self._read_json("positions.json")
        positions = positions_data.get('positions', [])
        self.positions_ready.emit(positions)

        orders_data = self._read_json("orders.json")
        orders = orders_data.get('orders', [])
        self.orders_ready.emit(orders)

    @Slot()
    def do_poll_history(self):
        """讀取歷史成交"""
        trades_data = self._read_json("closed_trades.json")
        trades = trades_data.get('trades', [])
        self.trades_ready.emit(trades)


class MT5DataReader(QObject):
    """輪詢 MT5 JSON 檔案並發送 Qt 信號（背景線程讀取，主線程接收）"""

    # 對外信號（與原介面相同）
    price_updated = Signal(float, float)        # (bid, ask)
    account_updated = Signal(dict)              # 帳戶資訊
    positions_updated = Signal(list)            # 持倉列表
    orders_updated = Signal(list)               # 掛單列表
    trades_updated = Signal(list)               # 已平倉交易
    connection_status = Signal(bool)            # MT5 連線狀態
    data_received = Signal()                    # 任何資料回傳時觸發（心跳用）

    # 內部信號：主線程 → Worker（觸發讀取）
    _request_fast = Signal()
    _request_slow = Signal()
    _request_history = Signal()

    def __init__(self, mt5_dir: str):
        super().__init__()
        self._last_connection_state = None

        # 建立 Worker 和 QThread
        self._thread = QThread()
        self._worker = _MT5Worker()
        self._worker.set_mt5_dir(mt5_dir)
        self._worker.moveToThread(self._thread)

        # 主線程 signal → Worker slot（跨線程 queued connection）
        self._request_fast.connect(self._worker.do_poll_fast)
        self._request_slow.connect(self._worker.do_poll_slow)
        self._request_history.connect(self._worker.do_poll_history)

        # Worker signal → 主線程 slot
        self._worker.price_ready.connect(self._on_price_ready)
        self._worker.account_ready.connect(self._on_account_ready)
        self._worker.positions_ready.connect(self._on_positions_ready)
        self._worker.orders_ready.connect(self._on_orders_ready)
        self._worker.trades_ready.connect(self._on_trades_ready)

        # QTimer 仍在主線程，只負責發送「請求讀取」信號
        self._fast_timer = QTimer(self)
        self._fast_timer.timeout.connect(self._request_fast.emit)

        self._slow_timer = QTimer(self)
        self._slow_timer.timeout.connect(self._request_slow.emit)

        self._history_timer = QTimer(self)
        self._history_timer.timeout.connect(self._request_history.emit)

        # 啟動背景線程
        self._thread.start()

    def set_mt5_dir(self, mt5_dir: str):
        """更新 MT5 路徑"""
        self._worker.set_mt5_dir(mt5_dir)

    def start(self):
        """啟動輪詢（增加間隔以降低 I/O 頻率）"""
        self._fast_timer.start(2000)     # 價格 2s
        self._slow_timer.start(5000)     # 帳戶 5s
        self._history_timer.start(10000)  # 歷史 10s
        logger.info("MT5DataReader started (worker thread)")

    def stop(self):
        """停止輪詢並結束背景線程"""
        self._fast_timer.stop()
        self._slow_timer.stop()
        self._history_timer.stop()

        self._thread.quit()
        self._thread.wait(3000)

    # === Worker 回傳 → 對外發送 ===

    def _on_price_ready(self, bid: float, ask: float, file_age: float):
        self.price_updated.emit(bid, ask)
        self.data_received.emit()

        connected = file_age < 30
        if connected != self._last_connection_state:
            self._last_connection_state = connected
            self.connection_status.emit(connected)

    def _on_account_ready(self, account: dict):
        self.account_updated.emit(account)
        self.data_received.emit()

    def _on_positions_ready(self, positions: list):
        self.positions_updated.emit(positions)
        self.data_received.emit()

    def _on_orders_ready(self, orders: list):
        self.orders_updated.emit(orders)

    def _on_trades_ready(self, trades: list):
        self.trades_updated.emit(trades)
