//+------------------------------------------------------------------+
//|                    HighLowLabels_EA.mq5                          |
//|   HighLowLabels v3.70 - MT5 Expert Advisor                      |
//|   Auto-detects HH/LL trend structures and trades XAUUSD         |
//+------------------------------------------------------------------+
#property copyright "HighLowLabels v3.70 EA"
#property version   "1.00"
#property strict

#include "HLL_Defines.mqh"
#include "HLL_SwingDetect.mqh"
#include "HLL_StructureDetect.mqh"
#include "HLL_TrendLine.mqh"
#include "HLL_RiskManager.mqh"
#include "HLL_Drawing.mqh"

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
// ===== 趨勢線 =====
input int    InpMaxLabels    = 100;          // 高/低點各收集幾個
input int    InpLookback     = 500;          // 回顧K棒數量
input int    InpMinLLDist    = 14;           // LL 畫線最小間距(根)
input int    InpMinHHDist    = 14;           // HH 畫線最小間距(根)
input bool   InpReentry      = true;         // 重入趨勢線(LL+HH)
input color  InpLLColor      = clrYellow;    // LL 趨勢線顏色
input color  InpHHColor      = clrLime;      // HH 趨勢線顏色
input color  InpReentryColor = clrAqua;      // 重入趨勢線顏色

// ===== 買賣訊號 =====
input bool   InpEnableBuy    = true;         // 啟用 Buy 訊號
input bool   InpEnableSell   = true;         // 啟用 Sell 訊號
input bool   InpEnableAdd    = true;         // 啟用加碼訊號 (②③)
input bool   InpEnableBuy3   = true;         // 啟用 Buy③
input bool   InpEnableSell3  = true;         // 啟用 Sell③
input color  InpBuyColor     = clrRed;       // Buy 訊號顏色
input color  InpSellColor    = clrGreen;     // Sell 訊號顏色

// ===== 止損設定 =====
input ENUM_SL_MODE InpSLMode = SL_MEDIAN;    // 止損模式
input bool   InpTrailSL      = false;        // 移動式停損
input bool   InpBreakeven    = false;        // 保本止損
input int    InpBevenBars    = 6;            // 保本觸發連續K棒數
input double InpBuySLOffset  = 0.002;        // 買入止損偏移比例
input double InpSellSLOffset = 0.002;        // 賣出止損偏移比例
input bool   InpTLSL         = false;        // 啟用反向趨勢線止損

// ===== 停利設定 =====
input double InpTPPct        = 95.0;         // 停利目標百分比(%)

// ===== 下單設定 =====
input ENUM_LOT_MODE InpLotMode = LOT_FIXED;  // 手數模式
input double InpFixedLot     = 0.01;         // 固定手數
input double InpRiskPercent  = 1.0;          // 風險百分比(%)
input int    InpMagicNumber  = 370070;       // Magic Number
input int    InpMaxSlippage  = 30;           // 最大滑點(points)

// ===== 進階設定 =====
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_CURRENT; // 計算時間框架
input bool   InpAlertOn      = true;         // 啟用推播通知
input bool   InpSoundAlert   = true;         // 啟用聲音提醒

// ===== 繪圖設定 =====
input bool   InpDrawTL       = true;         // 繪製趨勢線
input bool   InpDrawSignals  = true;         // 繪製訊號箭頭
input bool   InpDrawSLTP     = true;         // 繪製SL/TP線

//+------------------------------------------------------------------+
//| Global state                                                     |
//+------------------------------------------------------------------+
datetime        g_lastBarTime = 0;
ProcessedSignal g_processed[];
int             g_processedCnt = 0;

// Latest cross bars for TL stop loss checking
int             g_buyCrossB[];
int             g_buyCrossCnt = 0;
int             g_sellCrossB[];
int             g_sellCrossCnt = 0;

#include <Trade\Trade.mqh>
CTrade          g_trade;

//+------------------------------------------------------------------+
//| BuildComment - Encode signal info into order comment              |
//+------------------------------------------------------------------+
string BuildComment(const SignalInfo &sig)
{
    string typeStr;
    switch(sig.type)
    {
        case SIGNAL_BUY:   typeStr = "BUY";   break;
        case SIGNAL_SELL:  typeStr = "SELL";  break;
        case SIGNAL_BUY2:  typeStr = "BUY2"; break;
        case SIGNAL_SELL2: typeStr = "SELL2"; break;
        case SIGNAL_BUY3:  typeStr = "BUY3"; break;
        case SIGNAL_SELL3: typeStr = "SELL3"; break;
        default: typeStr = "UNK"; break;
    }
    return "HLL_" + typeStr + "_" + IntegerToString(sig.tlStartBar);
}

//+------------------------------------------------------------------+
//| ParseComment - Decode signal type from order comment              |
//+------------------------------------------------------------------+
bool ParseComment(string comment, SIGNAL_TYPE &type, int &tlStart)
{
    if(StringFind(comment, "HLL_") != 0) return false;

    string parts[];
    StringSplit(comment, '_', parts);
    if(ArraySize(parts) < 3) return false;

    if(parts[1] == "BUY")        type = SIGNAL_BUY;
    else if(parts[1] == "SELL")  type = SIGNAL_SELL;
    else if(parts[1] == "BUY2") type = SIGNAL_BUY2;
    else if(parts[1] == "SELL2") type = SIGNAL_SELL2;
    else if(parts[1] == "BUY3") type = SIGNAL_BUY3;
    else if(parts[1] == "SELL3") type = SIGNAL_SELL3;
    else return false;

    tlStart = (int)StringToInteger(parts[2]);
    return true;
}

//+------------------------------------------------------------------+
//| IsSignalProcessed - Check if signal already traded               |
//+------------------------------------------------------------------+
bool IsSignalProcessed(SIGNAL_TYPE type, int tlStartBar)
{
    for(int i = 0; i < g_processedCnt; i++)
    {
        if(g_processed[i].type == type && g_processed[i].tlStartBar == tlStartBar)
            return true;
    }
    return false;
}

//+------------------------------------------------------------------+
//| AddProcessedSignal                                                |
//+------------------------------------------------------------------+
void AddProcessedSignal(SIGNAL_TYPE type, int tlStartBar, ulong ticket)
{
    g_processedCnt++;
    ArrayResize(g_processed, g_processedCnt);
    g_processed[g_processedCnt - 1].type = type;
    g_processed[g_processedCnt - 1].tlStartBar = tlStartBar;
    g_processed[g_processedCnt - 1].processTime = TimeCurrent();
    g_processed[g_processedCnt - 1].ticket = ticket;
}

//+------------------------------------------------------------------+
//| IsSignalEnabled - Check if this signal type is enabled           |
//+------------------------------------------------------------------+
bool IsSignalEnabled(SIGNAL_TYPE type)
{
    switch(type)
    {
        case SIGNAL_BUY:   return InpEnableBuy;
        case SIGNAL_SELL:  return InpEnableSell;
        case SIGNAL_BUY2:  return InpEnableBuy && InpEnableAdd;
        case SIGNAL_SELL2: return InpEnableSell && InpEnableAdd;
        case SIGNAL_BUY3:  return InpEnableBuy && InpEnableAdd && InpEnableBuy3;
        case SIGNAL_SELL3: return InpEnableSell && InpEnableAdd && InpEnableSell3;
        default: return false;
    }
}

//+------------------------------------------------------------------+
//| ExecuteTrade - Place order for a signal                           |
//+------------------------------------------------------------------+
bool ExecuteTrade(const SignalInfo &signal)
{
    if(!IsSignalEnabled(signal.type)) return false;
    if(IsSignalProcessed(signal.type, signal.tlStartBar)) return false;
    if(!signal.isNew) return false; // Only trade barOffset == 1

    bool isBuy = (signal.type == SIGNAL_BUY ||
                  signal.type == SIGNAL_BUY2 ||
                  signal.type == SIGNAL_BUY3);

    double lot = CalcLotSize(_Symbol, signal.entryPrice, signal.slPrice,
                              InpLotMode, InpFixedLot, InpRiskPercent);

    g_trade.SetExpertMagicNumber(InpMagicNumber);
    g_trade.SetDeviationInPoints(InpMaxSlippage);

    string comment = BuildComment(signal);
    bool result = false;

    if(isBuy)
    {
        double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        result = g_trade.Buy(lot, _Symbol, ask, signal.slPrice, signal.tpPrice, comment);
    }
    else
    {
        double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        result = g_trade.Sell(lot, _Symbol, bid, signal.slPrice, signal.tpPrice, comment);
    }

    if(result)
    {
        ulong ticket = g_trade.ResultDeal();
        AddProcessedSignal(signal.type, signal.tlStartBar, ticket);

        string typeStr;
        switch(signal.type)
        {
            case SIGNAL_BUY:   typeStr = "Buy";    break;
            case SIGNAL_SELL:  typeStr = "Sell";   break;
            case SIGNAL_BUY2:  typeStr = "Buy②";  break;
            case SIGNAL_SELL2: typeStr = "Sell②"; break;
            case SIGNAL_BUY3:  typeStr = "Buy③";  break;
            case SIGNAL_SELL3: typeStr = "Sell③"; break;
            default: typeStr = "?"; break;
        }

        PrintFormat("HLL: %s executed at %.2f, SL=%.2f, TP=%.2f, RR=1:%.1f",
                    typeStr, signal.entryPrice, signal.slPrice,
                    signal.tpPrice, signal.riskReward);

        // Alerts
        if(InpAlertOn)
            SendNotification("HLL " + typeStr + " signal on " + _Symbol);
        if(InpSoundAlert)
            PlaySound("alert.wav");
    }
    else
    {
        PrintFormat("HLL: OrderSend failed: %d - %s", GetLastError(),
                    g_trade.ResultRetcodeDescription());
    }

    return result;
}

//+------------------------------------------------------------------+
//| ManagePositions - Per-tick position management                    |
//+------------------------------------------------------------------+
void ManagePositions(const int &hB[], const double &hP[], int hCnt,
                     const int &lB[], const double &lP[], int lCnt,
                     const double &high[], const double &low[],
                     const double &close[], int totalBars)
{
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

        bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
        double curSL = PositionGetDouble(POSITION_SL);
        double entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);

        // 1. Reverse TL stop loss
        if(InpTLSL)
        {
            const int &crossBars[] = isBuy ? g_sellCrossB : g_buyCrossB;
            int crossCnt = isBuy ? g_sellCrossCnt : g_buyCrossCnt;

            if(crossCnt > 0)
            {
                // Check if any reverse TL cross happened after entry
                datetime entryTime = (datetime)PositionGetInteger(POSITION_TIME);
                for(int c = 0; c < crossCnt; c++)
                {
                    datetime crossTime = iTime(_Symbol, InpTimeframe, crossBars[c]);
                    if(crossTime > entryTime)
                    {
                        g_trade.PositionClose(ticket);
                        PrintFormat("HLL: TL stop loss triggered for ticket %d", ticket);
                        break;
                    }
                }
            }
        }

        // Skip if position was just closed
        if(!PositionSelectByTicket(ticket)) continue;

        // 2. Trailing stop
        if(InpTrailSL && curSL != 0)
        {
            if(isBuy)
            {
                // Check most recent L pivot — move SL up only
                if(lCnt > 0)
                {
                    double newSL = lP[0] * (1.0 - InpBuySLOffset);
                    if(newSL > curSL)
                        g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
                }
            }
            else
            {
                // Check most recent H pivot — move SL down only
                if(hCnt > 0)
                {
                    double newSL = hP[0] * (1.0 + InpSellSLOffset);
                    if(newSL < curSL)
                        g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
                }
            }
        }

        // Skip if position was just modified/closed
        if(!PositionSelectByTicket(ticket)) continue;

        // 3. Breakeven
        if(InpBreakeven && curSL != 0)
        {
            if(CheckBreakevenCondition(entryPrice, isBuy, InpBevenBars, close, totalBars))
            {
                if((isBuy && entryPrice > curSL) || (!isBuy && entryPrice < curSL))
                {
                    g_trade.PositionModify(ticket, entryPrice, PositionGetDouble(POSITION_TP));
                    PrintFormat("HLL: Breakeven triggered for ticket %d", ticket);
                }
            }
        }
    }
}

//+------------------------------------------------------------------+
//| RecoverState - Rebuild processed signals from existing positions  |
//+------------------------------------------------------------------+
void RecoverState()
{
    g_processedCnt = 0;
    ArrayResize(g_processed, 0);

    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0) continue;
        if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
        if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

        string comment = PositionGetString(POSITION_COMMENT);
        SIGNAL_TYPE type;
        int tlStart;
        if(ParseComment(comment, type, tlStart))
        {
            AddProcessedSignal(type, tlStart, ticket);
        }
    }

    PrintFormat("HLL: Recovered %d existing positions", g_processedCnt);
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
    Print("HighLowLabels EA v1.00 starting on ", _Symbol);
    Print("Timeframe: ", EnumToString(InpTimeframe));
    Print("SL Mode: ", EnumToString(InpSLMode));
    Print("Lot Mode: ", EnumToString(InpLotMode));

    g_trade.SetExpertMagicNumber(InpMagicNumber);
    g_trade.SetDeviationInPoints(InpMaxSlippage);

    RecoverState();

    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    HLL_ClearAll();
    Print("HighLowLabels EA stopped, reason: ", reason);
}

//+------------------------------------------------------------------+
//| OnTick                                                           |
//+------------------------------------------------------------------+
void OnTick()
{
    ENUM_TIMEFRAMES tf = (InpTimeframe == PERIOD_CURRENT) ? Period() : InpTimeframe;

    // --- Detect new bar ---
    datetime currentBarTime = iTime(_Symbol, tf, 0);
    bool isNewBar = (currentBarTime != g_lastBarTime);

    // --- Copy price data ---
    int barsNeeded = InpLookback + 50; // extra buffer
    int totalBars = Bars(_Symbol, tf);
    if(totalBars < barsNeeded)
    {
        if(isNewBar)
            Print("HLL: Not enough bars. Have ", totalBars, ", need ", barsNeeded);
        return;
    }

    double high[], low[], open[], close[];
    ArraySetAsSeries(high, true);
    ArraySetAsSeries(low, true);
    ArraySetAsSeries(open, true);
    ArraySetAsSeries(close, true);

    int copied = CopyHigh(_Symbol, tf, 0, barsNeeded, high);
    if(copied < barsNeeded) return;
    CopyLow(_Symbol, tf, 0, barsNeeded, low);
    CopyOpen(_Symbol, tf, 0, barsNeeded, open);
    CopyClose(_Symbol, tf, 0, barsNeeded, close);

    // --- Per-tick position management (always runs) ---
    int hB_tick[], lB_tick[];
    double hP_tick[], lP_tick[];
    int hCnt_tick = 0, lCnt_tick = 0;

    // Lightweight swing collection for position management
    CollectSwingPoints(MathMin(InpLookback, totalBars - 2), InpMaxLabels,
                       high, low, barsNeeded,
                       hB_tick, hP_tick, hCnt_tick,
                       lB_tick, lP_tick, lCnt_tick);

    ManagePositions(hB_tick, hP_tick, hCnt_tick,
                    lB_tick, lP_tick, lCnt_tick,
                    high, low, close, barsNeeded);

    if(!isNewBar) return;
    g_lastBarTime = currentBarTime;

    // ====== Full recalculation on new bar ======
    PrintFormat("HLL: New bar at %s, recalculating...",
                TimeToString(currentBarTime));

    // 1. Collect swing points
    int hB[], lB[];
    double hP[], lP[];
    int hCnt = 0, lCnt = 0;
    CollectSwingPoints(MathMin(InpLookback, totalBars - 2), InpMaxLabels,
                       high, low, barsNeeded,
                       hB, hP, hCnt,
                       lB, lP, lCnt);

    // 2. Detect LL structure
    int llB[], hhB[];
    double llP[], hhP[];
    int llCnt = DetectLowerLows(hB, hP, hCnt, lB, lP, lCnt,
                                 high, low, barsNeeded, llB, llP);

    // 3. Detect HH structure
    int hhCnt = DetectHigherHighs(hB, hP, hCnt, lB, lP, lCnt,
                                    high, low, close, barsNeeded, hhB, hhP);

    // 4. Signal collection
    SignalInfo signals[];
    int signalCnt = 0;
    ArrayResize(signals, 0);

    // 5. HH trend lines (MUST be before LL — collects buyCrossB)
    TrendLineInfo hhTL[];
    int hhTLCnt = 0;
    ReentryLineInfo hhReentry[];
    int hhReentryCnt = 0;
    int buyCrossB[];
    int buyCrossCnt = 0;

    CalcHHTrendLines(hhB, hhP, hhCnt,
                     hB, hP, hCnt, lB, lP, lCnt,
                     high, low, open, close, barsNeeded,
                     InpMinHHDist, InpReentry,
                     hhTL, hhTLCnt,
                     hhReentry, hhReentryCnt,
                     buyCrossB, buyCrossCnt,
                     signals, signalCnt);

    // 6. LL trend lines (buyCrossB now ready)
    TrendLineInfo llTL[];
    int llTLCnt = 0;
    ReentryLineInfo llReentry[];
    int llReentryCnt = 0;
    int sellCrossB[];
    int sellCrossCnt = 0;

    CalcLLTrendLines(llB, llP, llCnt,
                     hB, hP, hCnt, lB, lP, lCnt,
                     high, low, open, close, barsNeeded,
                     InpMinLLDist, InpReentry,
                     buyCrossB, buyCrossCnt,
                     llTL, llTLCnt,
                     llReentry, llReentryCnt,
                     sellCrossB, sellCrossCnt,
                     signals, signalCnt);

    // Save cross bars for per-tick TL stop loss checking
    ArrayResize(g_buyCrossB, buyCrossCnt);
    if(buyCrossCnt > 0) ArrayCopy(g_buyCrossB, buyCrossB);
    g_buyCrossCnt = buyCrossCnt;

    ArrayResize(g_sellCrossB, sellCrossCnt);
    if(sellCrossCnt > 0) ArrayCopy(g_sellCrossB, sellCrossB);
    g_sellCrossCnt = sellCrossCnt;

    // 7. Fill SL/TP/RR for all signals
    FillSignalSLTP(signals, signalCnt,
                   hB, hP, hCnt, lB, lP, lCnt,
                   open, close, barsNeeded,
                   InpSLMode, InpBuySLOffset, InpSellSLOffset,
                   InpTPPct);

    // 8. Execute new signals
    for(int i = 0; i < signalCnt; i++)
    {
        if(signals[i].isNew)
            ExecuteTrade(signals[i]);
    }

    // 9. Draw on chart
    HLL_ClearAll();
    if(InpDrawTL)
    {
        HLL_DrawTrendLines(hhTL, hhTLCnt, llTL, llTLCnt,
                            hhReentry, hhReentryCnt, llReentry, llReentryCnt,
                            InpHHColor, InpLLColor, InpReentryColor, tf);
    }
    if(InpDrawSignals)
    {
        HLL_DrawSignals(signals, signalCnt,
                         InpBuyColor, InpSellColor,
                         clrAqua, clrFuchsia, clrDodgerBlue,
                         InpDrawSLTP, true, tf);
    }

    PrintFormat("HLL: Found %d signals, %d HH lines, %d LL lines",
                signalCnt, hhTLCnt, llTLCnt);
}
//+------------------------------------------------------------------+
