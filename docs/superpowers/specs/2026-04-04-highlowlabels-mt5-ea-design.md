# HighLowLabels v3.70 — MT5 EA 設計文件

## 概述

將 TradingView Pine Script 指標 HighLowLabels v3.70（1753 行）完整移植為 MetaTrader 5 Expert Advisor，實現自動偵測趨勢結構並下單交易。

### 需求摘要

- **交易品種**：XAUUSD，時間框架可透過參數調整
- **訊號下單**：Buy/Sell 主訊號一定下單，Buy②/Sell②/Buy③/Sell③ 透過參數開關控制加倉
- **倉位管理**：支援固定手數和風險百分比兩種模式
- **止損系統**：完整移植所有模式（預設、區間中位、移動式、保本、反向趨勢線止損）
- **圖表繪製**：趨勢線 + Buy/Sell 箭頭 + SL/TP 線（不畫 H/L 編號標籤）

---

## 架構

### 檔案結構

```
mt5_ea/
├── HighLowLabels_EA.mq5       // 主 EA：OnInit/OnTick/OnDeinit、下單執行、狀態管理
├── HLL_SwingDetect.mqh         // Swing High/Low 偵測（含同高同低處理）
├── HLL_StructureDetect.mqh     // LL/HH 結構偵測（含補充偵測）
├── HLL_TrendLine.mqh           // 趨勢線計算：切線掃描、重入、截斷
├── HLL_Signal.mqh              // 訊號產生：6 種訊號的偵測邏輯
├── HLL_RiskManager.mqh         // SL/TP/倉位計算、止損追蹤
└── HLL_Drawing.mqh             // 圖表繪製：趨勢線、箭頭、SL/TP 線
```

### 執行流程

```
OnInit()
  ├─ 初始化參數
  ├─ 掃描現有持倉（Magic Number 匹配），重建狀態
  └─ 設定 Timer（每秒檢查持倉管理）

OnTick()
  ├─ 偵測新 K 棒（iTime 比較）
  │   ├─ [是] → 完整重算流程（見下方）
  │   └─ [否] → 僅執行持倉管理（移動式停損、保本止損檢查）
  └─ 持倉管理（每 tick）

完整重算流程（新 K 棒觸發）：
  1. 收集 Swing High / Swing Low（回顧 i_lookback 根）
  2. 偵測 LL 結構（含 hasLH 檢查 + 補充偵測）
  3. 偵測 HH 結構（含 hasHL 檢查 + 補充偵測）
  4. ★ HH 趨勢線計算（必須先於 LL）
     ├─ 間距過濾
     ├─ v3.65 成立條件檢查
     ├─ v3.58 切線掃描 4 步驟
     ├─ 突破偵測 → 收集 buyCrossB
     ├─ v3.70 newerHH1 截斷
     ├─ Buy 訊號判定
     ├─ Sell③ 加碼訊號判定
     └─ Buy② 重入趨勢線 + 訊號
  5. ★ LL 趨勢線計算（buyCrossB 已就緒）
     ├─ 間距過濾（v3.42 取價格較低者）
     ├─ v3.65 成立條件檢查
     ├─ v3.58 切線掃描 4 步驟
     ├─ 跌破偵測 → 收集 sellCrossB
     ├─ v3.70 newerLL1 截斷
     ├─ Sell 訊號判定
     ├─ Buy③ 加碼訊號判定
     └─ Sell② 重入趨勢線 + 訊號
  6. ★ Buy 止損管理（sellCrossB 已就緒）
  7. 訊號確認 + 下單執行
  8. 圖表繪製
  9. 通知發送
```

**關鍵順序依賴**：HH 趨勢線 → buyCrossB → LL 趨勢線 → sellCrossB → Buy 止損管理。此順序與 Pine Script 完全一致，不可更改。

---

## 模組詳細設計

### 1. HLL_SwingDetect.mqh — Swing Point 偵測

#### 函式

```
bool IsSwingHigh(int barIndex, const double &high[], const double &open[], const double &close[], const double &low[], int totalBars)
bool IsSwingLow(int barIndex, const double &high[], const double &open[], const double &close[], const double &low[], int totalBars)
void CollectSwingPoints(int lookback, int maxLabels, ...)
```

#### 邏輯（對應 Pine 121-148 行）

**IsSwingHigh(barIndex)**：
1. 若 `high[barIndex] > high[barIndex+1]` 且 `high[barIndex] > high[barIndex-1]` → 高點
2. 若 `high[barIndex] == high[barIndex+1]` 且 `high[barIndex] > high[barIndex-1]` → 往左掃描同高，直到找到更低的 K 棒確認平頂

**IsSwingLow(barIndex)**：對稱邏輯。

**CollectSwingPoints**：從 barIndex=1 到 lookback，收集最多 maxLabels 個高/低點，存入 hB/hP/lB/lP 陣列。

#### Bar 索引約定

MQL5 使用 `ArraySetAsSeries(true)` 後，索引 0 = 當前 K 棒，與 Pine Script 的 `high[0]` = 當前 K 棒一致。所有偵測從 barIndex=1 開始（跳過未收盤的當前 K 棒）。

### 2. HLL_StructureDetect.mqh — LL/HH 結構偵測

#### 函式

```
int DetectLowerLows(hB[], hP[], lB[], lP[], out llB[], out llP[])
int DetectHigherHighs(hB[], hP[], lB[], lP[], out hhB[], out hhP[])
void SupplementLL(llB[], llP[], lB[], lP[])
void SupplementHH(hhB[], hhP[], hB[], hP[])
void SortByOffset(int &bars[], double &prices[])
```

#### LL 偵測邏輯（對應 Pine 299-417 行）

1. 建立突破點陣列 bkB/bkP = [當前K棒] + 所有 H 點
2. 區間起點快取 `_hZs`, `_lZs`（遞增指標，O(n²) 優化）
3. 對每個突破點 bk：
   - 找區間內 H 端點 ho（bkPr > hoPr，且 ho 之前無更高 H 點）
   - 找區間內 L 候選組 grp（ho 之前的所有 L 點）
   - 取 grp 中價格最低者 mi
   - 檢查 hasOHL：mi 之後是否有更高的 L 點
   - **v3.63 hasLH**：mi~olh 間峰頂 vs olh 之後峰頂，確認高點遞降
   - 兩者皆滿足 → 標記為 LL

#### 補充 LL 偵測（對應 Pine 419-450 行）

排序後，相鄰 LL 之間若有更低的 low 值且低於兩端 LL 價格 → 補充為新 LL。

#### HH 偵測（對應 Pine 452-600 行）

與 LL 完全對稱：突破點用 L 點，候選用 H 點，hasHL 用谷底比較法。

### 3. HLL_TrendLine.mqh — 趨勢線計算

#### 資料結構

```cpp
struct TrendLineInfo {
    int    startBar;     // 趨勢線起點 bar offset
    double startPrice;   // 起點價格
    int    endBar;       // 終點 bar offset
    double endPrice;     // 終點價格
    double slope;        // 斜率（tlSlope）
    int    crossBar;     // 突破/跌破點 offset（-1 = 未突破）
    bool   isActiveCross;// 是否為有效突破
    int    hh1Bar;       // HH1/LL1 的 bar offset（用於重入範圍界定）
    int    hh2Bar;       // HH2/LL2 的 bar offset
    double minLB;        // HH 對間最低 L 樞紐（Sell③ 用）
    double maxHB;        // LL 對間最高 H 樞紐（Buy③ 用）
    int    defRL;        // 前一個有效 HH/LL 的 offset
};

struct ReentryLineInfo {
    int    startBar;
    double startPrice;
    int    endBar;
    double endPrice;
    double slope;
    int    crossBar;     // 重入線突破點
    int    triggerBar;   // 觸發重入的 H/L 點
};
```

#### HH 下降趨勢線（對應 Pine 602-1031 行）

**間距過濾**（對應 606-621 行）：
- 排序 hhB/hhP
- 相鄰 HH 距離 < i_minHHDist → 不畫線

**遍歷 HH 對**（hh=0 到 hhCnt-2）：
1. 有效性：hh2Pr > hh1Pr（遞降方向）
2. **v3.65 成立條件**（631-649 行）：defRL+1 到 hh1Bar-1 區間最低 low (minPA) vs hh1Bar~hh2Bar 間最低 L 樞紐 (minLB)。minPA >= minLB → 跳過（低點未被跌破）
3. **起點掃描**（652-656 行）：hh1Bar 到 hh2Bar 中取最高 high 為 startOff/startPr
4. **v3.58 切線掃描 4 步驟**（657-696 行）：
   - Step 1：找 hh1Bar~startOff 間谷底 (valleyBar)
   - Step 2：找 HH1 之後跌破谷底的 L 樞紐 → scanStart
   - Step 3（Phase 1）：[scanStart, valleyBar] 找 bodyTop 最大斜率 → anchor
   - Step 4（Phase 2）：[valleyBar+1, startOff-1] 確保不切入實體 → 修正斜率
   - lineStartPr = anchorPr + sl * (startOff - anchorBar)
   - tlSlope = -sl；若 >= 0 → 跳過
5. **突破偵測**（697-705 行）：從 hh1Bar-1 往 0 掃描，close > lineP → crossBar
6. **isActiveCross 判定**（707-716 行）：
   - crossBar 存在且比 lastActiveHHCross 更新 → 有效
   - 記入 buyCrossB
   - 未突破時重置 lastActiveHHCross = -1
7. **截斷**（717-720 行）：endOff 不超過 newerHH1
8. **更新 newerHH1**（1031 行）：= hh1Bar

#### Sell③ 加碼訊號（對應 723-735 行）

條件：minLB 存在 + hh1Bar-1 >= defRL+1
從 hh1Bar-1 往 defRL+1 掃描：close < minLB → Sell③ 位置

#### Buy② 重入趨勢線（對應 833-1031 行）

1. 找 [crossBar, hh1Bar] 區間最低 L 峰值 (_minLZone)
2. 收集跌破後連續創新低的 L 點（多條重入線）
3. 從 startOff/startPr 出發，掃描 bodyTop 取最大斜率（最接近零的負斜率）
4. 檢查範圍內無新 HH 趨勢線 → 否則跳過
5. 突破偵測：close > 重入線價格 → _reCross
6. v3.69：_reCross 在 newerHH1 之後才有效
7. 截斷：遇到 newerHH1 時截斷

#### LL 上升趨勢線（對應 1033-1585 行）

完全對稱邏輯：
- 間距過濾 v3.42：距離不足時取價格較低的 LL 替換
- 成立條件：高點被突破 (maxPA <= maxHB → 跳過)
- 起點掃描取最低 low
- 切線掃描：bodyBot 取最小斜率 → 正斜率
- 跌破偵測：close < lineP
- sellCrossB 收集
- Buy③：close > maxHB
- Sell② 重入：連續創新高的 H 點，bodyBot 最小斜率

### 4. HLL_Signal.mqh — 訊號產生

#### 資料結構

```cpp
enum SIGNAL_TYPE {
    SIGNAL_BUY,      // Buy 主訊號
    SIGNAL_SELL,     // Sell 主訊號
    SIGNAL_BUY2,     // Buy② 重入
    SIGNAL_SELL2,    // Sell② 重入
    SIGNAL_BUY3,     // Buy③ 趨勢線確認加碼
    SIGNAL_SELL3     // Sell③ 趨勢線確認加碼
};

struct SignalInfo {
    SIGNAL_TYPE type;
    int         barOffset;    // 訊號觸發的 bar offset
    double      entryPrice;   // 進場價（該 bar 的 close）
    double      slPrice;      // 初始止損價
    double      tpPrice;      // 停利價
    double      riskReward;   // R:R 比
    int         tlStartBar;   // 所屬趨勢線起點（用於倉位配對）
    int         tlEndBar;     // 所屬趨勢線終點
    bool        isNew;        // 是否為本次重算新產生的訊號
};
```

#### 訊號判定規則

**Buy**（對應 Pine 742-751 行）：
- isActiveCross == true 且 crossBar >= 1
- 訊號位置 = crossBar - 1（突破後下一根 K 棒）
- 進場價 = close[crossBar-1]

**Sell**（對應 Pine 1179-1206 行）：
- isActiveCross == true 且 crossBar >= 1
- 訊號位置 = crossBar - 1
- 進場價 = close[crossBar-1]

**Buy②**（對應 Pine 904-912 行）：
- 重入線被突破 (_reCross >= 1)
- 訊號位置 = _reCross - 1

**Sell②**（對應 Pine 1458-1466 行）：
- 重入線被跌破 (_reCross >= 1)
- 訊號位置 = _reCross - 1

**Buy③**（對應 Pine 1159-1171 行）：
- close > LL 對間最高 H 樞紐 (maxHB)
- 從 ll1Bar-1 往 defRL+1 掃描第一個滿足條件的 bar

**Sell③**（對應 Pine 723-735 行）：
- close < HH 對間最低 L 樞紐 (minLB)
- 從 hh1Bar-1 往 defRL+1 掃描第一個滿足條件的 bar

#### 訊號確認機制（對應 Pine 213-261, 1723-1725 行）

Pine Script 的確認邏輯：
1. crossBar == 0（當前 K 棒突破）→ 設 pendingConfirm = true
2. 下一根新 K 棒開始時 → 確認訊號有效 → 下單

MT5 對應實現：
1. 完整重算在新 K 棒開盤時執行（基於上一根已收盤的 K 棒數據）
2. 若偵測到 barOffset == 1（上一根 K 棒）的新訊號 → 這是本次需要執行的訊號
3. barOffset > 1 的訊號 = 歷史訊號，不下單（但用於止損管理）
4. barOffset == 0 不會出現（當前 K 棒未收盤，不參與計算）

### 5. HLL_RiskManager.mqh — 風險管理

#### SL 計算（對應 Pine 的多種止損模式）

**預設模式**：
- Buy SL = 最近 L 樞紐 × (1 - i_buySL)
- Sell SL = 最近 H 樞紐 × (1 + i_sellSL)

**區間中位模式**（對應 Pine 753-768 行）：
- Buy SL = 趨勢線範圍內最低 2 個 L 峰值的中位數
- Sell SL = 趨勢線範圍內最高 2 個 H 峰值的中位數

#### TP 計算（對應 Pine 770-789 行）

實體 K 棒對稱投影：
1. 掃描 [signalBar, startOff] 範圍所有 K 棒
2. bodyHigh = max(open, close) 的最大值
3. bodyLow = min(open, close) 的最小值
4. Buy TP = bodyHigh + (bodyHigh - bodyLow) × (i_tpPct / 100)
5. Sell TP = bodyLow - (bodyHigh - bodyLow) × (i_tpPct / 100)

#### R:R 計算

- Buy: reward = TP - entryLow, risk = entryLow - SL
- Sell: reward = entryHigh - TP, risk = SL - entryHigh
- R:R = reward / risk（risk > 0 且 reward > 0 時）

#### 移動式停損（對應 Pine 1350-1362, 1656-1666 行）

- Buy：隨新 L 點更新，newSL = lP × (1 - i_buySL)，只允許向上（newSL > curSL）
- Sell：隨新 H 點更新，newSL = hP × (1 + i_sellSL)，只允許向下（newSL < curSL）

#### 保本止損（對應 Pine 1305-1321, 1613-1629 行）

- 連續 i_bevenBars 根 K 棒都獲利 → SL 移至進場價
- Buy：close[n] >= entryPrice 連續 N 根
- Sell：close[n] <= entryPrice 連續 N 根

#### 反向趨勢線止損（對應 Pine f_findHit 165-190 行）

`FindHit(prevOff, endOff, curSL, isSell, crossBars[])`：
1. hitC1：prevOff-1 到 endOff 掃描價格觸及 curSL
2. hitC2：crossBars 中在 [endOff, prevOff) 範圍內最新的突破點
3. 取兩者中較新的（offset 較大者）
4. 回傳 [hitOff, hitByC1]

#### TP 觸發偵測（對應 Pine 790-809 行）

從 signalBar-1 往 0 掃描：
- 若止損先觸發（low <= SL 或 TL 止損）→ 不標記 TP
- 若 high >= TP → 標記 TP 達標

在 MT5 中，TP 觸發 = 實際平倉（而非僅標記）。

#### 倉位計算

**固定手數模式**：直接使用 i_fixedLot

**風險百分比模式**：
```
riskAmount = AccountBalance × (i_riskPercent / 100)
pipValue = SymbolInfoDouble(SYMBOL_TRADE_TICK_VALUE) / SymbolInfoDouble(SYMBOL_TRADE_TICK_SIZE)
slDistance = |entryPrice - slPrice|
lotSize = riskAmount / (slDistance × pipValue)
lotSize = NormalizeLot(lotSize)  // 對齊 SYMBOL_VOLUME_STEP
```

### 6. HLL_Drawing.mqh — 圖表繪製

#### 繪製元素（選擇 B 方案：只畫關鍵元素）

| 元素 | MQL5 物件 | 對應 Pine |
|------|-----------|-----------|
| HH 下降趨勢線 | OBJ_TREND (STYLE_SOLID) | line.new(..., style_solid) |
| LL 上升趨勢線 | OBJ_TREND (STYLE_SOLID) | line.new(..., style_solid) |
| 重入趨勢線 | OBJ_TREND (STYLE_DASH) | line.new(..., style_dashed) |
| 微趨勢線（可選） | OBJ_TREND (STYLE_DOT) | line.new(..., style_dotted) |
| Buy/Sell 箭頭 | OBJ_ARROW_UP / OBJ_ARROW_DOWN | line.new + label.new |
| Buy②/Sell②/Buy③/Sell③ | OBJ_ARROW + OBJ_TEXT | line.new + label.new |
| SL 水平線 | OBJ_HLINE (STYLE_DASH) | line.new(..., style_dashed) |
| TP 水平線 | OBJ_HLINE (STYLE_DOT) | line.new(..., style_dotted) |
| ✕ SL / ✕ TL 標記 | OBJ_TEXT | label.new |
| ✓ TP 標記 | OBJ_TEXT | label.new |
| R:R 文字 | OBJ_TEXT | label.new |

#### 繪製策略

- 每次新 K 棒重算時，先清除所有以 "HLL_" 為前綴的物件，再重畫
- 物件命名：`HLL_{type}_{barIndex}_{seq}`（確保唯一性）

### 7. HighLowLabels_EA.mq5 — 主 EA

#### Input 參數

```cpp
// ===== 趨勢線 =====
input int    InpMaxLabels    = 100;   // 高/低點各收集幾個
input int    InpLookback     = 500;   // 回顧K棒數量
input int    InpMinLLDist    = 14;    // LL 畫線最小間距(根)
input int    InpMinHHDist    = 14;    // HH 畫線最小間距(根)
input bool   InpReentry      = true;  // 重入趨勢線(LL+HH)
input color  InpLLColor      = clrYellow;     // LL 趨勢線顏色
input color  InpHHColor      = clrLime;       // HH 趨勢線顏色
input color  InpReentryColor = clrAqua;       // 重入趨勢線顏色

// ===== 買賣訊號 =====
input bool   InpEnableBuy    = true;  // 啟用 Buy 訊號
input bool   InpEnableSell   = true;  // 啟用 Sell 訊號
input bool   InpEnableAdd    = true;  // 啟用加碼訊號 (②③)
input bool   InpEnableBuy3   = true;  // 啟用 Buy③
input bool   InpEnableSell3  = true;  // 啟用 Sell③
input color  InpBuyColor     = clrRed;        // Buy 訊號顏色
input color  InpSellColor    = clrGreen;      // Sell 訊號顏色

// ===== 止損設定 =====
input ENUM_SL_MODE InpSLMode = SL_MEDIAN;     // 止損模式(預設/區間中位)
input bool   InpTrailSL      = false; // 移動式停損
input bool   InpBreakeven    = false; // 保本止損
input int    InpBevenBars    = 6;     // 保本觸發連續K棒數
input double InpBuySLOffset  = 0.002; // 買入止損偏移比例
input double InpSellSLOffset = 0.002; // 賣出止損偏移比例
input bool   InpTLSL         = false; // 啟用反向趨勢線止損

// ===== 停利設定 =====
input double InpTPPct        = 95.0;  // 停利目標百分比(%)
input bool   InpShowRR       = true;  // 顯示風險報酬比

// ===== 下單設定 =====
input ENUM_LOT_MODE InpLotMode = LOT_FIXED;   // 手數模式(固定/風險百分比)
input double InpFixedLot     = 0.01;  // 固定手數
input double InpRiskPercent  = 1.0;   // 風險百分比(%)
input int    InpMagicNumber  = 370070;// Magic Number
input int    InpMaxSlippage  = 30;    // 最大滑點(points)

// ===== 進階設定 =====
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_CURRENT; // 計算時間框架
input bool   InpUseMicro     = false; // 啟用微趨勢線
input int    InpMicroDist    = 3;     // 微趨勢線最小間距
input int    InpMicroMax     = 5;     // 微趨勢線最大數量
input bool   InpAlertOn      = true;  // 啟用推播通知
input bool   InpSoundAlert   = true;  // 啟用聲音提醒

// ===== 繪圖設定 =====
input bool   InpDrawTL       = true;  // 繪製趨勢線
input bool   InpDrawSignals  = true;  // 繪製訊號箭頭
input bool   InpDrawSLTP     = true;  // 繪製SL/TP線
```

#### 自訂 Enum

```cpp
enum ENUM_SL_MODE {
    SL_DEFAULT,   // 預設（最近樞紐點）
    SL_MEDIAN     // 區間中位
};

enum ENUM_LOT_MODE {
    LOT_FIXED,    // 固定手數
    LOT_RISK_PCT  // 風險百分比
};
```

#### 狀態管理

```cpp
// 已處理訊號追蹤（防重複下單）
struct ProcessedSignal {
    SIGNAL_TYPE type;
    int         tlStartBar;  // 所屬趨勢線起點 bar_index（絕對值）
    datetime    processTime; // 處理時間
    ulong       ticket;      // 對應的倉位 ticket
};

ProcessedSignal g_processed[];  // 已處理訊號列表
datetime        g_lastBarTime;  // 上一根已處理 K 棒時間
```

#### 防重複開倉邏輯

判斷一個訊號是否已處理：
1. 將 barOffset 轉為絕對 bar_index = iBarShift 反算
2. 查找 g_processed 中是否存在相同 type + tlStartBar 的記錄
3. 若存在 → 跳過
4. 若不存在且 barOffset == 1（上一根剛收盤的 K 棒）→ 下單並記入 g_processed

#### EA 重啟恢復（OnInit）

1. 遍歷所有持倉 `PositionSelect`
2. 篩選 Magic Number == InpMagicNumber 且 Symbol 匹配
3. 從 Comment 欄位解析訊號類型和趨勢線資訊
4. 重建 g_processed 列表
5. 繼續管理已有倉位的止損/停利

#### 下單執行

```cpp
bool ExecuteTrade(SignalInfo &signal) {
    // 1. 計算手數
    double lot = CalcLotSize(signal);

    // 2. 構建下單請求
    MqlTradeRequest request = {};
    request.action    = TRADE_ACTION_DEAL;
    request.symbol    = _Symbol;
    request.volume    = lot;
    request.type      = (signal.type == SIGNAL_BUY || signal.type == SIGNAL_BUY2 || signal.type == SIGNAL_BUY3)
                        ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
    request.price     = (request.type == ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                                          : SymbolInfoDouble(_Symbol, SYMBOL_BID);
    request.sl        = signal.slPrice;
    request.tp        = signal.tpPrice;
    request.deviation = InpMaxSlippage;
    request.magic     = InpMagicNumber;
    request.comment   = BuildComment(signal);  // 例："HLL_BUY_12345" 編碼趨勢線資訊

    // 3. 發送訂單
    MqlTradeResult result = {};
    if (!OrderSend(request, result)) {
        PrintFormat("OrderSend failed: %d", GetLastError());
        return false;
    }

    // 4. 記錄已處理訊號
    AddProcessedSignal(signal, result.deal);
    return true;
}
```

#### 持倉管理（每 tick）

```cpp
void ManagePositions() {
    for (int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong ticket = PositionGetTicket(i);
        if (PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
        if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

        // 1. 反向趨勢線止損檢查
        if (InpTLSL && CheckTLStopLoss(ticket))
            ClosePosition(ticket);

        // 2. 移動式停損更新
        if (InpTrailSL)
            UpdateTrailingStop(ticket);

        // 3. 保本止損檢查
        if (InpBreakeven)
            CheckBreakeven(ticket);
    }
}
```

---

## 微趨勢線（可選功能）

對應 Pine 1682-1721 行。僅繪圖，不產生訊號，不影響下單邏輯。

- 微下降趨勢線：相鄰高點遞降，間距在 [microDist, minHHDist) 之間
- 微上升趨勢線：相鄰低點遞升，間距在 [microDist, minLLDist) 之間
- 延長 2 根 K 棒，點線繪製

---

## 通知系統

| Pine Script | MT5 對應 |
|---|---|
| `alert("Buy 訊號...")` | `Alert("Buy signal: ", _Symbol)` + `SendNotification(...)` |
| `alertcondition(...)` | `PlaySound("alert.wav")` |
| pendingConfirm 機制 | 新 K 棒開盤時確認後發送 |

---

## 關鍵設計決策

### 1. 為什麼新 K 棒時才重算？

Pine Script 的設計是只在 `barstate.islast` 時計算。MT5 對應為：偵測到新 K 棒（`iTime(0) != g_lastBarTime`）時觸發完整重算。這保證：
- 使用已收盤的 K 棒數據，避免未成形的即時數據干擾
- 與 Pine Script「每根K棒更新一次」模式行為一致
- 減少計算開銷

### 2. 為什麼用 barOffset 而非 datetime？

Pine Script 全部使用 bar offset（0=當前, 1=上一根...）。MQL5 的 `CopyRates` 也支援索引存取。保持 offset 系統可以：
- 最小化轉換邏輯的風險
- 確保所有索引計算與 Pine Script 一致
- 方便 debug 時與 Pine Script 逐行對比

### 3. SL/TP 是設在訂單上還是 EA 管理？

**混合方案**：
- 初始 SL/TP 設在訂單上（OrderSend 時帶 sl/tp），作為保底
- 移動式停損、保本止損、反向 TL 止損由 EA 每 tick 管理（OrderModify 更新 SL）
- 這樣即使 EA 崩潰，MT5 仍有基本 SL/TP 保護

### 4. Comment 編碼格式

```
HLL_{TYPE}_{TLSTART}_{SEQ}
```
- TYPE: BUY/SELL/BUY2/SELL2/BUY3/SELL3
- TLSTART: 趨勢線起點的絕對 bar_index
- SEQ: 序號（同一趨勢線的多個訊號區分）

例：`HLL_BUY_123456_1`

---

## 測試策略

1. **單元驗證**：在 MT5 Strategy Tester 中用視覺模式，對比 TradingView 同時段的標記是否一致
2. **關鍵驗證點**：
   - Swing High/Low 數量和位置是否與 Pine Script 一致
   - LL/HH 結構偵測結果是否一致
   - 趨勢線斜率和端點是否一致
   - 訊號觸發時機是否一致（同一根 K 棒）
3. **下單驗證**：Strategy Tester 回測，確認 SL/TP 觸發行為正確
