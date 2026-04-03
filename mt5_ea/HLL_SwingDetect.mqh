//+------------------------------------------------------------------+
//|                      HLL_SwingDetect.mqh                         |
//|        Swing High/Low detection with equal-price handling        |
//+------------------------------------------------------------------+
#ifndef HLL_SWINGDETECT_MQH
#define HLL_SWINGDETECT_MQH

#include "HLL_Defines.mqh"

//+------------------------------------------------------------------+
//| IsSwingHigh - Pine f_isSwingHigh (lines 121-133)                 |
//| barIndex: offset from current bar (1-based, 0=current skipped)   |
//| Arrays must be set as series (0=current bar)                     |
//+------------------------------------------------------------------+
bool IsSwingHigh(int off, const double &high[], int totalBars)
{
    if(off < 1 || off >= totalBars - 1)
        return false;

    double h = high[off];

    // Case 1: strict swing high
    if(h > high[off + 1] && h > high[off - 1])
        return true;

    // Case 2: equal high on left side — scan left for confirmation
    if(h == high[off + 1] && h > high[off - 1])
    {
        int lf = off + 1;
        while(lf + 1 < totalBars && high[lf + 1] == h)
            lf++;
        if(lf + 1 < totalBars && high[lf + 1] < h)
            return true;
    }

    return false;
}

//+------------------------------------------------------------------+
//| IsSwingLow - Pine f_isSwingLow (lines 136-148)                  |
//+------------------------------------------------------------------+
bool IsSwingLow(int off, const double &low[], int totalBars)
{
    if(off < 1 || off >= totalBars - 1)
        return false;

    double l = low[off];

    // Case 1: strict swing low
    if(l < low[off + 1] && l < low[off - 1])
        return true;

    // Case 2: equal low on left side
    if(l == low[off + 1] && l < low[off - 1])
    {
        int lf = off + 1;
        while(lf + 1 < totalBars && low[lf + 1] == l)
            lf++;
        if(lf + 1 < totalBars && low[lf + 1] > l)
            return true;
    }

    return false;
}

//+------------------------------------------------------------------+
//| CollectSwingPoints - Pine lines 276-297                          |
//| Collects up to maxLabels swing highs and lows                    |
//| from offset 1 to lookback                                        |
//+------------------------------------------------------------------+
void CollectSwingPoints(int lookback, int maxLabels,
                        const double &high[], const double &low[],
                        int totalBars,
                        int &hB[], double &hP[], int &hCnt,
                        int &lB[], double &lP[], int &lCnt)
{
    // Reset
    ArrayResize(hB, 0);
    ArrayResize(hP, 0);
    ArrayResize(lB, 0);
    ArrayResize(lP, 0);
    hCnt = 0;
    lCnt = 0;

    int maxLk = MathMin(lookback, totalBars - 2); // need off+1 access

    // Collect highs
    for(int i = 1; i <= maxLk; i++)
    {
        if(hCnt >= maxLabels)
            break;
        if(IsSwingHigh(i, high, totalBars))
        {
            int newSize = ArraySize(hB) + 1;
            ArrayResize(hB, newSize);
            ArrayResize(hP, newSize);
            hB[newSize - 1] = i;
            hP[newSize - 1] = high[i];
            hCnt++;
        }
    }

    // Collect lows
    for(int i = 1; i <= maxLk; i++)
    {
        if(lCnt >= maxLabels)
            break;
        if(IsSwingLow(i, low, totalBars))
        {
            int newSize = ArraySize(lB) + 1;
            ArrayResize(lB, newSize);
            ArrayResize(lP, newSize);
            lB[newSize - 1] = i;
            lP[newSize - 1] = low[i];
            lCnt++;
        }
    }
}

#endif
