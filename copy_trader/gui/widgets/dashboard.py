"""
黃金跟單系統 - 儀表板頁面 (Premium Dark Trading Theme)
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from gui import strings as S
from gui.theme import COLORS


class StatCard(QFrame):
    """指標卡片：圖示 + 標題 + 數值 (QFrame#statCard for gradient bg)"""

    def __init__(self, title: str, value: str = "---", icon: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self.title_label = QLabel(f"{icon} {title}" if icon else title)
        self.title_label.setObjectName("statCardTitle")
        self.title_label.setAlignment(Qt.AlignCenter)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("statCardValue")
        self.value_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, color: str = None):
        self.value_label.setText(value)
        if color:
            self.value_label.setStyleSheet(f"color: {color}; background: transparent;")
        else:
            self.value_label.setStyleSheet("")


class DashboardWidget(QWidget):
    """儀表板主頁面"""

    start_requested = Signal()
    stop_requested = Signal()
    reset_martingale_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # === 頂部控制列（卡片）===
        control_card = QFrame()
        control_card.setObjectName("card")
        control_row = QHBoxLayout(control_card)
        control_row.setContentsMargins(16, 10, 16, 10)
        control_row.setSpacing(16)

        # 狀態燈 + 文字
        self.status_label = QLabel(S.STATUS_STOPPED)
        self.status_label.setStyleSheet(f"color: {COLORS['loss']}; font-size: 13px; font-weight: bold;")
        control_row.addWidget(self.status_label)

        # === Hero 價格區域 ===
        price_widget = QWidget()
        price_layout = QHBoxLayout(price_widget)
        price_layout.setContentsMargins(0, 0, 0, 0)
        price_layout.setSpacing(8)

        self.lbl_symbol = QLabel("XAUUSD")
        self.lbl_symbol.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 14px; font-weight: bold;")
        price_layout.addWidget(self.lbl_symbol)

        self.lbl_bid = QLabel("---")
        self.lbl_bid.setObjectName("heroPriceValue")
        self.lbl_bid.setStyleSheet(f"color: {COLORS['text_tertiary']};")
        price_layout.addWidget(self.lbl_bid)

        slash = QLabel("/")
        slash.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 18px;")
        price_layout.addWidget(slash)

        self.lbl_ask = QLabel("---")
        self.lbl_ask.setObjectName("heroPriceValue")
        self.lbl_ask.setStyleSheet(f"color: {COLORS['text_tertiary']};")
        price_layout.addWidget(self.lbl_ask)

        # 點差
        self.lbl_spread = QLabel("")
        self.lbl_spread.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 11px;")
        price_layout.addWidget(self.lbl_spread)

        # 休市提示（預設顯示）
        self.lbl_market_closed = QLabel("非交易時段，即時價格暫停更新")
        self.lbl_market_closed.setStyleSheet(f"color: {COLORS['warning']}; font-size: 12px;")
        price_layout.addWidget(self.lbl_market_closed)

        control_row.addWidget(price_widget)

        control_row.addStretch()

        # 按鈕
        self.start_btn = QPushButton(S.BTN_START)
        self.start_btn.setObjectName("startButton")
        self.start_btn.setFixedHeight(36)
        self.start_btn.clicked.connect(self.start_requested.emit)
        control_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton(S.BTN_STOP)
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        control_row.addWidget(self.stop_btn)

        layout.addWidget(control_card)

        # === 帳戶指標列（使用 StatCard with icons）===
        account_row = QHBoxLayout()
        account_row.setSpacing(8)

        self.card_balance = StatCard(S.BALANCE, icon="\u25C8")
        self.card_equity = StatCard(S.EQUITY, icon="\u25CE")
        self.card_margin = StatCard(S.MARGIN, icon="\u25A3")
        self.card_free_margin = StatCard(S.FREE_MARGIN, icon="\u25A2")
        self.card_profit = StatCard(S.PROFIT, icon="\u25B2")

        for card in [self.card_balance, self.card_equity, self.card_margin,
                     self.card_free_margin, self.card_profit]:
            account_row.addWidget(card)

        layout.addLayout(account_row)

        # === 馬丁格爾 + 今日統計（並排）===
        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)

        # 馬丁格爾卡片
        self.mg_card = QFrame()
        self.mg_card.setObjectName("card")
        mg_layout = QVBoxLayout(self.mg_card)
        mg_layout.setContentsMargins(16, 12, 16, 12)
        mg_layout.setSpacing(8)

        mg_header = QLabel(S.MARTINGALE_STATUS)
        mg_header.setObjectName("sectionHeader")
        mg_layout.addWidget(mg_header)

        # 馬丁格爾指標
        mg_stats = QHBoxLayout()

        mg_level_col = QVBoxLayout()
        mg_level_col.setSpacing(2)
        lbl = QLabel(S.CURRENT_LEVEL)
        lbl.setObjectName("statCardTitle")
        lbl.setAlignment(Qt.AlignCenter)
        mg_level_col.addWidget(lbl)
        self.lbl_mg_level = QLabel("0")
        self.lbl_mg_level.setStyleSheet(f"color: {COLORS['accent']}; font-size: 32px; font-weight: bold; font-family: 'Cascadia Code', 'Consolas', monospace;")
        self.lbl_mg_level.setAlignment(Qt.AlignCenter)
        mg_level_col.addWidget(self.lbl_mg_level)
        mg_stats.addLayout(mg_level_col)

        mg_lot_col = QVBoxLayout()
        mg_lot_col.setSpacing(2)
        lbl2 = QLabel(S.LOT_SIZE)
        lbl2.setObjectName("statCardTitle")
        lbl2.setAlignment(Qt.AlignCenter)
        mg_lot_col.addWidget(lbl2)
        self.lbl_mg_lot = QLabel("0.01")
        self.lbl_mg_lot.setObjectName("statCardValue")
        self.lbl_mg_lot.setAlignment(Qt.AlignCenter)
        mg_lot_col.addWidget(self.lbl_mg_lot)
        mg_stats.addLayout(mg_lot_col)

        mg_loss_col = QVBoxLayout()
        mg_loss_col.setSpacing(2)
        lbl3 = QLabel(S.CONSECUTIVE_LOSSES)
        lbl3.setObjectName("statCardTitle")
        lbl3.setAlignment(Qt.AlignCenter)
        mg_loss_col.addWidget(lbl3)
        self.lbl_mg_losses = QLabel("0")
        self.lbl_mg_losses.setObjectName("statCardValue")
        self.lbl_mg_losses.setAlignment(Qt.AlignCenter)
        mg_loss_col.addWidget(self.lbl_mg_losses)
        mg_stats.addLayout(mg_loss_col)

        mg_layout.addLayout(mg_stats)

        reset_btn = QPushButton(S.BTN_RESET)
        reset_btn.clicked.connect(self.reset_martingale_requested.emit)
        mg_layout.addWidget(reset_btn)

        mid_row.addWidget(self.mg_card)

        # 今日統計卡片
        stats_card = QFrame()
        stats_card.setObjectName("card")
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(16, 12, 16, 12)
        stats_layout.setSpacing(8)

        stats_header = QLabel(S.TODAY_STATS)
        stats_header.setObjectName("sectionHeader")
        stats_layout.addWidget(stats_header)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(8)

        self.card_trades = StatCard(S.TOTAL_TRADES, "0", icon="\u03A3")
        self.card_wins = StatCard(S.WIN_COUNT, "0", icon="\u2713")
        self.card_losses = StatCard(S.LOSS_COUNT, "0", icon="\u2717")
        self.card_winrate = StatCard(S.WIN_RATE, "0%", icon="%")
        self.card_pnl = StatCard(S.DAILY_PNL, "$0.00", icon="$")
        self.card_api = StatCard(S.API_CALLS, "0", icon="\u21C4")

        stats_grid.addWidget(self.card_trades, 0, 0)
        stats_grid.addWidget(self.card_wins, 0, 1)
        stats_grid.addWidget(self.card_losses, 0, 2)
        stats_grid.addWidget(self.card_winrate, 1, 0)
        stats_grid.addWidget(self.card_pnl, 1, 1)
        stats_grid.addWidget(self.card_api, 1, 2)

        stats_layout.addLayout(stats_grid)

        mid_row.addWidget(stats_card)
        layout.addLayout(mid_row)

        # === 持倉摘要（卡片）===
        pos_card = QFrame()
        pos_card.setObjectName("card")
        pos_layout = QVBoxLayout(pos_card)
        pos_layout.setContentsMargins(16, 12, 16, 12)
        pos_layout.setSpacing(8)

        # 標題列
        pos_header_row = QHBoxLayout()
        pos_header = QLabel(S.NAV_POSITIONS)
        pos_header.setObjectName("sectionHeader")
        pos_header_row.addWidget(pos_header)
        pos_header_row.addStretch()
        self.pos_count_label = QLabel("0")
        self.pos_count_label.setObjectName("countBadge")
        pos_header_row.addWidget(self.pos_count_label)
        pos_layout.addLayout(pos_header_row)

        self.positions_table = QTableWidget()
        self.positions_table.setAlternatingRowColors(True)
        self.positions_table.setColumnCount(7)
        self.positions_table.setHorizontalHeaderLabels([
            S.POS_TICKET, S.POS_DIRECTION, S.POS_VOLUME,
            S.POS_ENTRY_PRICE, S.POS_CURRENT_PRICE, S.POS_SL, S.POS_PROFIT
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.positions_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.setMaximumHeight(200)
        pos_layout.addWidget(self.positions_table)

        # 空狀態提示
        self.pos_empty_label = QLabel(f"\u2205  {S.EMPTY_POSITIONS}")
        self.pos_empty_label.setObjectName("emptyState")
        self.pos_empty_label.setAlignment(Qt.AlignCenter)
        pos_layout.addWidget(self.pos_empty_label)

        layout.addWidget(pos_card)
        layout.addStretch()

    def set_trading_state(self, is_trading: bool):
        """更新交易狀態顯示"""
        self.start_btn.setEnabled(not is_trading)
        self.stop_btn.setEnabled(is_trading)
        if is_trading:
            self.status_label.setText(f"\u25CF {S.STATUS_RUNNING}")
            self.status_label.setStyleSheet(f"color: {COLORS['profit']}; font-size: 13px; font-weight: bold;")
        else:
            self.status_label.setText(f"\u25CB {S.STATUS_STOPPED}")
            self.status_label.setStyleSheet(f"color: {COLORS['loss']}; font-size: 13px; font-weight: bold;")

    def update_account(self, data: dict):
        """更新帳戶資訊"""
        self.card_balance.set_value(f"${data.get('balance', 0):,.2f}")
        self.card_equity.set_value(f"${data.get('equity', 0):,.2f}")
        self.card_margin.set_value(f"${data.get('margin', 0):,.2f}")
        self.card_free_margin.set_value(f"${data.get('free_margin', 0):,.2f}")

        profit = data.get('profit', 0)
        color = COLORS['profit'] if profit >= 0 else COLORS['loss']
        sign = "+" if profit >= 0 else ""
        self.card_profit.set_value(f"{sign}${profit:,.2f}", color)

    def update_martingale(self, level: int, lot_size: float):
        """更新馬丁格爾狀態"""
        self.lbl_mg_level.setText(str(level + 1))
        self.lbl_mg_lot.setText(f"{lot_size:.2f}")

        # 層級越高顏色越紅 + danger card border
        if level == 0:
            color = COLORS['accent']
            self.mg_card.setObjectName("card")
        elif level <= 2:
            color = COLORS['warning']
            self.mg_card.setObjectName("card")
        else:
            color = COLORS['loss']
            self.mg_card.setObjectName("dangerCard")

        # Force re-style after objectName change
        self.mg_card.setStyleSheet(self.mg_card.styleSheet())
        self.lbl_mg_level.setStyleSheet(
            f"color: {color}; font-size: 32px; font-weight: bold; "
            f"font-family: 'Cascadia Code', 'Consolas', monospace;"
        )

    def update_connection(self, connected: bool):
        """更新 MT5 連線/市場狀態"""
        if connected:
            self.lbl_market_closed.hide()
            self.lbl_bid.show()
            self.lbl_ask.show()
            self.lbl_spread.show()
        else:
            self.lbl_market_closed.show()
            mono = "font-family: 'Cascadia Code', 'Consolas', monospace;"
            self.lbl_bid.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 28px; font-weight: bold; {mono}")
            self.lbl_ask.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 28px; font-weight: bold; {mono}")
            self.lbl_spread.setText("")

    def update_price(self, bid: float, ask: float):
        """更新即時價格"""
        self.lbl_market_closed.hide()

        mono = "font-family: 'Cascadia Code', 'Consolas', monospace;"

        # 更新 bid（賣價 - 偏紅）
        self.lbl_bid.setText(f"{bid:.2f}")
        self.lbl_bid.setStyleSheet(
            f"color: {COLORS['loss']}; font-size: 28px; font-weight: bold; {mono}"
        )

        # 更新 ask（買價 - 偏綠）
        self.lbl_ask.setText(f"{ask:.2f}")
        self.lbl_ask.setStyleSheet(
            f"color: {COLORS['profit']}; font-size: 28px; font-weight: bold; {mono}"
        )

        # 點差
        spread = (ask - bid) * 100
        self.lbl_spread.setText(f"({spread:.0f}pts)")
        self.lbl_spread.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 11px;")

        self.lbl_symbol.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 14px; font-weight: bold;"
        )

    def update_positions(self, positions: list):
        """更新持倉表格"""
        count = len(positions)
        self.pos_count_label.setText(str(count))

        if count == 0:
            self.positions_table.hide()
            self.pos_empty_label.show()
        else:
            self.pos_empty_label.hide()
            self.positions_table.show()

        self.positions_table.setRowCount(count)
        for row, pos in enumerate(positions):
            ticket = str(pos.get('ticket', ''))
            ptype = pos.get('type', '')
            direction = S.POS_BUY if ptype in (0, 'buy') else S.POS_SELL
            volume = f"{pos.get('volume', 0):.2f}"
            entry = f"{pos.get('price_open', 0):.2f}"
            current = f"{pos.get('price_current', 0):.2f}"
            sl = f"{pos.get('sl', 0):.2f}"
            profit = pos.get('profit', 0)

            items = [ticket, direction, volume, entry, current, sl, f"{profit:+.2f}"]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 6:  # 盈虧欄
                    color = QColor(COLORS['profit']) if profit >= 0 else QColor(COLORS['loss'])
                    item.setForeground(color)
                if col == 1:  # 方向欄
                    color = QColor(COLORS['profit']) if ptype in (0, 'buy') else QColor(COLORS['loss'])
                    item.setForeground(color)
                self.positions_table.setItem(row, col, item)

    def update_stats(self, data: dict):
        """更新今日統計"""
        trades = data.get('daily_trades', 0)
        api_calls = data.get('api_calls', 0)
        daily_loss = data.get('daily_loss', 0)

        self.card_trades.set_value(str(trades))
        self.card_api.set_value(str(api_calls))

        pnl_color = COLORS['profit'] if daily_loss <= 0 else COLORS['loss']
        self.card_pnl.set_value(f"${-daily_loss:,.2f}", pnl_color)
