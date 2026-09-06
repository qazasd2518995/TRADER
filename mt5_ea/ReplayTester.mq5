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
input string ShotPrefix     = "frame";          // 影格檔名前綴（不要放子目錄，MQL5 不會自動建）
input int    ShotWidth      = 1280;
input int    ShotHeight     = 720;
input double Lots           = 0.10;
input int    PendingHours   = 4;                 // 掛單多久沒成交就撤
input int    ShotEveryNBars = 1;                 // 每幾根截一張（拉長可縮短片長）
// ── 圖表模式（非測試器）專用 ──────────────────────────────────────
input int    LeadBars       = 40;                // 掛單前先鋪幾根當背景
input int    TailBars       = 70;                // 掛單後再走幾根
input int    RedrawMs       = 40;                // 每格等重繪多久（太短會截到畫一半）
input int    MaxJobs        = 3;                 // 一次最多做幾筆（先看效果用）
input int    LabelSize      = 11;                // 標示文字大小
input int    HoldFrames     = 8;                 // 關鍵瞬間多停幾格

struct Job
{
   datetime when;
   bool     is_buy;
   double   entry;
   double   stop;
   double   target;
   bool     placed;
   // 以下由 Python 端算好帶進來 —— EA 在真實圖表上沒有訂單，算不出來
   datetime fill_at;      // 0 = 沒成交
   datetime exit_at;
   double   exit_price;   // 0 = 沒成交過
   string   outcome;      // TP / SL / BE / EXPIRED / TIMEOUT / NOFILL
};

Job      g_jobs[];
int      g_next  = 0;
int      g_shots = 0;
int      g_fails = 0;
int      g_bars  = 0;
datetime g_last_bar = 0;
CTrade   g_trade;
bool     g_in_tester = false;
bool     g_chart_done = false;

//+------------------------------------------------------------------+
int OnInit()
{
   g_in_tester = MQLInfoInteger(MQL_TESTER);
   if(!LoadJobs())
   {
      Print("讀不到 ", JobFile, "，或內容是空的");
      return INIT_FAILED;
   }
   if(g_in_tester)
   {
      g_trade.SetExpertMagicNumber(990001);
      g_trade.SetTypeFillingBySymbol(_Symbol);
      PrintFormat("測試器模式：回放 %d 筆報單，第一筆 %s",
                  ArraySize(g_jobs), TimeToString(g_jobs[0].when));
   }
   else
   {
      // 圖表模式：只畫圖、只截圖，一張單都不下。
      // 兩個模式剛好互補 —— 測試器能下單但截不了圖（ChartScreenShot 在
      // 那裡回報成功卻不寫檔），真實圖表能截圖，訂單就自己畫上去。
      PrintFormat("圖表模式：%d 筆報單，只畫圖不下單", ArraySize(g_jobs));
      EventSetTimer(1);
   }
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!g_in_tester)
      return;                       // 圖表模式不下單、也不靠 tick 推進
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
   EventKillTimer();
   PrintFormat("回放結束：掛出 %d 筆、截圖 %d 張（失敗 %d）", g_next, g_shots, g_fails);
}

//+------------------------------------------------------------------+
//| 圖表模式：在真實圖表上逐根捲動並截圖。                            |
//| 只做一次 —— 做完就把計時器關掉，不要一直重畫使用者的圖表。        |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(g_in_tester || g_chart_done)
      return;
   g_chart_done = true;
   EventKillTimer();
   RenderAll();
}

void RenderAll()
{
   long chart = ChartID();
   // 記住原本的樣子，做完要還原 —— 這是使用者在看的圖表。
   bool  old_auto  = (bool)ChartGetInteger(chart, CHART_AUTOSCROLL);
   bool  old_shift = (bool)ChartGetInteger(chart, CHART_SHIFT);
   long  old_scale = ChartGetInteger(chart, CHART_SCALE);

   ChartSetInteger(chart, CHART_AUTOSCROLL, false);
   ChartSetInteger(chart, CHART_SHIFT, false);
   ChartSetInteger(chart, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(chart, CHART_SHOW_GRID, false);
   ChartSetInteger(chart, CHART_SHOW_VOLUMES, CHART_VOLUME_HIDE);
   ChartSetInteger(chart, CHART_SHOW_PERIOD_SEP, false);

   int done = 0;
   for(int j = 0; j < ArraySize(g_jobs) && done < MaxJobs; j++)
      if(RenderOne(chart, g_jobs[j], j))
         done++;

   ChartSetInteger(chart, CHART_AUTOSCROLL, old_auto);
   ChartSetInteger(chart, CHART_SHIFT, old_shift);
   ChartSetInteger(chart, CHART_SCALE, old_scale);
   ChartNavigate(chart, CHART_END, 0);
   ChartRedraw(chart);
   PrintFormat("圖表回放完成：%d 筆、%d 張影格（失敗 %d）", done, g_shots, g_fails);
}

bool RenderOne(long chart, Job &job, int index)
{
   // 先把那段歷史拉進來。ChartNavigate 只捲得到「已經載入」的範圍，
   // 沒先載入的話它會默默停在最新的位置 —— 畫面看起來正常，日期卻是錯的
   // （實際踩過：整批 333 張都停在最新那天）。
   MqlRates probe[];
   int got = CopyRates(_Symbol, PERIOD_CURRENT,
                       job.when - 7200, job.when + 86400, probe);
   if(got <= 0)
   {
      PrintFormat("第 %d 筆：載不到 %s 附近的歷史（CopyRates=%d, err=%d）",
                  index, TimeToString(job.when), got, GetLastError());
      return false;
   }

   int total = Bars(_Symbol, PERIOD_CURRENT);
   int bar = iBarShift(_Symbol, PERIOD_CURRENT, job.when, false);
   if(bar < 0 || bar >= total)
   {
      PrintFormat("第 %d 筆：圖表找不到 %s（bar=%d, 總共 %d 根）",
                  index, TimeToString(job.when), bar, total);
      return false;
   }
   PrintFormat("第 %d 筆 %s %s：bar=%d / 共 %d 根，該根時間 %s",
               index, job.is_buy ? "買" : "賣", DoubleToString(job.entry, 2),
               bar, total, TimeToString(iTime(_Symbol, PERIOD_CURRENT, bar)));

   int from = MathMin(bar + LeadBars, total - 1);
   int to   = MathMax(bar - TailBars, 0);
   bool checked = false;
   int shot = 0;

   for(int i = from; i >= to; i--)
   {
      ChartNavigate(chart, CHART_END, -i);
      if(i <= bar)
         DrawLevels(chart, job, i);
      else
         ObjectsDeleteAll(chart, "rp_");
      ChartRedraw(chart);
      Sleep(RedrawMs);

      // 只驗第一格：ChartNavigate 是非同步的，捲不到位就整段畫錯位置，
      // 而且畫面上完全看不出哪裡不對（先前整批停在最新的日期）。
      if(!checked)
      {
         checked = true;
         long first_vis = ChartGetInteger(chart, CHART_FIRST_VISIBLE_BAR);
         long visible   = ChartGetInteger(chart, CHART_VISIBLE_BARS);
         long landed    = first_vis - visible + 1;      // 最右邊那根的索引
         if(MathAbs(landed - i) > 2)
            PrintFormat("⚠ 捲動沒到位：要 %d，實際落在 %d"
                        "（可見 %d 根）—— 圖表可能沒載入那麼多歷史",
                        i, (int)landed, (int)visible);
      }

      // 掛單出現、成交、出場這三個瞬間各多停幾格。一格就閃過去的話，
      // 觀眾根本來不及看到「就是這一刻進場的」。
      datetime at = iTime(_Symbol, PERIOD_CURRENT, i);
      int hold = 1;
      if(i == bar) hold = HoldFrames;
      else if(job.fill_at > 0 && at == job.fill_at) hold = HoldFrames;
      else if(job.exit_at > 0 && at == job.exit_at) hold = HoldFrames * 2;

      for(int k = 0; k < hold; k++)
      {
         string file = StringFormat("%s_%02d_%04d.png", ShotPrefix, index, shot);
         ResetLastError();
         if(ChartScreenShot(chart, file, ShotWidth, ShotHeight, ALIGN_RIGHT))
            g_shots++;
         else
         {
            g_fails++;
            if(g_fails <= 3)
               PrintFormat("截圖失敗 %s err=%d", file, GetLastError());
         }
         shot++;
      }
   }
   ObjectsDeleteAll(chart, "rp_");
   return true;
}

//+------------------------------------------------------------------+
//| 畫出這張單的全部資訊。now_bar 是目前最右邊那根的索引 —— 成交與     |
//| 出場標記只在「已經走到那個時間」之後才出現，不然等於劇透。        |
//+------------------------------------------------------------------+
void DrawLevels(long chart, Job &job, int now_bar)
{
   datetime now = iTime(_Symbol, PERIOD_CURRENT, now_bar);

   Level(chart, "rp_entry", job.entry, clrGold, STYLE_DASH,
         (job.is_buy ? "買進 " : "賣出 ") + DoubleToString(job.entry, 2));
   Level(chart, "rp_stop", job.stop, clrTomato, STYLE_DOT,
         "止損 " + DoubleToString(job.stop, 2));
   Level(chart, "rp_tp", job.target, clrSpringGreen, STYLE_DOT,
         "止盈 " + DoubleToString(job.target, 2));

   if(job.fill_at > 0 && now >= job.fill_at)
   {
      Mark(chart, "rp_fill", job.fill_at, job.entry,
           job.is_buy ? OBJ_ARROW_BUY : OBJ_ARROW_SELL, clrGold,
           "成交 " + DoubleToString(job.entry, 2));
      // 成交之後進場線改實線，一眼看得出「已經進場了」
      ObjectSetInteger(chart, "rp_entry", OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(chart, "rp_entry", OBJPROP_WIDTH, 3);
   }

   if(job.exit_at > 0 && job.exit_price > 0 && now >= job.exit_at)
   {
      bool win = (job.outcome == "TP");
      Mark(chart, "rp_exit", job.exit_at, job.exit_price, OBJ_ARROW_STOP,
           win ? clrSpringGreen : clrTomato,
           OutcomeText(job.outcome) + " " + DoubleToString(job.exit_price, 2));
   }

   DrawHUD(chart, job, now_bar);
}

//| 價位線 + 貼在線上的文字。只有線沒有數字的話，看的人得自己去對右邊
//| 的價格軸，那正是使用者反映看不清楚的地方。
void Level(long chart, string name, double price, color clr, int style,
           string label)
{
   if(ObjectFind(chart, name) < 0)
      ObjectCreate(chart, name, OBJ_HLINE, 0, 0, price);
   ObjectSetDouble(chart, name, OBJPROP_PRICE, price);
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_STYLE, style);
   ObjectSetInteger(chart, name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(chart, name, OBJPROP_BACK, true);

   // 文字釘在畫面左緣，跟著捲動走，永遠看得到
   string tag = name + "_txt";
   datetime anchor = iTime(_Symbol, PERIOD_CURRENT,
                           (int)ChartGetInteger(chart, CHART_FIRST_VISIBLE_BAR) - 2);
   if(ObjectFind(chart, tag) < 0)
      ObjectCreate(chart, tag, OBJ_TEXT, 0, anchor, price);
   ObjectMove(chart, tag, 0, anchor, price);
   ObjectSetString(chart, tag, OBJPROP_TEXT, " " + label);
   ObjectSetInteger(chart, tag, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, tag, OBJPROP_FONTSIZE, LabelSize);
   ObjectSetInteger(chart, tag, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
}

void Mark(long chart, string name, datetime when, double price,
          ENUM_OBJECT type, color clr, string label)
{
   if(ObjectFind(chart, name) < 0)
      ObjectCreate(chart, name, type, 0, when, price);
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_WIDTH, 4);

   string tag = name + "_txt";
   if(ObjectFind(chart, tag) < 0)
      ObjectCreate(chart, tag, OBJ_TEXT, 0, when, price);
   ObjectMove(chart, tag, 0, when, price);
   ObjectSetString(chart, tag, OBJPROP_TEXT, "  " + label);
   ObjectSetInteger(chart, tag, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, tag, OBJPROP_FONTSIZE, LabelSize);
   ObjectSetInteger(chart, tag, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
}

//+------------------------------------------------------------------+
//| 畫面右上角的即時狀態。這是「看盤感」的來源 —— 只有結束才報一個    |
//| 數字的話，中間那段是死的；浮動損益跟著每根 K 棒跳，才像真人在看。 |
//+------------------------------------------------------------------+
void DrawHUD(long chart, Job &job, int now_bar)
{
   datetime now   = iTime(_Symbol, PERIOD_CURRENT, now_bar);
   double   price = iClose(_Symbol, PERIOD_CURRENT, now_bar);
   bool     filled = (job.fill_at > 0 && now >= job.fill_at);
   bool     closed = (job.exit_at > 0 && now >= job.exit_at);

   string state;
   color  state_clr;
   if(!filled)
   {
      state = (job.outcome == "EXPIRED" && closed) ? "逾時撤單" : "掛單中，等待進場";
      state_clr = (job.outcome == "EXPIRED" && closed) ? clrSilver : clrGold;
   }
   else if(!closed)
   {
      state = "已進場，持倉中";
      state_clr = clrWhite;
   }
   else
   {
      state = OutcomeText(job.outcome);
      state_clr = (job.outcome == "TP") ? clrSpringGreen : clrTomato;
   }

   Hud(chart, "rp_hud1", 26, StringFormat("%s  %s",
       job.is_buy ? "買進" : "賣出", DoubleToString(job.entry, 2)),
       job.is_buy ? clrSpringGreen : clrTomato, 22);
   Hud(chart, "rp_hud2", 60, state, state_clr, 17);

   // 浮動損益：成交之後才有意義。收盤後就固定在出場價，不要再跳。
   if(filled)
   {
      double ref = closed ? job.exit_price : price;
      double pnl = (ref - job.entry) * (job.is_buy ? 1 : -1) * 100.0;
      Hud(chart, "rp_hud3", 100,
          StringFormat("%s%.0f USD / 手", pnl >= 0 ? "+" : "", pnl),
          pnl >= 0 ? clrSpringGreen : clrTomato, 30);
   }
   else
      ObjectDelete(chart, "rp_hud3");

   Hud(chart, "rp_hud4", 148, TimeToString(now, TIME_DATE|TIME_MINUTES),
       clrSilver, 13);
}

void Hud(long chart, string name, int y, string text, color clr, int size)
{
   if(ObjectFind(chart, name) < 0)
   {
      ObjectCreate(chart, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(chart, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(chart, name, OBJPROP_XDISTANCE, 22);
      ObjectSetInteger(chart, name, OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(chart, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_FONTSIZE, size);
   ObjectSetString(chart, name, OBJPROP_TEXT, text);
}

string OutcomeText(string code)
{
   if(code == "TP")      return "全部止盈";
   if(code == "SL")      return "止損出場";
   if(code == "BE")      return "保本出場";
   if(code == "TIMEOUT") return "逾時平倉";
   if(code == "EXPIRED") return "逾時撤單";
   return code;
}

//+------------------------------------------------------------------+
//| 掛一張限價單（只在測試器模式）。用限價而不是市價 —— 訊號源給的      |
//| 一律是進場價，用市價成交會變成另一個價位，畫面上的進場線就對不上。 |
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

//| 測試器模式的截圖。留著是為了對照 —— 它會回報成功卻不寫檔，
//| 那正是我們最後改走真實圖表的原因。
void Shoot()
{
   string file = StringFormat("%s_%05d.png", ShotPrefix, g_shots);
   ResetLastError();
   bool ok = ChartScreenShot(ChartID(), file, ShotWidth, ShotHeight, ALIGN_RIGHT);
   if(ok) g_shots++; else g_fails++;
   if(g_shots + g_fails <= 3)
      PrintFormat("截圖 %s -> %s  err=%d  chart=%I64d", file,
                  ok ? "回報成功" : "失敗", GetLastError(), ChartID());
}

bool LoadJobs()
{
   // 一定要 FILE_COMMON：測試器的每個 agent 有自己的沙箱
   // （Tester\Agent-...\MQL5\Files），讀不到終端那份 MQL5\Files。
   // 少了這個旗標，EA 會在 OnInit 就因為「找不到清單」而中止。
   int h = FileOpen(JobFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE)
   {
      PrintFormat("開不了共用資料夾裡的 %s（err=%d）—— 那份檔要放在 %s",
                  JobFile, GetLastError(),
                  TerminalInfoString(TERMINAL_COMMONDATA_PATH));
      return false;
   }

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) < 10 || StringGetCharacter(line, 0) == '#')
         continue;

      string part[];
      int cols = StringSplit(line, ',', part);
      if(cols < 5)
         continue;

      Job job;
      job.when   = StringToTime(part[0]);
      job.is_buy = (StringFind(part[1], "buy") >= 0);
      job.entry  = StringToDouble(part[2]);
      job.stop   = StringToDouble(part[3]);
      job.target = StringToDouble(part[4]);
      job.placed = false;
      // 舊格式只有 5 欄，缺的就當成沒成交，不要讓整份清單讀不進來
      job.fill_at    = (cols > 5) ? StringToTime(part[5]) : 0;
      job.exit_at    = (cols > 6) ? StringToTime(part[6]) : 0;
      job.exit_price = (cols > 7) ? StringToDouble(part[7]) : 0.0;
      job.outcome    = (cols > 8) ? part[8] : "";
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
