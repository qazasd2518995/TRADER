/* Localized strings — ported from gui/strings.py */

export const S = {
  // App
  APP_TITLE: "黃金跟單系統",
  APP_VERSION: "1.0.0",

  // Sidebar
  NAV_DASHBOARD: "儀表板",
  NAV_POSITIONS: "持倉",
  NAV_HISTORY: "歷史",
  NAV_SETTINGS: "設定",
  NAV_LOG: "日誌",
  NAV_TUTORIAL: "教學",
  NAV_GROUP_TRADING: "交易",
  NAV_GROUP_SYSTEM: "系統",

  // Buttons
  BTN_START: "開始交易",
  BTN_STOP: "停止交易",
  BTN_SAVE: "儲存設定",
  BTN_RESET_DEFAULTS: "恢復預設",
  BTN_RESET_MARTINGALE: "重置馬丁格爾",
  BTN_DOWNLOAD_EA: "下載 EA 檔案",
  BTN_DETECT_WINDOWS: "偵測視窗",
  BTN_BROWSE: "瀏覽",
  BTN_AUTO_DETECT: "自動偵測",
  BTN_TEST_CONNECTION: "測試連線",
  BTN_CLEAR: "清除",
  BTN_EXPORT: "匯出",
  BTN_ADD: "新增",
  BTN_REMOVE: "移除",
  BTN_RESET: "重置",

  // Status
  STATUS_RUNNING: "運行中",
  STATUS_STOPPED: "已停止",
  STATUS_ERROR: "錯誤",
  STATUS_CONNECTED: "已連線",
  STATUS_DISCONNECTED: "未連線",

  // Dashboard
  DASHBOARD_TITLE: "交易概覽",
  BALANCE: "餘額",
  EQUITY: "淨值",
  MARGIN: "保證金",
  FREE_MARGIN: "可用保證金",
  PROFIT: "盈虧",
  MARTINGALE_STATUS: "馬丁格爾狀態",
  CURRENT_LEVEL: "目前層級",
  LOT_SIZE: "手數",
  CONSECUTIVE_LOSSES: "連續虧損",
  TODAY_STATS: "今日統計",
  TOTAL_TRADES: "總交易數",
  WIN_COUNT: "勝場",
  LOSS_COUNT: "敗場",
  WIN_RATE: "勝率",
  DAILY_PNL: "今日盈虧",
  API_CALLS: "API 呼叫",
  UPTIME: "運行時間",

  // Empty
  EMPTY_POSITIONS: "目前無持倉",
  EMPTY_ORDERS: "目前無掛單",
  EMPTY_HISTORY: "尚無歷史成交",

  // Settings tabs
  SETTINGS_TRADING: "交易設定",
  SETTINGS_CAPTURE: "訊號擷取",
  SETTINGS_SAFETY: "安全設定",
  SETTINGS_MT5: "MT5 橋接",

  // Trading settings
  DEFAULT_LOT_SIZE: "基礎手數",
  USE_MARTINGALE: "啟用馬丁格爾",
  MARTINGALE_TABLE_TITLE: "馬丁格爾手數表",
  MARTINGALE_COL_LEVEL: "層級",
  MARTINGALE_COL_LOT: "手數",
  MARTINGALE_COL_CUMULATIVE: "累計手數",
  AUTO_EXECUTE: "自動執行交易",
  CANCEL_TIMEOUT: "掛單超時 (秒)",
  SYMBOL_NAME: "交易品種",

  // Capture settings
  PARSER_MODE: "解析模式",
  PARSER_REGEX: "Regex (快速, 免費)",
  PARSER_GROQ: "Groq LLM",
  PARSER_ANTHROPIC: "Anthropic Claude",
  API_KEY: "API 金鑰",
  CAPTURE_WINDOWS: "擷取視窗",
  CAPTURE_INTERVAL: "擷取間隔 (秒)",
  OCR_CONFIRM_COUNT: "OCR 確認次數",
  OCR_CONFIRM_DELAY: "確認延遲 (秒)",

  // Safety
  MIN_CONFIDENCE: "最低信心度",
  MAX_PRICE_DEVIATION: "最大價格偏差",
  SIGNAL_DEDUP_MINUTES: "訊號去重時間 (分鐘)",
  MAX_DAILY_LOSS: "每日最大虧損 ($)",
  MAX_OPEN_POSITIONS: "最大持倉數",

  // MT5
  MT5_FILES_DIR: "MT5 Files 路徑",
  MT5_CONNECTION: "MT5 連線狀態",

  // Positions
  POS_TICKET: "票號",
  POS_DIRECTION: "方向",
  POS_VOLUME: "手數",
  POS_ENTRY_PRICE: "進場價",
  POS_CURRENT_PRICE: "現價",
  POS_SL: "止損",
  POS_TP: "止盈",
  POS_PROFIT: "盈虧",
  POS_TIME: "開倉時間",
  POS_COMMENT: "備註",
  POS_BUY: "買入",
  POS_SELL: "賣出",

  // Orders
  ORDER_TITLE: "掛單",
  ORDER_TYPE: "類型",
  ORDER_PRICE: "掛單價",
  ORDER_BUY_LIMIT: "限價買入",
  ORDER_SELL_LIMIT: "限價賣出",
  ORDER_BUY_STOP: "停損買入",
  ORDER_SELL_STOP: "停損賣出",

  // History
  HISTORY_TITLE: "歷史成交",
  HISTORY_EXIT_PRICE: "出場價",
  HISTORY_CLOSE_TIME: "平倉時間",
  HISTORY_CHANGE: "變動%",
  FILTER_TODAY: "今天",
  FILTER_THIS_WEEK: "本週",
  FILTER_ALL: "全部",
  FILTER_FROM: "從",
  FILTER_TO: "到",
  FILTER_APPLY: "套用",

  // Logs
  LOG_TITLE: "系統日誌",
  LOG_FILTER_ALL: "全部",
  LOG_FILTER_INFO: "INFO 以上",
  LOG_FILTER_WARNING: "WARNING 以上",
  LOG_FILTER_ERROR: "僅 ERROR",
  LOG_AUTO_SCROLL: "自動捲動",

  // Tutorial
  TUTORIAL_TITLE: "使用教學",

  // Partial Close (Multi-TP)
  PARTIAL_CLOSE_TITLE: "多 TP 分批平倉",
  PARTIAL_CLOSE_TP: "TP",
  PARTIAL_CLOSE_RATIO: "平倉比例",
  PARTIAL_CLOSE_PREVIEW: "預覽",
  PARTIAL_CLOSE_REMAINING: "剩餘",
  PARTIAL_CLOSE_LAST_TP_NOTE: "最後一個 TP 由 MT5 自動平倉剩餘部位",
  PARTIAL_CLOSE_TOTAL_ERROR: "比例總和不可超過 100%",

  // Dialogs
  CONFIRM_QUIT: "確定要結束程式嗎？",
  CONFIRM_RESET: "確定要恢復所有設定為預設值嗎？",
  CONFIRM_RESET_MARTINGALE: "確定要重置馬丁格爾到第 1 層嗎？",
  SETTINGS_SAVED: "設定已儲存",
  CONNECTION_OK: "MT5 連線正常",
  CONNECTION_FAIL: "MT5 連線失敗，請檢查 EA 是否已掛載",

  // Auth — Sidebar
  NAV_PROFILE: "會員",
  NAV_ADMIN: "管理",
  NAV_GROUP_ACCOUNT: "帳號",

  // Auth — Login
  AUTH_LOGIN_TITLE: "會員登入",
  AUTH_EMAIL: "電子郵件",
  AUTH_PASSWORD: "密碼",
  AUTH_LOGIN_BTN: "登入",
  AUTH_LOGGING_IN: "登入中...",
  AUTH_LOGIN_FAILED: "登入失敗",
  AUTH_LOADING: "驗證中...",
  AUTH_LOGOUT: "登出",

  // Auth — Profile
  AUTH_PROFILE_TITLE: "會員資料",
  AUTH_MEMBER_INFO: "基本資料",
  AUTH_DISPLAY_NAME: "顯示名稱",
  AUTH_SUBSCRIPTION_INFO: "訂閱狀態",
  AUTH_PLAN: "訂閱方案",
  AUTH_STATUS: "狀態",
  AUTH_STARTS_AT: "開始日期",
  AUTH_EXPIRES_AT: "到期日期",
  AUTH_DAYS_REMAINING: "剩餘天數",
  AUTH_DAYS: "天",

  // Auth — Plans
  AUTH_PLAN_TRIAL: "試用版",
  AUTH_PLAN_STANDARD: "標準版",
  AUTH_PLAN_PREMIUM: "進階版",

  // Auth — Status
  AUTH_STATUS_ACTIVE: "有效",
  AUTH_STATUS_EXPIRED: "已過期",
  AUTH_STATUS_REVOKED: "已停用",

  // Auth — Subscription
  AUTH_SUBSCRIPTION_EXPIRED: "訂閱已過期",
  AUTH_CONTACT_ADMIN: "請聯繫管理員續約或升級方案。",

  // Auth — Password
  AUTH_CHANGE_PASSWORD: "修改密碼",
  AUTH_OLD_PASSWORD: "舊密碼",
  AUTH_NEW_PASSWORD: "新密碼",
  AUTH_CONFIRM_PASSWORD: "確認新密碼",
  AUTH_CHANGE_PASSWORD_BTN: "變更密碼",
  AUTH_PW_MISMATCH: "兩次輸入的密碼不一致",
  AUTH_PW_TOO_SHORT: "密碼至少需要 6 個字元",
  AUTH_PW_CHANGED: "密碼已更新",
  AUTH_PW_CHANGE_FAILED: "密碼變更失敗",
  AUTH_SAVING: "儲存中...",

  // Auth — Admin
  AUTH_ADMIN_TITLE: "使用者管理",
  AUTH_ADD_USER: "新增使用者",
  AUTH_EDIT_USER: "編輯使用者",
  AUTH_NO_ADMIN_ACCESS: "需要管理員權限",
  AUTH_NO_USERS: "尚無使用者",
  AUTH_ACTIONS: "操作",
  AUTH_EDIT: "編輯",
  AUTH_DELETE: "刪除",
  AUTH_CONFIRM_DELETE: "確定要刪除使用者",
  AUTH_CREATE: "建立",
  AUTH_CANCEL: "取消",
  AUTH_NOTES: "備註",
} as const;

/* Order type name map */
export const ORDER_TYPE_NAMES: Record<number, string> = {
  2: S.ORDER_BUY_LIMIT,
  3: S.ORDER_SELL_LIMIT,
  4: S.ORDER_BUY_STOP,
  5: S.ORDER_SELL_STOP,
};

/* Log level colors */
export const LOG_COLORS: Record<string, string> = {
  DEBUG: "text-text-tertiary",
  INFO: "text-[#c9d1d9]",
  WARNING: "text-warning",
  ERROR: "text-loss",
  CRITICAL: "text-[#ff6b6b]",
};

export const LOG_LEVELS: Record<string, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
};
