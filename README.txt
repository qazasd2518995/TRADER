============================================
  Copy Trader - 自動跟單 + 馬丁格爾
  (Windows 版本)
============================================

功能: 截圖 LINE 聊天視窗 → OCR 辨識 → 解析交易信號 → 自動下單到 MT5
支援: XAUUSD 黃金, 馬丁格爾加倉, 多 TP 分批平倉


【安裝步驟】

  1. 安裝 Python 3.8+
     https://www.python.org/downloads/
     安裝時勾選 "Add Python to PATH"

  2. 雙擊 install.bat (自動建虛擬環境 + 安裝依賴)

  3. 安裝 Tesseract-OCR (中文辨識引擎)
     https://github.com/UB-Mannheim/tesseract/wiki
     安裝時選擇語言包: chi_tra (繁體中文) + chi_sim (簡體中文)

  4. MetaTrader 5 設定:
     - 開啟 MT5 並登入帳戶
     - 將 mt5_ea/MT5_File_Bridge_Enhanced.mq5 複製到
       MT5 的 MQL5/Experts/ 目錄
     - 在 MT5 中編譯，然後拖到 XAUUSD 圖表上
     - 工具 > 選項 > Expert Advisors > 允許演算法交易

  5. 雙擊 run.bat 啟動


【設定修改】

  編輯 copy_trader/config.py:

  - capture_windows: 修改要監控的視窗名稱
  - parser_mode: "regex" (最快,免費) / "groq" / "anthropic"
  - default_lot_size: 預設手數 (0.01)
  - use_martingale: 馬丁格爾開關
  - martingale_multiplier: 加倍倍數 (2.0)
  - martingale_max_level: 最大加倍次數 (5)
  - max_daily_loss: 每日最大虧損限額 ($500)
  - min_confidence: 最低信心度 (0.9 = 90%)


【檔案結構】

  copy_trader/
  ├── app.py              ← 主程式入口
  ├── config.py           ← 設定檔 (修改這裡)
  ├── signal_capture/
  │   ├── screen_capture.py  ← 螢幕截圖 (pywin32)
  │   └── ocr.py             ← 文字辨識 (Tesseract/WinRT)
  ├── signal_parser/
  │   ├── regex_parser.py    ← Regex 解析 (最快)
  │   ├── groq_parser.py     ← Groq LLM 解析
  │   ├── parser.py          ← Claude LLM 解析
  │   ├── keyword_filter.py  ← 關鍵字預過濾
  │   └── prompts.py         ← LLM 提示詞
  └── trade_manager/
      └── manager.py         ← 交易管理 + 馬丁格爾

  mt5_ea/
  └── MT5_File_Bridge_Enhanced.mq5  ← MT5 橋接 EA


【信號格式範例】

  支援中文和英文:

  乘XAUUSD 黃金
  Sell：4903
  止損：4915
  止盈：4885

  黃金 做多
  進場: 2850
  止損: 2840
  止盈1: 2865
  止盈2: 2880

  XAUUSD BUY
  Entry: 2855
  SL: 2845
  TP1: 2870
  TP2: 2890
