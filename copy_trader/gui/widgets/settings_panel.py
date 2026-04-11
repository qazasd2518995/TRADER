"""
黃金跟單系統 - 設定面板 (Premium Dark Trading Theme)
"""
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTabWidget, QGroupBox, QLabel, QPushButton,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QScrollArea,
    QDialog, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from gui import strings as S
from gui.theme import COLORS

logger = logging.getLogger(__name__)


class WindowPickerDialog(QDialog):
    """視窗選擇對話框 - 顯示偵測到的視窗供使用者勾選"""

    def __init__(self, windows: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("選擇擷取視窗")
        self.setMinimumSize(560, 480)
        self.resize(560, 480)
        self.selected_windows = []

        self._build_ui(windows)

    def _build_ui(self, windows: list):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 說明
        desc = QLabel("勾選要擷取訊號的視窗，可複選：")
        desc.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 13px;")
        layout.addWidget(desc)

        # 搜尋框
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜尋視窗名稱...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        layout.addWidget(self.search_edit)

        # 視窗列表（使用 theme.py QListWidget 樣式）
        self.list_widget = QListWidget()

        self._all_windows = []
        for w in windows:
            title = w.get('name', '')
            if title and len(title) > 2:
                self._all_windows.append(title)
                item = QListWidgetItem(title)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.list_widget.addItem(item)

        layout.addWidget(self.list_widget, 1)

        # 底部資訊 + 按鈕
        footer = QHBoxLayout()

        self.count_label = QLabel(f"共 {self.list_widget.count()} 個視窗")
        self.count_label.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 12px;")
        footer.addWidget(self.count_label)

        footer.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        confirm_btn = QPushButton("確定加入")
        confirm_btn.setObjectName("saveButton")
        confirm_btn.clicked.connect(self._on_confirm)
        footer.addWidget(confirm_btn)

        layout.addLayout(footer)

    def _on_search(self, text: str):
        """即時過濾視窗列表"""
        keyword = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            match = keyword in item.text().lower() if keyword else True
            item.setHidden(not match)

    def _on_confirm(self):
        """收集勾選的視窗並關閉"""
        self.selected_windows = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                self.selected_windows.append(item.text())
        self.accept()


class MartingaleTableDialog(QDialog):
    """馬丁格爾手數表 - 獨立視窗（唯讀預覽）"""

    def __init__(self, base_lot: float, multiplier: float, max_level: int,
                 current_level: int = -1, lots_list: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(S.MARTINGALE_TABLE_TITLE)
        self.setMinimumSize(420, 400)
        self.resize(420, min(400, 120 + (max_level + 1) * 36))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 標題
        header = QLabel(S.MARTINGALE_TABLE_TITLE)
        header.setObjectName("sectionHeader")
        layout.addWidget(header)

        if lots_list:
            summary = QLabel(f"自訂手數  |  共 {len(lots_list)} 層")
        else:
            summary = QLabel(
                f"基礎手數: {base_lot:.2f}  |  倍率: x{multiplier:.1f}  |  最大層級: {max_level}"
            )
        summary.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        layout.addWidget(summary)

        # 表格
        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels([
            S.MARTINGALE_COL_LEVEL,
            S.MARTINGALE_COL_LOT,
            S.MARTINGALE_COL_CUMULATIVE
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)

        num_levels = len(lots_list) if lots_list else (max_level + 1)
        table.setRowCount(num_levels)

        cumulative = 0.0
        for level in range(num_levels):
            if lots_list:
                lot = lots_list[level]
            else:
                lot = round(base_lot * (multiplier ** level), 2)
            cumulative += lot

            level_item = QTableWidgetItem(str(level + 1))
            lot_item = QTableWidgetItem(f"{lot:.2f}")
            cum_item = QTableWidgetItem(f"{cumulative:.2f}")

            for item in [level_item, lot_item, cum_item]:
                item.setTextAlignment(Qt.AlignCenter)

            if level == current_level:
                highlight = QColor(COLORS['surface2'])
                accent = QColor(COLORS['accent'])
                for item in [level_item, lot_item, cum_item]:
                    item.setBackground(highlight)
                    item.setForeground(accent)
                    item.setFont(QFont("Microsoft JhengHei", 12, QFont.Bold))

            table.setItem(level, 0, level_item)
            table.setItem(level, 1, lot_item)
            table.setItem(level, 2, cum_item)

        for row in range(table.rowCount()):
            table.setRowHeight(row, 32)

        layout.addWidget(table, 1)

        # 關閉按鈕
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


def _make_section(title: str) -> tuple:
    """Create a card-style section with header + separator, returns (frame, inner_layout)"""
    card = QFrame()
    card.setObjectName("card")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(16, 14, 16, 14)
    inner.setSpacing(12)

    header = QLabel(title)
    header.setObjectName("sectionHeader")
    inner.addWidget(header)

    sep = QFrame()
    sep.setObjectName("separator")
    sep.setFrameShape(QFrame.HLine)
    inner.addWidget(sep)

    return card, inner


class SettingsPanel(QWidget):
    """設定面板"""

    config_saved = Signal(object)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._build_ui()
        self._load_from_config(config)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)

        # 分頁
        self.tabs = QTabWidget()

        self.tabs.addTab(self._build_trading_tab(), S.SETTINGS_TRADING)
        self.tabs.addTab(self._build_capture_tab(), S.SETTINGS_CAPTURE)
        self.tabs.addTab(self._build_safety_tab(), S.SETTINGS_SAFETY)
        self.tabs.addTab(self._build_mt5_tab(), S.SETTINGS_MT5)

        layout.addWidget(self.tabs)

        # 底部按鈕
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton(S.BTN_RESET_DEFAULTS)
        reset_btn.setObjectName("dangerButton")
        reset_btn.clicked.connect(self._on_reset_defaults)
        btn_row.addWidget(reset_btn)

        save_btn = QPushButton(S.BTN_SAVE)
        save_btn.setObjectName("saveButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _build_trading_tab(self) -> QWidget:
        """交易設定分頁"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # 基礎設定
        basic_card, basic_inner = _make_section(S.SETTINGS_TRADING)
        form = QFormLayout()
        form.setSpacing(12)

        self.spin_lot = QDoubleSpinBox()
        self.spin_lot.setRange(0.01, 10.0)
        self.spin_lot.setSingleStep(0.01)
        self.spin_lot.setDecimals(2)
        form.addRow(S.DEFAULT_LOT_SIZE, self.spin_lot)

        self.edit_symbol = QLineEdit()
        form.addRow(S.SYMBOL_NAME, self.edit_symbol)

        self.chk_auto_execute = QCheckBox()
        form.addRow(S.AUTO_EXECUTE, self.chk_auto_execute)

        self.spin_cancel_timeout = QSpinBox()
        self.spin_cancel_timeout.setRange(60, 86400)
        self.spin_cancel_timeout.setSuffix(" 秒")
        form.addRow(S.CANCEL_TIMEOUT, self.spin_cancel_timeout)

        basic_inner.addLayout(form)
        layout.addWidget(basic_card)

        # 馬丁格爾設定
        mg_card, mg_inner = _make_section(S.MARTINGALE_STATUS)

        mg_form = QFormLayout()
        mg_form.setSpacing(12)

        self.chk_martingale = QCheckBox()
        mg_form.addRow(S.USE_MARTINGALE, self.chk_martingale)

        mg_inner.addLayout(mg_form)

        # 可編輯馬丁手數表格
        mg_table_label = QLabel("每層手數設定：")
        mg_table_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        mg_inner.addWidget(mg_table_label)

        self.mg_table = QTableWidget()
        self.mg_table.setAlternatingRowColors(True)
        self.mg_table.setColumnCount(2)
        self.mg_table.setHorizontalHeaderLabels([
            S.MARTINGALE_COL_LEVEL, S.MARTINGALE_COL_LOT
        ])
        self.mg_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mg_table.verticalHeader().setVisible(False)
        self.mg_table.setMaximumHeight(220)
        mg_inner.addWidget(self.mg_table)

        # 新增/移除按鈕
        mg_btn_row = QHBoxLayout()
        mg_btn_row.setSpacing(8)

        add_level_btn = QPushButton(S.BTN_ADD + "層級")
        add_level_btn.clicked.connect(self._add_martingale_level)
        mg_btn_row.addWidget(add_level_btn)

        remove_level_btn = QPushButton(S.BTN_REMOVE + "最後")
        remove_level_btn.setObjectName("dangerButton")
        remove_level_btn.clicked.connect(self._remove_martingale_level)
        mg_btn_row.addWidget(remove_level_btn)

        mg_btn_row.addStretch()

        # 查看手數表按鈕
        show_table_btn = QPushButton(S.MARTINGALE_TABLE_TITLE)
        show_table_btn.setObjectName("accentOutlineButton")
        show_table_btn.clicked.connect(self._show_martingale_table)
        mg_btn_row.addWidget(show_table_btn)

        mg_inner.addLayout(mg_btn_row)

        layout.addWidget(mg_card)
        layout.addStretch()

        scroll.setWidget(widget)

        return scroll

    def _build_capture_tab(self) -> QWidget:
        """訊號擷取設定分頁"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        # 解析設定
        parser_card, parser_inner = _make_section(S.PARSER_MODE)
        pf = QFormLayout()
        pf.setSpacing(12)

        self.combo_parser = QComboBox()
        self.combo_parser.addItems([S.PARSER_REGEX, S.PARSER_GROQ, S.PARSER_ANTHROPIC])
        pf.addRow(S.PARSER_MODE, self.combo_parser)

        self.edit_groq_key = QLineEdit()
        self.edit_groq_key.setEchoMode(QLineEdit.Password)
        self.edit_groq_key.setPlaceholderText("gsk_...")
        pf.addRow(f"Groq {S.API_KEY}", self.edit_groq_key)

        self.edit_anthropic_key = QLineEdit()
        self.edit_anthropic_key.setEchoMode(QLineEdit.Password)
        self.edit_anthropic_key.setPlaceholderText("sk-ant-...")
        pf.addRow(f"Anthropic {S.API_KEY}", self.edit_anthropic_key)

        parser_inner.addLayout(pf)
        layout.addWidget(parser_card)

        # === 擷取視窗設定 ===
        capture_card, capture_inner = _make_section(S.CAPTURE_WINDOWS)

        # 說明文字
        hint = QLabel("已選擇要監控的視窗（點擊「偵測視窗」可從桌面視窗中挑選）：")
        hint.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        hint.setWordWrap(True)
        capture_inner.addWidget(hint)

        # 已選擇視窗列表 (uses theme.py QListWidget styles)
        self.windows_list = QListWidget()
        self.windows_list.setMinimumHeight(120)
        self.windows_list.setMaximumHeight(200)
        capture_inner.addWidget(self.windows_list)

        # 空狀態提示
        self.windows_empty_label = QLabel("尚未選擇任何視窗")
        self.windows_empty_label.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 12px; padding: 8px;"
        )
        self.windows_empty_label.setAlignment(Qt.AlignCenter)
        capture_inner.addWidget(self.windows_empty_label)

        # 按鈕列
        win_btns = QHBoxLayout()
        win_btns.setSpacing(8)

        detect_btn = QPushButton(S.BTN_DETECT_WINDOWS)
        detect_btn.setObjectName("accentOutlineButton")
        detect_btn.clicked.connect(self._detect_windows)
        win_btns.addWidget(detect_btn)

        add_btn = QPushButton("手動新增")
        add_btn.clicked.connect(self._add_window_manual)
        win_btns.addWidget(add_btn)

        win_btns.addStretch()

        remove_btn = QPushButton("移除選取")
        remove_btn.setObjectName("dangerButton")
        remove_btn.clicked.connect(self._remove_selected_window)
        win_btns.addWidget(remove_btn)

        capture_inner.addLayout(win_btns)
        layout.addWidget(capture_card)

        # OCR 設定
        ocr_card, ocr_inner = _make_section("OCR 設定")
        of = QFormLayout()
        of.setSpacing(12)

        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.5, 10.0)
        self.spin_interval.setSingleStep(0.5)
        self.spin_interval.setSuffix(" 秒")
        of.addRow(S.CAPTURE_INTERVAL, self.spin_interval)

        self.spin_confirm_count = QSpinBox()
        self.spin_confirm_count.setRange(1, 5)
        of.addRow(S.OCR_CONFIRM_COUNT, self.spin_confirm_count)

        self.spin_confirm_delay = QDoubleSpinBox()
        self.spin_confirm_delay.setRange(0.5, 5.0)
        self.spin_confirm_delay.setSingleStep(0.5)
        self.spin_confirm_delay.setSuffix(" 秒")
        of.addRow(S.OCR_CONFIRM_DELAY, self.spin_confirm_delay)

        ocr_inner.addLayout(of)
        layout.addWidget(ocr_card)
        layout.addStretch()

        scroll.setWidget(widget)
        return scroll

    def _build_safety_tab(self) -> QWidget:
        """安全設定分頁"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        safety_card, safety_inner = _make_section(S.SETTINGS_SAFETY)
        form = QFormLayout()
        form.setSpacing(12)

        self.spin_confidence = QDoubleSpinBox()
        self.spin_confidence.setRange(0.5, 1.0)
        self.spin_confidence.setSingleStep(0.05)
        self.spin_confidence.setDecimals(2)
        form.addRow(S.MIN_CONFIDENCE, self.spin_confidence)

        self.spin_deviation = QDoubleSpinBox()
        self.spin_deviation.setRange(0.001, 0.1)
        self.spin_deviation.setSingleStep(0.005)
        self.spin_deviation.setDecimals(3)
        form.addRow(S.MAX_PRICE_DEVIATION, self.spin_deviation)

        self.spin_dedup = QSpinBox()
        self.spin_dedup.setRange(1, 60)
        self.spin_dedup.setSuffix(" 分鐘")
        form.addRow(S.SIGNAL_DEDUP_MINUTES, self.spin_dedup)

        self.spin_daily_loss = QDoubleSpinBox()
        self.spin_daily_loss.setRange(10, 100000)
        self.spin_daily_loss.setSingleStep(50)
        self.spin_daily_loss.setPrefix("$ ")
        form.addRow(S.MAX_DAILY_LOSS, self.spin_daily_loss)

        self.spin_max_positions = QSpinBox()
        self.spin_max_positions.setRange(1, 50)
        form.addRow(S.MAX_OPEN_POSITIONS, self.spin_max_positions)

        safety_inner.addLayout(form)
        layout.addWidget(safety_card)
        layout.addStretch()
        return widget

    def _build_mt5_tab(self) -> QWidget:
        """MT5 橋接設定分頁"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(14)

        mt5_card, mt5_inner = _make_section(S.SETTINGS_MT5)

        # 路徑
        path_row = QHBoxLayout()
        self.edit_mt5_dir = QLineEdit()
        path_row.addWidget(QLabel(S.MT5_FILES_DIR))
        path_row.addWidget(self.edit_mt5_dir, 1)

        browse_btn = QPushButton(S.BTN_BROWSE)
        browse_btn.clicked.connect(self._browse_mt5_dir)
        path_row.addWidget(browse_btn)

        auto_btn = QPushButton(S.BTN_AUTO_DETECT)
        auto_btn.clicked.connect(self._auto_detect_mt5)
        path_row.addWidget(auto_btn)

        mt5_inner.addLayout(path_row)

        # 測試連線
        test_btn = QPushButton(S.BTN_TEST_CONNECTION)
        test_btn.setObjectName("accentOutlineButton")
        test_btn.clicked.connect(self._test_connection)
        mt5_inner.addWidget(test_btn)

        self.lbl_connection = QLabel("")
        mt5_inner.addWidget(self.lbl_connection)

        layout.addWidget(mt5_card)
        layout.addStretch()
        return widget

    # === 資料操作 ===

    def _load_from_config(self, config):
        """從 Config 載入到 UI"""
        self.spin_lot.setValue(config.default_lot_size)
        self.edit_symbol.setText(getattr(config, 'symbol_name', 'XAUUSD'))
        self.chk_auto_execute.setChecked(config.auto_execute)
        self.spin_cancel_timeout.setValue(config.cancel_pending_after_seconds)

        self.chk_martingale.setChecked(config.use_martingale)

        # 載入馬丁手數表
        lots = getattr(config, 'martingale_lots', [])
        if not lots:
            lots = [
                round(config.default_lot_size * (config.martingale_multiplier ** i), 2)
                for i in range(config.martingale_max_level + 1)
            ]
        self._populate_martingale_table(lots)

        # 解析模式
        mode_map = {"regex": 0, "groq": 1, "anthropic": 2}
        self.combo_parser.setCurrentIndex(mode_map.get(config.parser_mode, 0))
        self.edit_groq_key.setText(config.groq_api_key)
        self.edit_anthropic_key.setText(config.anthropic_api_key)

        # 擷取
        self.spin_interval.setValue(config.capture_interval)
        self.spin_confirm_count.setValue(config.ocr_confirm_count)
        self.spin_confirm_delay.setValue(config.ocr_confirm_delay)

        # 視窗
        self.windows_list.clear()
        for w in config.capture_windows:
            self.windows_list.addItem(f"{w.window_name}  |  {w.app_name}")
        self._update_windows_empty_state()

        # 安全
        self.spin_confidence.setValue(config.min_confidence)
        self.spin_deviation.setValue(config.max_price_deviation)
        self.spin_dedup.setValue(config.signal_dedup_minutes)
        self.spin_daily_loss.setValue(config.max_daily_loss)
        self.spin_max_positions.setValue(config.max_open_positions)

        # MT5
        self.edit_mt5_dir.setText(config.mt5_files_dir)

    def get_current_config(self):
        """從 UI 建構 Config 物件"""
        from config import Config, CaptureWindow

        mode_map = {0: "regex", 1: "groq", 2: "anthropic"}

        # 從 windows_list 解析視窗
        windows = []
        for i in range(self.windows_list.count()):
            text = self.windows_list.item(i).text()
            parts = text.split("  |  ", 1)
            win_name = parts[0].strip()
            app_name = parts[1].strip() if len(parts) > 1 else "LINE"
            if win_name:
                windows.append(CaptureWindow(
                    window_name=win_name,
                    app_name=app_name,
                    name=f"win_{i}"
                ))

        config = Config()
        config.default_lot_size = self.spin_lot.value()
        config.auto_execute = self.chk_auto_execute.isChecked()
        config.cancel_pending_after_seconds = self.spin_cancel_timeout.value()
        config.use_martingale = self.chk_martingale.isChecked()

        # 從表格讀取自訂手數
        lots = self._get_martingale_lots_from_table()
        config.martingale_lots = lots
        config.martingale_max_level = max(len(lots) - 1, 1) if lots else 4
        config.parser_mode = mode_map.get(self.combo_parser.currentIndex(), "regex")
        config.groq_api_key = self.edit_groq_key.text()
        config.anthropic_api_key = self.edit_anthropic_key.text()
        config.capture_interval = self.spin_interval.value()
        config.capture_windows = windows
        config.ocr_confirm_count = self.spin_confirm_count.value()
        config.ocr_confirm_delay = self.spin_confirm_delay.value()
        config.min_confidence = self.spin_confidence.value()
        config.max_price_deviation = self.spin_deviation.value()
        config.signal_dedup_minutes = self.spin_dedup.value()
        config.max_daily_loss = self.spin_daily_loss.value()
        config.max_open_positions = self.spin_max_positions.value()
        config.mt5_files_dir = self.edit_mt5_dir.text()

        return config

    def _on_save(self):
        """儲存設定"""
        config = self.get_current_config()
        try:
            from config import save_config
            save_config(config)
            self.config = config
            self.config_saved.emit(config)
            QMessageBox.information(self, S.APP_TITLE, S.SETTINGS_SAVED)
        except Exception as e:
            QMessageBox.warning(self, S.APP_TITLE, f"儲存失敗：{e}")

    def _on_reset_defaults(self):
        """恢復預設"""
        reply = QMessageBox.question(
            self, S.CONFIRM_RESET_TITLE, S.CONFIRM_RESET,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            from config import Config
            self._load_from_config(Config())

    def _populate_martingale_table(self, lots: list):
        """填充馬丁手數表格"""
        self.mg_table.setRowCount(len(lots))
        for i, lot in enumerate(lots):
            # 層級（唯讀）
            level_item = QTableWidgetItem(str(i))
            level_item.setTextAlignment(Qt.AlignCenter)
            level_item.setFlags(level_item.flags() & ~Qt.ItemIsEditable)
            self.mg_table.setItem(i, 0, level_item)

            # 手數（可編輯）
            lot_item = QTableWidgetItem(f"{lot:.2f}")
            lot_item.setTextAlignment(Qt.AlignCenter)
            self.mg_table.setItem(i, 1, lot_item)

    def _get_martingale_lots_from_table(self) -> list:
        """從表格讀取手數列表"""
        lots = []
        for i in range(self.mg_table.rowCount()):
            item = self.mg_table.item(i, 1)
            if item:
                try:
                    val = float(item.text())
                    lots.append(round(val, 2))
                except ValueError:
                    lots.append(0.01)
        return lots

    def _add_martingale_level(self):
        """新增一層馬丁格爾"""
        row = self.mg_table.rowCount()
        last_lot = 0.01
        if row > 0:
            item = self.mg_table.item(row - 1, 1)
            if item:
                try:
                    last_lot = float(item.text()) * 2
                except ValueError:
                    pass

        self.mg_table.setRowCount(row + 1)

        level_item = QTableWidgetItem(str(row))
        level_item.setTextAlignment(Qt.AlignCenter)
        level_item.setFlags(level_item.flags() & ~Qt.ItemIsEditable)
        self.mg_table.setItem(row, 0, level_item)

        lot_item = QTableWidgetItem(f"{last_lot:.2f}")
        lot_item.setTextAlignment(Qt.AlignCenter)
        self.mg_table.setItem(row, 1, lot_item)

    def _remove_martingale_level(self):
        """移除最後一層"""
        rows = self.mg_table.rowCount()
        if rows > 1:
            self.mg_table.setRowCount(rows - 1)

    def _show_martingale_table(self):
        """開啟馬丁格爾手數表對話框（唯讀預覽）"""
        lots = self._get_martingale_lots_from_table()
        dialog = MartingaleTableDialog(
            base_lot=lots[0] if lots else self.spin_lot.value(),
            multiplier=1.0,
            max_level=len(lots) - 1 if lots else 4,
            lots_list=lots,
            parent=self
        )
        dialog.exec()

    # === 視窗操作 ===

    def _update_windows_empty_state(self):
        """更新空狀態顯示"""
        has_items = self.windows_list.count() > 0
        self.windows_list.setVisible(has_items)
        self.windows_empty_label.setVisible(not has_items)

    def _add_window_manual(self):
        """手動新增一個視窗"""
        self.windows_list.addItem("  |  LINE")
        self._update_windows_empty_state()
        new_item = self.windows_list.item(self.windows_list.count() - 1)
        new_item.setFlags(new_item.flags() | Qt.ItemIsEditable)
        self.windows_list.setCurrentItem(new_item)
        self.windows_list.editItem(new_item)

    def _remove_selected_window(self):
        """移除選取的視窗"""
        row = self.windows_list.currentRow()
        if row >= 0:
            self.windows_list.takeItem(row)
            self._update_windows_empty_state()

    def _detect_windows(self):
        """偵測所有可見視窗 - 開啟選擇對話框"""
        try:
            from signal_capture.screen_capture import list_app_windows
            windows = list_app_windows("")
            if not windows:
                QMessageBox.information(self, S.APP_TITLE, "未找到任何視窗")
                return

            dialog = WindowPickerDialog(windows, self)
            if dialog.exec() == QDialog.Accepted and dialog.selected_windows:
                for title in dialog.selected_windows:
                    already = False
                    for i in range(self.windows_list.count()):
                        if title in self.windows_list.item(i).text():
                            already = True
                            break
                    if not already:
                        self.windows_list.addItem(f"{title}  |  LINE")
                self._update_windows_empty_state()

        except Exception as e:
            QMessageBox.warning(self, S.APP_TITLE, f"偵測視窗失敗：{e}")

    def _browse_mt5_dir(self):
        """瀏覽 MT5 目錄"""
        path = QFileDialog.getExistingDirectory(self, S.MT5_FILES_DIR)
        if path:
            self.edit_mt5_dir.setText(path)

    def _auto_detect_mt5(self):
        """自動偵測 MT5 目錄"""
        from config import Config
        temp = Config()
        detected = temp._find_mt5_files_dir()
        self.edit_mt5_dir.setText(detected)

    def _test_connection(self):
        """測試 MT5 連線"""
        import os
        import time
        path = self.edit_mt5_dir.text()
        price_file = os.path.join(path, "XAUUSD_price.json")

        if os.path.exists(price_file):
            age = time.time() - os.path.getmtime(price_file)
            if age < 30:
                self.lbl_connection.setText(f"{S.CONNECTION_OK} ({age:.0f}秒前更新)")
                self.lbl_connection.setStyleSheet(f"color: {COLORS['profit']};")
            else:
                self.lbl_connection.setText(f"價格檔案已過期 ({age:.0f}秒)")
                self.lbl_connection.setStyleSheet(f"color: {COLORS['warning']};")
        else:
            self.lbl_connection.setText(S.CONNECTION_FAIL)
            self.lbl_connection.setStyleSheet(f"color: {COLORS['loss']};")
