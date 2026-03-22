"""
黃金跟單系統 - 日誌瀏覽器 (Premium Dark Trading Theme)
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QComboBox, QCheckBox, QFileDialog, QLabel, QFrame
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QTextCharFormat, QColor, QFont

from gui import strings as S


# 日誌等級顏色 (updated palette)
LOG_COLORS = {
    "DEBUG": "#484f58",
    "INFO": "#c9d1d9",
    "WARNING": "#f0b90b",
    "ERROR": "#ff5252",
    "CRITICAL": "#ff6b6b",
}

# 等級優先序
LOG_LEVELS = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


class LogViewer(QWidget):
    """日誌瀏覽器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_level = 0
        self._auto_scroll = True
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # === 工具列卡片 ===
        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        toolbar_layout = QHBoxLayout(toolbar_card)
        toolbar_layout.setContentsMargins(16, 10, 16, 10)

        header = QLabel(f"\u2630  {S.LOG_TITLE}")
        header.setObjectName("sectionHeader")
        toolbar_layout.addWidget(header)

        toolbar_layout.addStretch()

        # 篩選
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            S.LOG_FILTER_ALL, S.LOG_FILTER_INFO,
            S.LOG_FILTER_WARNING, S.LOG_FILTER_ERROR
        ])
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar_layout.addWidget(self.filter_combo)

        self.chk_scroll = QCheckBox(S.LOG_AUTO_SCROLL)
        self.chk_scroll.setChecked(True)
        self.chk_scroll.toggled.connect(self._on_scroll_toggled)
        toolbar_layout.addWidget(self.chk_scroll)

        clear_btn = QPushButton(S.BTN_CLEAR)
        clear_btn.clicked.connect(self._on_clear)
        toolbar_layout.addWidget(clear_btn)

        export_btn = QPushButton(S.BTN_EXPORT)
        export_btn.setObjectName("accentOutlineButton")
        export_btn.clicked.connect(self._on_export)
        toolbar_layout.addWidget(export_btn)

        layout.addWidget(toolbar_card)

        # 日誌文字區
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(5000)
        self.text_edit.setFont(QFont("Cascadia Code", 12))
        layout.addWidget(self.text_edit)

    @Slot(str, str)
    def append_log(self, level: str, message: str):
        """新增一筆日誌"""
        level_num = LOG_LEVELS.get(level, 0)
        if level_num < self._min_level:
            return

        color = LOG_COLORS.get(level, "#c9d1d9")

        html = f'<span style="color: {color};">{message}</span>'
        self.text_edit.appendHtml(html)

        if self._auto_scroll:
            scrollbar = self.text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _on_filter_changed(self, index: int):
        """篩選等級變更"""
        level_map = {0: 0, 1: 1, 2: 2, 3: 3}
        self._min_level = level_map.get(index, 0)

    def _on_scroll_toggled(self, checked: bool):
        self._auto_scroll = checked

    def _on_clear(self):
        self.text_edit.clear()

    def _on_export(self):
        """匯出日誌"""
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出日誌", "copy_trader_log.txt",
            "文字檔 (*.txt);;所有檔案 (*)"
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
