"""
黃金跟單系統 - 日誌處理器
將 Python logging 輸出導向 Qt GUI
"""
import logging
from PySide6.QtCore import QObject, Signal


class LogSignalEmitter(QObject):
    """用來發送日誌信號的 QObject"""
    log_message = Signal(str, str)  # (level, message)


class QLogHandler(logging.Handler):
    """自訂 logging Handler，將日誌轉為 Qt Signal"""

    def __init__(self, emitter: LogSignalEmitter):
        super().__init__()
        self.emitter = emitter
        self.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        ))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.emitter.log_message.emit(record.levelname, msg)
        except Exception:
            pass


def setup_gui_logging(log_viewer_widget) -> QLogHandler:
    """設定 GUI 日誌系統"""
    emitter = LogSignalEmitter()
    handler = QLogHandler(emitter)
    handler.setLevel(logging.DEBUG)

    # 連接到 log viewer
    emitter.log_message.connect(log_viewer_widget.append_log)

    # 加入 root logger
    root = logging.getLogger()
    root.addHandler(handler)

    # 保留檔案 handler
    has_file_handler = any(
        isinstance(h, logging.FileHandler) for h in root.handlers
    )
    if not has_file_handler:
        fh = logging.FileHandler('copy_trader.log', encoding='utf-8')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        root.addHandler(fh)

    return handler
