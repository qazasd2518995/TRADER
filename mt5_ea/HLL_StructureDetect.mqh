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
