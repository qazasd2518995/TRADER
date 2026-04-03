# HighLowLabels v3.70 MT5 EA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Pine Script HighLowLabels v3.70 indicator into a standalone MT5 Expert Advisor that auto-detects trend structures and places trades on XAUUSD.

**Architecture:** 7 MQL5 files — 1 main EA + 6 include headers. Bottom-up build order: shared types → swing detection → structure detection → trend lines → signals → risk management → drawing → main EA. All bar indexing uses offset (0=current) matching Pine Script convention via `ArraySetAsSeries(true)`.

**Tech Stack:** MQL5 (MetaTrader 5), Pine Script v5 reference at `/Users/justin/Downloads/HighLowLabels_v3.70.pine`

**Spec:** `/Users/justin/trader/docs/superpowers/specs/2026-04-04-highlowlabels-mt5-ea-design.md`

---

### Task 1: Shared Types and Enums

**Files:**
- Create: `mt5_ea/HLL_Defines.mqh`

This file contains all shared enums, structs, and constants used across all modules.

- [ ] **Step 1: Create HLL_Defines.mqh with all shared types**

```cpp
//+------------------------------------------------------------------+
//|                         HLL_Defines.mqh                          |
//|          HighLowLabels v3.70 - Shared Types & Constants          |
//+------------------------------------------------------------------+
#ifndef HLL_DEFINES_MQH
#define HLL_DEFINES_MQH

//--- Custom enums for input parameters
enum ENUM_SL_MODE {
    SL_DEFAULT,   // 預設（最近樞紐點）
    SL_MEDIAN     // 區間中位
};

enum ENUM_LOT_MODE {
    LOT_FIXED,    // 固定手數
    LOT_RISK_PCT  // 風險百分比
};

enum SIGNAL_TYPE {
    SIGNAL_NONE = -1,
    SIGNAL_BUY,      // Buy 主訊號
    SIGNAL_SELL,     // Sell 主訊號
    SIGNAL_BUY2,     // Buy② 重入
    SIGNAL_SELL2,    // Sell② 重入
    SIGNAL_BUY3,     // Buy③ 趨勢線確認加碼
    SIGNAL_SELL3     // Sell③ 趨勢線確認加碼
};

//--- Trend line info (HH or LL pair)
struct TrendLineInfo {
    int    startBar;      // 趨勢線起點 bar offset
    double startPrice;    // 起點價格
    int    endBar;        // 終點 bar offset
    double endPrice;      // 終點價格
    double slope;         // 斜率 (tlSlope)
    int    crossBar;      // 突破/跌破點 offset (-1 = 未突破)
    bool   isActiveCross; // 是否為有效突破
    int    hh1Bar;        // HH1/LL1 的 bar offset
    int    hh2Bar;        // HH2/LL2 的 bar offset
    double minLB;         // HH 對間最低 L 樞紐 (Sell③)
    double maxHB;         // LL 對間最高 H 樞紐 (Buy③)
    int    defRL;         // 前一個有效 HH/LL 的 offset
};

//--- Reentry trend line
struct ReentryLineInfo {
    int    startBar;
    double startPrice;
    int    endBar;
    double endPrice;
    double slope;
    int    crossBar;      // 重入線突破點
    int    triggerBar;    // 觸發重入的 H/L 點
};

//--- Signal info for trade execution
struct SignalInfo {
    SIGNAL_TYPE type;
    int         barOffset;    // 訊號觸發的 bar offset
    double      entryPrice;   // 進場價
    double      slPrice;      // 初始止損價
    double      tpPrice;      // 停利價
    double      riskReward;   // R:R 比
    int         tlStartBar;   // 所屬趨勢線起點 (絕對 bar_index)
    bool        isNew;        // 是否為本次重算新產生
};

//--- Processed signal tracking (anti-duplicate)
struct ProcessedSignal {
    SIGNAL_TYPE type;
    int         tlStartBar;   // 趨勢線起點 bar_index (絕對值)
    datetime    processTime;
    ulong       ticket;       // 對應的倉位 ticket
};

//--- Object name prefix for chart drawing
#define HLL_PREFIX "HLL_"

//--- Max arrays
#define HLL_MAX_POINTS 300

#endif
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_Defines.mqh
git commit -m "feat(mt5): add shared types and enums for HLL EA"
```

---

### Task 2: Swing Point Detection

**Files:**
- Create: `mt5_ea/HLL_SwingDetect.mqh`
- Reference: Pine Script lines 121-297

- [ ] **Step 1: Create HLL_SwingDetect.mqh**

```cpp
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
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_SwingDetect.mqh
git commit -m "feat(mt5): add swing high/low detection module"
```

---

### Task 3: Structure Detection (LL/HH)

**Files:**
- Create: `mt5_ea/HLL_StructureDetect.mqh`
- Reference: Pine Script lines 150-600

- [ ] **Step 1: Create HLL_StructureDetect.mqh with sort utility and LL detection**

```cpp
//+------------------------------------------------------------------+
//|                   HLL_StructureDetect.mqh                        |
//|       LL/HH structure detection with cached zone starts          |
//+------------------------------------------------------------------+
#ifndef HLL_STRUCTUREDETECT_MQH
#define HLL_STRUCTUREDETECT_MQH

#include "HLL_Defines.mqh"

//+------------------------------------------------------------------+
//| SortByOffset - Pine f_sortByOffset (lines 151-161)               |
//| Bubble sort: offset ascending (newest first)                     |
//+------------------------------------------------------------------+
void SortByOffset(int &bars[], double &prices[])
{
    int n = ArraySize(bars);
    for(int i = 0; i < n - 1; i++)
    {
        for(int j = i + 1; j < n; j++)
        {
            if(bars[j] < bars[i])
            {
                int tb = bars[i];
                double tp = prices[i];
                bars[i] = bars[j];
                prices[i] = prices[j];
                bars[j] = tb;
                prices[j] = tp;
            }
        }
    }
}

//+------------------------------------------------------------------+
//| DetectLowerLows - Pine lines 299-450                             |
//| Detects LL structure with hasOHL + hasLH (v3.63) checks          |
//| Includes supplement LL detection (lines 419-450)                 |
//+------------------------------------------------------------------+
int DetectLowerLows(const int &hB[], const double &hP[], int hCnt,
                    const int &lB[], const double &lP[], int lCnt,
                    const double &high[], const double &low[],
                    int totalBars,
                    int &llB[], double &llP[])
{
    ArrayResize(llB, 0);
    ArrayResize(llP, 0);

    // Build breakpoint array: [current bar] + all H points
    int bkB[];
    double bkP[];
    ArrayResize(bkB, hCnt + 1);
    ArrayResize(bkP, hCnt + 1);
    bkB[0] = 0;
    bkP[0] = high[0];
    for(int i = 0; i < hCnt; i++)
    {
        bkB[i + 1] = hB[i];
        bkP[i + 1] = hP[i];
    }
    int bkCnt = hCnt + 1;

    // LL mark array (per L point)
    bool llMk[];
    ArrayResize(llMk, lCnt);
    ArrayInitialize(llMk, false);

    int llCnt = 0;
    int _hZs = 0; // H zone start cache
    int _lZs = 0; // L zone start cache

    for(int bk = 0; bk < bkCnt; bk++)
    {
        int bkBar = bkB[bk];
        double bkPr = bkP[bk];

        // Advance zone starts past bkBar
        while(_hZs < hCnt)
        {
            if(hB[_hZs] > bkBar) break;
            _hZs++;
        }
        while(_lZs < lCnt)
        {
            if(lB[_lZs] > bkBar) break;
            _lZs++;
        }

        if(_hZs >= hCnt || _lZs >= lCnt)
            continue;

        for(int ho = _hZs; ho < hCnt; ho++)
        {
            int hoBar = hB[ho];
            double hoPr = hP[ho];

            if(bkPr <= hoPr)
                continue;

            // Check ho is unexploded (no higher H before it in zone)
            bool unex = true;
            for(int hm = _hZs; hm < hCnt; hm++)
            {
                if(hB[hm] >= hoBar) break;
                if(hP[hm] >= hoPr)
                {
                    unex = false;
                    break;
                }
            }
            if(!unex) continue;

            // Collect L candidates in group (before hoBar)
            int grp[];
            ArrayResize(grp, 0);
            for(int l2 = _lZs; l2 < lCnt; l2++)
            {
                if(lB[l2] >= hoBar) break;
                int newSz = ArraySize(grp) + 1;
                ArrayResize(grp, newSz);
                grp[newSz - 1] = l2;
            }

            int gs = ArraySize(grp);
            if(gs < 2) continue;

            // Find lowest price in group
            int mp = 0;
            for(int g = 1; g < gs; g++)
            {
                if(lP[grp[g]] < lP[grp[mp]])
                    mp = g;
            }
            int mi = grp[mp];

            if(llMk[mi]) continue;

            // Check hasOHL: a higher L exists after mi
            bool hasOHL = false;
            int olhIdx = -1;
            for(int g = 0; g < gs; g++)
            {
                int gi = grp[g];
                if(lB[gi] > lB[mi] && lP[gi] > lP[mi])
                {
                    hasOHL = true;
                    olhIdx = gi;
                    break;
                }
            }

            // v3.63: hasLH - peaks must descend
            bool hasLH = false;
            if(hasOHL)
            {
                int miBar = lB[mi];
                int olhBar = lB[olhIdx];

                // Peak1: highest H pivot between mi and olh
                double peak1 = -1e18;
                for(int ha = _hZs; ha < hCnt; ha++)
                {
                    if(hB[ha] >= olhBar) break;
                    if(hB[ha] <= miBar) continue;
                    if(hP[ha] > peak1) peak1 = hP[ha];
                }
                // Fallback: use bar high if no H pivot found
                if(peak1 <= -1e18 && olhBar - miBar > 1)
                {
                    for(int b = miBar + 1; b < olhBar; b++)
                    {
                        if(b < totalBars && high[b] > peak1)
                            peak1 = high[b];
                    }
                }

                // Peak2: first H pivot after olhBar (older)
                double peak2 = -1e18;
                for(int hb = _hZs; hb < hCnt; hb++)
                {
                    if(hB[hb] >= olhBar)
                    {
                        peak2 = hP[hb];
                        break;
                    }
                }

                // Peaks must descend (or pass if no comparison)
                if(peak1 > -1e18 && peak2 > -1e18)
                    hasLH = (peak1 < peak2);
                else
                    hasLH = true;
            }

            if(hasOHL && hasLH)
            {
                llMk[mi] = true;
                llCnt++;
                int sz = ArraySize(llB) + 1;
                ArrayResize(llB, sz);
                ArrayResize(llP, sz);
                llB[sz - 1] = lB[mi];
                llP[sz - 1] = lP[mi];
            }
        }
    }

    // --- Supplement LL detection (Pine lines 419-450) ---
    SortByOffset(llB, llP);
    int origLLCnt = llCnt;
    for(int i = 0; i < origLLCnt - 1; i++)
    {
        int p1Bar = llB[i];
        double p1Pr = llP[i];
        int p2Bar = llB[i + 1];
        double p2Pr = llP[i + 1];
        double thr = MathMin(p1Pr, p2Pr);

        int mcBar = -1;
        double mcVal = 1e18;
        if(p2Bar - p1Bar > 2)
        {
            for(int b = p1Bar + 1; b < p2Bar; b++)
            {
                if(b < totalBars && low[b] < mcVal)
                {
                    mcVal = low[b];
                    mcBar = b;
                }
            }
        }

        if(mcBar >= 0 && mcVal < thr)
        {
            bool dup = false;
            for(int k = 0; k < llCnt; k++)
            {
                if(llB[k] == mcBar)
                {
                    dup = true;
                    break;
                }
            }
            if(!dup)
            {
                llCnt++;
                int sz = ArraySize(llB) + 1;
                ArrayResize(llB, sz);
                ArrayResize(llP, sz);
                llB[sz - 1] = mcBar;
                llP[sz - 1] = low[mcBar];
            }
        }
    }

    return llCnt;
}

//+------------------------------------------------------------------+
//| DetectHigherHighs - Pine lines 452-600                           |
//| Symmetric to DetectLowerLows with hasHL trough comparison        |
//+------------------------------------------------------------------+
int DetectHigherHighs(const int &hB[], const double &hP[], int hCnt,
                      const int &lB[], const double &lP[], int lCnt,
                      const double &high[], const double &low[],
                      const double &close[],
                      int totalBars,
                      int &hhB[], double &hhP[])
{
    ArrayResize(hhB, 0);
    ArrayResize(hhP, 0);

    // Build breakpoint array: [current bar] + all L points
    int bkB[];
    double bkP[];
    ArrayResize(bkB, lCnt + 1);
    ArrayResize(bkP, lCnt + 1);
    bkB[0] = 0;
    bkP[0] = low[0];
    for(int i = 0; i < lCnt; i++)
    {
        bkB[i + 1] = lB[i];
        bkP[i + 1] = lP[i];
    }
    int bkLCnt = lCnt + 1;

    bool hhMk[];
    ArrayResize(hhMk, hCnt);
    ArrayInitialize(hhMk, false);

    int hhCnt = 0;
    int _lZs2 = 0;
    int _hZs2 = 0;

    for(int bk = 0; bk < bkLCnt; bk++)
    {
        int bkBar = bkB[bk];
        double bkPr = bkP[bk];

        while(_lZs2 < lCnt)
        {
            if(lB[_lZs2] > bkBar) break;
            _lZs2++;
        }
        while(_hZs2 < hCnt)
        {
            if(hB[_hZs2] > bkBar) break;
            _hZs2++;
        }

        if(_lZs2 >= lCnt || _hZs2 >= hCnt)
            continue;

        for(int lo = _lZs2; lo < lCnt; lo++)
        {
            int loBar = lB[lo];
            double loPr = lP[lo];

            if(bkPr >= loPr) continue;

            bool unex = true;
            for(int lm = _lZs2; lm < lCnt; lm++)
            {
                if(lB[lm] >= loBar) break;
                if(lP[lm] <= loPr)
                {
                    unex = false;
                    break;
                }
            }
            if(!unex) continue;

            int grp[];
            ArrayResize(grp, 0);
            for(int h2 = _hZs2; h2 < hCnt; h2++)
            {
                if(hB[h2] >= loBar) break;
                int newSz = ArraySize(grp) + 1;
                ArrayResize(grp, newSz);
                grp[newSz - 1] = h2;
            }

            int gs = ArraySize(grp);
            if(gs < 2) continue;

            int mp = 0;
            for(int g = 1; g < gs; g++)
            {
                if(hP[grp[g]] > hP[grp[mp]])
                    mp = g;
            }
            int mi = grp[mp];

            if(hhMk[mi]) continue;

            // hasOLH: a lower H exists after mi
            bool hasOLH = false;
            int olhIdx = -1;
            for(int g = 0; g < gs; g++)
            {
                int gi = grp[g];
                if(hB[gi] > hB[mi] && hP[gi] < hP[mi])
                {
                    hasOLH = true;
                    olhIdx = gi;
                    break;
                }
            }

            // v3.63: hasHL - troughs must ascend
            bool hasHL = false;
            if(hasOLH)
            {
                int miBar = hB[mi];
                int olhBar = hB[olhIdx];

                double trough1 = 1e18;
                for(int la = _lZs2; la < lCnt; la++)
                {
                    if(lB[la] >= olhBar) break;
                    if(lB[la] <= miBar) continue;
                    if(lP[la] < trough1) trough1 = lP[la];
                }
                if(trough1 >= 1e18 && olhBar - miBar > 1)
                {
                    for(int b = miBar + 1; b < olhBar; b++)
                    {
                        if(b < totalBars && low[b] < trough1)
                            trough1 = low[b];
                    }
                }

                double trough2 = 1e18;
                for(int lb = _lZs2; lb < lCnt; lb++)
                {
                    if(lB[lb] >= olhBar)
                    {
                        trough2 = lP[lb];
                        break;
                    }
                }

                if(trough1 < 1e18 && trough2 < 1e18)
                    hasHL = (trough1 > trough2);
                else
                    hasHL = true;
            }

            if(hasOLH && hasHL)
            {
                hhMk[mi] = true;
                hhCnt++;
                int sz = ArraySize(hhB) + 1;
                ArrayResize(hhB, sz);
                ArrayResize(hhP, sz);
                hhB[sz - 1] = hB[mi];
                hhP[sz - 1] = hP[mi];
            }
        }
    }

    // --- Supplement HH detection (Pine lines 569-600) ---
    SortByOffset(hhB, hhP);
    int origHHCnt = hhCnt;
    for(int i = 0; i < origHHCnt - 1; i++)
    {
        int p1Bar = hhB[i];
        double p1Pr = hhP[i];
        int p2Bar = hhB[i + 1];
        double p2Pr = hhP[i + 1];
        double thr = MathMax(p1Pr, p2Pr);

        int mcBar = -1;
        double mcVal = -1e18;
        if(p2Bar - p1Bar > 2)
        {
            for(int b = p1Bar + 1; b < p2Bar; b++)
            {
                if(b < totalBars && close[b] > mcVal)
                {
                    mcVal = close[b];
                    mcBar = b;
                }
            }
        }

        if(mcBar >= 0 && mcVal > thr)
        {
            bool dup = false;
            for(int k = 0; k < hhCnt; k++)
            {
                if(hhB[k] == mcBar)
                {
                    dup = true;
                    break;
                }
            }
            if(!dup)
            {
                hhCnt++;
                int sz = ArraySize(hhB) + 1;
                ArrayResize(hhB, sz);
                ArrayResize(hhP, sz);
                hhB[sz - 1] = mcBar;
                hhP[sz - 1] = high[mcBar];
            }
        }
    }

    return hhCnt;
}

#endif
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_StructureDetect.mqh
git commit -m "feat(mt5): add LL/HH structure detection with v3.63 checks"
```

---

### Task 4: Trend Line Calculation — HH Descending

**Files:**
- Create: `mt5_ea/HLL_TrendLine.mqh`
- Reference: Pine Script lines 602-1031 (HH), 1033-1585 (LL)

This is the largest and most complex module. It handles:
- HH/LL distance filtering
- v3.65 validity checks
- v3.58 tangent scan (4 steps)
- Breakout/breakdown detection
- isActiveCross tracking
- v3.70 newerHH1/newerLL1 truncation
- Buy②/Sell② reentry trend lines
- Sell③/Buy③ confirmation signals
- buyCrossB/sellCrossB collection

- [ ] **Step 1: Create HLL_TrendLine.mqh with HH descending trend line logic**

```cpp
//+------------------------------------------------------------------+
//|                      HLL_TrendLine.mqh                           |
//|     Trend line calculation: tangent scan, reentry, truncation    |
//+------------------------------------------------------------------+
#ifndef HLL_TRENDLINE_MQH
#define HLL_TRENDLINE_MQH

#include "HLL_Defines.mqh"
#include "HLL_StructureDetect.mqh"

//+------------------------------------------------------------------+
//| FindHit - Pine f_findHit (lines 165-190)                         |
//| Detects SL hit by price or reverse TL crossover                  |
//| Returns: hitOff (bar offset), hitByC1 (true=price hit)           |
//+------------------------------------------------------------------+
void FindHit(int prevOff, int endOff, double curSL, bool isSell,
             const double &high[], const double &low[],
             const int &crossBars[], int crossBarsCnt,
             int &hitOff, bool &hitByC1)
{
    hitOff = -1;
    hitByC1 = false;

    int hitC1 = -1;
    if(prevOff > endOff)
    {
        for(int chk = prevOff - 1; chk >= endOff; chk--)
        {
            if((isSell && high[chk] >= curSL) || (!isSell && low[chk] <= curSL))
            {
                hitC1 = chk;
                break;
            }
        }
    }

    int hitC2 = -1;
    if(crossBarsCnt > 0)
    {
        for(int bc = 0; bc < crossBarsCnt; bc++)
        {
            int bcOff = crossBars[bc];
            if(bcOff >= endOff && bcOff < prevOff)
            {
                if(hitC2 < 0 || bcOff > hitC2)
                    hitC2 = bcOff;
            }
        }
    }

    if(hitC1 >= 0 && hitC2 >= 0)
    {
        hitOff = MathMax(hitC1, hitC2);
        hitByC1 = (hitC1 >= hitC2);
    }
    else if(hitC1 >= 0)
    {
        hitOff = hitC1;
        hitByC1 = true;
    }
    else if(hitC2 >= 0)
    {
        hitOff = hitC2;
    }
}

//+------------------------------------------------------------------+
//| CalcHHTrendLines - Pine lines 602-1031                           |
//| Calculates HH descending trend lines                             |
//| MUST be called BEFORE CalcLLTrendLines (buyCrossB dependency)    |
//|                                                                  |
//| Outputs:                                                         |
//|   hhTL[]          - trend line info array                        |
//|   hhReentry[]     - reentry line info array                      |
//|   buyCrossB[]     - buy breakout bar offsets (for Sell SL)       |
//|   signals[]       - Buy, Buy②, Sell③ signals appended           |
//+------------------------------------------------------------------+
void CalcHHTrendLines(
    int &hhB[], double &hhP[], int &hhCnt,
    const int &hB[], const double &hP[], int hCnt,
    const int &lB[], const double &lP[], int lCnt,
    const double &high[], const double &low[],
    const double &open[], const double &close[],
    int totalBars,
    int minHHDist, bool enableReentry,
    // outputs
    TrendLineInfo &hhTL[], int &hhTLCnt,
    ReentryLineInfo &hhReentry[], int &hhReentryCnt,
    int &buyCrossB[], int &buyCrossCnt,
    SignalInfo &signals[], int &signalCnt)
{
    hhTLCnt = 0;
    hhReentryCnt = 0;
    buyCrossCnt = 0;
    ArrayResize(hhTL, 0);
    ArrayResize(hhReentry, 0);
    ArrayResize(buyCrossB, 0);

    // --- Distance filtering (Pine 606-621) ---
    SortByOffset(hhB, hhP);
    int fhB[];
    double fhP[];
    ArrayResize(fhB, 0);
    ArrayResize(fhP, 0);
    for(int fi = 0; fi < hhCnt; fi++)
    {
        if(ArraySize(fhB) == 0)
        {
            int sz = 1;
            ArrayResize(fhB, sz);
            ArrayResize(fhP, sz);
            fhB[0] = hhB[fi];
            fhP[0] = hhP[fi];
        }
        else
        {
            if(hhB[fi] - fhB[ArraySize(fhB) - 1] >= minHHDist)
            {
                int sz = ArraySize(fhB) + 1;
                ArrayResize(fhB, sz);
                ArrayResize(fhP, sz);
                fhB[sz - 1] = hhB[fi];
                fhP[sz - 1] = hhP[fi];
            }
        }
    }
    // Replace hhB/hhP with filtered
    ArrayResize(hhB, ArraySize(fhB));
    ArrayResize(hhP, ArraySize(fhP));
    ArrayCopy(hhB, fhB);
    ArrayCopy(hhP, fhP);
    hhCnt = ArraySize(hhB);

    int newerHH1 = -1;
    int lastActiveHHCross = -1;

    for(int hh = 0; hh < hhCnt - 1; hh++)
    {
        int hh1Bar = hhB[hh];
        double hh1Pr = hhP[hh];
        int hh2Bar = hhB[hh + 1];
        double hh2Pr = hhP[hh + 1];

        // Must be descending: hh2 (older) price > hh1 (newer) price
        if(hh2Pr <= hh1Pr) continue;

        // --- v3.65: validity check (Pine 631-649) ---
        int defRL = (hh > 0) ? hhB[hh - 1] : 0;

        double minPA = 1e18;
        if(hh1Bar - 1 >= defRL + 1)
        {
            for(int b = defRL + 1; b <= hh1Bar - 1; b++)
            {
                if(b < totalBars && low[b] < minPA)
                    minPA = low[b];
            }
        }

        double minLB = 1e18;
        for(int l = 0; l < lCnt; l++)
        {
            if(lB[l] >= hh2Bar) break;
            if(lB[l] > hh1Bar)
            {
                if(lP[l] < minLB) minLB = lP[l];
            }
        }

        if(minPA < 1e18 && minLB < 1e18 && minPA >= minLB)
            continue;

        // --- Start point scan (Pine 652-656) ---
        double startPr = hh2Pr;
        int startOff = hh2Bar;
        for(int b = hh1Bar; b <= hh2Bar; b++)
        {
            if(b < totalBars && high[b] > startPr)
            {
                startPr = high[b];
                startOff = b;
            }
        }

        // --- v3.58 tangent scan 4 steps (Pine 657-696) ---

        // Step 1: valley between hh1Bar and startOff
        int valleyBar = hh1Bar;
        double valleyLow = (hh1Bar < totalBars) ? low[hh1Bar] : 1e18;
        for(int b = hh1Bar + 1; b < startOff; b++)
        {
            if(b < totalBars && low[b] < valleyLow)
            {
                valleyLow = low[b];
                valleyBar = b;
            }
        }

        // Step 2: find L pivot after HH1 that broke below valley
        int scanStart = hh1Bar;
        for(int j = 0; j < lCnt; j++)
        {
            if(lB[j] >= hh1Bar) break;
            if(lP[j] < valleyLow)
                scanStart = lB[j];
        }

        // Step 3 (Phase 1): find anchor with max slope in [scanStart, valleyBar]
        double bestS = -1e18;
        int anchorBar = scanStart;
        double anchorPr = (scanStart < totalBars) ?
            MathMax(open[scanStart], close[scanStart]) : 0;
        for(int b = scanStart; b <= valleyBar; b++)
        {
            if(b >= totalBars || b == startOff) continue;
            double bodyTop = MathMax(open[b], close[b]);
            double d = (double)(startOff - b);
            if(d == 0) continue;
            double s = (bodyTop - startPr) / d;
            if(s > bestS)
            {
                bestS = s;
                anchorBar = b;
                anchorPr = bodyTop;
            }
        }

        // Step 4 (Phase 2): refine slope in [valleyBar+1, startOff-1]
        double d_anchor = (double)(startOff - anchorBar);
        if(d_anchor == 0) continue;
        double sl = (startPr - anchorPr) / d_anchor;
        for(int b = valleyBar + 1; b < startOff; b++)
        {
            if(b >= totalBars) continue;
            double bodyTop = MathMax(open[b], close[b]);
            double d_b = (double)(b - anchorBar);
            if(d_b == 0) continue;
            double s = (bodyTop - anchorPr) / d_b;
            if(s > sl) sl = s;
        }

        double lineStartPr = anchorPr + sl * (double)(startOff - anchorBar);
        double tlSlope = -sl;
        if(tlSlope >= 0) continue;

        startPr = lineStartPr;

        // --- Breakout detection (Pine 697-705) ---
        int crossBar = -1;
        if(hh1Bar - 1 >= 0)
        {
            int bScan = hh1Bar - 1;
            while(bScan >= 0)
            {
                double lineP = startPr + tlSlope * (double)(startOff - bScan);
                if(bScan < totalBars && close[bScan] > lineP)
                {
                    crossBar = bScan;
                    break;
                }
                bScan--;
            }
        }

        // --- isActiveCross (Pine 707-716) ---
        bool isActiveCross = false;
        if(crossBar >= 0)
        {
            if(lastActiveHHCross < 0 || crossBar > lastActiveHHCross)
                isActiveCross = true;
        }

        if(isActiveCross)
        {
            int sz = ArraySize(buyCrossB) + 1;
            ArrayResize(buyCrossB, sz);
            buyCrossB[sz - 1] = crossBar;
            buyCrossCnt = sz;
            lastActiveHHCross = crossBar;
        }

        if(crossBar < 0)
            lastActiveHHCross = -1;

        // --- Truncation (Pine 717-720) ---
        int endOff = (crossBar >= 0) ? MathMax(crossBar - 2, 0) : 0;
        if(newerHH1 >= 0 && endOff < newerHH1)
            endOff = newerHH1;
        double endPr = startPr + tlSlope * (double)(startOff - endOff);

        // Store trend line
        int tlIdx = hhTLCnt;
        hhTLCnt++;
        ArrayResize(hhTL, hhTLCnt);
        hhTL[tlIdx].startBar = startOff;
        hhTL[tlIdx].startPrice = startPr;
        hhTL[tlIdx].endBar = endOff;
        hhTL[tlIdx].endPrice = endPr;
        hhTL[tlIdx].slope = tlSlope;
        hhTL[tlIdx].crossBar = crossBar;
        hhTL[tlIdx].isActiveCross = isActiveCross;
        hhTL[tlIdx].hh1Bar = hh1Bar;
        hhTL[tlIdx].hh2Bar = hh2Bar;
        hhTL[tlIdx].minLB = minLB;
        hhTL[tlIdx].maxHB = -1;  // Not used for HH lines
        hhTL[tlIdx].defRL = defRL;

        // --- Sell③ signal (Pine 723-735) ---
        if(isActiveCross && minLB < 1e18 && hh1Bar - 1 >= defRL + 1)
        {
            for(int b = hh1Bar - 1; b >= MathMax(defRL + 1, 0); b--)
            {
                if(b < totalBars && close[b] < minLB)
                {
                    int sIdx = signalCnt;
                    signalCnt++;
                    ArrayResize(signals, signalCnt);
                    signals[sIdx].type = SIGNAL_SELL3;
                    signals[sIdx].barOffset = b;
                    signals[sIdx].entryPrice = close[b];
                    signals[sIdx].slPrice = 0;
                    signals[sIdx].tpPrice = 0;
                    signals[sIdx].riskReward = 0;
                    signals[sIdx].tlStartBar = startOff;
                    signals[sIdx].isNew = (b == 1);
                    break;
                }
            }
        }

        // --- Buy signal (Pine 742-751) ---
        if(isActiveCross && crossBar >= 1)
        {
            int buyScan = crossBar - 1;
            if(buyScan < totalBars)
            {
                int sIdx = signalCnt;
                signalCnt++;
                ArrayResize(signals, signalCnt);
                signals[sIdx].type = SIGNAL_BUY;
                signals[sIdx].barOffset = buyScan;
                signals[sIdx].entryPrice = close[buyScan];
                signals[sIdx].slPrice = 0;  // Calculated later by RiskManager
                signals[sIdx].tpPrice = 0;
                signals[sIdx].riskReward = 0;
                signals[sIdx].tlStartBar = startOff;
                signals[sIdx].isNew = (buyScan == 1);
            }
        }

        // --- Buy② reentry (Pine 833-1031) ---
        if(isActiveCross && enableReentry)
        {
            // Step 0: min L in [crossBar, hh1Bar]
            double _minLZone = 1e18;
            int _minLZoneBar = -1;
            for(int rl = 0; rl < lCnt; rl++)
            {
                if(lB[rl] >= crossBar && lB[rl] <= hh1Bar)
                {
                    if(lP[rl] < _minLZone)
                    {
                        _minLZone = lP[rl];
                        _minLZoneBar = lB[rl];
                    }
                }
            }

            // Step 1: collect consecutive new-low L points
            int _reLLBars[];
            ArrayResize(_reLLBars, 0);
            if(_minLZone < 1e18)
            {
                double _runMin = _minLZone;
                for(int rl = lCnt - 1; rl >= 0; rl--)
                {
                    if(lB[rl] >= crossBar) continue;
                    if(lP[rl] < _runMin)
                    {
                        int sz = ArraySize(_reLLBars) + 1;
                        ArrayResize(_reLLBars, sz);
                        _reLLBars[sz - 1] = lB[rl];
                        _runMin = lP[rl];
                    }
                }
            }

            int _reLLCnt = ArraySize(_reLLBars);
            for(int ri = 0; ri < _reLLCnt; ri++)
            {
                int _reLLBar = _reLLBars[ri];
                if(startOff <= _reLLBar) continue;

                // Step 2: scan body tops for max slope (closest to zero negative)
                double _reSlope = -1e18;
                for(int rb = _reLLBar; rb < startOff; rb++)
                {
                    if(rb >= totalBars) continue;
                    double bodyTop = MathMax(open[rb], close[rb]);
                    double d = (double)(startOff - rb);
                    if(d == 0) continue;
                    double s = (bodyTop - startPr) / d;
                    if(s > _reSlope) _reSlope = s;
                }

                // Check for newer HH TL in range
                bool _skipReentry = false;
                for(int chk = 0; chk < hhCnt - 1; chk++)
                {
                    int cHH1 = hhB[chk];
                    if(cHH1 >= _reLLBar && cHH1 <= _minLZoneBar)
                    {
                        if(hhP[chk + 1] > hhP[chk])
                        {
                            _skipReentry = true;
                            break;
                        }
                    }
                }

                if(_reSlope < 0 && _reSlope > -1e18 && !_skipReentry)
                {
                    // Step 3: breakout detection
                    int _reCross = -1;
                    if(_reLLBar - 1 >= 0)
                    {
                        int rs = _reLLBar - 1;
                        while(rs >= 0)
                        {
                            double lp = startPr + _reSlope * (double)(startOff - rs);
                            if(rs < totalBars && close[rs] > lp)
                            {
                                _reCross = rs;
                                break;
                            }
                            rs--;
                        }
                    }

                    // v3.69: invalidate if cross before newerHH1
                    if(newerHH1 >= 0 && _reCross >= 0 && _reCross < newerHH1)
                        _reCross = -1;

                    int _reEnd = (_reCross >= 0) ? MathMax(_reCross - 2, 0) : 0;
                    if(newerHH1 >= 0 && _reEnd < newerHH1)
                        _reEnd = newerHH1;

                    double _reEndPr = startPr + _reSlope * (double)(startOff - _reEnd);

                    // Store reentry line
                    int reIdx = hhReentryCnt;
                    hhReentryCnt++;
                    ArrayResize(hhReentry, hhReentryCnt);
                    hhReentry[reIdx].startBar = startOff;
                    hhReentry[reIdx].startPrice = startPr;
                    hhReentry[reIdx].endBar = _reEnd;
                    hhReentry[reIdx].endPrice = _reEndPr;
                    hhReentry[reIdx].slope = _reSlope;
                    hhReentry[reIdx].crossBar = _reCross;
                    hhReentry[reIdx].triggerBar = _reLLBar;

                    // Buy② signal
                    if(_reCross >= 1)
                    {
                        int _reBuyBar = _reCross - 1;
                        if(_reBuyBar < totalBars)
                        {
                            int sIdx = signalCnt;
                            signalCnt++;
                            ArrayResize(signals, signalCnt);
                            signals[sIdx].type = SIGNAL_BUY2;
                            signals[sIdx].barOffset = _reBuyBar;
                            signals[sIdx].entryPrice = close[_reBuyBar];
                            signals[sIdx].slPrice = 0;
                            signals[sIdx].tpPrice = 0;
                            signals[sIdx].riskReward = 0;
                            signals[sIdx].tlStartBar = startOff;
                            signals[sIdx].isNew = (_reBuyBar == 1);
                        }
                    }
                }
            }
        }

        newerHH1 = hh1Bar;
    }
}

//+------------------------------------------------------------------+
//| CalcLLTrendLines - Pine lines 1033-1585                          |
//| Calculates LL ascending trend lines                              |
//| MUST be called AFTER CalcHHTrendLines (needs buyCrossB)          |
//+------------------------------------------------------------------+
void CalcLLTrendLines(
    int &llB[], double &llP[], int &llCnt,
    const int &hB[], const double &hP[], int hCnt,
    const int &lB[], const double &lP[], int lCnt,
    const double &high[], const double &low[],
    const double &open[], const double &close[],
    int totalBars,
    int minLLDist, bool enableReentry,
    const int &buyCrossB[], int buyCrossCnt,
    // outputs
    TrendLineInfo &llTL[], int &llTLCnt,
    ReentryLineInfo &llReentry[], int &llReentryCnt,
    int &sellCrossB[], int &sellCrossCnt,
    SignalInfo &signals[], int &signalCnt)
{
    llTLCnt = 0;
    llReentryCnt = 0;
    sellCrossCnt = 0;
    ArrayResize(llTL, 0);
    ArrayResize(llReentry, 0);
    ArrayResize(sellCrossB, 0);

    // --- Distance filtering v3.42 (Pine 1036-1057) ---
    SortByOffset(llB, llP);
    int fB[];
    double fP[];
    ArrayResize(fB, 0);
    ArrayResize(fP, 0);
    for(int fi = 0; fi < llCnt; fi++)
    {
        int fSz = ArraySize(fB);
        if(fSz == 0)
        {
            ArrayResize(fB, 1);
            ArrayResize(fP, 1);
            fB[0] = llB[fi];
            fP[0] = llP[fi];
        }
        else
        {
            if(llB[fi] - fB[fSz - 1] >= minLLDist)
            {
                ArrayResize(fB, fSz + 1);
                ArrayResize(fP, fSz + 1);
                fB[fSz] = llB[fi];
                fP[fSz] = llP[fi];
            }
            else
            {
                // v3.42: keep lower price
                if(llP[fi] < fP[fSz - 1])
                {
                    fB[fSz - 1] = llB[fi];
                    fP[fSz - 1] = llP[fi];
                }
            }
        }
    }
    ArrayResize(llB, ArraySize(fB));
    ArrayResize(llP, ArraySize(fP));
    ArrayCopy(llB, fB);
    ArrayCopy(llP, fP);
    llCnt = ArraySize(llB);

    int newerLL1 = -1;
    int lastActiveLLCross = -1;

    for(int ll = 0; ll < llCnt - 1; ll++)
    {
        int ll1Bar = llB[ll];
        double ll1Pr = llP[ll];
        int ll2Bar = llB[ll + 1];
        double ll2Pr = llP[ll + 1];

        // Must be ascending: ll2 (older) price < ll1 (newer) price
        if(ll2Pr >= ll1Pr) continue;

        // --- v3.65 validity check (Pine 1067-1085) ---
        int defRL = (ll > 0) ? llB[ll - 1] : 0;

        double maxPA = -1.0;
        if(ll1Bar - 1 >= defRL + 1)
        {
            for(int b = defRL + 1; b <= ll1Bar - 1; b++)
            {
                if(b < totalBars && high[b] > maxPA)
                    maxPA = high[b];
            }
        }

        double maxHB = -1.0;
        for(int h = 0; h < hCnt; h++)
        {
            if(hB[h] >= ll2Bar) break;
            if(hB[h] > ll1Bar)
            {
                if(hP[h] > maxHB) maxHB = hP[h];
            }
        }

        if(maxPA >= 0 && maxHB >= 0 && maxPA <= maxHB)
            continue;

        // --- Start point scan (Pine 1088-1092) ---
        double startPr = ll2Pr;
        int startOff = ll2Bar;
        for(int b = ll1Bar; b <= ll2Bar; b++)
        {
            if(b < totalBars && low[b] < startPr)
            {
                startPr = low[b];
                startOff = b;
            }
        }

        // --- v3.58 tangent scan (Pine 1093-1131) ---

        // Step 1: peak between ll1Bar and startOff
        int peakBar = ll1Bar;
        double peakHigh = (ll1Bar < totalBars) ? high[ll1Bar] : -1e18;
        for(int b = ll1Bar + 1; b < startOff; b++)
        {
            if(b < totalBars && high[b] > peakHigh)
            {
                peakHigh = high[b];
                peakBar = b;
            }
        }

        // Step 2: find H pivot after LL1 that broke above peak
        int scanStart = ll1Bar;
        for(int j = 0; j < hCnt; j++)
        {
            if(hB[j] >= ll1Bar) break;
            if(hP[j] > peakHigh)
                scanStart = hB[j];
        }

        // Step 3 (Phase 1): find anchor with min slope
        double bestS = 1e18;
        int anchorBar = scanStart;
        double anchorPr = (scanStart < totalBars) ?
            MathMin(open[scanStart], close[scanStart]) : 1e18;
        for(int b = scanStart; b <= peakBar; b++)
        {
            if(b >= totalBars || b == startOff) continue;
            double bodyBot = MathMin(open[b], close[b]);
            double d = (double)(startOff - b);
            if(d == 0) continue;
            double s = (bodyBot - startPr) / d;
            if(s < bestS)
            {
                bestS = s;
                anchorBar = b;
                anchorPr = bodyBot;
            }
        }

        // Step 4 (Phase 2): refine slope
        double d_anchor = (double)(startOff - anchorBar);
        if(d_anchor == 0) continue;
        double sl = (startPr - anchorPr) / d_anchor;
        for(int b = peakBar + 1; b < startOff; b++)
        {
            if(b >= totalBars) continue;
            double bodyBot = MathMin(open[b], close[b]);
            double d_b = (double)(b - anchorBar);
            if(d_b == 0) continue;
            double s = (bodyBot - anchorPr) / d_b;
            if(s < sl) sl = s;
        }

        double lineStartPr = anchorPr + sl * (double)(startOff - anchorBar);
        double tlSlope = -sl;
        if(tlSlope <= 0) continue;

        startPr = lineStartPr;

        // --- Breakdown detection (Pine 1133-1141) ---
        int crossBar = -1;
        if(ll1Bar - 1 >= 0)
        {
            int bScan = ll1Bar - 1;
            while(bScan >= 0)
            {
                double lineP = startPr + tlSlope * (double)(startOff - bScan);
                if(bScan < totalBars && close[bScan] < lineP)
                {
                    crossBar = bScan;
                    break;
                }
                bScan--;
            }
        }

        // --- isActiveCross (Pine 1143-1152) ---
        bool isActiveCross = false;
        if(crossBar >= 0)
        {
            if(lastActiveLLCross < 0 || crossBar > lastActiveLLCross)
                isActiveCross = true;
        }

        if(isActiveCross)
        {
            int sz = ArraySize(sellCrossB) + 1;
            ArrayResize(sellCrossB, sz);
            sellCrossB[sz - 1] = crossBar;
            sellCrossCnt = sz;
            lastActiveLLCross = crossBar;
        }

        if(crossBar < 0)
            lastActiveLLCross = -1;

        // --- Truncation (Pine 1153-1155) ---
        int endOff = (crossBar >= 0) ? MathMax(crossBar - 2, 0) : 0;
        if(newerLL1 >= 0 && endOff < newerLL1)
            endOff = newerLL1;
        double endPr = startPr + tlSlope * (double)(startOff - endOff);

        // Store trend line
        int tlIdx = llTLCnt;
        llTLCnt++;
        ArrayResize(llTL, llTLCnt);
        llTL[tlIdx].startBar = startOff;
        llTL[tlIdx].startPrice = startPr;
        llTL[tlIdx].endBar = endOff;
        llTL[tlIdx].endPrice = endPr;
        llTL[tlIdx].slope = tlSlope;
        llTL[tlIdx].crossBar = crossBar;
        llTL[tlIdx].isActiveCross = isActiveCross;
        llTL[tlIdx].hh1Bar = ll1Bar;
        llTL[tlIdx].hh2Bar = ll2Bar;
        llTL[tlIdx].minLB = -1;
        llTL[tlIdx].maxHB = maxHB;
        llTL[tlIdx].defRL = defRL;

        // --- Buy③ signal (Pine 1159-1171) ---
        if(isActiveCross && maxHB > -1e18 && ll1Bar - 1 >= defRL + 1)
        {
            for(int b = ll1Bar - 1; b >= MathMax(defRL + 1, 0); b--)
            {
                if(b < totalBars && close[b] > maxHB)
                {
                    int sIdx = signalCnt;
                    signalCnt++;
                    ArrayResize(signals, signalCnt);
                    signals[sIdx].type = SIGNAL_BUY3;
                    signals[sIdx].barOffset = b;
                    signals[sIdx].entryPrice = close[b];
                    signals[sIdx].slPrice = 0;
                    signals[sIdx].tpPrice = 0;
                    signals[sIdx].riskReward = 0;
                    signals[sIdx].tlStartBar = startOff;
                    signals[sIdx].isNew = (b == 1);
                    break;
                }
            }
        }

        // --- Sell signal (Pine 1179-1206) ---
        if(isActiveCross && crossBar >= 1)
        {
            int sellScan = crossBar - 1;
            if(sellScan < totalBars)
            {
                int sIdx = signalCnt;
                signalCnt++;
                ArrayResize(signals, signalCnt);
                signals[sIdx].type = SIGNAL_SELL;
                signals[sIdx].barOffset = sellScan;
                signals[sIdx].entryPrice = close[sellScan];
                signals[sIdx].slPrice = 0;
                signals[sIdx].tpPrice = 0;
                signals[sIdx].riskReward = 0;
                signals[sIdx].tlStartBar = startOff;
                signals[sIdx].isNew = (sellScan == 1);
            }
        }

        // --- Sell② reentry (Pine 1378-1585) ---
        if(isActiveCross && enableReentry)
        {
            // Step 0: max H in [crossBar, ll1Bar]
            double _maxHZone = -1e18;
            int _maxHZoneBar = -1;
            for(int rh = 0; rh < hCnt; rh++)
            {
                if(hB[rh] >= crossBar && hB[rh] <= ll1Bar)
                {
                    if(hP[rh] > _maxHZone)
                    {
                        _maxHZone = hP[rh];
                        _maxHZoneBar = hB[rh];
                    }
                }
            }

            // Step 1: collect consecutive new-high H points
            int _reHHBars[];
            ArrayResize(_reHHBars, 0);
            if(_maxHZone > -1e18)
            {
                double _runMax = _maxHZone;
                for(int rh = hCnt - 1; rh >= 0; rh--)
                {
                    if(hB[rh] >= crossBar) continue;
                    if(hP[rh] > _runMax)
                    {
                        int sz = ArraySize(_reHHBars) + 1;
                        ArrayResize(_reHHBars, sz);
                        _reHHBars[sz - 1] = hB[rh];
                        _runMax = hP[rh];
                    }
                }
            }

            int _reHHCnt = ArraySize(_reHHBars);
            for(int ri = 0; ri < _reHHCnt; ri++)
            {
                int _reHHBar = _reHHBars[ri];
                if(startOff <= _reHHBar) continue;

                // Step 2: scan body bots for min slope
                double _reSlope = 1e18;
                for(int rb = _reHHBar; rb < startOff; rb++)
                {
                    if(rb >= totalBars) continue;
                    double bodyBot = MathMin(open[rb], close[rb]);
                    double d = (double)(startOff - rb);
                    if(d == 0) continue;
                    double s = (bodyBot - startPr) / d;
                    if(s < _reSlope) _reSlope = s;
                }

                // Check for newer LL TL in range
                bool _skipReentry = false;
                for(int chk = 0; chk < llCnt - 1; chk++)
                {
                    int cLL1 = llB[chk];
                    if(cLL1 >= _reHHBar && cLL1 <= _maxHZoneBar)
                    {
                        if(llP[chk + 1] < llP[chk])
                        {
                            _skipReentry = true;
                            break;
                        }
                    }
                }

                if(_reSlope > 0 && _reSlope < 1e18 && !_skipReentry)
                {
                    // Step 3: breakdown detection
                    int _reCross = -1;
                    if(_reHHBar - 1 >= 0)
                    {
                        int rs = _reHHBar - 1;
                        while(rs >= 0)
                        {
                            double lp = startPr + _reSlope * (double)(startOff - rs);
                            if(rs < totalBars && close[rs] < lp)
                            {
                                _reCross = rs;
                                break;
                            }
                            rs--;
                        }
                    }

                    // v3.69: invalidate if cross before newerLL1
                    if(newerLL1 >= 0 && _reCross >= 0 && _reCross < newerLL1)
                        _reCross = -1;

                    int _reEnd = (_reCross >= 0) ? MathMax(_reCross - 2, 0) : 0;
                    if(newerLL1 >= 0 && _reEnd < newerLL1)
                        _reEnd = newerLL1;

                    double _reEndPr = startPr + _reSlope * (double)(startOff - _reEnd);

                    // Store reentry line
                    int reIdx = llReentryCnt;
                    llReentryCnt++;
                    ArrayResize(llReentry, llReentryCnt);
                    llReentry[reIdx].startBar = startOff;
                    llReentry[reIdx].startPrice = startPr;
                    llReentry[reIdx].endBar = _reEnd;
                    llReentry[reIdx].endPrice = _reEndPr;
                    llReentry[reIdx].slope = _reSlope;
                    llReentry[reIdx].crossBar = _reCross;
                    llReentry[reIdx].triggerBar = _reHHBar;

                    // Sell② signal
                    if(_reCross >= 1)
                    {
                        int _reSellBar = _reCross - 1;
                        if(_reSellBar < totalBars)
                        {
                            int sIdx = signalCnt;
                            signalCnt++;
                            ArrayResize(signals, signalCnt);
                            signals[sIdx].type = SIGNAL_SELL2;
                            signals[sIdx].barOffset = _reSellBar;
                            signals[sIdx].entryPrice = close[_reSellBar];
                            signals[sIdx].slPrice = 0;
                            signals[sIdx].tpPrice = 0;
                            signals[sIdx].riskReward = 0;
                            signals[sIdx].tlStartBar = startOff;
                            signals[sIdx].isNew = (_reSellBar == 1);
                        }
                    }
                }
            }
        }

        newerLL1 = ll1Bar;
    }
}

#endif
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_TrendLine.mqh
git commit -m "feat(mt5): add HH/LL trend line calculation with tangent scan, reentry, and truncation"
```

---

### Task 5: Risk Manager (SL/TP/Lot Calculation)

**Files:**
- Create: `mt5_ea/HLL_RiskManager.mqh`
- Reference: Pine Script SL/TP logic, design spec section 5

- [ ] **Step 1: Create HLL_RiskManager.mqh**

```cpp
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
bool CheckBreakevenCondition(int signalBar, double entryPrice, bool isBuy,
                             int bevenBars, const double &close[], int totalBars)
{
    if(signalBar - bevenBars < 0) return false;

    for(int pb = 1; pb <= bevenBars; pb++)
    {
        int idx = signalBar - pb;
        if(idx < 0 || idx >= totalBars) return false;
        if(isBuy && close[idx] < entryPrice) return false;
        if(!isBuy && close[idx] > entryPrice) return false;
    }
    return true;
}

#endif
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_RiskManager.mqh
git commit -m "feat(mt5): add risk manager with SL/TP/lot/breakeven calculation"
```

---

### Task 6: Chart Drawing Module

**Files:**
- Create: `mt5_ea/HLL_Drawing.mqh`

- [ ] **Step 1: Create HLL_Drawing.mqh**

```cpp
//+------------------------------------------------------------------+
//|                       HLL_Drawing.mqh                            |
//|         Chart drawing: trend lines, arrows, SL/TP lines          |
//+------------------------------------------------------------------+
#ifndef HLL_DRAWING_MQH
#define HLL_DRAWING_MQH

#include "HLL_Defines.mqh"

//+------------------------------------------------------------------+
//| ClearAllObjects - Remove all HLL_ prefixed objects               |
//+------------------------------------------------------------------+
void HLL_ClearAll()
{
    int total = ObjectsTotal(0);
    for(int i = total - 1; i >= 0; i--)
    {
        string name = ObjectName(0, i);
        if(StringFind(name, HLL_PREFIX) == 0)
            ObjectDelete(0, name);
    }
}

//+------------------------------------------------------------------+
//| DrawTrendLine - Draw a trend line on chart                       |
//+------------------------------------------------------------------+
void HLL_DrawTrendLine(string name, datetime t1, double p1,
                        datetime t2, double p2,
                        color clr, int style, int width)
{
    string fullName = HLL_PREFIX + name;
    ObjectCreate(0, fullName, OBJ_TREND, 0, t1, p1, t2, p2);
    ObjectSetInteger(0, fullName, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, fullName, OBJPROP_STYLE, style);
    ObjectSetInteger(0, fullName, OBJPROP_WIDTH, width);
    ObjectSetInteger(0, fullName, OBJPROP_RAY_RIGHT, false);
    ObjectSetInteger(0, fullName, OBJPROP_BACK, true);
}

//+------------------------------------------------------------------+
//| DrawArrow - Draw buy/sell arrow                                  |
//+------------------------------------------------------------------+
void HLL_DrawArrow(string name, datetime time, double price,
                    int arrowCode, color clr)
{
    string fullName = HLL_PREFIX + name;
    ObjectCreate(0, fullName, OBJ_ARROW, 0, time, price);
    ObjectSetInteger(0, fullName, OBJPROP_ARROWCODE, arrowCode);
    ObjectSetInteger(0, fullName, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, fullName, OBJPROP_WIDTH, 2);
    ObjectSetInteger(0, fullName, OBJPROP_ANCHOR, (arrowCode == 233) ?
                     ANCHOR_TOP : ANCHOR_BOTTOM);
}

//+------------------------------------------------------------------+
//| DrawHLine - Draw horizontal SL/TP line                           |
//+------------------------------------------------------------------+
void HLL_DrawHLine(string name, datetime t1, datetime t2,
                    double price, color clr, int style)
{
    string fullName = HLL_PREFIX + name;
    ObjectCreate(0, fullName, OBJ_TREND, 0, t1, price, t2, price);
    ObjectSetInteger(0, fullName, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, fullName, OBJPROP_STYLE, style);
    ObjectSetInteger(0, fullName, OBJPROP_WIDTH, 1);
    ObjectSetInteger(0, fullName, OBJPROP_RAY_RIGHT, false);
    ObjectSetInteger(0, fullName, OBJPROP_BACK, true);
}

//+------------------------------------------------------------------+
//| DrawText - Draw text label on chart                              |
//+------------------------------------------------------------------+
void HLL_DrawText(string name, datetime time, double price,
                   string text, color clr, int fontSize = 8)
{
    string fullName = HLL_PREFIX + name;
    ObjectCreate(0, fullName, OBJ_TEXT, 0, time, price);
    ObjectSetString(0, fullName, OBJPROP_TEXT, text);
    ObjectSetInteger(0, fullName, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, fullName, OBJPROP_FONTSIZE, fontSize);
}

//+------------------------------------------------------------------+
//| DrawAllTrendLines - Draw HH/LL trend lines and reentry lines     |
//+------------------------------------------------------------------+
void HLL_DrawTrendLines(const TrendLineInfo &hhTL[], int hhTLCnt,
                         const TrendLineInfo &llTL[], int llTLCnt,
                         const ReentryLineInfo &hhReentry[], int hhReentryCnt,
                         const ReentryLineInfo &llReentry[], int llReentryCnt,
                         color hhColor, color llColor, color reentryColor,
                         ENUM_TIMEFRAMES tf)
{
    // HH descending lines (solid)
    for(int i = 0; i < hhTLCnt; i++)
    {
        datetime t1 = iTime(_Symbol, tf, hhTL[i].startBar);
        datetime t2 = iTime(_Symbol, tf, hhTL[i].endBar);
        string name = "HHTL_" + IntegerToString(i);
        HLL_DrawTrendLine(name, t1, hhTL[i].startPrice, t2, hhTL[i].endPrice,
                           hhColor, STYLE_SOLID, 1);
    }

    // LL ascending lines (solid)
    for(int i = 0; i < llTLCnt; i++)
    {
        datetime t1 = iTime(_Symbol, tf, llTL[i].startBar);
        datetime t2 = iTime(_Symbol, tf, llTL[i].endBar);
        string name = "LLTL_" + IntegerToString(i);
        HLL_DrawTrendLine(name, t1, llTL[i].startPrice, t2, llTL[i].endPrice,
                           llColor, STYLE_SOLID, 1);
    }

    // HH reentry lines (dashed)
    for(int i = 0; i < hhReentryCnt; i++)
    {
        datetime t1 = iTime(_Symbol, tf, hhReentry[i].startBar);
        datetime t2 = iTime(_Symbol, tf, hhReentry[i].endBar);
        string name = "HHRE_" + IntegerToString(i);
        HLL_DrawTrendLine(name, t1, hhReentry[i].startPrice, t2, hhReentry[i].endPrice,
                           reentryColor, STYLE_DASH, 1);
    }

    // LL reentry lines (dashed)
    for(int i = 0; i < llReentryCnt; i++)
    {
        datetime t1 = iTime(_Symbol, tf, llReentry[i].startBar);
        datetime t2 = iTime(_Symbol, tf, llReentry[i].endBar);
        string name = "LLRE_" + IntegerToString(i);
        HLL_DrawTrendLine(name, t1, llReentry[i].startPrice, t2, llReentry[i].endPrice,
                           reentryColor, STYLE_DASH, 1);
    }
}

//+------------------------------------------------------------------+
//| DrawSignals - Draw Buy/Sell arrows and SL/TP/RR labels           |
//+------------------------------------------------------------------+
void HLL_DrawSignals(const SignalInfo &signals[], int signalCnt,
                      color buyColor, color sellColor,
                      color slColor, color tpBuyColor, color tpSellColor,
                      bool drawSLTP, bool drawRR,
                      ENUM_TIMEFRAMES tf)
{
    for(int i = 0; i < signalCnt; i++)
    {
        datetime time = iTime(_Symbol, tf, signals[i].barOffset);
        string suffix = IntegerToString(i);
        bool isBuy = (signals[i].type == SIGNAL_BUY ||
                      signals[i].type == SIGNAL_BUY2 ||
                      signals[i].type == SIGNAL_BUY3);

        // Arrow
        string typeStr;
        switch(signals[i].type)
        {
            case SIGNAL_BUY:   typeStr = "Buy";    break;
            case SIGNAL_SELL:  typeStr = "Sell";   break;
            case SIGNAL_BUY2:  typeStr = "Buy②";  break;
            case SIGNAL_SELL2: typeStr = "Sell②"; break;
            case SIGNAL_BUY3:  typeStr = "Buy③";  break;
            case SIGNAL_SELL3: typeStr = "Sell③"; break;
            default: typeStr = "?"; break;
        }

        color arrowClr = isBuy ? buyColor : sellColor;
        int arrowCode = isBuy ? 233 : 234; // up / down arrow
        HLL_DrawArrow("SIG_" + suffix, time, signals[i].entryPrice, arrowCode, arrowClr);
        HLL_DrawText("SIGTXT_" + suffix, time, signals[i].entryPrice, typeStr, arrowClr);

        // SL/TP lines
        if(drawSLTP && signals[i].slPrice > 0)
        {
            datetime t2 = iTime(_Symbol, tf, MathMax(signals[i].barOffset - 5, 0));
            HLL_DrawHLine("SL_" + suffix, time, t2, signals[i].slPrice, slColor, STYLE_DASH);
            HLL_DrawText("SLTXT_" + suffix, t2, signals[i].slPrice,
                          "SL " + DoubleToString(signals[i].slPrice, 2), slColor);
        }

        if(drawSLTP && signals[i].tpPrice > 0)
        {
            datetime t2 = iTime(_Symbol, tf, MathMax(signals[i].barOffset - 5, 0));
            color tpClr = isBuy ? tpBuyColor : tpSellColor;
            HLL_DrawHLine("TP_" + suffix, time, t2, signals[i].tpPrice, tpClr, STYLE_DOT);
            HLL_DrawText("TPTXT_" + suffix, t2, signals[i].tpPrice,
                          "TP " + DoubleToString(signals[i].tpPrice, 2), tpClr);
        }

        // R:R
        if(drawRR && signals[i].riskReward > 0)
        {
            HLL_DrawText("RR_" + suffix, time, signals[i].entryPrice,
                          "RR 1:" + DoubleToString(signals[i].riskReward, 1), clrWhite);
        }
    }
}

#endif
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HLL_Drawing.mqh
git commit -m "feat(mt5): add chart drawing module for trend lines, signals, SL/TP"
```

---

### Task 7: Main EA — HighLowLabels_EA.mq5

**Files:**
- Create: `mt5_ea/HighLowLabels_EA.mq5`
- Reference: Design spec section 7

- [ ] **Step 1: Create the main EA file**

```cpp
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
                // Find latest L pivot
                for(int k = 0; k < lCnt; k++)
                {
                    double newSL = lP[k] * (1.0 - InpBuySLOffset);
                    if(newSL > curSL)
                    {
                        g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
                        break;
                    }
                    break; // Only check most recent
                }
            }
            else
            {
                for(int k = 0; k < hCnt; k++)
                {
                    double newSL = hP[k] * (1.0 + InpSellSLOffset);
                    if(newSL < curSL)
                    {
                        g_trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
                        break;
                    }
                    break;
                }
            }
        }

        // Skip if position was just modified/closed
        if(!PositionSelectByTicket(ticket)) continue;

        // 3. Breakeven
        if(InpBreakeven && curSL != 0)
        {
            if(CheckBreakevenCondition(0, entryPrice, isBuy, InpBevenBars, close, totalBars))
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
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/HighLowLabels_EA.mq5
git commit -m "feat(mt5): add main HighLowLabels EA with auto-trading and position management"
```

---

### Task 8: Compile Verification

**Files:**
- All 7 files created in Tasks 1-7

- [ ] **Step 1: Verify all include paths are correct**

Check that `HighLowLabels_EA.mq5` includes all 6 `.mqh` files and that the include chain has no circular dependencies:

```
HighLowLabels_EA.mq5
 ├── HLL_Defines.mqh (no deps)
 ├── HLL_SwingDetect.mqh → HLL_Defines.mqh
 ├── HLL_StructureDetect.mqh → HLL_Defines.mqh
 ├── HLL_TrendLine.mqh → HLL_Defines.mqh, HLL_StructureDetect.mqh
 ├── HLL_RiskManager.mqh → HLL_Defines.mqh
 └── HLL_Drawing.mqh → HLL_Defines.mqh
```

- [ ] **Step 2: Review all function signatures match between declarations and calls**

Key cross-module calls to verify:
- `CollectSwingPoints` called in main EA matches declaration in `HLL_SwingDetect.mqh`
- `DetectLowerLows` / `DetectHigherHighs` called in main EA matches `HLL_StructureDetect.mqh`
- `CalcHHTrendLines` / `CalcLLTrendLines` called in main EA matches `HLL_TrendLine.mqh`
- `FillSignalSLTP` called in main EA matches `HLL_RiskManager.mqh`
- `HLL_ClearAll` / `HLL_DrawTrendLines` / `HLL_DrawSignals` matches `HLL_Drawing.mqh`
- `FindHit` in `HLL_TrendLine.mqh` uses correct parameter types

- [ ] **Step 3: Final commit**

```bash
git add mt5_ea/
git commit -m "feat(mt5): complete HighLowLabels v3.70 EA - all modules ready for MT5 compilation"
```

---

### Task 9: Documentation and User Guide

**Files:**
- Create: `mt5_ea/README_HLL_EA.txt`

- [ ] **Step 1: Create usage instructions**

```
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
```

- [ ] **Step 2: Commit**

```bash
git add mt5_ea/README_HLL_EA.txt
git commit -m "docs: add HighLowLabels EA installation and usage guide"
```
