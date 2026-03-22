"""
黃金跟單系統 - 歷史成交頁面 (Premium Dark Trading Theme)
"""
import time
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QFrame,
    QPushButton, QDateEdit
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor

from gui import strings as S
from gui.theme import COLORS


class TradeHistoryWidget(QWidget):
    """歷史成交頁面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_trades = []
        self._filter_mode = "all"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # === 摘要指標卡片列 ===
        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)

        self.lbl_total = self._make_stat("\u03A3 總交易", "0")
        self.lbl_wins = self._make_stat("\u2713 勝場", "0")
        self.lbl_losses = self._make_stat("\u2717 敗場", "0")
        self.lbl_winrate = self._make_stat("% 勝率", "0%")
        self.lbl_pnl = self._make_stat("$ 總盈虧", "$0.00")

        for widget in [self.lbl_total, self.lbl_wins, self.lbl_losses,
                       self.lbl_winrate, self.lbl_pnl]:
            summary_row.addWidget(widget)

        layout.addLayout(summary_row)

        # === 日期篩選列 ===
        filter_card = QFrame()
        filter_card.setObjectName("toolbarCard")
        filter_row = QHBoxLayout(filter_card)
        filter_row.setContentsMargins(16, 8, 16, 8)
        filter_row.setSpacing(8)

        # 快捷按鈕 - underline style
        self.btn_today = QPushButton(S.FILTER_TODAY)
        self.btn_today.setObjectName("filterButton")
        self.btn_today.setCheckable(True)
        self.btn_today.clicked.connect(lambda: self._set_filter("today"))

        self.btn_week = QPushButton(S.FILTER_THIS_WEEK)
        self.btn_week.setObjectName("filterButton")
        self.btn_week.setCheckable(True)
        self.btn_week.clicked.connect(lambda: self._set_filter("week"))

        self.btn_all = QPushButton(S.FILTER_ALL)
        self.btn_all.setObjectName("filterButton")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_all.clicked.connect(lambda: self._set_filter("all"))

        self._filter_buttons = [self.btn_today, self.btn_week, self.btn_all]

        for btn in self._filter_buttons:
            btn.setFixedHeight(32)
            filter_row.addWidget(btn)

        # 分隔
        filter_row.addSpacing(16)

        # 自訂日期區間
        lbl_from = QLabel(S.FILTER_FROM)
        lbl_from.setFixedWidth(20)
        filter_row.addWidget(lbl_from)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.setDisplayFormat("yyyy/MM/dd")
        filter_row.addWidget(self.date_from)

        lbl_to = QLabel(S.FILTER_TO)
        lbl_to.setFixedWidth(20)
        filter_row.addWidget(lbl_to)

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setDisplayFormat("yyyy/MM/dd")
        filter_row.addWidget(self.date_to)

        self.btn_apply = QPushButton(S.FILTER_APPLY)
        self.btn_apply.setObjectName("accentOutlineButton")
        self.btn_apply.setFixedHeight(32)
        self.btn_apply.clicked.connect(lambda: self._set_filter("custom"))
        filter_row.addWidget(self.btn_apply)

        filter_row.addStretch()
        layout.addWidget(filter_card)

        # === 歷史表格卡片 ===
        table_card = QFrame()
        table_card.setObjectName("card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 12, 16, 12)
        table_layout.setSpacing(8)

        header = QLabel(f"\u25F7  {S.HISTORY_TITLE}")
        header.setObjectName("sectionHeader")
        table_layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            S.POS_TICKET, S.POS_DIRECTION, S.POS_VOLUME,
            S.POS_ENTRY_PRICE, S.HISTORY_EXIT_PRICE,
            S.POS_PROFIT, S.HISTORY_CHANGE, S.HISTORY_CLOSE_TIME
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)

        table_layout.addWidget(self.table)

        # 空狀態
        self.empty_label = QLabel(f"\u2205  {S.EMPTY_HISTORY}")
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        table_layout.addWidget(self.empty_label)

        layout.addWidget(table_card, 1)

    def _make_stat(self, title: str, value: str) -> QFrame:
        """建立摘要指標 (StatCard style)"""
        card = QFrame()
        card.setObjectName("statCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        t = QLabel(title)
        t.setObjectName("statCardTitle")
        t.setAlignment(Qt.AlignCenter)
        layout.addWidget(t)

        v = QLabel(value)
        v.setObjectName("statCardValue")
        v.setAlignment(Qt.AlignCenter)
        layout.addWidget(v)

        card._value_label = v
        return card

    def _set_filter(self, mode: str):
        """設定篩選模式並重新顯示"""
        self._filter_mode = mode

        self.btn_today.setChecked(mode == "today")
        self.btn_week.setChecked(mode == "week")
        self.btn_all.setChecked(mode == "all")

        self._apply_filter()

    def _get_filter_range(self):
        """取得當前篩選的時間範圍 (timestamp)"""
        if self._filter_mode == "today":
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            return today.timestamp(), time.time() + 86400

        elif self._filter_mode == "week":
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            monday = today - timedelta(days=today.weekday())
            return monday.timestamp(), time.time() + 86400

        elif self._filter_mode == "custom":
            qd_from = self.date_from.date()
            qd_to = self.date_to.date()
            dt_from = datetime(qd_from.year(), qd_from.month(), qd_from.day())
            dt_to = datetime(qd_to.year(), qd_to.month(), qd_to.day(), 23, 59, 59)
            return dt_from.timestamp(), dt_to.timestamp()

        else:  # "all"
            return 0, float('inf')

    def _apply_filter(self):
        """根據篩選模式過濾並顯示交易"""
        t_start, t_end = self._get_filter_range()

        filtered = [
            t for t in self._all_trades
            if t_start <= t.get('time_close', 0) <= t_end
        ]

        filtered.sort(key=lambda t: t.get('time_close', 0))

        self._render_trades(filtered)

    def update_trades(self, trades: list):
        """更新歷史成交（外部呼叫，儲存完整列表後套用篩選）"""
        self._all_trades = trades
        self._apply_filter()

    def _render_trades(self, trades: list):
        """渲染交易到表格"""
        count = len(trades)

        if count == 0:
            self.table.hide()
            self.empty_label.show()
        else:
            self.empty_label.hide()
            self.table.show()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(count)

        total_profit = 0.0
        wins = 0
        losses = 0

        for row, trade in enumerate(trades):
            profit = trade.get('profit', 0)
            total_profit += profit
            if profit >= 0:
                wins += 1
            else:
                losses += 1

            ptype = trade.get('type', '')
            direction = S.POS_BUY if ptype in (0, 'buy') else S.POS_SELL

            close_time = trade.get('time_close', '')
            if isinstance(close_time, (int, float)) and close_time > 0:
                close_time = time.strftime('%m/%d %H:%M', time.localtime(close_time))

            change = trade.get('change_percent', 0)

            values = [
                str(trade.get('ticket', '')),
                direction,
                f"{trade.get('volume', 0):.2f}",
                f"{trade.get('price_open', 0):.2f}",
                f"{trade.get('price_close', 0):.2f}",
                f"{profit:+.2f}",
                f"{change:+.2f}%",
                str(close_time)
            ]

            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)

                if col == 1:  # 方向
                    color = QColor(COLORS['profit']) if ptype in (0, 'buy') else QColor(COLORS['loss'])
                    item.setForeground(color)
                elif col in (5, 6):  # 盈虧 / 變動%
                    color = QColor(COLORS['profit']) if profit >= 0 else QColor(COLORS['loss'])
                    item.setForeground(color)

                self.table.setItem(row, col, item)

        self.table.setSortingEnabled(True)

        # 更新摘要指標
        total = wins + losses
        wr = (wins / total * 100) if total > 0 else 0
        sign = "+" if total_profit >= 0 else ""
        pnl_color = COLORS['profit'] if total_profit >= 0 else COLORS['loss']

        self.lbl_total._value_label.setText(str(total))
        self.lbl_wins._value_label.setText(str(wins))
        self.lbl_losses._value_label.setText(str(losses))
        self.lbl_winrate._value_label.setText(f"{wr:.1f}%")
        self.lbl_pnl._value_label.setText(f"{sign}${total_profit:.2f}")
        self.lbl_pnl._value_label.setStyleSheet(f"color: {pnl_color}; background: transparent;")
