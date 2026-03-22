"""
黃金跟單系統 - 主視窗
"""
import asyncio
import time
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QStackedWidget, QStatusBar, QLabel,
    QButtonGroup, QFrame, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QFont

from gui import strings as S
from gui.widgets.dashboard import DashboardWidget
from gui.widgets.settings_panel import SettingsPanel
from gui.widgets.positions_table import PositionsWidget
from gui.widgets.trade_history import TradeHistoryWidget
from gui.widgets.log_viewer import LogViewer
from gui.widgets.tutorial import TutorialWidget
from gui.mt5_data_reader import MT5DataReader
from gui.backend_bridge import BackendBridge
from gui.tray import SystemTrayManager
from gui.log_handler import setup_gui_logging


class MainWindow(QMainWindow):
    """主視窗：側邊欄導航 + 內容區 + 狀態列"""

    # 側邊欄按鈕索引 -> stack 索引映射
    # 按鈕順序: 儀表板(0), 持倉(1), 歷史(2), 設定(3), 日誌(4), 教學(5)
    # stack 順序: 儀表板(0), 設定(1), 持倉(2), 歷史(3), 日誌(4), 教學(5)
    NAV_INDEX_MAP = [0, 2, 3, 1, 4, 5]

    def __init__(self, config, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.config = config
        self.loop = loop
        self._is_trading = False
        self._start_time = None

        # 心跳動畫
        self._heartbeat_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._heartbeat_index = 0
        self._last_data_time = 0.0  # 上次收到 MT5 資料的時間

        self.setWindowTitle(S.APP_TITLE)
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)

        # 後端橋接
        self.bridge = BackendBridge(loop)

        # MT5 資料讀取器
        self.mt5_reader = MT5DataReader(config.mt5_files_dir)

        # 建構 UI
        self._build_ui()

        # 日誌系統
        self.log_handler = setup_gui_logging(self.log_viewer)

        # 系統列
        self.tray_manager = SystemTrayManager(self)

        # 連接信號
        self._connect_signals()

        # 狀態列更新計時器
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_status_bar)
        self._uptime_timer.start(1000)

        # 啟動 MT5 資料讀取
        self.mt5_reader.start()

    def _build_ui(self):
        """建構主介面"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 內容區（先建立，側邊欄需要引用） ===
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)

        # === 側邊欄 ===
        sidebar = self._build_sidebar()
        main_layout.addWidget(sidebar)

        # 建立各頁面
        self.dashboard = DashboardWidget()
        self.settings_panel = SettingsPanel(self.config)
        self.positions_widget = PositionsWidget()
        self.trade_history = TradeHistoryWidget()
        self.log_viewer = LogViewer()
        self.tutorial = TutorialWidget()

        self.stack.addWidget(self.dashboard)       # 0
        self.stack.addWidget(self.settings_panel)   # 1
        self.stack.addWidget(self.positions_widget)  # 2
        self.stack.addWidget(self.trade_history)     # 3
        self.stack.addWidget(self.log_viewer)        # 4
        self.stack.addWidget(self.tutorial)          # 5

        main_layout.addWidget(self.stack, 1)

        # === 狀態列 ===
        self._build_status_bar()

    def _build_sidebar(self) -> QWidget:
        """建立側邊欄 - 180px 寬，文字導航+分組"""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        # Brand area
        brand = QFrame()
        brand.setObjectName("sidebarBrand")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)

        title = QLabel(S.APP_TITLE)
        title.setObjectName("sidebarTitle")
        brand_layout.addWidget(title)

        version = QLabel(f"v{S.APP_VERSION}")
        version.setObjectName("sidebarVersion")
        brand_layout.addWidget(version)

        layout.addWidget(brand)

        # === 交易分組 ===
        trading_label = QLabel(S.NAV_GROUP_TRADING)
        trading_label.setObjectName("tradingSectionLabel")
        layout.addWidget(trading_label)

        # 導航按鈕 - 分為交易組和系統組
        trading_nav = [
            S.NAV_DASHBOARD,   # btn 0 -> stack 0
            S.NAV_POSITIONS,   # btn 1 -> stack 2
            S.NAV_HISTORY,     # btn 2 -> stack 3
        ]
        system_nav = [
            S.NAV_SETTINGS,    # btn 3 -> stack 1
            S.NAV_LOG,         # btn 4 -> stack 4
            S.NAV_TUTORIAL,    # btn 5 -> stack 5
        ]

        self.nav_buttons = []
        self.nav_group = QButtonGroup(self)

        btn_index = 0
        for label in trading_nav:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("tradingNavButton")
            layout.addWidget(btn)
            self.nav_group.addButton(btn, btn_index)
            self.nav_buttons.append(btn)
            btn_index += 1

        # === 系統分組 ===
        system_label = QLabel(S.NAV_GROUP_SYSTEM)
        system_label.setObjectName("sidebarSectionLabel")
        layout.addWidget(system_label)

        for label in system_nav:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("systemNavButton")
            layout.addWidget(btn)
            self.nav_group.addButton(btn, btn_index)
            self.nav_buttons.append(btn)
            btn_index += 1

        self.nav_buttons[0].setChecked(True)
        self.nav_group.idClicked.connect(self._on_nav_clicked)

        layout.addStretch()

        # === 分隔線 ===
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        # === MT5 連線指示 ===
        self.sidebar_mt5 = QLabel("\u25CF MT5: " + S.STATUS_DISCONNECTED)
        self.sidebar_mt5.setObjectName("mt5Indicator")
        self.sidebar_mt5.setStyleSheet("color: #ff5252;")
        layout.addWidget(self.sidebar_mt5)

        # === 開始/停止按鈕 ===
        btn_container = QWidget()
        btn_layout = QVBoxLayout(btn_container)
        btn_layout.setContentsMargins(12, 8, 12, 0)
        btn_layout.setSpacing(6)

        self.start_btn = QPushButton(S.BTN_START)
        self.start_btn.setObjectName("startButton")
        self.start_btn.clicked.connect(self._on_start_clicked)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton(S.BTN_STOP)
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self.stop_btn)

        layout.addWidget(btn_container)

        return sidebar

    def _on_nav_clicked(self, btn_index: int):
        """導航按鈕點擊 - 映射到 stack 索引"""
        stack_index = self.NAV_INDEX_MAP[btn_index]
        self.stack.setCurrentIndex(stack_index)

    def _build_status_bar(self):
        """建立狀態列"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.status_heartbeat = QLabel("\u2801 等待資料...")
        self.status_heartbeat.setObjectName("heartbeatLabel")
        self.status_heartbeat.setStyleSheet("color: #484f58;")
        self.status_mt5 = QLabel(f"MT5: {S.STATUS_DISCONNECTED}")
        self.status_price = QLabel("XAUUSD: ---")
        self.status_price.setObjectName("priceLabel")
        self.status_martingale = QLabel(f"{S.CURRENT_LEVEL}: 0")
        self.status_uptime = QLabel(f"{S.UPTIME}: --:--:--")
        self.status_state = QLabel(S.STATUS_STOPPED)

        for label in [self.status_heartbeat, self.status_state, self.status_mt5,
                       self.status_price, self.status_martingale, self.status_uptime]:
            self.status_bar.addPermanentWidget(label)

    def _connect_signals(self):
        """連接所有信號"""
        # MT5 資料 → 心跳 + 儀表板
        self.mt5_reader.data_received.connect(self._on_data_received)
        self.mt5_reader.price_updated.connect(self._on_price_updated)
        self.mt5_reader.account_updated.connect(self.dashboard.update_account)
        self.mt5_reader.positions_updated.connect(self.dashboard.update_positions)
        self.mt5_reader.positions_updated.connect(self.positions_widget.update_positions)
        self.mt5_reader.orders_updated.connect(self.positions_widget.update_orders)
        self.mt5_reader.trades_updated.connect(self.trade_history.update_trades)
        self.mt5_reader.connection_status.connect(self._on_mt5_connection)
        self.mt5_reader.connection_status.connect(self.dashboard.update_connection)

        # 後端橋接 → 儀表板
        self.bridge.martingale_updated.connect(self.dashboard.update_martingale)
        self.bridge.martingale_updated.connect(self._on_martingale_updated)
        self.bridge.stats_updated.connect(self.dashboard.update_stats)
        self.bridge.status_changed.connect(self._on_backend_status)

        # 設定面板 → 儲存
        self.settings_panel.config_saved.connect(self._on_config_saved)

        # 儀表板按鈕
        self.dashboard.start_requested.connect(self._on_start_clicked)
        self.dashboard.stop_requested.connect(self._on_stop_clicked)
        self.dashboard.reset_martingale_requested.connect(self._on_reset_martingale)

    def _on_start_clicked(self):
        """開始交易"""
        if self._is_trading:
            return

        # 從設定面板取得最新設定
        config = self.settings_panel.get_current_config()
        self.config = config

        # 更新 MT5 reader 路徑
        self.mt5_reader.set_mt5_dir(config.mt5_files_dir)

        # 啟動後端
        asyncio.ensure_future(self.bridge.start_trading(config), loop=self.loop)

        self._is_trading = True
        self._start_time = asyncio.get_event_loop().time()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.dashboard.set_trading_state(True)
        self.status_state.setText(f"\u25CF {S.STATUS_RUNNING}")
        self.status_state.setStyleSheet("color: #3fb68b;")

    def _on_stop_clicked(self):
        """停止交易"""
        if not self._is_trading:
            return

        self.bridge.stop_trading()

        self._is_trading = False
        self._start_time = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.dashboard.set_trading_state(False)
        self.status_state.setText(f"\u25CB {S.STATUS_STOPPED}")
        self.status_state.setStyleSheet("color: #ff5252;")

    def _on_reset_martingale(self):
        """重置馬丁格爾"""
        reply = QMessageBox.question(
            self, S.CONFIRM_RESET_MARTINGALE_TITLE,
            S.CONFIRM_RESET_MARTINGALE,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.bridge.reset_martingale()

    def _on_price_updated(self, bid: float, ask: float):
        """更新價格顯示"""
        self.status_price.setText(f"XAUUSD: {bid:.2f} / {ask:.2f}")
        self.dashboard.update_price(bid, ask)

    def _on_mt5_connection(self, connected: bool):
        """MT5 連線狀態"""
        if connected:
            self.status_mt5.setText(f"MT5: {S.STATUS_CONNECTED}")
            self.status_mt5.setStyleSheet("color: #3fb68b;")
            self.sidebar_mt5.setText(f"\u25CF MT5: {S.STATUS_CONNECTED}")
            self.sidebar_mt5.setStyleSheet("color: #3fb68b; background: transparent; border: none; padding: 6px 16px; font-size: 12px;")
        else:
            self.status_mt5.setText(f"MT5: {S.STATUS_DISCONNECTED}")
            self.status_mt5.setStyleSheet("color: #ff5252;")
            self.sidebar_mt5.setText(f"\u25CF MT5: {S.STATUS_DISCONNECTED}")
            self.sidebar_mt5.setStyleSheet("color: #ff5252; background: transparent; border: none; padding: 6px 16px; font-size: 12px;")

    def _on_martingale_updated(self, level: int, lot_size: float):
        """馬丁格爾狀態更新"""
        self.status_martingale.setText(f"{S.CURRENT_LEVEL}: {level} ({lot_size}手)")

    def _on_backend_status(self, status: str):
        """後端狀態變更"""
        if status == "error":
            self._on_stop_clicked()
            self.status_state.setText(f"\u26A0 {S.STATUS_ERROR}")
            self.status_state.setStyleSheet("color: #f0b90b;")

    def _on_config_saved(self, config):
        """設定已儲存"""
        self.config = config
        self.mt5_reader.set_mt5_dir(config.mt5_files_dir)

    def _on_data_received(self):
        """收到 MT5 資料 - 更新心跳時間"""
        self._last_data_time = time.time()

    def _update_status_bar(self):
        """更新狀態列（每秒）"""
        # 運行時間
        if self._is_trading and self._start_time is not None:
            elapsed = asyncio.get_event_loop().time() - self._start_time
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            self.status_uptime.setText(f"{S.UPTIME}: {hours:02d}:{minutes:02d}:{seconds:02d}")
        else:
            self.status_uptime.setText(f"{S.UPTIME}: --:--:--")

        # 心跳動畫
        self._heartbeat_index = (self._heartbeat_index + 1) % len(self._heartbeat_frames)
        spinner = self._heartbeat_frames[self._heartbeat_index]

        if self._last_data_time > 0:
            ago = int(time.time() - self._last_data_time)
            if ago <= 5:
                # 正常：綠色旋轉 + 倒數到下次更新
                next_in = max(1, 2 - ago)  # 價格每 2 秒更新
                self.status_heartbeat.setText(f"{spinner} 資料正常 ({next_in}s)")
                self.status_heartbeat.setStyleSheet("color: #3fb68b;")
            elif ago <= 15:
                # 稍慢：黃色警告
                self.status_heartbeat.setText(f"{spinner} 更新延遲 {ago}s")
                self.status_heartbeat.setStyleSheet("color: #f0b90b;")
            else:
                # 停滯：紅色警告，可能當機
                self.status_heartbeat.setText(f"\u25CF 無回應 {ago}s")
                self.status_heartbeat.setStyleSheet("color: #ff5252;")
        else:
            self.status_heartbeat.setText(f"{spinner} 等待資料...")
            self.status_heartbeat.setStyleSheet("color: #484f58;")

    # === 視窗事件 ===
    def closeEvent(self, event):
        """關閉視窗時最小化到系統列"""
        if self.tray_manager.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            self._quit_app()

    def show_and_raise(self):
        """從系統列還原"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def quit_app(self):
        """真正結束程式"""
        reply = QMessageBox.question(
            self, S.CONFIRM_QUIT_TITLE, S.CONFIRM_QUIT,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._quit_app()

    def _quit_app(self):
        """執行結束"""
        if self._is_trading:
            self.bridge.stop_trading()
        self.mt5_reader.stop()
        self.tray_manager.tray.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
