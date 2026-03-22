"""
黃金跟單系統 - 繁體中文 UI 字串
"""

# 應用程式
APP_TITLE = "黃金跟單系統"
APP_VERSION = "1.0.0"

# 側邊欄
NAV_DASHBOARD = "\u25C8  儀表板"
NAV_SETTINGS = "\u2699  設定"
NAV_POSITIONS = "\u25A4  持倉"
NAV_HISTORY = "\u25F7  歷史"
NAV_LOG = "\u2630  日誌"
NAV_TUTORIAL = "\u2139  教學"

# 側邊欄分組
NAV_GROUP_TRADING = "\u25B8 交易"
NAV_GROUP_SYSTEM = "\u25B8 系統"

# 控制按鈕
BTN_START = "\u25B6  開始交易"
BTN_STOP = "\u25A0  停止交易"
BTN_SAVE = "儲存設定"
BTN_RESET_DEFAULTS = "恢復預設"
BTN_RESET_MARTINGALE = "重置馬丁格爾"
BTN_DOWNLOAD_EA = "下載 EA 檔案"
BTN_DETECT_WINDOWS = "偵測視窗"
BTN_BROWSE = "瀏覽"
BTN_AUTO_DETECT = "自動偵測"
BTN_TEST_CONNECTION = "測試連線"
BTN_CLEAR = "清除"
BTN_EXPORT = "匯出"
BTN_ADD = "新增"
BTN_REMOVE = "移除"
BTN_RESET = "重置"

# 狀態
STATUS_RUNNING = "運行中"
STATUS_STOPPED = "已停止"
STATUS_ERROR = "錯誤"
STATUS_CONNECTED = "已連線"
STATUS_DISCONNECTED = "未連線"

# 儀表板
DASHBOARD_TITLE = "交易概覽"
ACCOUNT_INFO = "帳戶資訊"
BALANCE = "餘額"
EQUITY = "淨值"
MARGIN = "保證金"
FREE_MARGIN = "可用保證金"
PROFIT = "盈虧"
MARTINGALE_STATUS = "馬丁格爾狀態"
CURRENT_LEVEL = "目前層級"
LOT_SIZE = "手數"
CONSECUTIVE_LOSSES = "連續虧損"
TODAY_STATS = "今日統計"
TOTAL_TRADES = "總交易數"
WIN_COUNT = "勝場"
LOSS_COUNT = "敗場"
WIN_RATE = "勝率"
DAILY_PNL = "今日盈虧"
API_CALLS = "API 呼叫"
FILTERED = "已過濾"
UPTIME = "運行時間"

# 空狀態
EMPTY_POSITIONS = "目前無持倉"
EMPTY_ORDERS = "目前無掛單"
EMPTY_HISTORY = "尚無歷史成交"

# 設定分頁
SETTINGS_TRADING = "交易設定"
SETTINGS_CAPTURE = "訊號擷取"
SETTINGS_SAFETY = "安全設定"
SETTINGS_MT5 = "MT5 橋接"

# 交易設定
DEFAULT_LOT_SIZE = "基礎手數"
USE_MARTINGALE = "啟用馬丁格爾"
MARTINGALE_MULTIPLIER = "馬丁倍率"
MARTINGALE_MAX_LEVEL = "最大層級"
MARTINGALE_TABLE_TITLE = "馬丁格爾手數表"
MARTINGALE_COL_LEVEL = "層級"
MARTINGALE_COL_LOT = "手數"
MARTINGALE_COL_CUMULATIVE = "累計手數"
AUTO_EXECUTE = "自動執行交易"
CANCEL_TIMEOUT = "掛單超時 (秒)"
SYMBOL_NAME = "交易品種"

# 訊號擷取設定
PARSER_MODE = "解析模式"
PARSER_REGEX = "Regex (快速, 免費)"
PARSER_GROQ = "Groq LLM"
PARSER_ANTHROPIC = "Anthropic Claude"
API_KEY = "API 金鑰"
CAPTURE_MODE = "擷取模式"
CAPTURE_WINDOW = "視窗擷取"
CAPTURE_REGION = "區域擷取"
CAPTURE_WINDOWS = "擷取視窗"
WINDOW_NAME = "視窗名稱"
APP_NAME = "應用程式"
CAPTURE_INTERVAL = "擷取間隔 (秒)"
OCR_CONFIRM_COUNT = "OCR 確認次數"
OCR_CONFIRM_DELAY = "確認延遲 (秒)"

# 安全設定
MIN_CONFIDENCE = "最低信心度"
MAX_PRICE_DEVIATION = "最大價格偏差"
SIGNAL_DEDUP_MINUTES = "訊號去重時間 (分鐘)"
MAX_DAILY_LOSS = "每日最大虧損 ($)"
MAX_OPEN_POSITIONS = "最大持倉數"

# MT5 設定
MT5_FILES_DIR = "MT5 Files 路徑"
MT5_CONNECTION = "MT5 連線狀態"

# 持倉表格
POS_TICKET = "票號"
POS_DIRECTION = "方向"
POS_VOLUME = "手數"
POS_ENTRY_PRICE = "進場價"
POS_CURRENT_PRICE = "現價"
POS_SL = "止損"
POS_TP = "止盈"
POS_PROFIT = "盈虧"
POS_TIME = "開倉時間"
POS_COMMENT = "備註"
POS_BUY = "買入"
POS_SELL = "賣出"

# 掛單表格
ORDER_TITLE = "掛單"
ORDER_TYPE = "類型"
ORDER_PRICE = "掛單價"
ORDER_BUY_LIMIT = "限價買入"
ORDER_SELL_LIMIT = "限價賣出"
ORDER_BUY_STOP = "停損買入"
ORDER_SELL_STOP = "停損賣出"

# 歷史表格
HISTORY_TITLE = "歷史成交"
HISTORY_EXIT_PRICE = "出場價"
HISTORY_CLOSE_TIME = "平倉時間"
HISTORY_CHANGE = "變動%"

# 歷史篩選
FILTER_TODAY = "今天"
FILTER_THIS_WEEK = "本週"
FILTER_ALL = "全部"
FILTER_FROM = "從"
FILTER_TO = "到"
FILTER_APPLY = "套用"

# 日誌
LOG_TITLE = "系統日誌"
LOG_FILTER_ALL = "全部"
LOG_FILTER_INFO = "INFO 以上"
LOG_FILTER_WARNING = "WARNING 以上"
LOG_FILTER_ERROR = "僅 ERROR"
LOG_AUTO_SCROLL = "自動捲動"

# 教學
TUTORIAL_TITLE = "使用教學"
TUTORIAL_CONTENT = """
<div style="font-family: 'Microsoft JhengHei', sans-serif; color: #e6edf3; line-height: 1.8;">

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟一：安裝 MetaTrader 5</h2>
<ol style="color: #8b949e;">
<li>前往 <b style="color: #e6edf3;">MetaTrader 5</b> 官網下載並安裝</li>
<li>開設模擬帳戶或連接您的經紀商帳戶</li>
<li>確認可以看到 <b style="color: #e6edf3;">XAUUSD</b>（黃金）交易品種</li>
</ol>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟二：安裝 EA（專家顧問）</h2>
<ol style="color: #8b949e;">
<li>點擊上方 <b style="color: #e6edf3;">「下載 EA 檔案」</b> 按鈕，將 <code style="color: #64ffda; background: rgba(21,27,40,0.75); padding: 2px 6px; border-radius: 3px;">.mq5</code> 檔案儲存到電腦</li>
<li>在 MT5 中開啟 <b style="color: #e6edf3;">MetaEditor</b>（按 F4 或點選工具列圖示）</li>
<li>將 <code style="color: #64ffda; background: rgba(21,27,40,0.75); padding: 2px 6px; border-radius: 3px;">.mq5</code> 檔案複製到 <code style="color: #64ffda; background: rgba(21,27,40,0.75); padding: 2px 6px; border-radius: 3px;">MQL5/Experts/</code> 資料夾</li>
<li>在 MetaEditor 中按 <b style="color: #e6edf3;">F7</b> 編譯</li>
<li>回到 MT5 主視窗，在「導航」面板找到編譯好的 EA</li>
</ol>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟三：掛載 EA 到圖表</h2>
<ol style="color: #8b949e;">
<li>開啟一張 <b style="color: #e6edf3;">XAUUSD</b> 圖表</li>
<li>從「導航」面板將 EA 拖曳到圖表上</li>
<li>在彈出的設定視窗中：
    <ul>
    <li>勾選 <b style="color: #e6edf3;">「允許自動交易」</b></li>
    <li>確認 <code style="color: #64ffda; background: rgba(21,27,40,0.75); padding: 2px 6px; border-radius: 3px;">EnableTrading = true</code></li>
    </ul>
</li>
<li>確認 MT5 工具列上的 <b style="color: #e6edf3;">「自動交易」</b> 按鈕已啟用（綠色）</li>
<li>EA 啟動後會開始寫入 JSON 檔案到 <code style="color: #64ffda; background: rgba(21,27,40,0.75); padding: 2px 6px; border-radius: 3px;">MQL5/Files/</code> 資料夾</li>
</ol>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟四：設定擷取視窗</h2>
<ol style="color: #8b949e;">
<li>開啟 <b style="color: #e6edf3;">LINE 桌面版</b>，打開您要跟單的聊天視窗</li>
<li>在本系統的 <b style="color: #e6edf3;">「設定」 > 「訊號擷取」</b> 頁面</li>
<li>點擊 <b style="color: #e6edf3;">「偵測視窗」</b> 按鈕，選擇您的 LINE 聊天視窗</li>
<li>或手動輸入視窗名稱（部分匹配即可）</li>
</ol>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟五：開始交易</h2>
<ol style="color: #8b949e;">
<li>確認 MT5 已連線（狀態列顯示「已連線」）</li>
<li>確認設定無誤（手數、馬丁格爾、安全設定等）</li>
<li>點擊 <b style="color: #e6edf3;">「開始交易」</b> 按鈕</li>
<li>系統將自動：擷取螢幕 > OCR 辨識 > 解析訊號 > 執行交易</li>
<li>在「儀表板」監控即時狀態和統計數據</li>
</ol>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
步驟六：監控與管理</h2>
<ul style="color: #8b949e;">
<li><b style="color: #e6edf3;">儀表板</b>：查看帳戶資訊、馬丁格爾狀態、今日統計、持倉</li>
<li><b style="color: #e6edf3;">持倉</b>：查看所有開倉部位和掛單</li>
<li><b style="color: #e6edf3;">歷史</b>：查看已平倉交易記錄</li>
<li><b style="color: #e6edf3;">日誌</b>：查看系統運行日誌，排查問題</li>
</ul>

<h2 style="color: #64ffda; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px;">
常見問題</h2>

<h3 style="color: #e6edf3;">Q: MT5 顯示「未連線」？</h3>
<p style="color: #8b949e;">確認 MT5 已開啟且 EA 已掛載到 XAUUSD 圖表。檢查「設定」 > 「MT5 橋接」中的路徑是否正確。</p>

<h3 style="color: #e6edf3;">Q: 沒有偵測到訊號？</h3>
<p style="color: #8b949e;">確認 LINE 視窗已開啟，且在「設定」 > 「訊號擷取」中正確配置視窗名稱。查看「日誌」頁面了解詳情。</p>

<h3 style="color: #e6edf3;">Q: 馬丁格爾層級不對？</h3>
<p style="color: #8b949e;">馬丁格爾狀態會自動儲存，重啟後不會遺失。如需手動重置，在「儀表板」點擊「重置馬丁格爾」。</p>

<h3 style="color: #e6edf3;">Q: 如何停止交易？</h3>
<p style="color: #8b949e;">點擊「停止交易」按鈕。已掛出的訂單不會自動取消，需在 MT5 中手動管理。</p>

</div>
"""

# 對話框
CONFIRM_QUIT = "確定要結束程式嗎？"
CONFIRM_QUIT_TITLE = "結束確認"
CONFIRM_RESET = "確定要恢復所有設定為預設值嗎？"
CONFIRM_RESET_TITLE = "恢復預設確認"
CONFIRM_RESET_MARTINGALE = "確定要重置馬丁格爾到第 0 層嗎？"
CONFIRM_RESET_MARTINGALE_TITLE = "重置馬丁格爾"
SETTINGS_SAVED = "設定已儲存"
CONNECTION_OK = "MT5 連線正常"
CONNECTION_FAIL = "MT5 連線失敗，請檢查 EA 是否已掛載"
EA_SAVE_TITLE = "儲存 EA 檔案"
EA_SAVE_SUCCESS = "EA 檔案已儲存至："
