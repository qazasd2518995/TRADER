//+------------------------------------------------------------------+
//|                     HLL_RiskManager.mqh                          |
//|      SL/TP calculation, lot sizing, position management          |
//+------------------------------------------------------------------+
#ifndef HLL_RISKMANAGER_MQH
#define HLL_RISKMANAGER_MQH

#include "HLL_Defines.mqh"

//+------------------------------------------------------------------+
//| CalcBuySL - Calculate Buy stop loss                              |
//| slMode: SL_DEFAULT or SL_MEDIAN                                  |
//+------------------------------------------------------------------+
double CalcBuySL(int signalBar, int tlStartOff,
                 const int &lB[], const double &lP[], int lCnt,
                 ENUM_SL_MODE slMode, double buySLOffset)
{
    if(slMode == SL_MEDIAN)
    {
        // 區間中位: lowest 2 L pivots in [signalBar, tlStartOff]
        double lo1 = 1e18, lo2 = 1e18;
        for(int k = 0; k < lCnt; k++)
        {
            if(lB[k] >= signalBar && lB[k] <= tlStartOff)
            {
                double lp = lP[k];
                if(lp < lo1) { lo2 = lo1; lo1 = lp; }
                else if(lp < lo2) { lo2 = lp; }
            }
        }
        if(lo2 < 1e18)
            return (lo1 + lo2) / 2.0;
        if(lo1 < 1e18)
            return lo1;
        return 0;
    }
    else
    {
        // 預設: nearest L pivot >= signalBar
        for(int k = 0; k < lCnt; k++)
        {
            if(lB[k] >= signalBar)
                return lP[k] * (1.0 - buySLOffset);
        }
        return 0;
    }
}

//+------------------------------------------------------------------+
//| CalcSellSL - Calculate Sell stop loss                            |
//+------------------------------------------------------------------+
double CalcSellSL(int signalBar, int tlStartOff,
                  const int &hB[], const double &hP[], int hCnt,
                  ENUM_SL_MODE slMode, double sellSLOffset)
{
    if(slMode == SL_MEDIAN)
    {
        double hi1 = -1e18, hi2 = -1e18;
        for(int k = 0; k < hCnt; k++)
        {
            if(hB[k] >= signalBar && hB[k] <= tlStartOff)
            {
                double hp = hP[k];
                if(hp > hi1) { hi2 = hi1; hi1 = hp; }
                else if(hp > hi2) { hi2 = hp; }
            }
        }
        if(hi2 > -1e18)
            return (hi1 + hi2) / 2.0;
        if(hi1 > -1e18)
            return hi1;
        return 0;
    }
    else
    {
        for(int k = 0; k < hCnt; k++)
        {
            if(hB[k] >= signalBar)
                return hP[k] * (1.0 + sellSLOffset);
        }
        return 0;
    }
}

//+------------------------------------------------------------------+
//| CalcTP - Calculate take profit (body symmetric projection)       |
//| Pine lines 770-789 (Buy) / 1207-1227 (Sell)                     |
//+------------------------------------------------------------------+
double CalcTP(int signalBar, int tlStartOff, bool isBuy,
              const double &open[], const double &close[],
              int totalBars, double tpPct)
{
    double bodyHigh = MathMax(open[signalBar], close[signalBar]);
    double bodyLow  = MathMin(open[signalBar], close[signalBar]);

    for(int b = signalBar; b <= tlStartOff; b++)
    {
        if(b >= totalBars) break;
        double bt = MathMax(open[b], close[b]);
        double bb = MathMin(open[b], close[b]);
        if(bt > bodyHigh) bodyHigh = bt;
        if(bb < bodyLow)  bodyLow = bb;
    }

    double range = bodyHigh - bodyLow;
    if(isBuy)
        return bodyHigh + range * (tpPct / 100.0);
    else
        return bodyLow - range * (tpPct / 100.0);
}

//+------------------------------------------------------------------+
//| CalcRR - Calculate risk/reward ratio                             |
//+------------------------------------------------------------------+
double CalcRR(double entryPrice, double slPrice, double tpPrice, bool isBuy)
{
    double reward, risk;
    if(isBuy)
    {
        reward = tpPrice - entryPrice;
        risk = entryPrice - slPrice;
    }
    else
    {
        reward = entryPrice - tpPrice;
        risk = slPrice - entryPrice;
    }

    if(risk > 0 && reward > 0)
        return reward / risk;
    return 0;
}

//+------------------------------------------------------------------+
//| CalcLotSize - Calculate position size                            |
//+------------------------------------------------------------------+
double CalcLotSize(string symbol, double entryPrice, double slPrice,
                   ENUM_LOT_MODE lotMode, double fixedLot, double riskPct)
{
    if(lotMode == LOT_FIXED)
        return fixedLot;

    // Risk percentage mode
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (riskPct / 100.0);

    double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
    double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
    if(tickSize == 0 || tickValue == 0)
        return fixedLot; // fallback

    double pipValue = tickValue / tickSize;
    double slDistance = MathAbs(entryPrice - slPrice);
    if(slDistance == 0)
        return fixedLot; // fallback

    double lot = riskAmount / (slDistance * pipValue);

    // Normalize to volume step
    double minLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
    double maxLot  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
    double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

    if(lotStep > 0)
        lot = MathFloor(lot / lotStep) * lotStep;

    if(lot < minLot) lot = minLot;
    if(lot > maxLot) lot = maxLot;

    return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
//| FillSignalSLTP - Populate SL/TP/RR for all signals               |
//+------------------------------------------------------------------+
void FillSignalSLTP(SignalInfo &signals[], int signalCnt,
                    const int &hB[], const double &hP[], int hCnt,
                    const int &lB[], const double &lP[], int lCnt,
                    const double &open[], const double &close[],
                    int totalBars,
                    ENUM_SL_MODE slMode, double buySLOffset, double sellSLOffset,
                    double tpPct)
{
    for(int i = 0; i < signalCnt; i++)
    {
        bool isBuy = (signals[i].type == SIGNAL_BUY ||
                      signals[i].type == SIGNAL_BUY2 ||
                      signals[i].type == SIGNAL_BUY3);

        int sigBar = signals[i].barOffset;
        int tlStart = signals[i].tlStartBar;

        if(isBuy)
        {
            signals[i].slPrice = CalcBuySL(sigBar, tlStart, lB, lP, lCnt, slMode, buySLOffset);
            signals[i].tpPrice = CalcTP(sigBar, tlStart, true, open, close, totalBars, tpPct);
        }
        else
        {
            signals[i].slPrice = CalcSellSL(sigBar, tlStart, hB, hP, hCnt, slMode, sellSLOffset);
            signals[i].tpPrice = CalcTP(sigBar, tlStart, false, open, close, totalBars, tpPct);
        }

        signals[i].riskReward = CalcRR(signals[i].entryPrice,
                                        signals[i].slPrice,
                                        signals[i].tpPrice, isBuy);
    }
}

//+------------------------------------------------------------------+
//| CheckBreakeven - Move SL to entry after N profitable bars        |
//| Pine lines 1305-1321 (Sell), 1613-1629 (Buy)                    |
//+------------------------------------------------------------------+
bool CheckBreakevenCondition(double entryPrice, bool isBuy,
                             int bevenBars, const double &close[], int totalBars)
{
    // Check if the last N closed bars are all profitable
    // close[1] = last closed bar, close[2] = 2 bars ago, etc.
    if(bevenBars + 1 > totalBars) return false;

    for(int pb = 1; pb <= bevenBars; pb++)
    {
        if(pb >= totalBars) return false;
        if(isBuy && close[pb] < entryPrice) return false;
        if(!isBuy && close[pb] > entryPrice) return false;
    }
    return true;
}

#endif
