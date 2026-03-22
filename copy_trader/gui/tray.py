"""
黃金跟單系統 - 系統列圖示管理
"""
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtCore import Qt

from gui import strings as S


def _create_tray_icon() -> QIcon:
    """建立一個簡單的程式圖示（金色圓形 + Au 文字）"""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # 金色圓形背景
    painter.setBrush(QColor("#f39c12"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)

    # Au 文字
    painter.setPen(QColor("#1a1a2e"))
    font = QFont("Arial", 22, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "Au")

    painter.end()
    return QIcon(pixmap)


class SystemTrayManager:
    """系統列圖示管理"""

    def __init__(self, main_window):
        self.main_window = main_window

        self.tray = QSystemTrayIcon(_create_tray_icon(), main_window)
        self.tray.setToolTip(S.APP_TITLE)

        # 右鍵選單
        menu = QMenu()
        menu.addAction(f"顯示{S.APP_TITLE}", main_window.show_and_raise)
        menu.addSeparator()
        menu.addAction(S.BTN_START, main_window._on_start_clicked)
        menu.addAction(S.BTN_STOP, main_window._on_stop_clicked)
        menu.addSeparator()
        menu.addAction("結束", main_window.quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason):
        """雙擊系統列圖示 → 顯示主視窗"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.main_window.show_and_raise()
