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
