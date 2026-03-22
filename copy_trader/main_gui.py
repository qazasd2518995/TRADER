"""
黃金跟單系統 - GUI 啟動入口
"""
import sys
import os
import asyncio
import logging

# 確保能找到模組（開發環境與 PyInstaller 打包後都能正確定位）
if getattr(sys, 'frozen', False):
    # PyInstaller 打包後：模組在 _MEIPASS 內
    _base = sys._MEIPASS
else:
    # 開發環境：main_gui.py 所在目錄就是 copy_trader/
    _base = os.path.dirname(os.path.abspath(__file__))

if _base not in sys.path:
    sys.path.insert(0, _base)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
import qasync

from config import load_config
from gui.theme import DARK_THEME
from gui.main_window import MainWindow
from gui import strings as S


def main():
    # 基礎 logging（GUI handler 會在 MainWindow 初始化時加入）
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('copy_trader.log', encoding='utf-8')
        ]
    )

    # 建立 Qt 應用程式
    app = QApplication(sys.argv)
    app.setApplicationName(S.APP_TITLE)

    # 全域字型
    font = QFont("Microsoft JhengHei", 10)
    app.setFont(font)

    # 套用深色主題
    app.setStyleSheet(DARK_THEME)

    # 建立 qasync 事件迴圈（整合 Qt + asyncio）
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # 載入設定
    config = load_config()

    # 建立主視窗
    window = MainWindow(config, loop)
    window.show()

    # 執行事件迴圈
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
