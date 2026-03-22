"""
黃金跟單系統 - 深色主題 QSS 樣式表 (Premium Dark Trading Theme)
"""

# 配色方案
COLORS = {
    "bg_base": "#0a0e17",         # 最深背景 (navy)
    "bg_sidebar": "#0d1117",      # 側邊欄 (GitHub-dark)
    "surface1": "rgba(21,27,40,0.75)",  # 卡片/面板 (glass)
    "surface2": "#151b28",        # 懸停 (solid fallback)
    "bg_input": "#0c1018",        # 輸入框
    "border": "rgba(255,255,255,0.06)",  # 邊框-淡 (glass)
    "border_strong": "rgba(255,255,255,0.12)",  # 邊框-強
    "accent": "#64ffda",          # 強調色
    "text": "#e6edf3",            # 主文字
    "text_dim": "#8b949e",        # 次要文字
    "text_tertiary": "#484f58",   # 三級文字
    "profit": "#3fb68b",          # 獲利 (TradingView)
    "loss": "#ff5252",            # 虧損
    "warning": "#f0b90b",         # 警告 (Binance gold)
}

DARK_THEME = """
/* ===== 全域 ===== */
QWidget {
    background-color: #0a0e17;
    color: #e6edf3;
    font-family: "Microsoft JhengHei", "微軟正黑體", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* ===== 側邊欄 ===== */
#sidebar {
    background-color: #0d1117;
    border-right: 1px solid rgba(255,255,255,0.06);
}

#sidebarTitle {
    color: #64ffda;
    font-size: 15px;
    font-weight: bold;
    padding: 16px 16px 2px 16px;
    background: transparent;
}

#sidebarVersion {
    color: #484f58;
    font-size: 10px;
    padding: 0px 16px 8px 16px;
    background: transparent;
}

#sidebarBrand {
    background: transparent;
    border: none;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    border-radius: 0;
    padding: 0;
}

#sidebarSectionLabel {
    color: #484f58;
    font-size: 10px;
    font-weight: bold;
    padding: 14px 16px 4px 16px;
    text-transform: uppercase;
    letter-spacing: 1px;
    background: transparent;
}

#sidebar QPushButton {
    border: none;
    border-left: 3px solid transparent;
    padding: 12px 16px;
    border-radius: 0;
    color: #8b949e;
    font-size: 13px;
    text-align: left;
    min-height: 22px;
    background: transparent;
}
#sidebar QPushButton:hover {
    background-color: rgba(255,255,255,0.04);
    color: #e6edf3;
}
#sidebar QPushButton:checked {
    border-left: 3px solid #64ffda;
    color: #64ffda;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(100,255,218,0.10), stop:1 rgba(100,255,218,0.0));
}

/* 交易組導航按鈕 - 較大字體 */
#tradingNavButton {
    font-size: 14px;
    font-weight: bold;
}
/* 系統組導航按鈕 - 較小字體 */
#systemNavButton {
    font-size: 12px;
}

/* 交易分組標題 - 加大加粗 */
#tradingSectionLabel {
    color: #8b949e;
    font-size: 11px;
    font-weight: bold;
    padding: 16px 16px 4px 16px;
    text-transform: uppercase;
    letter-spacing: 1px;
    background: transparent;
}

/* ===== MT5 連線指示燈 ===== */
#mt5Indicator {
    background: transparent;
    border: none;
    padding: 6px 16px;
    font-size: 12px;
    color: #8b949e;
}

/* ===== 開始/停止按鈕 ===== */
#startButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3fb68b, stop:1 #2d9a6f);
    color: #0a0e17;
    font-weight: bold;
    font-size: 13px;
    border: none;
    border-radius: 6px;
    border-left: 0;
    padding: 10px;
    min-height: 36px;
}
#startButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4cc89a, stop:1 #3fb68b);
}
#startButton:disabled {
    background-color: #111b24;
    color: #2d4a3c;
}

#stopButton {
    background-color: transparent;
    color: #ff5252;
    font-weight: bold;
    font-size: 13px;
    border: 1px solid #ff5252;
    border-left: 1px solid #ff5252;
    border-radius: 6px;
    padding: 10px;
    min-height: 36px;
}
#stopButton:hover {
    background-color: rgba(255, 82, 82, 0.10);
}
#stopButton:disabled {
    border-color: #2a1a1e;
    color: #5a2a30;
}

/* ===== 卡片 (QFrame#card) - Glass Morphism ===== */
QFrame#card {
    background-color: rgba(21,27,40,0.75);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 16px;
}

/* ===== 指標卡片 (QFrame#statCard) - Gradient ===== */
QFrame#statCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(21,27,40,0.85), stop:1 rgba(30,40,60,0.65));
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 12px;
}

/* ===== QGroupBox (舊相容) ===== */
QGroupBox {
    background-color: rgba(21,27,40,0.75);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 16px;
    padding-top: 28px;
    margin-top: 8px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 4px 12px;
    color: #64ffda;
    font-size: 13px;
}

/* ===== 段落標題 ===== */
QLabel#sectionHeader {
    color: #e6edf3;
    font-size: 14px;
    font-weight: bold;
    padding: 4px 0;
    background: transparent;
}

/* ===== 指標卡片文字 ===== */
QLabel#statCardTitle {
    color: #484f58;
    font-size: 11px;
    background: transparent;
}
QLabel#statCardValue {
    color: #e6edf3;
    font-size: 18px;
    font-weight: bold;
    background: transparent;
}

/* ===== Hero 價格 ===== */
QLabel#heroPriceValue {
    font-size: 28px;
    font-weight: bold;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    background: transparent;
}

/* ===== 計數徽章 ===== */
QLabel#countBadge {
    background-color: rgba(100,255,218,0.12);
    color: #64ffda;
    font-size: 11px;
    font-weight: bold;
    padding: 2px 10px;
    border-radius: 10px;
    border: none;
}

/* ===== 篩選按鈕 ===== */
QPushButton#filterButton {
    padding: 4px 14px;
    font-size: 12px;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    background: transparent;
    color: #8b949e;
    min-height: 30px;
}
QPushButton#filterButton:hover {
    color: #e6edf3;
    background: transparent;
}
QPushButton#filterButton:checked {
    color: #64ffda;
    border-bottom: 2px solid #64ffda;
    background: transparent;
}

/* ===== 工具列卡片 ===== */
QFrame#toolbarCard {
    background-color: rgba(21,27,40,0.60);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
    padding: 10px 16px;
}

/* ===== 表格 ===== */
QTableWidget {
    background-color: #0a0e17;
    gridline-color: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    selection-background-color: rgba(100,255,218,0.08);
    alternate-background-color: rgba(255,255,255,0.02);
}
QTableWidget::item {
    padding: 6px 10px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
}
QTableWidget::item:selected {
    background-color: rgba(100,255,218,0.08);
}
QTableWidget::item:hover {
    background-color: rgba(255,255,255,0.04);
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(21,27,40,0.90), stop:1 rgba(15,20,30,0.90));
    color: #8b949e;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid rgba(255,255,255,0.04);
    border-bottom: 1px solid rgba(255,255,255,0.08);
    font-weight: bold;
    font-size: 12px;
}
QHeaderView::section:last {
    border-right: none;
}

/* ===== 按鈕 ===== */
QPushButton {
    background-color: rgba(21,27,40,0.75);
    color: #e6edf3;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 13px;
    min-height: 28px;
}
QPushButton:hover {
    background-color: rgba(255,255,255,0.06);
    border-color: rgba(255,255,255,0.12);
}
QPushButton:pressed {
    background-color: rgba(255,255,255,0.02);
}
QPushButton:disabled {
    background-color: rgba(13,17,23,0.5);
    color: #484f58;
    border-color: rgba(255,255,255,0.04);
}

/* ===== 輸入元件 ===== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #0c1018;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 6px 10px;
    color: #e6edf3;
    min-height: 28px;
    selection-background-color: rgba(100,255,218,0.15);
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #64ffda;
}
QComboBox::drop-down {
    border: none;
    width: 30px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #8b949e;
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background-color: #0c1018;
    border: 1px solid rgba(255,255,255,0.08);
    selection-background-color: rgba(100,255,218,0.12);
    color: #e6edf3;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: rgba(21,27,40,0.75);
    border: none;
    width: 20px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: rgba(255,255,255,0.06);
}

/* ===== CheckBox ===== */
QCheckBox {
    spacing: 8px;
    color: #e6edf3;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(255,255,255,0.12);
    border-radius: 4px;
    background-color: #0c1018;
}
QCheckBox::indicator:checked {
    background-color: #64ffda;
    border-color: #64ffda;
}

/* ===== TabWidget - Underline Style ===== */
QTabWidget::pane {
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    background-color: #0a0e17;
    margin-top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: #8b949e;
    padding: 10px 22px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 4px;
}
QTabBar::tab:selected {
    color: #64ffda;
    border-bottom: 2px solid #64ffda;
    background-color: transparent;
}
QTabBar::tab:hover:!selected {
    color: #e6edf3;
    border-bottom: 2px solid rgba(255,255,255,0.12);
    background-color: transparent;
}

/* ===== ScrollBar - Slim ===== */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    border-radius: 3px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.12);
    border-radius: 3px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255,255,255,0.20);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    border-radius: 3px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.12);
    border-radius: 3px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: rgba(255,255,255,0.20);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ===== 狀態列 ===== */
QStatusBar {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0d1117, stop:0.5 #111922, stop:1 #0d1117);
    border-top: 1px solid rgba(255,255,255,0.06);
    color: #8b949e;
    font-size: 12px;
}
QStatusBar QLabel {
    padding: 2px 8px;
    color: #8b949e;
    background: transparent;
}
QStatusBar QLabel#heartbeatLabel {
    font-weight: bold;
    min-width: 120px;
}
QStatusBar QLabel#priceLabel {
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-weight: bold;
    font-size: 12px;
}

/* ===== PlainTextEdit (日誌) ===== */
QPlainTextEdit {
    background-color: #0c1018;
    color: #c9d1d9;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 10px;
    selection-background-color: rgba(100,255,218,0.12);
}

/* ===== ScrollArea ===== */
QScrollArea {
    border: none;
    background-color: #0a0e17;
}

/* ===== Label ===== */
QLabel {
    color: #e6edf3;
    background-color: transparent;
}
QLabel#valueLabel {
    font-size: 18px;
    font-weight: bold;
    color: #64ffda;
}
QLabel#profitLabel {
    color: #3fb68b;
    font-weight: bold;
}
QLabel#lossLabel {
    color: #ff5252;
    font-weight: bold;
}
QLabel#dimLabel {
    color: #8b949e;
    font-size: 11px;
}
QLabel#sectionTitle {
    color: #64ffda;
    font-size: 16px;
    font-weight: bold;
    padding: 4px 0;
}
QLabel#emptyState {
    color: #484f58;
    font-size: 14px;
    padding: 40px;
}

/* ===== Splitter ===== */
QSplitter::handle {
    background-color: rgba(255,255,255,0.06);
}

/* ===== ToolTip ===== */
QToolTip {
    background-color: #151b28;
    color: #e6edf3;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 6px;
    padding: 6px;
}

/* ===== Menu ===== */
QMenu {
    background-color: #151b28;
    border: 1px solid rgba(255,255,255,0.08);
    color: #e6edf3;
    padding: 4px;
    border-radius: 8px;
}
QMenu::item:selected {
    background-color: rgba(255,255,255,0.06);
}
QMenu::separator {
    height: 1px;
    background-color: rgba(255,255,255,0.06);
    margin: 4px 8px;
}

/* ===== 分隔線 ===== */
QFrame#separator {
    background-color: rgba(255,255,255,0.06);
    max-height: 1px;
    min-height: 1px;
}

/* ===== 馬丁格爾危險區域 ===== */
QFrame#dangerCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(255,82,82,0.08), stop:1 rgba(21,27,40,0.75));
    border: 1px solid rgba(255,82,82,0.25);
    border-radius: 10px;
    padding: 16px;
}

/* ===== 匯出按鈕 (accent outline) ===== */
QPushButton#accentOutlineButton {
    background: transparent;
    color: #64ffda;
    border: 1px solid #64ffda;
    border-radius: 8px;
}
QPushButton#accentOutlineButton:hover {
    background: rgba(100,255,218,0.08);
}

/* ===== 儲存按鈕 (green gradient) ===== */
QPushButton#saveButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #3fb68b, stop:1 #2d9a6f);
    color: #0a0e17;
    font-weight: bold;
    border: none;
    border-radius: 8px;
}
QPushButton#saveButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #4cc89a, stop:1 #3fb68b);
}

/* ===== 危險按鈕 (red outline) ===== */
QPushButton#dangerButton {
    background: transparent;
    color: #ff5252;
    border: 1px solid rgba(255,82,82,0.4);
}
QPushButton#dangerButton:hover {
    background: rgba(255,82,82,0.08);
    border-color: #ff5252;
}

/* ===== ListWidget (dialog) ===== */
QListWidget {
    background-color: #0c1018;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
    padding: 4px;
    font-size: 13px;
}
QListWidget::item {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
    color: #e6edf3;
}
QListWidget::item:hover {
    background-color: rgba(255,255,255,0.04);
}
QListWidget::item:selected {
    background-color: rgba(100,255,218,0.08);
}

/* ===== DateEdit ===== */
QDateEdit {
    background-color: #0c1018;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 4px 8px;
    color: #e6edf3;
    min-height: 28px;
}
QDateEdit:focus {
    border: 1px solid #64ffda;
}

/* ===== Calendar popup ===== */
QCalendarWidget {
    background-color: #0c1018;
}
"""
