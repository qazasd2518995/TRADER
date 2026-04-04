HighLowLabels v3.70 - MT5 Expert Advisor
=========================================

安裝步驟：
1. 將所有 .mq5 和 .mqh 檔案複製到 MT5 的 MQL5/Experts/ 目錄
2. 在 MetaEditor 中開啟 HighLowLabels_EA.mq5
3. 按 F7 編譯
4. 在 MT5 的「導航」面板中找到 HighLowLabels_EA
5. 拖拽到 XAUUSD 圖表上

檔案清單：
- HighLowLabels_EA.mq5    主 EA
- HLL_Defines.mqh          共用類型定義
- HLL_SwingDetect.mqh      Swing 高低點偵測
- HLL_StructureDetect.mqh  LL/HH 結構偵測
- HLL_TrendLine.mqh        趨勢線計算
- HLL_RiskManager.mqh      風險管理（SL/TP/手數）
- HLL_Drawing.mqh          圖表繪製

參數說明：
- InpLookback: 回顧 K 棒數量（預設 500）
- InpMinLLDist/InpMinHHDist: 趨勢線最小間距（預設 14）
- InpSLMode: SL_DEFAULT=最近樞紐點, SL_MEDIAN=區間中位
- InpLotMode: LOT_FIXED=固定手數, LOT_RISK_PCT=風險百分比
- InpMagicNumber: 370070（用於識別本 EA 的訂單）
- InpTimeframe: PERIOD_CURRENT=當前圖表時間框架

注意事項：
- 確保已啟用自動交易（Algo Trading 按鈕為綠色）
- EA 重啟後會自動恢復已有持倉的管理
- 所有圖表物件以 "HLL_" 為前綴，移除 EA 時會自動清除
- 建議先在 Strategy Tester 中以視覺模式測試
