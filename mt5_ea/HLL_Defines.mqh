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
