//+------------------------------------------------------------------+
//|                MT5_File_Bridge_Enhanced.mq5                      |
//|   Enhanced file-based bridge for Python-MT5 integration         |
//|   Supports buy/sell/modify/close commands & full state export   |
//+------------------------------------------------------------------+
#property copyright "Artan Ahmadi - Enhanced v4.3"
#property version   "4.30"

// 交易品種預設「自動」：EA 掛在哪張圖表就用那個品種，自動對應各券商
// (XAUUSD / XAUUSD.s / GOLD ...)，會員不用改。留空或 "AUTO" = 自動；
// 只有想指定成另一個「這家券商真的有」的代號時才填。
input string TradingSymbol = "";
input int    WriteIntervalSec = 1;
input double DefaultLotSize = 0.01;
input bool   EnableTrading = true;
input bool   DetailedLogging = true;

// === 行情輸出（給會員端的圖表用）=================================
input bool   ExportChartData = true;                          // 輸出多週期 K 線給會員端畫圖
input int    ChartBarCount   = 400;                           // 每個週期輸出幾根
input string WatchlistSymbols = "XAGUSD,USOIL,BTCUSD,EURUSD"; // 市場總覽的自選商品（逗號分隔）

// === Cloud Hub auto-trading (members poll the central hub directly) ===
input bool   EnableHubPolling = false;                                  // (選用) EA 直接向雲端 Hub 抓訊號; 會員端建議用 Python agent, 此處保持關閉
input string HubUrl           = "https://gold-signal-hub-tw.fly.dev";   // 中央 Hub 網址
input string HubToken         = "";                                     // 你的存取 token (向管理者索取; 走 EA-direct 才需要)
input int    HubPollSeconds   = 3;                                      // 每幾秒輪詢一次 Hub
input double HubLotSize       = 0.0;                                    // 下單手數; 0 = 用 DefaultLotSize
input int    HubMagic         = 990001;                                 // Hub 來源訂單的 magic number

// === ATR-based SL/TP inputs ======================================
input bool   UseATRBasedSLTP     = false;        // enable ATR-based SL/TP when missing
input ENUM_TIMEFRAMES ATR_TF     = PERIOD_M15;   // ATR timeframe
input int    ATR_Period          = 14;           // ATR period
input double ATR_SL_Multiplier   = 1.5;          // SL = ATR * this
input double ATR_TP_Multiplier   = 3.0;          // TP = ATR * this (ignored if RR>0)
input double ATR_RR_Override     = 0.0;          // If >0, TP = SL * RR (e.g., 2.0)
// ================================================================

datetime last_write = 0;
datetime last_account_write = 0;
datetime last_trades_write = 0;
datetime last_positions_write = 0;

// NEW writers cadence
datetime last_orders_write     = 0;
datetime last_tick_write       = 0;
datetime last_symbolinfo_write = 0;
datetime last_orderbook_write  = 0;
datetime last_rates_write      = 0;

// 多週期 K 線各寫各的：小週期跳得快就寫得勤，大週期沒必要一直重寫
datetime last_rates_m5   = 0;
datetime last_rates_m15  = 0;
datetime last_rates_h1   = 0;
datetime last_rates_h4   = 0;
datetime last_rates_d1   = 0;
datetime last_watchlist  = 0;

// Cloud Hub polling state
long     g_hub_last_seq  = -1;     // -1 = 尚未初始化 (第一次連線會對齊到 latest_seq)
datetime g_last_hub_poll = 0;
bool     g_hub_warned    = false;

// Runtime hub config (from inputs, overridable by Files/hub_config.txt)
bool     g_hub_on       = false;
string   g_hub_url      = "";
string   g_hub_token    = "";
int      g_hub_interval = 3;

// 實際交易/報價用的券商代號。OnInit 決定：預設用圖表品種，自動對應各券商。
string   g_sym          = "";

//+------------------------------------------------------------------+
//| Trade command structure                                          |
//+------------------------------------------------------------------+
struct TradeCommand
{
   string   action;       // "buy", "sell", "modify", or "close"
   string   symbol;       // Trading symbol
   double   lot_size;     // Position size
   double   stop_loss;    // Stop loss price
   double   take_profit;  // Take profit price
   double   price;        // Entry price (0 = market order, >0 = pending order)
   string   pending_order_type; // "limit" = crossed price must be rejected, never changed to stop
   string   comment;      // Order comment
   int      magic_number; // Magic number
   string   trade_id;     // Unique trade ID (added)
   long     ticket;       // Position ticket (for modify/close)
    double   close_volume; // Partial close volume (lots). If 0, close full volume
};

//+------------------------------------------------------------------+
//| Utility: trading allowed?                                        |
//+------------------------------------------------------------------+
bool IsTradeAllowedFunc()
{
   return (MQLInfoInteger(MQL_TRADE_ALLOWED) && TerminalInfoInteger(TERMINAL_TRADE_ALLOWED));
}

//+------------------------------------------------------------------+
//| 決定實際要用的券商黃金代號                                        |
//| 一律以「EA 掛的這張圖表」的品種為準，自動對應各券商               |
//| (XAUUSD / XAUUSD.s / GOLD ...)。只有當 TradingSymbol 明確填了     |
//| 另一個、且這家券商真的有的代號時，才用它覆蓋。                    |
//+------------------------------------------------------------------+
string ResolveTradeSymbol()
{
   string chart = Symbol();
   string want  = TradingSymbol;
   if(want != "" && want != "AUTO" && want != "auto" &&
      want != chart && SymbolSelect(want, true))
      return want;                 // 會員明確指定了別的、且券商有 → 用它
   SymbolSelect(chart, true);       // 確保主商品在 Market Watch 裡
   return chart;                    // 預設：跟著圖表走
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   g_sym = ResolveTradeSymbol();
   Print("Enhanced MT5 File Bridge v4.3 started. 交易品種=", g_sym,
         "（圖表=", Symbol(), " / 參數TradingSymbol=", (TradingSymbol=="" ? "AUTO" : TradingSymbol), "）");
   Print("Auto trading enabled: ", IsTradeAllowedFunc());
   Print("Default lot size: ", DoubleToString(DefaultLotSize,2));
   EventSetTimer(1);

   // Write static symbol info at startup
   WriteSymbolInfo(g_sym);

   LoadHubConfig();

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("Enhanced MT5 File Bridge stopped. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| OnTimer                                                          |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Existing writers
   if(TimeCurrent() - last_write >= WriteIntervalSec) { WritePriceData(); last_write = TimeCurrent(); }
   if(TimeCurrent() - last_account_write >= 2)        { WriteAccountInfo(); last_account_write = TimeCurrent(); }
   if(TimeCurrent() - last_positions_write >= 2)      { WritePositions(); last_positions_write = TimeCurrent(); }
   if(TimeCurrent() - last_trades_write >= 10)        { WriteClosedTrades(); last_trades_write = TimeCurrent(); }

   // NEW: richer state
   if(TimeCurrent() - last_tick_write >= 1)           { WriteTickData(); last_tick_write = TimeCurrent(); }
   if(TimeCurrent() - last_orders_write >= 2)         { WritePendingOrders(); last_orders_write = TimeCurrent(); }
   if(TimeCurrent() - last_orderbook_write >= 2)      { WriteOrderBook(); last_orderbook_write = TimeCurrent(); }
   if(TimeCurrent() - last_rates_write >= 10)         { WriteRatesM1(g_sym, ChartBarCount); last_rates_write = TimeCurrent(); }

   // 多週期 K 線 + 自選報價。週期越大、重算越沒意義，所以間隔拉開；
   // 全部加起來平均每秒不到一次寫檔，比原本的 tick 檔還輕。
   if(ExportChartData)
   {
      if(TimeCurrent() - last_rates_m5  >= 15)  { WriteRates(g_sym, PERIOD_M5,  ChartBarCount, "rates_M5.json");  last_rates_m5  = TimeCurrent(); }
      if(TimeCurrent() - last_rates_m15 >= 30)  { WriteRates(g_sym, PERIOD_M15, ChartBarCount, "rates_M15.json"); last_rates_m15 = TimeCurrent(); }
      if(TimeCurrent() - last_rates_h1  >= 60)  { WriteRates(g_sym, PERIOD_H1,  ChartBarCount, "rates_H1.json");  last_rates_h1  = TimeCurrent(); }
      if(TimeCurrent() - last_rates_h4  >= 180) { WriteRates(g_sym, PERIOD_H4,  ChartBarCount, "rates_H4.json");  last_rates_h4  = TimeCurrent(); }
      if(TimeCurrent() - last_rates_d1  >= 300) { WriteRates(g_sym, PERIOD_D1,  ChartBarCount, "rates_D1.json");  last_rates_d1  = TimeCurrent(); }
      if(TimeCurrent() - last_watchlist >= 3)   { WriteWatchlist(); last_watchlist = TimeCurrent(); }
   }
   // re-dump symbol specs every 1h (in case of broker changes)
   if(TimeCurrent() - last_symbolinfo_write >= 3600)  { WriteSymbolInfo(g_sym); last_symbolinfo_write = TimeCurrent(); }

   // Command processor
   if(EnableTrading && IsTradeAllowedFunc())
      CheckTradeCommands();

   // Cloud Hub poller — pull signals straight from the central hub
   if(g_hub_on && IsTradeAllowedFunc() && (TimeLocal() - g_last_hub_poll >= g_hub_interval))
   {
      g_last_hub_poll = TimeLocal();
      PollHub();
   }
}

//+------------------------------------------------------------------+
//| Write price data (basic snapshot)                                |
//+------------------------------------------------------------------+
void WritePriceData()
{
   double bid = SymbolInfoDouble(g_sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(g_sym, SYMBOL_ASK);
   long   volume = SymbolInfoInteger(g_sym, SYMBOL_VOLUME);
   datetime t = TimeCurrent();

   string json = "{";
   json += "\"symbol\":\"" + g_sym + "\",";
   json += "\"bid\":" + DoubleToString(bid, 5) + ",";
   json += "\"ask\":" + DoubleToString(ask, 5) + ",";
   json += "\"volume\":" + IntegerToString(volume) + ",";
   json += "\"timestamp\":" + IntegerToString(t) + ",";
   json += "\"server_time\":\"" + TimeToString(t) + "\"";
   json += "}";

   int handle = FileOpen(g_sym + "_price.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle, json);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| NEW: Full tick snapshot                                          |
//+------------------------------------------------------------------+
void WriteTickData()
{
   MqlTick tick;
   if(!SymbolInfoTick(g_sym, tick)) return;

   double spread = 0.0;
   if(tick.ask > 0 && tick.bid > 0) spread = tick.ask - tick.bid;

   string json = "{";
   json += "\"symbol\":\""+g_sym+"\",";
   json += "\"bid\":"+DoubleToString(tick.bid, 5)+",";
   json += "\"ask\":"+DoubleToString(tick.ask, 5)+",";
   json += "\"last\":"+DoubleToString(tick.last, 5)+",";
   json += "\"volume_real\":"+DoubleToString(tick.volume_real, 2)+",";
   json += "\"spread\":"+DoubleToString(spread, 5)+",";
   json += "\"time\":"+IntegerToString((long)tick.time)+",";
   json += "\"time_msc\":"+IntegerToString((long)tick.time_msc)+",";
   json += "\"flags\":"+IntegerToString((int)tick.flags);
   json += "}";

   int h = FileOpen(g_sym + "_tick.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h!=INVALID_HANDLE){ FileWrite(h, json); FileClose(h); }
}

//+------------------------------------------------------------------+
//| NEW: Symbol/contract specs (static)                              |
//+------------------------------------------------------------------+
void WriteSymbolInfo(string sym)
{
   string j="{";
   j+="\"symbol\":\""+sym+"\",";
   j+="\"digits\":"          + IntegerToString((int)SymbolInfoInteger(sym, SYMBOL_DIGITS)) + ",";
   j+="\"point\":"           + DoubleToString(SymbolInfoDouble(sym, SYMBOL_POINT), 8) + ",";
   j+="\"contract_size\":"   + DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE), 2) + ",";
   j+="\"volume_min\":"      + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), 2) + ",";
   j+="\"volume_max\":"      + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX), 2) + ",";
   j+="\"volume_step\":"     + DoubleToString(SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), 2) + ",";
   j+="\"tick_size\":"       + DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE), 8) + ",";
   j+="\"tick_value\":"      + DoubleToString(SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), 5) + ",";
   j+="\"stops_level\":" + IntegerToString((int)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL)) + ",";
   j+="\"freeze_level\":" + IntegerToString((int)SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL)) + ",";
   j+="\"swap_type\":0,";
   j+="\"swap_long\":"       + DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_LONG), 5) + ",";
   j+="\"swap_short\":"      + DoubleToString(SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT), 5) + ",";
   j+="\"trade_mode\":"      + IntegerToString((int)SymbolInfoInteger(sym, SYMBOL_TRADE_MODE));
   j+="}";
   int h=FileOpen("symbol_info.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h!=INVALID_HANDLE){ FileWrite(h, j); FileClose(h); }
}

//+------------------------------------------------------------------+
//| Enriched account information                                     |
//+------------------------------------------------------------------+
void WriteAccountInfo()
{
   double balance      = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity       = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin       = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_margin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double profit       = AccountInfoDouble(ACCOUNT_PROFIT);
   long   leverage     = AccountInfoInteger(ACCOUNT_LEVERAGE);
   string currency     = AccountInfoString(ACCOUNT_CURRENCY);

   long   login        = (long)AccountInfoInteger(ACCOUNT_LOGIN);
   string name         = AccountInfoString(ACCOUNT_NAME);
   string server       = AccountInfoString(ACCOUNT_SERVER);
   string company      = AccountInfoString(ACCOUNT_COMPANY);
   double credit       = AccountInfoDouble(ACCOUNT_CREDIT);
   long   trade_mode   = (long)AccountInfoInteger(ACCOUNT_TRADE_MODE);

   bool   connected    = (bool)TerminalInfoInteger(TERMINAL_CONNECTED);
   long   ping_last    = (long)TerminalInfoInteger(TERMINAL_PING_LAST);
   long   build        = (long)TerminalInfoInteger(TERMINAL_BUILD);

   datetime t = TimeCurrent();

   string json = "{";
   json += "\"login\":"+IntegerToString(login)+",";
   json += "\"name\":\""+name+"\",";
   json += "\"server\":\""+server+"\",";
   json += "\"company\":\""+company+"\",";
   json += "\"currency\":\""+currency+"\",";
   json += "\"leverage\":"+IntegerToString((int)leverage)+",";
   json += "\"trade_mode\":"+IntegerToString((int)trade_mode)+",";

   json += "\"balance\":"+DoubleToString(balance,2)+",";
   json += "\"equity\":"+DoubleToString(equity,2)+",";
   json += "\"margin\":"+DoubleToString(margin,2)+",";
   json += "\"free_margin\":"+DoubleToString(free_margin,2)+",";
   json += "\"profit\":"+DoubleToString(profit,2)+",";
   json += "\"credit\":"+DoubleToString(credit,2)+",";

   json += "\"terminal_connected\":"+(connected?"true":"false")+",";
   json += "\"terminal_ping_ms\":"+IntegerToString((int)ping_last)+",";
   json += "\"terminal_build\":"+IntegerToString((int)build)+",";

   // GMT offset: difference between server time and UTC (seconds)
   datetime gmt = TimeGMT();
   long gmt_offset = (long)(t - gmt);
   json += "\"gmt_offset\":"+IntegerToString(gmt_offset)+",";

   json += "\"timestamp\":"+IntegerToString(t)+",";
   json += "\"timestamp_gmt\":"+IntegerToString((long)gmt)+",";
   json += "\"server_time\":\""+TimeToString(t)+"\"";
   json += "}";

   int handle = FileOpen("account_info.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE){ FileWrite(handle, json); FileClose(handle); }
}

//+------------------------------------------------------------------+
//| Positions                                                        |
//+------------------------------------------------------------------+
void WritePositions()
{
   string json = "{";
   json += "\"timestamp\":" + IntegerToString(TimeCurrent()) + ",";
   json += "\"positions\":[";
   
   int positions_count = 0;
   int total_positions = PositionsTotal();
   
   for(int i = 0; i < total_positions; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      
      if(PositionSelectByTicket(ticket))
      {
         string symbol = PositionGetString(POSITION_SYMBOL);
         long type = PositionGetInteger(POSITION_TYPE);
         double volume = PositionGetDouble(POSITION_VOLUME);
         double price_open = PositionGetDouble(POSITION_PRICE_OPEN);
         double price_current = PositionGetDouble(POSITION_PRICE_CURRENT);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double profit = PositionGetDouble(POSITION_PROFIT);
         double swap = PositionGetDouble(POSITION_SWAP);
         long magic = PositionGetInteger(POSITION_MAGIC);
         string comment = PositionGetString(POSITION_COMMENT);
         datetime time_open = (datetime)PositionGetInteger(POSITION_TIME);
         
         if(positions_count > 0) json += ",";
         
         json += "{";
         json += "\"ticket\":" + IntegerToString((long)ticket) + ",";
         json += "\"symbol\":\"" + symbol + "\",";
         json += "\"type\":\"" + (type == POSITION_TYPE_BUY ? "buy" : "sell") + "\",";
         json += "\"volume\":" + DoubleToString(volume, 2) + ",";
         json += "\"price_open\":" + DoubleToString(price_open, 5) + ",";
         json += "\"price_current\":" + DoubleToString(price_current, 5) + ",";
         json += "\"sl\":" + DoubleToString(sl, 5) + ",";
         json += "\"tp\":" + DoubleToString(tp, 5) + ",";
         json += "\"profit\":" + DoubleToString(profit, 2) + ",";
         json += "\"swap\":" + DoubleToString(swap, 2) + ",";
         json += "\"magic\":" + IntegerToString((int)magic) + ",";
         json += "\"comment\":\"" + comment + "\",";
         json += "\"time_open\":\"" + TimeToString(time_open) + "\",";
         json += "\"time_open_timestamp\":" + IntegerToString(time_open);
         json += "}";
         
         positions_count++;
      }
   }
   
   json += "]";
   json += "}";
   
   int handle = FileOpen("positions.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle, json);
      FileClose(handle);
      if(DetailedLogging && positions_count > 0)
         Print("Updated positions file with ", positions_count, " open positions");
   }
}

//+------------------------------------------------------------------+
//| Closed trades history                                            |
//+------------------------------------------------------------------+
void WriteClosedTrades()
{
   datetime from = TimeCurrent() - (30 * 24 * 3600); // Last 30 days
   datetime to = TimeCurrent() + 3600; // 1h buffer
   HistorySelect(0, TimeCurrent());
   Sleep(100);
   if(!HistorySelect(from, to))
   {
      if(DetailedLogging) Print("Failed to select history for period");
      return;
   }
   
   string json = "{";
   json += "\"timestamp\":" + IntegerToString(TimeCurrent()) + ",";
   json += "\"trades\":[";
   
   int total_deals = HistoryDealsTotal();
   int exported_count = 0;
   
   for(int i = total_deals - 1; i >= 0; i--)
   {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket <= 0) continue;
      long deal_entry = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
      if(deal_entry != DEAL_ENTRY_OUT) continue; // only exits
      
      string symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
      long deal_type = HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
      double volume = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
      double price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
      double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
      double swap = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
      double commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
      datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
      long position_id = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
      long deal_magic = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
      string comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);

      double entry_price = 0;
      datetime open_time = 0;
      double sl = 0;
      double tp = 0;
      double total_position_profit = 0;
      double total_position_swap = 0;
      double total_position_commission = 0;

      for(int j = 0; j < total_deals; j++)
      {
         ulong related_ticket = HistoryDealGetTicket(j);
         if(related_ticket <= 0) continue;
         long related_position_id = HistoryDealGetInteger(related_ticket, DEAL_POSITION_ID);
         long related_entry = HistoryDealGetInteger(related_ticket, DEAL_ENTRY);
         if(related_position_id == position_id)
         {
            if(related_entry == DEAL_ENTRY_IN)
            {
               entry_price = HistoryDealGetDouble(related_ticket, DEAL_PRICE);
               open_time = (datetime)HistoryDealGetInteger(related_ticket, DEAL_TIME);
            }
            total_position_profit += HistoryDealGetDouble(related_ticket, DEAL_PROFIT);
            total_position_swap += HistoryDealGetDouble(related_ticket, DEAL_SWAP);
            total_position_commission += HistoryDealGetDouble(related_ticket, DEAL_COMMISSION);
         }
      }
      
      if(HistoryOrdersTotal() > 0)
      {
         for(int k = 0; k < HistoryOrdersTotal(); k++)
         {
            ulong order_ticket = HistoryOrderGetTicket(k);
            if(order_ticket <= 0) continue;
            long order_position_id = HistoryOrderGetInteger(order_ticket, ORDER_POSITION_ID);
            if(order_position_id == position_id)
            {
               sl = HistoryOrderGetDouble(order_ticket, ORDER_SL);
               tp = HistoryOrderGetDouble(order_ticket, ORDER_TP);
               string order_comment = HistoryOrderGetString(order_ticket, ORDER_COMMENT);
               if(StringLen(order_comment) > 0 && order_comment != "")
               {
                  if(StringFind(order_comment, "[sl") < 0 && StringFind(order_comment, "[tp") < 0)
                     comment = order_comment;
               }
               break;
            }
         }
      }
      
      double change_percent = 0;
      if(entry_price > 0)
      {
         if(deal_type == DEAL_TYPE_BUY) change_percent = ((price - entry_price) / entry_price) * 100.0;
         else                            change_percent = ((entry_price - price) / entry_price) * 100.0;
      }
      
      if(json != "{\"timestamp\":" + IntegerToString(TimeCurrent()) + ",\"trades\":[")
         json += ",";
      
      json += "{";
      json += "\"ticket\":" + IntegerToString((long)deal_ticket) + ",";
      json += "\"position_id\":" + IntegerToString((long)position_id) + ",";
      // magic 讓下游可以分辨這筆是哪個 EA/策略下的單 (跟單系統的 magic 是 999999)，
      // 不必再靠 comment 格式猜——positions.json/orders.json 本來就有這欄，
      // 只有 closed_trades.json 漏了。
      json += "\"magic\":" + IntegerToString((int)deal_magic) + ",";
      json += "\"symbol\":\"" + symbol + "\",";
      json += "\"type\":\"" + (deal_type == DEAL_TYPE_BUY ? "buy" : "sell") + "\",";
      json += "\"volume\":" + DoubleToString(volume, 2) + ",";
      json += "\"entry_price\":" + DoubleToString(entry_price, 5) + ",";
      json += "\"exit_price\":" + DoubleToString(price, 5) + ",";
      json += "\"sl\":" + DoubleToString(sl, 5) + ",";
      json += "\"tp\":" + DoubleToString(tp, 5) + ",";
      json += "\"profit\":" + DoubleToString(total_position_profit + total_position_swap + total_position_commission, 2) + ",";
      json += "\"change_percent\":" + DoubleToString(change_percent, 2) + ",";
      json += "\"comment\":\"" + comment + "\",";
      json += "\"open_timestamp\":" + IntegerToString(open_time) + ",";
      json += "\"close_time\":\"" + TimeToString(deal_time) + "\",";
      json += "\"close_timestamp\":" + IntegerToString(deal_time);
      json += "}";
      
      exported_count++;
   }
   
   json += "]";
   json += "}";
   
   int handle = FileOpen("closed_trades.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle != INVALID_HANDLE)
   {
      FileWrite(handle, json);
      FileClose(handle);
      if(DetailedLogging) Print("Updated closed trades file: ", exported_count, " trades exported from ", total_deals, " total deals");
   }
   else if(DetailedLogging) { Print("Failed to open closed_trades.json for writing"); }
}

//+------------------------------------------------------------------+
//| NEW: Pending orders                                              |
//+------------------------------------------------------------------+
void WritePendingOrders()
{
   string j = "{\"timestamp\":"+IntegerToString(TimeCurrent())+",\"orders\":[";
   int n = 0;
   for(int i=0;i<OrdersTotal();i++)
   {
      ulong t = OrderGetTicket(i);
      if(t==0) continue;
      if(n > 0) j+=",";
      n++;
      j+="{\"ticket\":"+IntegerToString((long)t)
        +",\"symbol\":\""+OrderGetString(ORDER_SYMBOL)+"\""
        +",\"type\":"+IntegerToString((int)OrderGetInteger(ORDER_TYPE))
        +",\"price\":"+DoubleToString(OrderGetDouble(ORDER_PRICE_OPEN),5)
        +",\"sl\":"+DoubleToString(OrderGetDouble(ORDER_SL),5)
        +",\"tp\":"+DoubleToString(OrderGetDouble(ORDER_TP),5)
        +",\"volume\":"+DoubleToString(OrderGetDouble(ORDER_VOLUME_CURRENT),2)
        +",\"time_setup\":\""+TimeToString((datetime)OrderGetInteger(ORDER_TIME_SETUP))+"\""
        +",\"time_expiration\":\""+TimeToString((datetime)OrderGetInteger(ORDER_TIME_EXPIRATION))+"\""
        +",\"magic\":"+IntegerToString((int)OrderGetInteger(ORDER_MAGIC))
        +",\"comment\":\""+OrderGetString(ORDER_COMMENT)+"\"}";
   }
   j+="]}";
   int h=FileOpen("orders.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h!=INVALID_HANDLE){ FileWrite(h, j); FileClose(h); }
}

//+------------------------------------------------------------------+
//| NEW: Order book (Depth of Market)                                |
//+------------------------------------------------------------------+
void WriteOrderBook()
{
   if(!MarketBookAdd(g_sym)) return;

   MqlBookInfo bi[];
   if(MarketBookGet(g_sym, bi))
   {
      string j = "{\"timestamp\":"+IntegerToString(TimeCurrent())+",\"levels\":[";
      for(int i=0;i<ArraySize(bi);i++)
      {
         if(i) j+=",";
         j+="{\"type\":"+IntegerToString((int)bi[i].type)
           +",\"price\":"+DoubleToString(bi[i].price,5)
           +",\"volume\":"+DoubleToString(bi[i].volume,2)+"}";
      }
      j+="]}";
      int h=FileOpen(g_sym + "_orderbook.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
      if(h!=INVALID_HANDLE){ FileWrite(h, j); FileClose(h); }
   }

   MarketBookRelease(g_sym);
}

//+------------------------------------------------------------------+
//| NEW: Compact OHLCV history (M1)                                  |
//+------------------------------------------------------------------+
// 舊介面保留：轉呼叫通用版，寫出來的 rates_M1.json 格式不變
void WriteRatesM1(string sym, int count)
{
   WriteRates(sym, PERIOD_M1, count, "rates_M1.json");
}

//+------------------------------------------------------------------+
//| 輸出任一週期的 K 線給會員端畫圖                                   |
//| 多帶一個 digits，讓前端知道要顯示幾位小數——黃金 2 位、外匯 5 位， |
//| 寫死會很醜。                                                      |
//+------------------------------------------------------------------+
void WriteRates(string sym, ENUM_TIMEFRAMES tf, int count, string outfile)
{
   MqlRates rates[];
   int copied = CopyRates(sym, tf, 0, count, rates);
   if(copied<=0) return;

   ArraySetAsSeries(rates, true);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0) digits = 5;

   string j = "{\"symbol\":\""+sym+"\",\"timeframe\":\""+TimeframeName(tf)+"\""
            + ",\"digits\":"+IntegerToString(digits)
            + ",\"server_time\":"+IntegerToString((long)TimeCurrent())
            + ",\"bars\":[";
   for(int i=copied-1;i>=0;i--)  // oldest -> newest
   {
      if(i != copied-1) j+=",";
      j+="{\"t\":"+IntegerToString((long)rates[i].time)
        +",\"o\":"+DoubleToString(rates[i].open,digits)
        +",\"h\":"+DoubleToString(rates[i].high,digits)
        +",\"l\":"+DoubleToString(rates[i].low,digits)
        +",\"c\":"+DoubleToString(rates[i].close,digits)
        +",\"tv\":"+IntegerToString((long)rates[i].tick_volume)+"}";
   }
   j+="]}";

   int h=FileOpen(outfile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h!=INVALID_HANDLE){ FileWrite(h, j); FileClose(h); }
}

string TimeframeName(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
   }
   return "M1";
}

//+------------------------------------------------------------------+
//| 市場總覽：自選商品的即時報價與當日漲跌                            |
//| 券商代號常有後綴（XAGUSD.m、BTCUSD.raw），所以先試原名，不行就掃   |
//| 一遍 Market Watch 找開頭相同的。找不到就跳過——不要讓一個沒上架的  |
//| 商品害整份檔案寫不出來。                                          |
//+------------------------------------------------------------------+
void WriteWatchlist()
{
   string parts[];
   int n = StringSplit(WatchlistSymbols, ',', parts);

   string all[];
   ArrayResize(all, n+1);
   all[0] = g_sym;              // 主商品永遠排第一
   for(int i=0;i<n;i++) all[i+1] = parts[i];

   string j = "{\"server_time\":"+IntegerToString((long)TimeCurrent())+",\"items\":[";
   int written = 0;

   for(int i=0;i<ArraySize(all);i++)
   {
      string want = all[i];
      StringTrimLeft(want); StringTrimRight(want);
      if(want == "") continue;
      if(i > 0 && want == g_sym) continue;   // 別重複列主商品

      string sym = ResolveSymbol(want);
      if(sym == "") continue;

      MqlTick tick;
      if(!SymbolInfoTick(sym, tick)) continue;
      if(tick.bid <= 0) continue;

      // 當日漲跌拿 D1 這根的開盤價當基準
      double base = 0;
      MqlRates d1[];
      if(CopyRates(sym, PERIOD_D1, 0, 2, d1) >= 1)
      {
         ArraySetAsSeries(d1, true);
         base = d1[0].open;
      }
      if(base <= 0) base = tick.bid;

      double chg  = tick.bid - base;
      double chgp = (base != 0) ? (chg / base * 100.0) : 0;
      int digits  = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(digits <= 0) digits = 5;

      if(written > 0) j += ",";
      j += "{\"symbol\":\""+want+"\""
         + ",\"resolved\":\""+sym+"\""
         + ",\"bid\":"+DoubleToString(tick.bid, digits)
         + ",\"ask\":"+DoubleToString(tick.ask, digits)
         + ",\"digits\":"+IntegerToString(digits)
         + ",\"change\":"+DoubleToString(chg, digits)
         + ",\"change_pct\":"+DoubleToString(chgp, 2)+"}";
      written++;
   }
   j += "]}";

   int h=FileOpen("watchlist.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h!=INVALID_HANDLE){ FileWrite(h, j); FileClose(h); }
}

// 找出券商實際的商品代號：先試原名，再掃 Market Watch 找前綴相同的
string ResolveSymbol(string want)
{
   if(SymbolSelect(want, true) && SymbolInfoDouble(want, SYMBOL_BID) > 0)
      return want;

   int total = SymbolsTotal(false);
   for(int i=0;i<total;i++)
   {
      string name = SymbolName(i, false);
      if(StringFind(name, want) == 0)          // XAGUSD → XAGUSD.m
      {
         if(SymbolSelect(name, true) && SymbolInfoDouble(name, SYMBOL_BID) > 0)
            return name;
      }
   }
   return "";
}

// --------------------------- JSON helpers -------------------------
string ExtractStringValue(string json, string key)
{
   string search = "\"" + key + "\":\"";
   int start = StringFind(json, search);
   if(start < 0) return "";
   start += StringLen(search);
   int end = StringFind(json, "\"", start);
   if(end < 0) return "";
   return StringSubstr(json, start, end - start);
}

double ExtractDoubleValue(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start < 0) return 0;
   start += StringLen(search);
   int end = StringFind(json, ",", start);
   if(end < 0) end = StringFind(json, "}", start);
   if(end < 0) return 0;
   string value = StringSubstr(json, start, end - start);
   StringReplace(value, " ", "");
   return StringToDouble(value);
}

long ExtractLongValue(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start < 0) return 0;
   start += StringLen(search);
   int end = StringFind(json, ",", start);
   if(end < 0) end = StringFind(json, "}", start);
   if(end < 0) return 0;
   string value = StringSubstr(json, start, end - start);
   StringReplace(value, " ", "");
   return StringToInteger(value);
}

// --------------------------- ATR helpers --------------------------
double RoundToTick(string sym, double price)
{
   double tick = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0.0) tick = SymbolInfoDouble(sym, SYMBOL_POINT);
   return (MathRound(price / tick) * tick);
}

bool EnforceStopsLevel(string sym, double &sl, double &tp, double open_price, bool is_buy)
{
   int    digits      = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double tick        = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(tick <= 0.0) tick = SymbolInfoDouble(sym, SYMBOL_POINT);
   int    stops_level = 0;
   double min_dist    = stops_level * tick;

   bool ok = true;

   if(is_buy)
   {
      if(sl > 0 && (open_price - sl) < min_dist) { sl = open_price - min_dist; ok = false; }
      if(tp > 0 && (tp - open_price) < min_dist) { tp = open_price + min_dist; ok = false; }
   }
   else
   {
      if(sl > 0 && (sl - open_price) < min_dist) { sl = open_price + min_dist; ok = false; }
      if(tp > 0 && (open_price - tp) < min_dist) { tp = open_price - min_dist; ok = false; }
   }

   if(sl > 0) sl = NormalizeDouble(RoundToTick(sym, sl), digits);
   if(tp > 0) tp = NormalizeDouble(RoundToTick(sym, tp), digits);
   return ok;
}

bool GetATR(string sym, ENUM_TIMEFRAMES tf, int period, double &atr_out)
{
   atr_out = 0.0;
   int handle = iATR(sym, tf, period);
   if(handle == INVALID_HANDLE) return false;

   double buf[];
   int copied = CopyBuffer(handle, 0, 0, 2, buf); // latest two values
   if(copied <= 0) { IndicatorRelease(handle); return false; }

   atr_out = buf[0]; // price units
   IndicatorRelease(handle);
   return (atr_out > 0.0);
}

void ComputeATRSltp(string sym, bool is_buy, double open_price, double &sl, double &tp)
{
   double atr=0.0;
   if(!GetATR(sym, ATR_TF, ATR_Period, atr)) return;

   double sl_dist = atr * ATR_SL_Multiplier;
   double tp_dist = 0.0;

   if(ATR_RR_Override > 0.0)         tp_dist = sl_dist * ATR_RR_Override;
   else if(ATR_TP_Multiplier > 0.0)  tp_dist = atr * ATR_TP_Multiplier;

   if(is_buy)
   {
      if(sl <= 0) sl = open_price - sl_dist;
      if(tp <= 0 && tp_dist > 0) tp = open_price + tp_dist;
   }
   else
   {
      if(sl <= 0) sl = open_price + sl_dist;
      if(tp <= 0 && tp_dist > 0) tp = open_price - tp_dist;
   }

   EnforceStopsLevel(sym, sl, tp, open_price, is_buy);
}

// --------------------------- Trading core -------------------------
bool ParseTradeCommand(string json, TradeCommand &cmd)
{
   cmd.action = ExtractStringValue(json, "action");
   cmd.symbol = ExtractStringValue(json, "symbol");
   cmd.lot_size = ExtractDoubleValue(json, "lot_size");
   cmd.stop_loss = ExtractDoubleValue(json, "stop_loss");
   cmd.take_profit = ExtractDoubleValue(json, "take_profit");
   cmd.comment = ExtractStringValue(json, "comment");
   cmd.magic_number = (int)ExtractDoubleValue(json, "magic_number");
   cmd.trade_id = ExtractStringValue(json, "trade_id");
   cmd.ticket = ExtractLongValue(json, "ticket");
   cmd.close_volume = ExtractDoubleValue(json, "close_volume");
   cmd.price = ExtractDoubleValue(json, "price");
   cmd.pending_order_type = ExtractStringValue(json, "pending_order_type");

   if(cmd.action != "buy" && cmd.action != "sell" && cmd.action != "modify" && cmd.action != "close" && cmd.action != "delete")
      return false;

   if(cmd.action == "buy" || cmd.action == "sell")
   {
      if(cmd.lot_size <= 0) cmd.lot_size = DefaultLotSize;
      // 一律用 EA 端解析到的券商代號(如 XAUUSD.s)下單，會員端送來的 "XAUUSD"
      // 只是通用代號、在別家券商不存在，照送會被拒單。
      cmd.symbol = g_sym;
      if(cmd.trade_id == "") cmd.trade_id = IntegerToString(TimeCurrent());
   }

   if(cmd.action == "modify" || cmd.action == "close" || cmd.action == "delete")
   {
      if(cmd.ticket <= 0) { Print("Error: ticket required for ", cmd.action, " command"); return false; }
   }
   return true;
}

bool ExecuteBuySellCommand(TradeCommand &cmd, long &retcode, string &detail)
{
   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req); ZeroMemory(res);
   retcode = 0;
   detail = "";

   req.symbol       = cmd.symbol;
   req.volume       = NormalizeDouble(cmd.lot_size, 2);
   req.type_filling = ORDER_FILLING_IOC;
   req.deviation    = 50;
   req.magic        = cmd.magic_number;
   req.comment      = cmd.comment;

   int digits = (int)SymbolInfoInteger(cmd.symbol, SYMBOL_DIGITS);
   bool is_market = (cmd.price <= 0);

   if(cmd.action == "buy")
   {
      double market_price = SymbolInfoDouble(cmd.symbol, SYMBOL_ASK);
      if(market_price <= 0)
      {
         detail = "invalid ask price";
         Print("Error: ", detail);
         return false;
      }

      if(is_market)
      {
         req.action = TRADE_ACTION_DEAL;
         req.type = ORDER_TYPE_BUY;
         req.price = NormalizeDouble(market_price, digits);
      }
      else
      {
         double entry = NormalizeDouble(cmd.price, digits);
         if(cmd.pending_order_type == "limit" && entry >= market_price)
         {
            detail = "buy limit crossed before execution";
            return false;
         }
         req.action = TRADE_ACTION_PENDING;
         if(entry < market_price)
            req.type = ORDER_TYPE_BUY_LIMIT;
         else
            req.type = ORDER_TYPE_BUY_STOP;
         req.price = entry;
      }

      double sl = cmd.stop_loss;
      double tp = cmd.take_profit;

      if(UseATRBasedSLTP && (sl <= 0 || tp <= 0))
         ComputeATRSltp(cmd.symbol, true, req.price, sl, tp);

      if(sl > 0) req.sl = NormalizeDouble(sl, digits);
      if(tp > 0) req.tp = NormalizeDouble(tp, digits);
   }
   else // sell
   {
      double market_price = SymbolInfoDouble(cmd.symbol, SYMBOL_BID);
      if(market_price <= 0)
      {
         detail = "invalid bid price";
         Print("Error: ", detail);
         return false;
      }

      if(is_market)
      {
         req.action = TRADE_ACTION_DEAL;
         req.type = ORDER_TYPE_SELL;
         req.price = NormalizeDouble(market_price, digits);
      }
      else
      {
         double entry = NormalizeDouble(cmd.price, digits);
         if(cmd.pending_order_type == "limit" && entry <= market_price)
         {
            detail = "sell limit crossed before execution";
            return false;
         }
         req.action = TRADE_ACTION_PENDING;
         if(entry > market_price)
            req.type = ORDER_TYPE_SELL_LIMIT;
         else
            req.type = ORDER_TYPE_SELL_STOP;
         req.price = entry;
      }

      double sl = cmd.stop_loss;
      double tp = cmd.take_profit;

      if(UseATRBasedSLTP && (sl <= 0 || tp <= 0))
         ComputeATRSltp(cmd.symbol, false, req.price, sl, tp);

      if(sl > 0) req.sl = NormalizeDouble(sl, digits);
      if(tp > 0) req.tp = NormalizeDouble(tp, digits);
   }

   if(DetailedLogging)
      Print("Executing trade: ", cmd.action, " ", req.volume, " ", cmd.symbol,
            " @ ", req.price, " SL:", req.sl, " TP:", req.tp,
            is_market ? " [MARKET]" : " [PENDING]");

   bool sent = OrderSend(req, res);
   retcode = (long)res.retcode;
   detail = res.comment;
   if(DetailedLogging)
      Print("Order result - Sent: ", sent, " RetCode: ", res.retcode,
            " Deal: ", res.deal, " Order: ", res.order);

   return (sent && (res.retcode == TRADE_RETCODE_DONE || res.retcode == TRADE_RETCODE_PLACED));
}

bool ExecuteModifyCommand(TradeCommand &cmd, long &retcode, string &detail)
{
   retcode = 0;
   detail = "";
   if(!PositionSelectByTicket(cmd.ticket))
   {
      detail = "position ticket not found";
      Print("Error: Position ticket ", cmd.ticket, " not found");
      return false;
   }
   
   string symbol = PositionGetString(POSITION_SYMBOL);
   MqlTradeRequest req; MqlTradeResult  res;
   ZeroMemory(req); ZeroMemory(res);

   req.action = TRADE_ACTION_SLTP;
   req.position = cmd.ticket;
   req.symbol = symbol;

   double current_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);
   req.sl = (cmd.stop_loss  > 0) ? NormalizeDouble(cmd.stop_loss, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) : current_sl;
   req.tp = (cmd.take_profit> 0) ? NormalizeDouble(cmd.take_profit,(int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) : current_tp;

   if(DetailedLogging) Print("Modifying position ", cmd.ticket, " - New SL: ", req.sl, " New TP: ", req.tp);

   bool sent = OrderSend(req, res);
   retcode = (long)res.retcode;
   detail = res.comment;
   if(DetailedLogging) Print("Modify result - Sent: ", sent, " RetCode: ", res.retcode);
   return(sent && res.retcode == TRADE_RETCODE_DONE);
}

bool ExecuteCloseCommand(TradeCommand &cmd, long &retcode, string &detail)
{
   retcode = 0;
   detail = "";
   if(!PositionSelectByTicket(cmd.ticket))
   {
      detail = "position ticket not found";
      Print("Error: Position ticket ", cmd.ticket, " not found");
      return false;
   }
   
   string symbol = PositionGetString(POSITION_SYMBOL);
   double pos_volume = PositionGetDouble(POSITION_VOLUME);
   long type = PositionGetInteger(POSITION_TYPE);
   
   MqlTradeRequest req; MqlTradeResult  res;
   ZeroMemory(req); ZeroMemory(res);

   req.action = TRADE_ACTION_DEAL;
   req.position = cmd.ticket;
   req.symbol = symbol;
   // Determine desired close volume (partial or full)
   double desired_volume = pos_volume;
   if(cmd.close_volume > 0 && cmd.close_volume < pos_volume)
      desired_volume = cmd.close_volume;
   
   // Normalize to broker volume step/min/max
   double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(vmin <= 0) vmin = 0.01;
   if(vmax <= 0) vmax = pos_volume;
   if(vstep <= 0) vstep = 0.01;
   
   desired_volume = MathMax(vmin, MathMin(desired_volume, vmax));
   desired_volume = MathFloor(desired_volume / vstep) * vstep;
   if(desired_volume <= 0)
   {
      detail = "desired close volume invalid";
      Print("Error: desired close volume invalid: ", desired_volume);
      return false;
   }
   req.volume = NormalizeDouble(desired_volume, 2);
   req.deviation = 50;
   req.type_filling = ORDER_FILLING_IOC;

   if(type == POSITION_TYPE_BUY)
   {
      req.type = ORDER_TYPE_SELL;
      req.price = SymbolInfoDouble(symbol, SYMBOL_BID);
   }
   else
   {
      req.type = ORDER_TYPE_BUY;
      req.price = SymbolInfoDouble(symbol, SYMBOL_ASK);
   }

   req.comment = (cmd.comment != "") ? cmd.comment : "Manual close";

   if(DetailedLogging) Print("Closing position ", cmd.ticket, " - Volume: ", req.volume, " @ ", req.price);

   bool sent = OrderSend(req, res);
   retcode = (long)res.retcode;
   detail = res.comment;
   if(DetailedLogging) Print("Close result - Sent: ", sent, " RetCode: ", res.retcode, " Deal: ", res.deal);
   return(sent && res.retcode == TRADE_RETCODE_DONE);
}

bool ExecuteDeleteCommand(TradeCommand &cmd, long &retcode, string &detail)
{
   MqlTradeRequest req;
   MqlTradeResult  res;
   ZeroMemory(req); ZeroMemory(res);
   retcode = 0;
   detail = "";

   req.action = TRADE_ACTION_REMOVE;
   req.order  = cmd.ticket;

   if(DetailedLogging) Print("Deleting pending order ", cmd.ticket);

   bool sent = OrderSend(req, res);
   retcode = (long)res.retcode;
   detail = res.comment;
   if(DetailedLogging) Print("Delete result - Sent: ", sent, " RetCode: ", res.retcode);
   return(sent && res.retcode == TRADE_RETCODE_DONE);
}

// --------------------------- Logging & commands -------------------
void LogTradeAction(string action, bool result, TradeCommand &cmd, long retcode, string detail)
{
   int handle = FileOpen("trade_results.txt", FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_READ);
   if(handle != INVALID_HANDLE)
   {
      FileSeek(handle, 0, SEEK_END);
      string msg = TimeToString(TimeCurrent()) + " | " + action + " | " + (result ? "SUCCESS" : "FAIL") + " | ";
      if(action == "modify" || action == "close" || action == "delete") msg += "ticket:" + IntegerToString(cmd.ticket) + " | ";
      else msg += DoubleToString(cmd.lot_size, 2) + " | ";
      msg += cmd.symbol + " | " + cmd.trade_id + " | retcode:" + IntegerToString((int)retcode);
      if(detail != "") msg += " | " + detail;
      FileWrite(handle, msg);
      FileClose(handle);
   }
   if(DetailedLogging)
      Print("Trade logged: ", action, " ", (result ? "SUCCESS" : "FAIL"), " Symbol: ", cmd.symbol, " TradeID: ", cmd.trade_id, " RetCode: ", retcode, " Detail: ", detail);
}

void CheckTradeCommands()
{
   int handle = FileOpen("commands.json", FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE) return;

   string cmd = "";
   string line;
   while(!FileIsEnding(handle))
   {
      line = FileReadString(handle);
      cmd += line;
   }
   FileClose(handle);

   if(StringLen(cmd) < 10) return;
   if(DetailedLogging) Print("Received command: ", cmd);

   TradeCommand trade_cmd;
   if(ParseTradeCommand(cmd, trade_cmd))
   {
      bool result = false;
      long retcode = 0;
      string detail = "";
      if(trade_cmd.action=="buy" || trade_cmd.action=="sell") result = ExecuteBuySellCommand(trade_cmd, retcode, detail);
      else if(trade_cmd.action=="modify") result = ExecuteModifyCommand(trade_cmd, retcode, detail);
      else if(trade_cmd.action=="close")  result = ExecuteCloseCommand(trade_cmd, retcode, detail);
      else if(trade_cmd.action=="delete") result = ExecuteDeleteCommand(trade_cmd, retcode, detail);

      LogTradeAction(trade_cmd.action, result, trade_cmd, retcode, detail);

      int h = FileOpen("commands.json", FILE_WRITE|FILE_TXT|FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWrite(h, "{}"); FileClose(h); }
   }
   else
   {
      if(DetailedLogging) Print("Failed to parse command: ", cmd);
   }
}

//+------------------------------------------------------------------+
//| Cloud Hub polling — pull signals & execute (member-side direct)  |
//+------------------------------------------------------------------+
bool ExtractBoolValue(string json, string key)
{
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start < 0) return false;
   start += StringLen(search);
   string rest = StringSubstr(json, start, 6);
   StringReplace(rest, " ", "");
   return (StringFind(rest, "true") == 0);
}

double ExtractFirstTP(string json)
{
   int k = StringFind(json, "\"take_profit\":");
   if(k < 0) return 0.0;
   int b = StringFind(json, "[", k);
   if(b < 0) return 0.0;
   int e1 = StringFind(json, ",", b + 1);
   int e2 = StringFind(json, "]", b + 1);
   int e = e2;
   if(e1 >= 0 && (e2 < 0 || e1 < e2)) e = e1;
   if(e < 0) return 0.0;
   string v = StringSubstr(json, b + 1, e - (b + 1));
   StringReplace(v, " ", "");
   if(v == "" || v == "null") return 0.0;
   return StringToDouble(v);
}

void ProcessHubSignalElement(string elem)
{
   long seq = ExtractLongValue(elem, "seq");
   if(seq <= g_hub_last_seq) return;

   string typ      = ExtractStringValue(elem, "type");
   string dir      = ExtractStringValue(elem, "direction");
   double entry    = ExtractDoubleValue(elem, "entry_price");
   double sl       = ExtractDoubleValue(elem, "stop_loss");
   double tp       = ExtractFirstTP(elem);
   bool   isMarket = ExtractBoolValue(elem, "is_market_order");

   bool ok = (typ == "trade_signal")
          && (dir == "buy" || dir == "sell")
          && sl > 0 && tp > 0
          && (isMarket || entry > 0);

   if(ok)
   {
      TradeCommand cmd;
      cmd.action       = dir;
      cmd.symbol       = Symbol();                       // 用「圖表的品種」, 自動對應各券商 (如 XAUUSD.s)
      cmd.lot_size     = (HubLotSize > 0 ? HubLotSize : DefaultLotSize);
      cmd.stop_loss    = sl;
      cmd.take_profit  = tp;
      cmd.price        = (isMarket ? 0.0 : entry);       // 0 = 市價, >0 = 掛單
      cmd.comment      = "hub_" + IntegerToString(seq);
      cmd.magic_number = HubMagic;
      cmd.trade_id     = "hub_" + IntegerToString(seq);
      cmd.ticket       = 0;
      cmd.close_volume = 0;

      long rc = 0; string detail = "";
      bool done = ExecuteBuySellCommand(cmd, rc, detail);
      Print("[Hub] seq=", seq, " ", dir, " entry=", entry, " sl=", sl, " tp=", tp,
            " => ", (done ? "OK" : "FAIL"), " rc=", rc, " ", detail);
   }
   else if(DetailedLogging)
   {
      Print("[Hub] 略過 seq=", seq, " (type=", typ, " dir=", dir, " sl=", sl, " tp=", tp, ")");
   }

   g_hub_last_seq = seq;   // 不論成功與否都前進, 避免卡住或重下舊單
}

void ParseAndExecuteSignals(string body)
{
   int sigPos = StringFind(body, "\"signals\":");
   if(sigPos < 0) return;
   int arrStart = StringFind(body, "[", sigPos);
   if(arrStart < 0) return;

   int n = StringLen(body);
   int i = arrStart + 1;
   int depth = 0;
   int elemStart = -1;
   bool inStr = false;
   ushort prev = 0;

   while(i < n)
   {
      ushort ch = StringGetCharacter(body, i);
      if(ch == '"' && prev != '\\')
      {
         inStr = !inStr;
      }
      else if(!inStr)
      {
         if(ch == '{')
         {
            if(depth == 0) elemStart = i;
            depth++;
         }
         else if(ch == '}')
         {
            depth--;
            if(depth == 0 && elemStart >= 0)
            {
               ProcessHubSignalElement(StringSubstr(body, elemStart, i - elemStart + 1));
               elemStart = -1;
            }
         }
         else if(ch == ']' && depth == 0)
         {
            break;
         }
      }
      prev = ch;
      i++;
   }
}

void LoadHubConfig()
{
   // 1) start from EA inputs
   g_hub_on       = EnableHubPolling;
   g_hub_url      = HubUrl;
   g_hub_token    = HubToken;
   g_hub_interval = (HubPollSeconds > 0 ? HubPollSeconds : 3);

   // 2) override from Files/hub_config.txt if present (繞過 MT5 輸入快取)
   int h = FileOpen("hub_config.txt", FILE_READ|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      string cfg = "";
      while(!FileIsEnding(h)) cfg += FileReadString(h);
      FileClose(h);
      if(StringLen(cfg) > 2)
      {
         g_hub_on = ExtractBoolValue(cfg, "enabled");
         string u = ExtractStringValue(cfg, "url");
         string t = ExtractStringValue(cfg, "token");
         long   p = ExtractLongValue(cfg, "poll_seconds");
         if(u != "") g_hub_url = u;
         if(t != "") g_hub_token = t;
         if(p > 0)   g_hub_interval = (int)p;
         Print("[Hub] config 來自 hub_config.txt");
      }
   }

   Print("[Hub] init: enabled=", g_hub_on, " url=", g_hub_url,
         " token_len=", StringLen(g_hub_token), " interval=", g_hub_interval, "s");
}

void PollHub()
{
   long after = (g_hub_last_seq < 0 ? 0 : g_hub_last_seq);
   string url = g_hub_url + "/signals?after=" + IntegerToString(after) + "&limit=50&token=" + g_hub_token;

   char  data[];
   char  result[];
   string result_headers;
   ResetLastError();
   int code = WebRequest("GET", url, "", 10000, data, result, result_headers);

   if(code == -1)
   {
      int err = GetLastError();
      if(!g_hub_warned)
      {
         Print("[Hub] WebRequest 失敗 err=", err,
               " — 請到 [工具>選項>EA交易] 勾選 '允許 WebRequest', 並把此網址加入清單: ", g_hub_url);
         g_hub_warned = true;
      }
      return;
   }
   g_hub_warned = false;

   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   if(code != 200)
   {
      int err = GetLastError();
      Print("[Hub] code=", code, " GetLastError=", err, " body_len=", StringLen(body),
            " body=", StringSubstr(body, 0, 80));
      if(StringFind(body, "\"signals\"") < 0)
         return;   // 真的失敗 (body 無資料)
      // 否則 body 其實有資料 → 繼續往下處理
   }

   // 第一次成功連線: 對齊到目前最新 seq, 不補下歷史訊號
   if(g_hub_last_seq < 0)
   {
      long latest = ExtractLongValue(body, "latest_seq");
      g_hub_last_seq = (latest > 0 ? latest : 0);
      Print("[Hub] 已連線, 從 seq=", g_hub_last_seq, " 開始 (不補歷史)");
      return;
   }

   ParseAndExecuteSignals(body);
}
