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
