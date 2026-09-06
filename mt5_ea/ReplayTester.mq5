//+------------------------------------------------------------------+
//| 訊號回放 —— 給策略測試器「視覺模式」用。                          |
//|                                                                  |
//| 做什麼                                                            |
//|   讀一份報單清單，在測試器的時間軸走到各自時間點時把單掛出去。      |
//|   掛單線、進場箭頭、止損止盈線、出場箭頭全部由 MT5 自己畫 ——      |
//|   那正是我們要的「真實 MT5 畫面」。                                |
//|                                                                  |
//| 為什麼順便截圖                                                    |
//|   測試器的圖表也是一張真的 chart，所以 EA 可以直接呼叫            |
//|   ChartScreenShot 產生影格，不必錄螢幕 —— 錄螢幕會被彈窗、        |
//|   遮擋、解析度變化毀掉，而且毀了不能重來。                        |
//|                                                                  |
//| 清單格式（MQL5/Files/replay_job.csv，一行一筆）                    |
//|   2026.08.19 19:24:58,buy,4370.0,4360.0,4385.0                   |
//|   時間,方向,進場,止損,止盈                                        |
//|                                                                  |
//| 這支只在測試器裡跑。掛到真實圖表上會直接拒絕執行 —— 它會照著      |
//| 清單無條件下單，接到實盤等於照著舊訊號重下一輪。                  |
//+------------------------------------------------------------------+
#property copyright "Gold Copy Trader"
#property strict

#include <Trade/Trade.mqh>

input string JobFile        = "replay_job.csv";  // 報單清單
input bool   TakeShots      = true;              // 每根 K 棒截一張圖
input string ShotPrefix     = "replay/frame";    // 影格檔名前綴
input int    ShotWidth      = 1280;
input int    ShotHeight     = 720;
input double Lots           = 0.10;
input int    PendingHours   = 4;                 // 掛單多久沒成交就撤
input int    ShotEveryNBars = 1;                 // 每幾根截一張（拉長可縮短片長）

struct Job
{
   datetime when;
   bool     is_buy;
   double   entry;
   double   stop;
   double   target;
   bool     placed;
};

Job      g_jobs[];
int      g_next  = 0;
int      g_shots = 0;
int      g_bars  = 0;
datetime g_last_bar = 0;
CTrade   g_trade;

//+------------------------------------------------------------------+
int OnInit()
{
   if(!MQLInfoInteger(MQL_TESTER))
   {
      // 實盤圖表上絕不能跑：它會照著清單無條件下單。
      Alert("ReplayTester 只能在策略測試器裡執行");
      return INIT_FAILED;
   }
   if(!LoadJobs())
   {
      Print("讀不到 ", JobFile, "，或內容是空的");
      return INIT_FAILED;
   }
   g_trade.SetExpertMagicNumber(990001);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   PrintFormat("回放 %d 筆報單，第一筆 %s", ArraySize(g_jobs),
               TimeToString(g_jobs[0].when));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   datetime now = TimeCurrent();

   // 走到時間就掛單。清單已按時間排序，所以只要往前推 g_next。
   while(g_next < ArraySize(g_jobs) && g_jobs[g_next].when <= now)
   {
      PlaceOrder(g_jobs[g_next]);
      g_next++;
   }

   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_last_bar)
      return;                       // 同一根裡不重複截圖
   g_last_bar = bar;
   g_bars++;

   if(TakeShots && (g_bars % ShotEveryNBars) == 0)
      Shoot();
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   PrintFormat("回放結束：掛出 %d 筆、截圖 %d 張", g_next, g_shots);
}

//+------------------------------------------------------------------+
//| 掛一張限價單。用限價而不是市價 —— 訊號源給的一律是進場價，        |
//| 用市價成交會變成另一個價位，畫面上的進場線就對不上。              |
//+------------------------------------------------------------------+
void PlaceOrder(Job &job)
{
   datetime expiry = job.when + PendingHours * 3600;
   bool ok;
   if(job.is_buy)
      ok = g_trade.BuyLimit(Lots, job.entry, _Symbol, job.stop, job.target,
                            ORDER_TIME_SPECIFIED, expiry, "replay");
   else
      ok = g_trade.SellLimit(Lots, job.entry, _Symbol, job.stop, job.target,
                             ORDER_TIME_SPECIFIED, expiry, "replay");
   job.placed = ok;
   if(!ok)
      PrintFormat("掛單失敗 %s %s %.2f  retcode=%d",
                  TimeToString(job.when), job.is_buy ? "buy" : "sell",
                  job.entry, g_trade.ResultRetcode());
}

//+------------------------------------------------------------------+
//| 截一張測試器圖表。ALIGN_RIGHT 讓最新那根貼齊右緣，播起來才像      |
//| K 棒一根一根長出來。                                              |
//+------------------------------------------------------------------+
void Shoot()
{
   string file = StringFormat("%s_%05d.png", ShotPrefix, g_shots);
   if(ChartScreenShot(ChartID(), file, ShotWidth, ShotHeight, ALIGN_RIGHT))
      g_shots++;
   else if(g_shots == 0)
      PrintFormat("ChartScreenShot 失敗 err=%d —— 測試器可能不是視覺模式",
                  GetLastError());
}

//+------------------------------------------------------------------+
bool LoadJobs()
{
   int h = FileOpen(JobFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE)
      return false;

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) < 10 || StringGetCharacter(line, 0) == '#')
         continue;

      string part[];
      if(StringSplit(line, ',', part) < 5)
         continue;

      Job job;
      job.when   = StringToTime(part[0]);
      job.is_buy = (StringFind(part[1], "buy") >= 0);
      job.entry  = StringToDouble(part[2]);
      job.stop   = StringToDouble(part[3]);
      job.target = StringToDouble(part[4]);
      job.placed = false;
      if(job.when <= 0 || job.entry <= 0)
         continue;

      int n = ArraySize(g_jobs);
      ArrayResize(g_jobs, n + 1);
      g_jobs[n] = job;
   }
   FileClose(h);
   return ArraySize(g_jobs) > 0;
}
//+------------------------------------------------------------------+
