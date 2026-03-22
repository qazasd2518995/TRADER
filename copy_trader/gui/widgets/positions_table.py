"""
黃金跟單系統 - 持倉與掛單頁面 (Premium Dark Trading Theme)
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from gui import strings as S
from gui.theme import COLORS


class PositionsWidget(QWidget):
    """持倉 + 掛單頁面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        splitter = QSplitter(Qt.Vertical)

        # === 持倉卡片 ===
        pos_card = QFrame()
        pos_card.setObjectName("card")
        pos_layout = QVBoxLayout(pos_card)
        pos_layout.setContentsMargins(16, 12, 16, 12)
        pos_layout.setSpacing(8)

        pos_header_row = QHBoxLayout()
        pos_header = QLabel(f"\u25A4  {S.NAV_POSITIONS}")
        pos_header.setObjectName("sectionHeader")
        pos_header_row.addWidget(pos_header)
        pos_header_row.addStretch()
        self.pos_count_label = QLabel("0")
        self.pos_count_label.setObjectName("countBadge")
        pos_header_row.addWidget(self.pos_count_label)
        pos_layout.addLayout(pos_header_row)

        self.positions_table = QTableWidget()
        self.positions_table.setAlternatingRowColors(True)
        self.positions_table.setColumnCount(9)
        self.positions_table.setHorizontalHeaderLabels([
            S.POS_TICKET, S.POS_DIRECTION, S.POS_VOLUME,
            S.POS_ENTRY_PRICE, S.POS_CURRENT_PRICE,
            S.POS_SL, S.POS_TP, S.POS_PROFIT, S.POS_COMMENT
        ])
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.positions_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.positions_table.verticalHeader().setVisible(False)
        self.positions_table.setSortingEnabled(True)
        pos_layout.addWidget(self.positions_table)

        self.pos_empty_label = QLabel(f"\u2205  {S.EMPTY_POSITIONS}")
        self.pos_empty_label.setObjectName("emptyState")
        self.pos_empty_label.setAlignment(Qt.AlignCenter)
        pos_layout.addWidget(self.pos_empty_label)

        splitter.addWidget(pos_card)

        # === 掛單卡片 ===
        orders_card = QFrame()
        orders_card.setObjectName("card")
        orders_layout = QVBoxLayout(orders_card)
        orders_layout.setContentsMargins(16, 12, 16, 12)
        orders_layout.setSpacing(8)

        orders_header_row = QHBoxLayout()
        orders_header = QLabel(f"\u25B7  {S.ORDER_TITLE}")
        orders_header.setObjectName("sectionHeader")
        orders_header_row.addWidget(orders_header)
        orders_header_row.addStretch()
        self.orders_count_label = QLabel("0")
        self.orders_count_label.setObjectName("countBadge")
        orders_header_row.addWidget(self.orders_count_label)
        orders_layout.addLayout(orders_header_row)

        self.orders_table = QTableWidget()
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.setColumnCount(7)
        self.orders_table.setHorizontalHeaderLabels([
            S.POS_TICKET, S.ORDER_TYPE, S.POS_VOLUME,
            S.ORDER_PRICE, S.POS_SL, S.POS_TP, S.POS_COMMENT
        ])
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.orders_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.orders_table.verticalHeader().setVisible(False)
        orders_layout.addWidget(self.orders_table)

        self.orders_empty_label = QLabel(f"\u2205  {S.EMPTY_ORDERS}")
        self.orders_empty_label.setObjectName("emptyState")
        self.orders_empty_label.setAlignment(Qt.AlignCenter)
        orders_layout.addWidget(self.orders_empty_label)

        splitter.addWidget(orders_card)

        layout.addWidget(splitter)

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

        self.positions_table.setSortingEnabled(False)
        self.positions_table.setRowCount(count)

        for row, pos in enumerate(positions):
            ptype = pos.get('type', '')
            direction = S.POS_BUY if ptype in (0, 'buy') else S.POS_SELL
            profit = pos.get('profit', 0)

            values = [
                str(pos.get('ticket', '')),
                direction,
                f"{pos.get('volume', 0):.2f}",
                f"{pos.get('price_open', 0):.2f}",
                f"{pos.get('price_current', 0):.2f}",
                f"{pos.get('sl', 0):.2f}",
                f"{pos.get('tp', 0):.2f}",
                f"{profit:+.2f}",
                pos.get('comment', '')
            ]

            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)

                if col == 1:  # 方向
                    color = QColor(COLORS['profit']) if ptype in (0, 'buy') else QColor(COLORS['loss'])
                    item.setForeground(color)
                elif col == 7:  # 盈虧
                    color = QColor(COLORS['profit']) if profit >= 0 else QColor(COLORS['loss'])
                    item.setForeground(color)

                self.positions_table.setItem(row, col, item)

        self.positions_table.setSortingEnabled(True)

    def update_orders(self, orders: list):
        """更新掛單表格"""
        count = len(orders)
        self.orders_count_label.setText(str(count))

        if count == 0:
            self.orders_table.hide()
            self.orders_empty_label.show()
        else:
            self.orders_empty_label.hide()
            self.orders_table.show()

        self.orders_table.setRowCount(count)

        type_names = {
            2: S.ORDER_BUY_LIMIT, 3: S.ORDER_SELL_LIMIT,
            4: S.ORDER_BUY_STOP, 5: S.ORDER_SELL_STOP
        }

        for row, order in enumerate(orders):
            otype = order.get('type', 0)
            type_name = type_names.get(otype, str(otype))

            values = [
                str(order.get('ticket', '')),
                type_name,
                f"{order.get('volume', 0):.2f}",
                f"{order.get('price', 0):.2f}",
                f"{order.get('sl', 0):.2f}",
                f"{order.get('tp', 0):.2f}",
                order.get('comment', '')
            ]

            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                self.orders_table.setItem(row, col, item)
