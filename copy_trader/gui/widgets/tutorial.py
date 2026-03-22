"""
黃金跟單系統 - 使用教學頁面 (Premium Dark Trading Theme)
"""
import shutil
import logging
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QFileDialog, QMessageBox, QFrame
)
from PySide6.QtCore import Qt

from gui import strings as S
from gui.theme import COLORS

logger = logging.getLogger(__name__)


def _find_ea_source() -> Path:
    """Find EA file — works both in dev and PyInstaller bundle."""
    import sys
    # PyInstaller stores data files relative to sys._MEIPASS (the _internal dir)
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS) / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"
    # Dev mode: relative to this file
    return Path(__file__).parent.parent.parent / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"


EA_SOURCE = _find_ea_source()


class TutorialWidget(QWidget):
    """使用教學頁面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # === 頭部卡片 ===
        header_card = QFrame()
        header_card.setObjectName("card")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title = QLabel(f"\u2B25  {S.TUTORIAL_TITLE}")
        title.setObjectName("sectionHeader")
        title.setStyleSheet(f"color: {COLORS['warning']}; font-size: 15px; font-weight: bold;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        download_btn = QPushButton(S.BTN_DOWNLOAD_EA)
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS['warning']}, stop:1 #d4a20a);
                color: #0a0e17;
                font-weight: bold;
                font-size: 13px;
                padding: 8px 20px;
                border-radius: 8px;
                border: none;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ffd54f, stop:1 {COLORS['warning']});
            }}
        """)
        download_btn.clicked.connect(self._download_ea)
        header_layout.addWidget(download_btn)

        layout.addWidget(header_card)

        # 教學內容
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QLabel()
        content.setTextFormat(Qt.RichText)
        content.setWordWrap(True)
        content.setText(S.TUTORIAL_CONTENT)
        content.setStyleSheet("""
            QLabel {
                padding: 20px;
                font-size: 14px;
                line-height: 1.6;
                background: transparent;
            }
        """)
        content.setAlignment(Qt.AlignTop)

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _download_ea(self):
        """下載 EA 檔案"""
        if not EA_SOURCE.exists():
            QMessageBox.warning(self, S.APP_TITLE, f"找不到 EA 檔案：{EA_SOURCE}")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self, S.EA_SAVE_TITLE,
            "MT5_File_Bridge_Enhanced.mq5",
            "MQL5 檔案 (*.mq5);;所有檔案 (*)"
        )
        if dest:
            try:
                shutil.copy2(str(EA_SOURCE), dest)
                QMessageBox.information(
                    self, S.APP_TITLE,
                    f"{S.EA_SAVE_SUCCESS}\n{dest}"
                )
                logger.info(f"EA file downloaded to: {dest}")
            except Exception as e:
                QMessageBox.warning(self, S.APP_TITLE, f"儲存失敗：{e}")
