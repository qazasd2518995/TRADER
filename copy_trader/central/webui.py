"""會員端控制台的前端。

整頁的 HTML/CSS/JS 都放在這個模組的 PAGE 樣板裡，理由是打包：PyInstaller 的
spec 只收 .py，額外的 .html/.css 檔要另外加進 datas，改一次前端就要動一次
build.spec。放在模組裡就跟著程式碼一起進包，Windows / macOS 兩條打包線都不用改。

樣板刻意不是 f-string——CSS 與 JS 裡的大括號太多，f-string 得整份跳脫，之後
沒人敢改。這裡改用 __TOKEN__ 佔位符，最後用 str.replace 填。

配色不是挑順眼的。獲利紅 #d42a3f 與虧損綠 #0e7c5a 是跑過 dataviz 的色覺
驗證器選出來的：紅綠是紅綠色盲最危險的組合，這組在 protanopia 模擬下
OKLab ΔE 9.1（門檻 8.0），深色模式那組是 11.2。除了顏色，每個數字另外帶
正負號、▲▼ 與「贏／輸」文字，色覺缺陷或黑白列印都還讀得出來。

紅漲綠跌是台灣盤房的慣例，跟 MT5 原生配色相反，這是刻意的——使用者是台灣會員。
"""

from __future__ import annotations

import json

from copy_trader.central.membership import (
    HIGH_FREQ, LOW_FREQ, MID_FREQ, MIN_PASSWORD_LENGTH, SCHEDULE_LIMIT, ULTRA_HIGH_FREQ,
)
from typing import Any

CLIENT_FIELDS = """
      <div class="field-group">
        <h3>訊號來源設定</h3>
        <div id="sourceSettings"></div>
        <input type="hidden" id="source_profiles" />
        <p class="hint">手數、馬丁、止盈處理全部在這張表裡「每個來源各自設定」。選「均注」= 每筆固定手數、不進關卡；選「馬丁」= 逐關加碼(基礎手數 × 倍數)，各來源層級獨立、互不影響。止盈處理選「分批平倉」= <b>直接填每個止盈要平多少手</b>(例 0.01/0.01/0.01，每段最低 0.01 手，基礎手數會自動 = 三段總和)；選「保本移損」= <b>價格觸及你設定的保本距離時，把停損移到進場價位</b>(例:填 3，進場後朝有利方向走 3 美元就保本)，手數整筆不動；距離填 0 = 不啟用，改成觸及第一個止盈才保本。多個止盈的訊號在保本之後還會逐關把停損往前推(觸及第二個止盈就推到第一個)。選「單一點位」= 止盈掛最近那一關就不再動。中頻訊號一單只有一個止盈，沒有東西可以分批，所以只給單一點位／保本移損。每日止盈 / 止損:該來源當日損益達到就今日停跟。0 代表不限。MT5 連線自動偵測，登入後自動開始跟單。</p>
      </div>

      <div class="field-group" id="scheduleGroup">
        <h3>自動排程</h3>
        <div id="scheduleBox"></div>
        <input type="hidden" id="auto_schedules" />
        <p class="hint" id="scheduleHint"></p>
      </div>
"""

CENTRAL_FIELDS = """
      <div class="field-group">
        <h3>LINE 本機資料庫</h3>
        <div class="field-grid">
          <label>加密資料庫路徑<input id="line_database_path" placeholder="可留空自動尋找；多個候選時請明確選擇" /></label>
          <label>安全金鑰名稱<input id="line_keychain_service" placeholder="line-db-research" /></label>
          <label class="field-wide">聊天室設定（JSON）<textarea id="line_chats" spellcheck="false" placeholder='[{"name":"gold_signal_1","chat_name":"（乘）黃金報單🈲言群","display_name":"黃金報單🈲言群","trusted_senders":["乘","James"],"parser_profile":"mid_frequency_v1","max_trade_age_seconds":300,"recall_watch_seconds":2592000},{"name":"high_freq_yuyu","chat_name":"🈲禁言群🈲 Focus forex 焦點利潤","display_name":"焦點利潤(yuyu)","trusted_senders":["yuyu（yu__o822"],"parser_profile":"yuyu_range_v1","max_trade_age_seconds":180,"recall_watch_seconds":2592000}]'></textarea></label>
        </div>
        <div class="inline-actions">
          <button class="btn" id="findLineDatabase" type="button">自動尋找資料庫</button>
          <button class="btn" id="testLineDatabase" type="button">測試 LINE 資料庫</button>
          <span class="hint" id="lineDatabaseResult"></span>
        </div>
        <p class="hint">金鑰不會存進設定或送到瀏覽器。macOS 從 Keychain 讀取；Windows 從目前使用者的 Credential Manager 讀取。第一次設定請依 docs/windows-line-database.md 操作。</p>
      </div>
      <div class="field-group">
        <h3>第三來源：超高頻交易</h3>
        <div class="field-grid">
          <label>中央 MT5 Files 路徑<input id="market_mt5_files_dir" placeholder="可留空自動偵測；必須掛新版 MT5 bridge" /></label>
          <label class="switch">啟用實單訊號<input id="ultra_strategy_enabled" type="checkbox" /></label>
          <label>每日最多訊號<input id="ultra_max_signals_per_day" type="number" min="1" max="96" /></label>
          <label>訊號冷卻（秒）<input id="ultra_cooldown_seconds" type="number" min="60" /></label>
          <label>未成交撤單（秒）<input id="ultra_pending_expiry_seconds" type="number" min="120" /></label>
          <label>最大 spread（美元）<input id="ultra_max_spread" type="number" step="0.01" min="0.05" /></label>
          <label>最小 H1 ATR<input id="ultra_min_h1_atr" type="number" step="0.1" min="0.1" /></label>
          <label>最大 H1 ATR<input id="ultra_max_h1_atr" type="number" step="0.1" min="1" /></label>
        </div>
        <p class="hint">這是獨立的市場資料模型，不讀 LINE、不使用乘或 yuyu 的名稱／訊息 ID。開啟後發布的都是可實際掛單事件，不是 shadow；會員端仍需在「訊號來源設定」親自開啟「超高頻交易」。</p>
      </div>
      <div class="field-group">
        <h3>訊號發布</h3>
        <div class="field-grid">
          <label>雲端 Hub URL<input id="hub_url" placeholder="留空 = 本機自架 Hub；雲端填 https://...fly.dev" /></label>
          <label>Hub 密碼<input id="token" type="password" /></label>
          <label>輪詢秒數<input id="interval" placeholder="1.0" /></label>
          <label class="switch">開啟程式後自動開始<input id="auto_start" type="checkbox" /></label>
        </div>
      </div>
      <div class="field-group">
        <h3>本機自架 Hub</h3>
        <div class="field-grid">
          <label>Hub 監聽 IP<input id="host" placeholder="0.0.0.0" /></label>
          <label>Hub Port<input id="port" placeholder="8765" /></label>
          <label class="switch">Cloudflare Tunnel<input id="cloudflare_tunnel" type="checkbox" /></label>
          <label>cloudflared 路徑<input id="cloudflared_path" placeholder="可留空自動搜尋" /></label>
        </div>
      </div>
"""


PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
/* ---------------------------------------------------------------- tokens */
/* 這組 token 跟官網 (website/assets/css/tokens.css) 是同一套。
   兩邊共用同一組色碼與字型，會員從官網點進來、裝好打開程式，看到的是同一個產品，
   而不是兩個長得不一樣的東西。改色請兩邊一起改。 */
:root {
  color-scheme: dark;

  --paper:    #0A0A0B;   /* 頁面底 */
  --card:     #141416;   /* 卡片 */
  --sunk:     #1E1E21;   /* 卡片內凹 / 軌道底 */
  --raised:   #2A2A2E;   /* 再上一層 */

  --ink:      #EDEDEF;   /* 主文（對 --card 15.7:1） */
  --ink-2:    #A0A0A8;   /* 次要（7.1:1） */
  --muted:    #82828C;   /* 說明（4.8:1，剛好過 WCAG AA） */

  --hair:     #26262A;   /* 細線 */
  --rule:     #3A3A40;   /* hover 邊框 / 輸入框 */

  --gold:     #D4A017;
  --gold-mark:#F0C65C;
  --gold-lit: #F0C65C;
  --gold-hi:  #FFE08A;

  /* 獲利=綠、虧損=紅。
     這裡從原本的「獲利=藍」改過來 —— 官網、使用者提供的設計圖、以及 MT5 原生
     配色都是綠漲紅跌，產品跟行銷頁對不起來比配色偏好更傷。
     深底上用亮一階的 400 色階，不用 500，否則在 #141416 上對比不足。 */
  --win:      #22AB94;
  --loss:     #F7525F;
  --win-wash: rgba(34, 171, 148, 0.14);
  --loss-wash:rgba(247, 82, 95, 0.14);

  /* 連線/存活指示燈。跟 --win 同色系但更沉，才不會跟損益數字搶眼。 */
  --ok:       #089981;
  --ok-wash:  rgba(8, 153, 129, 0.14);

  /* 點綴色。金色是品牌主軸，但整頁只有金 + 綠紅會太素；
     這幾個只用在圖表線條與卡片頂邊，不進文字，避免畫面變彩虹。 */
  --accent-cyan:   #22B8CF;
  --accent-violet: #A855F7;
  --accent-blue:   #3179F5;

  /* 均線顏色。三條要一眼分得開，又不能跟漲跌的紅綠打架。 */
  --ma-5:  #F0C65C;
  --ma-20: #22B8CF;
  --ma-60: #A855F7;

  --shadow:   0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
  /* 卡片的玻璃質感：頂部一層極淡的白，模擬光從上方打下來。
     沒有這層，深色卡片會扁成一塊色塊。 */
  --glass: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,0) 42%);

  /* Manrope 只有拉丁字，中文會自動落到後面的系統字型。
     這正是我們要的：英數用 Manrope 的幾何感，中文用平台原生字。 */
  --display: 'Manrope', -apple-system, BlinkMacSystemFont, "PingFang TC",
             "Microsoft JhengHei", sans-serif;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang TC",
          "Microsoft JhengHei", "Noto Sans TC", sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, "SF Mono",
          Menlo, Consolas, monospace;
}

/* 深色是預設。想要亮底的人可以用右上角切換，選擇記在 localStorage。 */
:root[data-theme="light"] {
  color-scheme: light;
  --paper:#F7F7F5; --card:#FFFFFF; --sunk:#F0F0EE; --raised:#E6E6E3;
  --ink:#16161A; --ink-2:#54545C; --muted:#6E6E76;
  --hair:#E5E5E2; --rule:#CFCFCA;
  --gold:#A87C10; --gold-mark:#C08F14; --gold-lit:#D4A017; --gold-hi:#F0C65C;
  --win:#089981; --loss:#D92C3C;
  --win-wash:rgba(8,153,129,.10); --loss-wash:rgba(217,44,60,.10);
  --ok:#068043; --ok-wash:rgba(6,128,67,.10);
  --ma-5:#B07D0A; --ma-20:#0E8FA8; --ma-60:#7C3AED;
  --accent-cyan:#0E8FA8; --accent-violet:#7C3AED; --accent-blue:#1E53E5;
  --shadow: 0 1px 2px rgba(22,22,26,.05), 0 8px 24px -16px rgba(22,22,26,.28);
  --glass: linear-gradient(180deg, rgba(0,0,0,.02), rgba(0,0,0,0) 42%);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3, h4 {
  margin: 0; font-family: var(--display);
  font-weight: 700; letter-spacing: -0.02em; line-height: 1.15;
}
/* 中文是方塊字，負字距一大就糊。西文字母之間本來就有視覺空隙才收得動。 */
:lang(zh-Hant) h1, :lang(zh-Hant) h2, :lang(zh-Hant) h3 { letter-spacing: -0.005em; }
:focus-visible { outline: 2px solid var(--gold-mark); outline-offset: 2px; border-radius: 3px; }

/* ------------------------------------------------------------------ rail */
.rail {
  position: sticky; top: 0; z-index: 40;
  display: flex; align-items: center; gap: 20px; flex-wrap: wrap;
  padding: 12px 24px;
  background: color-mix(in srgb, var(--card) 88%, transparent);
  backdrop-filter: saturate(1.4) blur(12px);
  border-bottom: 1px solid var(--hair);
}
.rail-id { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
.rail-id h1 { font-size: 17px; font-weight: 800; }
.rail-sub { margin: 0; font-size: 12px; color: var(--muted); letter-spacing: .06em; }

/* 金條標記：這支程式的識別，也是階梯的縮影 */
.bullion {
  width: 34px; height: 22px; flex: none; border-radius: 3px 3px 2px 2px;
  background: linear-gradient(158deg, var(--gold-hi) 0%, var(--gold-lit) 46%, var(--gold) 100%);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.55), inset 0 -2px 4px rgba(0,0,0,.16);
  clip-path: polygon(12% 0, 88% 0, 100% 100%, 0 100%);
}

.rail-state { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px;
  background: var(--sunk); border: 1px solid var(--hair);
  font-size: 12px; color: var(--ink-2); white-space: nowrap;
}
.chip b { font-weight: 600; color: var(--ink); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.chip.is-live .dot { background: var(--ok); animation: pulse 2s ease-out infinite; }
.chip.is-warn .dot { background: var(--gold-mark); }
.chip.is-off  .dot { background: var(--muted); }
@keyframes pulse {
  0%   { box-shadow: 0 0 0 0 var(--ok-wash); }
  70%  { box-shadow: 0 0 0 7px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}

.rail-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.btn {
  font: inherit; font-family: var(--display);
  font-size: 13px; font-weight: 700; letter-spacing: -.01em;
  padding: 8px 16px; border-radius: 999px;
  border: 1px solid var(--rule); background: transparent; color: var(--ink);
  cursor: pointer; transition: background .15s, border-color .15s, transform .08s;
}
.btn:hover { background: var(--sunk); }
.btn:active { transform: translateY(1px); }
.btn[disabled] { opacity: .45; cursor: not-allowed; }
.btn-go {
  border-color: transparent; color: #2a1d05; font-weight: 600;
  background: linear-gradient(158deg, var(--gold-hi), var(--gold-lit) 60%, var(--gold-mark));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.5);
}
.btn-go:hover { filter: brightness(1.05); background: linear-gradient(158deg, var(--gold-hi), var(--gold-lit) 60%, var(--gold-mark)); }
.btn-quiet { border-color: transparent; background: transparent; color: var(--muted); }
.btn-quiet:hover { background: var(--sunk); color: var(--ink); }


/* ------------------------------------------------------------------ side */
/* 左側欄。訊號中心沒有等級制度，所以整條是 client-only。 */
/* 側欄不存在時整個 shell 要收成一欄。
   display:none 的元素會被完全移出 grid，剩下的 .shell-main 就會遞補到
   第一欄（248px），整頁內容被壓成一條直的 —— 訊號中心沒有側欄，
   會員尚未登入時也沒有，兩種情況都會中。 */
.shell { display: grid; grid-template-columns: minmax(0, 1fr); gap: 26px; align-items: start; }
body[data-role="client"]:not(.auth-locked) .shell {
  grid-template-columns: 248px minmax(0, 1fr);
}
.shell-main { min-width: 0; }   /* 沒有這行，裡面的寬表格會把整個 grid 撐爆 */

/* 視窗窄的時候側欄收起來 —— 硬擠成兩欄會讓表格沒地方站 */
@media (max-width: 1180px) {
  .shell { grid-template-columns: 1fr; }
  .side { display: none; }
}

/* 側欄要跟著頁面一起捲(黏在視窗上)。兩個坑都得處理:
   1. .shell 是 align-items:start 的 grid,格線項目的高度就是內容高度 —— sticky
      的可黏範圍等於那個盒子,所以捲過側欄自己的高度之後它就跟著跑掉了。
      解法是讓 .side 撐滿整列(align-self:stretch),真正 sticky 的是裡面那層。
   2. 側欄長出市場總覽 + 17 項方案功能之後比一個螢幕還高,黏住也只看得到上半段。
      給它自己的高度上限與捲軸,內容再長都黏得住。 */
.side { align-self: stretch; }
.side[hidden] { display: none; }
.side-sticky {
  position: sticky; top: 76px;
  display: flex; flex-direction: column; gap: 14px;
  max-height: calc(100vh - 92px);
  overflow-y: auto;
  overscroll-behavior: contain;
  /* 捲軸壓在卡片上很醜,留一點右邊距;下面再把捲軸做細 */
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: var(--rule) transparent;
}
.side-sticky::-webkit-scrollbar { width: 6px; }
.side-sticky::-webkit-scrollbar-track { background: transparent; }
.side-sticky::-webkit-scrollbar-thumb { background: var(--rule); border-radius: 999px; }
.side-sticky::-webkit-scrollbar-thumb:hover { background: var(--muted); }

.side-head {
  margin: 0 0 10px; font-size: 11px; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase; color: var(--muted);
}

/* 會員卡 */
.side-card {
  padding: 16px 18px 18px;
  background: var(--card); border: 1px solid var(--hair); border-radius: 16px;
  box-shadow: var(--shadow);
  position: relative; overflow: hidden;
}
/* 頂邊一道金線，讓這張卡在側欄裡是主角 */
.side-card::before {
  content: ''; position: absolute; inset: 0 0 auto; height: 2px;
  background: linear-gradient(90deg, transparent, var(--gold-lit), transparent);
}
.side-tier { display: flex; align-items: baseline; gap: 7px; margin: 0; }
.side-tier b {
  font-family: var(--display); font-size: 21px; font-weight: 800;
  letter-spacing: -.02em; color: var(--gold-mark);
}
.side-tier span { font-size: 13px; color: var(--ink-2); }
.side-exp { margin: 10px 0 0; font-size: 12px; color: var(--muted); }
.side-exp b { color: var(--ink-2); font-weight: 600; font-family: var(--mono); }

/* 到期進度條的四段警示色。依「還剩幾天」升級：
     >14 天 金色 · 8~14 天 琥珀 · 4~7 天 橘 · 1~3 天 紅(會脈動) · 到期 實心紅
   用剩餘天數而不是百分比當門檻：會員在意的是「還能用幾天」。用百分比的話，
   一年方案剩 30 天會被算成 8% 而爆紅，7 天試用第一天卻是 100% 全綠 —— 兩邊都錯。
   條長本來就已經表達比例了。
   刻意不用綠色當「充足」：這一頁的綠色專門表示獲利，拿去講時間會讀成兩件事。 */
:root {
  --lv-mid:  #C98A1E;   /* 琥珀 */
  --lv-warn: #C4551B;   /* 橘 */
  --lv-crit: #C21F35;   /* 紅 */
}
[data-theme="dark"] { --lv-mid: #E8B04B; --lv-warn: #F0813C; --lv-crit: #E23B4E; }

.side-meter {
  height: 6px; border-radius: 999px; background: var(--sunk);
  margin-top: 12px; overflow: hidden;
}
.side-meter i {
  display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--gold), var(--gold-lit));
  transition: width .6s cubic-bezier(.2,.7,.3,1), background-color .4s;
}
.side-meter.lv-mid  i { background: linear-gradient(90deg, var(--lv-mid),  var(--gold-hi)); }
.side-meter.lv-warn i { background: linear-gradient(90deg, var(--lv-warn), var(--lv-mid)); }
.side-meter.lv-crit i { background: linear-gradient(90deg, var(--lv-crit), var(--lv-warn)); }
.side-meter.lv-out  i { background: var(--lv-crit); }
/* 額度用完時條長是 0，空軌道跟「還沒載入」長得一樣。把軌道本身染紅，
   一眼就分得出是「用完了」而不是「沒資料」。 */
.side-meter.lv-out { background: color-mix(in srgb, var(--lv-crit) 26%, var(--sunk)); }
/* 只有「危急」才動。整天都在閃的介面，會員三天後就自動忽略它了。 */
.side-meter.lv-crit { animation: meterPulse 1.9s ease-in-out infinite; }
@keyframes meterPulse {
  0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--lv-crit) 55%, transparent); }
  50%      { box-shadow: 0 0 0 5px color-mix(in srgb, var(--lv-crit) 0%, transparent); }
}
@media (prefers-reduced-motion: reduce) { .side-meter.lv-crit { animation: none; } }

.side-left { margin: 8px 0 0; font-size: 12px; color: var(--muted); }
/* 數字本身也跟著變色 —— 只有一條 6px 的細線在變色，滑過去不一定看得到 */
.side-exp b.lv-mid,  .side-left.lv-mid  { color: var(--lv-mid); }
.side-exp b.lv-warn, .side-left.lv-warn { color: var(--lv-warn); }
.side-exp b.lv-crit, .side-left.lv-crit,
.side-exp b.lv-out,  .side-left.lv-out  { color: var(--lv-crit); font-weight: 700; }

/* 進階版以上:非開盤/未跟單自動暫停計時的狀態徽章。
   計時中=綠、暫停=灰,一眼看出方案時間現在到底有沒有在扣。 */
.side-pause {
  margin: 8px 0 0; font-size: 11.5px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px; border-radius: 999px;
}
.side-pause.is-live { color: var(--ok); background: color-mix(in srgb, var(--ok) 14%, transparent); }
.side-pause.is-paused { color: var(--muted); background: var(--sunk); }

/* 區塊導覽 */
.side-nav {
  padding: 16px 14px 14px;
  background: var(--card); border: 1px solid var(--hair); border-radius: 16px;
}
.side-nav a {
  display: block; padding: 8px 10px; border-radius: 8px;
  font-size: 13.5px; color: var(--ink-2); text-decoration: none;
  transition: background-color .15s, color .15s;
}
.side-nav a:hover { background: var(--sunk); color: var(--ink); }

/* 方案功能。每一項都對應後端 entitlements 的一個欄位，沒有裝飾用的假項目。 */
.side-feats {
  padding: 16px 14px 14px;
  background: var(--card); border: 1px solid var(--hair); border-radius: 16px;
}
.side-feats ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.side-feats li {
  display: flex; align-items: center; gap: 9px;
  padding: 7px 10px; border-radius: 8px;
  font-size: 13px; color: var(--ink-2);
}
.side-feats li.is-off { color: var(--muted); }
.side-feats li .val { margin-left: auto; font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
/* 鎖住的功能把「需XX版」標成金色，當作升級誘因 */
.side-feats li .val--lock { color: var(--gold); font-family: inherit; font-weight: 600; letter-spacing: .02em; }

.dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--muted); }
.dot--on { background: var(--win); box-shadow: 0 0 0 3px var(--win-wash); }

/* 鎖頭用 CSS 畫，不用 emoji —— emoji 在 Windows 與 macOS 上長得不一樣，
   而且會跟著系統字型走，大小控制不了。 */
.lockmark {
  position: relative; flex: none; width: 9px; height: 8px;
  border-radius: 1.5px; background: currentColor; opacity: .55; margin-top: 3px;
}
.lockmark::before {
  content: ''; position: absolute; left: 50%; top: -5px;
  width: 6px; height: 6px; transform: translateX(-50%);
  border: 1.5px solid currentColor; border-bottom: 0;
  border-radius: 3px 3px 0 0;
}

.side-legend {
  display: flex; gap: 14px; flex-wrap: wrap;
  margin: 12px 0 0; padding-top: 11px;
  border-top: 1px solid var(--hair);
  font-size: 11.5px; color: var(--muted);
}
.side-legend .lg { display: inline-flex; align-items: center; gap: 6px; }


/* ------------------------------------------------------------- dashboard */
/* 儀表板網格。原本整頁是單欄往下堆，資訊密度低、看起來不像交易介面。
   改成上方一列統計卡、中間分欄、下方表格。 */

/* 頂部統計卡列。sparkline 只畫得出真實序列的才畫 ——
   畫不出來的（例如來源數）就留白，不硬湊一條假曲線。 */
.dash-stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
@media (max-width: 1400px) { .dash-stats { grid-template-columns: repeat(3, minmax(0,1fr)); } }
@media (max-width: 820px)  { .dash-stats { grid-template-columns: repeat(2, minmax(0,1fr)); } }

.dstat {
  position: relative;
  padding: 15px 17px 0;
  background: var(--card); border: 1px solid var(--hair); border-radius: 14px;
  overflow: hidden;
  transition: border-color .25s;
}
.dstat:hover { border-color: var(--rule); }
.dstat dt { font-size: 11.5px; color: var(--muted); margin-bottom: 6px; }
.dstat dd {
  margin: 0; font-family: var(--display);
  font-size: 26px; font-weight: 800; letter-spacing: -.035em; line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.dstat .sub { display: block; margin-top: 5px; font-size: 11.5px; color: var(--muted); min-height: 16px; }
.dstat:not(:has(.spark)) { padding-bottom: 15px; }
.dstat .spark { display: block; width: calc(100% + 34px); height: 42px; margin: 10px -17px 0; }

/* 主網格 */
.dash-grid { display: grid; gap: 14px; margin-bottom: 14px; }
.dash-grid--2 { grid-template-columns: minmax(0, 1.85fr) minmax(0, 1fr); }
.dash-grid--3 { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.35fr); }
@media (max-width: 1180px) { .dash-grid--2, .dash-grid--3 { grid-template-columns: 1fr; } }

/* 面板：標題進到卡片裡，整頁才會是一個個明確的方塊，
   而不是浮在外面的小標題配一張沒有頭的卡。 */
.panel {
  display: flex; flex-direction: column;
  background: var(--card); border: 1px solid var(--hair); border-radius: 16px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.panel-head {
  display: flex; align-items: baseline; gap: 10px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--hair);
}
/* 標題不參與壓縮 —— 右邊的說明文字跟時區標註會把它擠成兩行 */
.panel-head h2 { font-size: 14.5px; font-weight: 700; white-space: nowrap; flex: 0 0 auto; }
.panel-head .spacer, .panel-head .tz-note { min-width: 0; }
.panel-head p { margin: 0; font-size: 12px; color: var(--muted); }
.panel-head .spacer { margin-left: auto; text-align: right; }
.panel-body { padding: 16px 18px; flex: 1; min-height: 0; }
.panel-body--flush { padding: 0; }
.panel-body--tight { padding: 12px 14px; }

/* hero 卡在三欄網格裡要跟其他 panel 對齊 */
.dash-grid--3 > .card.hero { border-radius: 16px; grid-template-columns: 1fr; }

/* 圓環：勝率的視覺化。中央放總筆數，右邊是圖例。 */
.donut-wrap { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.donut { position: relative; flex: none; width: 146px; height: 146px; }
.donut svg { transform: rotate(-90deg); display: block; }
.donut-mid {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
}
.donut-mid b {
  font-family: var(--display); font-size: 27px; font-weight: 800;
  letter-spacing: -.035em; font-variant-numeric: tabular-nums;
}
.donut-mid span { font-size: 11px; color: var(--muted); }
.donut-legend { display: grid; gap: 14px; min-width: 0; margin: 0; }
.donut-legend > div { display: flex; align-items: flex-start; gap: 9px; }
.donut-legend i { width: 8px; height: 8px; border-radius: 2px; margin-top: 5px; flex: none; }
.donut-legend dt { font-size: 12px; color: var(--muted); }
.donut-legend dd {
  margin: 2px 0 0; font-family: var(--mono);
  font-size: 15px; font-variant-numeric: tabular-nums; color: var(--ink);
}
.donut-legend dd em { font-style: normal; font-size: 12px; color: var(--muted); margin-left: 5px; }

/* 底部狀態列。黏在視窗底部，讓「系統到底有沒有在跑」隨時看得到 ——
   原本要捲回頁面最上面才看得到那幾顆狀態燈。 */
.statusbar {
  position: sticky; bottom: 0; z-index: 30;
  display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
  margin: 22px -28px -72px;
  padding: 11px 28px;
  background: color-mix(in srgb, var(--card) 92%, transparent);
  backdrop-filter: saturate(1.4) blur(12px);
  border-top: 1px solid var(--hair);
  font-size: 12px; color: var(--muted);
}
.statusbar .sb { display: inline-flex; align-items: center; gap: 7px; white-space: nowrap; }
.statusbar .sb b { color: var(--ink-2); font-weight: 600; }
.statusbar .sb .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; color: var(--ink-2); }
.statusbar .spacer { margin-left: auto; }
.sb-meter { width: 84px; height: 4px; border-radius: 999px; background: var(--sunk); overflow: hidden; }
.sb-meter i { display: block; height: 100%; border-radius: 999px; background: var(--win); transition: width .5s; }
.sb-meter.is-warn i { background: var(--gold-lit); }
.sb-meter.is-over i { background: var(--loss); }
@media (max-width: 900px) { .statusbar { display: none; } }


/* 面板裡的長表格要自己捲，不然一個有幾十筆紀錄的表格會把整列撐到近千像素高，
   同列的其他面板被拉出一大片空白。 */
/* 升級卡。金色描邊是全站唯一一處用滿版金的地方，用來標示這是行動點。 */
.side-up {
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--gold-lit) 12%, transparent), transparent 62%),
    var(--card);
  border-color: color-mix(in srgb, var(--gold-lit) 34%, var(--hair));
}
.side-up-h { margin: 0 0 6px; font-size: 13px; font-weight: 700; color: var(--gold-lit); }
.side-up-b { margin: 0 0 11px; font-size: 11.5px; line-height: 1.55; color: var(--ink-2); }
.side-up-ig {
  margin: 0; display: flex; align-items: center; gap: 7px;
  font-size: 11px; color: var(--muted);
}
.side-up-ig b { font-family: var(--mono); font-size: 12px; color: var(--ink); }
.side-up-site {
  display: inline-block; margin-top: 8px; font-size: 11.5px; font-weight: 600;
  color: var(--gold); text-decoration: none;
}
.side-up-site:hover { text-decoration: underline; }

/* 策略卡片的運行狀態與管理鍵 */
.sc-run {
  margin-left: auto; display: inline-flex; align-items: center; gap: 5px;
  font-size: 11px; color: var(--muted); white-space: nowrap;
}
.sc-run .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); }
.sc-run.on { color: var(--win); }
.sc-run.on .dot {
  background: var(--win);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--win) 20%, transparent);
}
.sc-foot {
  display: flex; align-items: center; gap: 10px;
  margin-top: 12px; padding-top: 11px; border-top: 1px solid var(--hair);
}
.sc-meta { font-size: 11px; color: var(--muted); }
.sc-manage { margin-left: auto; padding: 5px 13px; font-size: 12px; }
/* 卡片本體從 button 換成 div，游標與鍵盤焦點要自己補回來 */
.source-card { cursor: pointer; }
.source-card:focus-visible { outline: 2px solid var(--gold-lit); outline-offset: 2px; }

/* 頂部主導覽。每一項都對到頁面上真的存在的區塊——沒有點了不會動的裝飾項。 */
/* flex: 1 1 auto + min-width: 0 讓導覽在空間不夠時自己壓縮橫捲，
   而不是把右邊那排按鈕擠到第二行去。 */
.rail-nav {
  display: flex; align-items: center; gap: 2px; margin-left: 8px;
  flex: 1 1 auto; min-width: 0;
  overflow-x: auto; scrollbar-width: none;
}
.rail-nav::-webkit-scrollbar { display: none; }
/* 桌面寬度不讓頂欄換行 —— 換行會多吃一整排的高度。導覽是唯一可壓縮的
   區塊（其他都是按鈕和狀態，壓了會看不懂），塞不下就讓它橫捲。
   窄螢幕維持 wrap，因為那時導覽本來就藏起來了。 */
@media (min-width: 1181px) {
  .rail { flex-wrap: nowrap; }
  .rail-state, .rail-actions { flex: 0 0 auto; }
}
/* 導覽藏起來時，改由 rail-id 把右邊那組推到底 */
@media (max-width: 1180px) {
  .rail-id { margin-right: auto; }
}
.rail-nav a {
  position: relative; text-decoration: none; white-space: nowrap;
  padding: 7px 11px; border-radius: 8px; flex: 0 0 auto;
  font-size: 12.5px; color: var(--ink-2);
  transition: color .18s, background .18s;
}
.rail-nav a:hover { color: var(--ink); background: var(--sunk); }
/* 官網是外部連結,不是頁內錨點 —— 用金色與一道分隔線跟其他項目區隔,
   免得使用者點下去以為只是捲動,結果整個跳出面板。 */
.rail-nav a.rail-out {
  color: var(--gold); margin-left: 4px; padding-left: 15px;
}
.rail-nav a.rail-out::before {
  content: ""; position: absolute; left: 0; top: 8px; bottom: 8px;
  width: 1px; background: var(--hair);
}
.rail-nav a.rail-out:hover { color: var(--gold-hi); }
.rail-nav a.is-on { color: var(--ink); background: var(--sunk); }
/* 目前所在的區塊底下畫一道金線，跟分頁列的作法一致 */
.rail-nav a.is-on::after {
  content: ""; position: absolute; left: 13px; right: 13px; bottom: 3px;
  height: 2px; border-radius: 2px; background: var(--gold-lit);
  box-shadow: 0 0 10px color-mix(in srgb, var(--gold-lit) 55%, transparent);
}
@media (max-width: 1180px) { .rail-nav { display: none; } }

/* 市場總覽（側欄）。價格用等寬字，跳動的時候數字不會左右晃。 */
.wl { list-style: none; margin: 0; padding: 0; }
.wl li {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 0; border-top: 1px solid var(--hair);
}
.wl li:first-child { border-top: 0; }
.wl-sym { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.wl-sym b { font-size: 12.5px; font-weight: 600; }
.wl-sym span { font-size: 10.5px; color: var(--muted); }
.wl-px { margin-left: auto; display: flex; flex-direction: column;
         align-items: flex-end; gap: 1px; }
.wl-px b { font-family: var(--mono); font-size: 12.5px; font-weight: 600; }
.wl-px span { font-family: var(--mono); font-size: 10.5px; color: var(--muted); }
.wl-px span.up { color: var(--win); }
.wl-px span.down { color: var(--loss); }
.wl-foot { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--hair);
           font-size: 10.5px; color: var(--muted); display: flex; }
.wl-foot span { margin-left: auto; font-family: var(--mono); }

/* ── K 線圖 ─────────────────────────────────────────────────────
   自己用 SVG 畫的。不外掛圖表庫的理由：這個檔案是刻意單檔內嵌的
   （PyInstaller 的 .spec 才不用跟著改），而且自己畫才能跟其他圖用
   同一套色票，不會有一塊長得像別人的產品。 */
.dash-grid--hero { grid-template-columns: minmax(0, 2.15fr) minmax(0, 1fr); }
.dash-grid--hero .source-grid { max-height: 396px; overflow: auto; }

.panel--chart { display: flex; flex-direction: column; overflow: hidden; }

.chart-bar {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 12px 16px; border-bottom: 1px solid var(--hair);
  background: linear-gradient(180deg, rgba(255,255,255,.028), transparent);
}
.chart-sym { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.chart-sym b { font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
.chart-tf-cur { font-size: 12px; color: var(--ink-2); }
.chart-feed {
  font-size: 10.5px; letter-spacing: .04em; color: var(--muted);
  padding: 2px 7px; border: 1px solid var(--hair); border-radius: 999px;
}
.chart-tfs { margin-left: auto; }
.chart-tfs .pill { padding: 4px 10px; font-size: 12px; }
.chart-toggles { display: flex; gap: 5px; flex-wrap: wrap; }
.ind {
  font: inherit; font-size: 11px; letter-spacing: .02em;
  padding: 4px 9px; border-radius: 7px; cursor: pointer;
  color: var(--muted); background: transparent;
  border: 1px solid var(--hair); transition: color .18s, border-color .18s;
}
.ind:hover { color: var(--ink-2); border-color: var(--rule); }
.ind.is-on { color: var(--ink); border-color: var(--rule); background: var(--sunk); }
/* 開著的指標左邊點一個色點，顏色對應線的顏色 */
.ind[data-ma].is-on::before {
  content: ""; display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; margin-right: 6px; vertical-align: 1px;
}
.ind[data-ma="5"].is-on::before  { background: var(--ma-5); }
.ind[data-ma="20"].is-on::before { background: var(--ma-20); }
.ind[data-ma="60"].is-on::before { background: var(--ma-60); }

/* OHLC 讀數列。滑鼠移到哪根就顯示哪根，沒移就顯示最新。 */
.chart-read {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 9px 16px; font-family: var(--mono); font-size: 11.5px;
  color: var(--ink-2); border-bottom: 1px solid var(--hair);
  min-height: 34px;
}
.chart-read .ohlc { display: flex; gap: 10px; }
.chart-read .ohlc span { color: var(--muted); }
.chart-read .ohlc b { font-weight: 600; color: var(--ink); margin-left: 3px; }
.chart-read .ohlc b.up { color: var(--win); }
.chart-read .ohlc b.down { color: var(--loss); }
.chart-read .mas { display: flex; gap: 12px; margin-left: auto; }
.chart-read .mas i { font-style: normal; }

/* K 線的 SVG 元素。用 viewBox 拉伸，所以線寬要用 vector-effect 固定住，
   不然橫向拉開後蠟燭的邊會變成粗細不一。 */
#klineChart .k-grid { stroke: var(--hair); stroke-width: 1; vector-effect: non-scaling-stroke; }
#klineChart .k-axis { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
#klineChart .k-axis--x { text-anchor: middle; }
#klineChart .k-wick { stroke-width: 1; vector-effect: non-scaling-stroke; }
#klineChart .k-wick.up   { stroke: var(--win); }
#klineChart .k-wick.down { stroke: var(--loss); }
#klineChart .k-body.up   { fill: var(--win); }
#klineChart .k-body.down { fill: var(--loss); }
#klineChart .k-vol { opacity: .34; }
#klineChart .k-vol.up   { fill: var(--win); }
#klineChart .k-vol.down { fill: var(--loss); }
#klineChart .k-ma { fill: none; stroke-width: 1.4; vector-effect: non-scaling-stroke;
                    stroke-linejoin: round; stroke-linecap: round; }
#klineChart .k-lastline { stroke-width: 1; stroke-dasharray: 3 3; opacity: .7;
                          vector-effect: non-scaling-stroke; }
#klineChart .k-lastline.up   { stroke: var(--win); }
#klineChart .k-lastline.down { stroke: var(--loss); }
#klineChart .k-lastbg.up   { fill: var(--win); }
#klineChart .k-lastbg.down { fill: var(--loss); }
#klineChart .k-lasttxt { fill: #fff; font-size: 10.5px; font-family: var(--mono); font-weight: 600; }
#klineChart .k-cross { stroke: var(--rule); stroke-width: 1; stroke-dasharray: 3 3;
                       vector-effect: non-scaling-stroke; pointer-events: none; }

.kline-wrap { position: relative; flex: 0 0 auto; height: 372px; }
.kline-wrap svg { display: block; width: 100%; height: 100%; }
.kline-empty {
  position: absolute; inset: 0; display: none;
  align-items: center; justify-content: center;
  font-size: 13px; color: var(--muted);
}
.kline-wrap.is-empty .kline-empty { display: flex; }
.kline-wrap.is-empty svg { opacity: 0; }

/* 訊號中心的資訊列：左邊欄位名、右邊值，中間拉開 */
.mock { display: flex; flex-direction: column; gap: 1px;
        background: var(--hair); border: 1px solid var(--hair);
        border-radius: 12px; overflow: hidden; }
.mock-row { display: flex; align-items: center; gap: 14px;
            padding: 13px 16px; background: var(--sunk); min-height: 46px; }
.mock-row .k { color: var(--ink-2); font-size: 13px; flex: 0 0 auto; }
.mock-row .v { margin-left: auto; text-align: right; color: var(--ink);
               font-family: var(--mono); font-size: 13px; word-break: break-all; }
.mock-row .dot { width: 7px; height: 7px; border-radius: 50%;
                 background: var(--muted); flex: 0 0 auto; }
.mock-row .v.up { color: var(--win); }
.mock-row .v.down { color: var(--loss); }
.mock-row .dot--on { background: var(--win);
                     box-shadow: 0 0 0 3px color-mix(in srgb, var(--win) 22%, transparent); }
.dash-grid--3 .table-scroll { max-height: 360px; overflow: auto; }
.dash-grid--2 .table-scroll { max-height: 300px; overflow: auto; }
.dash-grid .source-grid { max-height: 344px; overflow: auto; }
.dash-grid--2 #pending, .dash-grid--2 #positions { max-height: 300px; overflow: auto; }

/* 面板內的捲軸細一點，不要在卡片裡切出一條粗灰帶 */
.panel-body ::-webkit-scrollbar, .panel-body::-webkit-scrollbar { width: 8px; height: 8px; }
.panel-body ::-webkit-scrollbar-thumb, .panel-body::-webkit-scrollbar-thumb {
  background: var(--rule); border-radius: 999px; border: 2px solid var(--card);
}
.panel-body ::-webkit-scrollbar-track, .panel-body::-webkit-scrollbar-track { background: transparent; }

/* 馬丁階梯在三欄裡不需要撐滿，內容多高就多高 */
.dash-grid--3 > .card.hero { align-self: stretch; }


/* ------------------------------------------------------------------ 質感 */
/* 深色介面很容易變成一片扁平的黑。這一段補的是「光」——
   卡片頂部的受光面、關鍵數字的光暈、按鈕的金屬漸層。
   點綴色只進圖形（線條、頂邊、圓環），不進文字，畫面才不會變彩虹。 */

.card, .panel, .dstat, .side-card, .side-nav, .side-feats {
  background-image: var(--glass);
  background-repeat: no-repeat;
}

/* 統計卡：頂邊一道該指標自己的顏色。0.5px 太細會被螢幕吃掉，用 2px 加低透明度。 */
.dstat::before {
  content: ''; position: absolute; inset: 0 0 auto; height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent, var(--gold-lit)) 42%, transparent);
  opacity: .55;
}
.dstat:hover::before { opacity: 1; }
.dstat--cyan   { --accent: var(--accent-cyan); }
.dstat--win    { --accent: var(--win); }
.dstat--violet { --accent: var(--accent-violet); }
.dstat--gold   { --accent: var(--gold-lit); }
.dstat--loss   { --accent: var(--loss); }

/* 關鍵數字的光暈。只給有語意顏色的數字（賺/賠/勝率），中性數字不加，
   否則整排都在發光就等於都沒發光。 */
.dstat dd.up    { text-shadow: 0 0 26px color-mix(in srgb, var(--win) 42%, transparent); }
.dstat dd.down  { text-shadow: 0 0 26px color-mix(in srgb, var(--loss) 38%, transparent); }
.dstat dd.gold  { color: var(--gold-mark); text-shadow: 0 0 26px color-mix(in srgb, var(--gold-lit) 40%, transparent); }
.figure-value.up   { text-shadow: 0 0 40px color-mix(in srgb, var(--win) 34%, transparent); }
.figure-value.down { text-shadow: 0 0 40px color-mix(in srgb, var(--loss) 30%, transparent); }

/* 面板標題列：底部一條極淡的金線，讓標題跟內容之間有層次而不只是一條灰線 */
.panel-head {
  position: relative;
  background: linear-gradient(180deg, rgba(255,255,255,.03), transparent);
}
.panel-head::after {
  content: ''; position: absolute; inset: auto 0 -1px; height: 1px;
  background: linear-gradient(90deg, var(--gold-line, rgba(212,160,23,.22)), transparent 60%);
}

/* 主要按鈕：真的金屬漸層，不是一塊平的金色 */
.btn-go {
  background: linear-gradient(158deg, var(--gold-hi) 0%, var(--gold-lit) 46%, var(--gold-mark) 100%);
  border-color: transparent;
  color: #2a1d05;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.55), 0 6px 20px -10px rgba(212,160,23,.7);
}
.btn-go:hover { filter: brightness(1.06); }

/* 側欄的等級卡：加一層徑向金光，讓它在側欄裡是主角 */
.side-card {
  background-image:
    radial-gradient(120% 90% at 50% -10%, rgba(212,160,23,.16), transparent 62%),
    var(--glass);
}

/* 各來源績效卡：左側一道彩色邊，兩個來源用不同顏色，一眼分得出來 */
.source-card { position: relative; overflow: hidden; }
.source-card::before {
  content: ''; position: absolute; inset: 0 auto 0 0; width: 3px;
  background: var(--src-accent, var(--accent-cyan));
  opacity: .8;
}
.source-grid > .source-card:nth-child(2n)   { --src-accent: var(--accent-violet); }
.source-grid > .source-card:nth-child(3n+1) { --src-accent: var(--accent-cyan); }

/* 圓環發光 */
.donut svg circle { filter: drop-shadow(0 0 10px color-mix(in srgb, currentColor 40%, transparent)); }
.donut-mid b { text-shadow: 0 0 30px rgba(255,255,255,.18); }

/* 分頁選中時的金線加一點光 */
.view-tab.is-on::after { box-shadow: 0 0 12px color-mix(in srgb, var(--gold-lit) 60%, transparent); }

/* 狀態列的指示燈：加呼吸感，讓「還在跑」是看得出來的 */
.statusbar .dot { box-shadow: 0 0 0 0 currentColor; }
.statusbar .sb .dot[style*="--ok"] { animation: sb-pulse 2.4s ease-out infinite; }
@keyframes sb-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(8,153,129,.45); }
  70%  { box-shadow: 0 0 0 6px rgba(8,153,129,0); }
  100% { box-shadow: 0 0 0 0 rgba(8,153,129,0); }
}

@media (prefers-reduced-motion: reduce) {
  .statusbar .sb .dot { animation: none; }
}

/* ------------------------------------------------------------------ main */
/* 1240px 對桌面程式來說太窄，右邊會空一大片。放寬到 1560，
   表格與圖表才有呼吸空間。 */
main { max-width: 1560px; margin: 0 auto; padding: 22px 28px 72px; }
.card {
  background: var(--card); border: 1px solid var(--hair);
  border-radius: 16px; padding: 20px 22px; box-shadow: var(--shadow);
}
.eyebrow {
  margin: 0 0 6px; font-size: 11px; font-weight: 600;
  letter-spacing: .13em; text-transform: uppercase; color: var(--muted);
}
.section-head {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin: 30px 0 12px;
}
.section-head h2 { font-size: 20px; font-weight: 800; }
.section-head p { margin: 0; font-size: 12.5px; color: var(--muted); }
.section-head .spacer { margin-left: auto; }

/* --------------------------------------------------------------- hero band */
.hero {
  display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 28px; align-items: stretch; padding: 22px 24px;
}
@media (max-width: 900px) { .hero { grid-template-columns: 1fr; gap: 22px; } }

/* 馬丁階梯 —— 這頁的招牌。高度就是該層手數，亮起來的是已經走過的關卡。 */
.ladder-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 14px; }
.ladder-head h2 { font-size: 14px; }
.ladder-level { font-size: 12px; color: var(--muted); }
.rungs {
  display: flex; align-items: flex-end; gap: 10px;
  height: 158px; margin: 22px 0 12px;   /* 上緣留給「現在」標記 */
}
/* display:flex 會蓋掉 hidden 屬性預設的 display:none，要明寫 */
.rungs[hidden] { display: none; }
.rung {
  flex: 1 1 0; min-width: 0;
  display: flex; flex-direction: column; justify-content: flex-end; align-items: center;
  height: 100%;
}
/* 條的高度就是該關手數，1 → 16 手的落差本身就是重點，所以線性不壓縮 */
.rung-bar {
  position: relative; width: 100%; max-width: 46px;
  border-radius: 4px 4px 0 0;
  background: var(--sunk);
  border: 1px solid var(--hair); border-bottom: none;
  transition: height .5s cubic-bezier(.2,.7,.3,1);
}
.rung.is-lit .rung-bar {
  background: linear-gradient(158deg, var(--gold-hi) 0%, var(--gold-lit) 42%, var(--gold) 100%);
  border-color: transparent;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,.62), inset 0 -4px 8px rgba(0,0,0,.16);
}
.rung.is-current .rung-bar {
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,.72), inset 0 -4px 8px rgba(0,0,0,.16),
              0 0 0 2px var(--card), 0 0 0 4px var(--gold-mark);
}
.rung.is-current .rung-bar::after {
  content: "現在"; position: absolute; left: 50%; top: -20px; transform: translateX(-50%);
  font-size: 10.5px; font-weight: 700; letter-spacing: .1em; color: var(--gold); white-space: nowrap;
}
.rung.is-current .rung-bar::before {
  content: ""; position: absolute; inset: 0; border-radius: inherit;
  background: linear-gradient(120deg, transparent 35%, rgba(255,255,255,.75) 50%, transparent 65%);
  background-size: 260% 100%;
  animation: sheen 3.2s ease-in-out infinite;
}
@keyframes sheen {
  0%, 62% { background-position: 130% 0; }
  100%    { background-position: -30% 0; }
}
.rung-lot {
  font-size: 12px; font-weight: 600; margin-top: 6px;
  font-variant-numeric: tabular-nums; color: var(--ink-2);
}
.rung.is-lit .rung-lot { color: var(--ink); }
.rung-tag { font-size: 10.5px; color: var(--muted); letter-spacing: .04em; }
.ladder-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }
.ladder-tabs:empty { display: none; }
.src-tab {
  font: inherit; font-size: 12px; padding: 4px 11px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--hair); background: var(--sunk); color: var(--ink-2);
  display: inline-flex; align-items: center; gap: 6px;
}
.src-tab:hover { color: var(--ink); }
.src-tab.is-on { background: var(--card); color: var(--ink); font-weight: 600; border-color: var(--gold-mark); }
.src-tab.is-off { opacity: .5; text-decoration: line-through; }
.src-tab .badge {
  font-size: 10px; font-weight: 700; letter-spacing: .04em;
  padding: 1px 5px; border-radius: 4px; background: var(--card); color: var(--gold);
  border: 1px solid var(--hair);
}
/* 均注沒有關卡可以爬，用一張說明卡取代階梯 */
.flat-note {
  display: flex; align-items: center; gap: 16px;
  height: 158px; margin: 22px 0 12px; padding: 0 22px;
  border: 1px dashed var(--rule); border-radius: 10px; background: var(--sunk);
}
.flat-note[hidden] { display: none; }
.flat-note .big {
  font-family: var(--display); font-size: 34px; font-weight: 800;
  letter-spacing: -.035em; font-variant-numeric: tabular-nums;
}
.flat-note p { margin: 4px 0 0; font-size: 12.5px; color: var(--muted); max-width: 30ch; }

/* 每群下單設定表 */
#sourceSettings { overflow-x: auto; }
.src-table { width: 100%; min-width: 680px; border-collapse: collapse; font-size: 13px; }
.src-table th {
  text-align: left; font-size: 11px; font-weight: 600; letter-spacing: .06em;
  color: var(--muted); padding: 6px 10px; border-bottom: 1px solid var(--hair); white-space: nowrap;
}
.src-table td { padding: 8px 10px; border-bottom: 1px solid var(--hair); }
.src-table tr:last-child td { border-bottom: none; }
.src-table input[type="number"], .src-table select {
  font: inherit; font-size: 12.5px; padding: 5px 8px; width: 100%; min-width: 72px;
  border: 1px solid var(--rule); border-radius: 6px; background: var(--paper); color: var(--ink);
}
.src-table input[disabled], .src-table select[disabled] { opacity: .4; cursor: not-allowed; }
.src-table input[type="checkbox"] { width: 17px; height: 17px; accent-color: var(--gold-mark); }
/* 分批手數輸入 + 提示,直向堆在「多 TP 處理」那一格裡,不外溢到隔壁欄 */
.sp-tp-cell { min-width: 148px; }
.src-table .sp-lots { display: block; margin-top: 5px; width: 100%; min-width: 0; box-sizing: border-box; text-align: center; letter-spacing: .03em; }
.sp-ratio-note { display: block; margin-top: 3px; font-size: 11px; line-height: 1.35; max-width: 150px; }
.sp-ratio-note.is-bad  { color: var(--loss); font-weight: 600; }
.sp-ratio-note.is-warn { color: var(--gold); }
.sp-ratio-note.is-ok   { color: var(--muted); }
.src-name { font-weight: 600; white-space: nowrap; }
.src-meta { font-size: 11px; color: var(--muted); font-weight: 400; }
/* 鎖住的來源列(等級沒買到)。整列調淡 + 一顆鎖,不是只把欄位 disabled ——
   只 disable 的話那一列看起來仍然像「可以用、只是壞了」。 */
.src-table tr.is-locked { background: var(--sunk); }
.src-table tr.is-locked .src-name { color: var(--muted); }
.src-lock {
  display: inline-flex; align-items: center; gap: 6px; margin-top: 4px;
  font-size: 11px; font-weight: 600; color: var(--gold); letter-spacing: .02em;
}
.src-lock .lockmark { margin-top: 2px; }
/* 保本移損的距離觸發。只有選了保本移損才顯示(見 syncSourceProfiles)。
   標題放在輸入框上面而不是同一行 —— 同一行會把這一格撐爆、壓到隔壁欄。 */
.src-table .sp-be-wrap { display: block; margin-top: 5px; }
.src-table .sp-be-wrap span {
  display: block; margin-bottom: 3px; font-size: 11px; color: var(--muted);
}
.src-table .sp-be-wrap input { width: 100%; min-width: 0; box-sizing: border-box; text-align: center; }

/* ── 自動排程 ─────────────────────────────────────────────────────── */
.sched-table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 13px; }
.sched-table th {
  padding: 8px 10px; text-align: left; font-size: 11px; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--hair);
}
.sched-table td { padding: 8px 10px; border-bottom: 1px solid var(--hair); }
.sched-table tr:last-child td { border-bottom: none; }
.sched-table input[type="time"] {
  font: inherit; font-size: 13px; padding: 5px 8px;
  border: 1px solid var(--rule); border-radius: 6px;
  background: var(--paper); color: var(--ink);
}
.sched-table input[type="checkbox"] { width: 17px; height: 17px; accent-color: var(--gold-mark); }
.sched-empty td { color: var(--muted); padding: 18px 10px; }
.sc-days { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.sc-day {
  width: 26px; height: 26px; padding: 0; flex: none;
  font: inherit; font-size: 12px; cursor: pointer;
  border: 1px solid var(--rule); border-radius: 6px;
  background: var(--paper); color: var(--muted);
}
.sc-day.is-on { background: rgba(212, 160, 23, .14); border-color: var(--gold-mark); color: var(--gold); font-weight: 700; }
.sc-day[disabled] { opacity: .45; cursor: not-allowed; }
.sc-note { font-size: 11px; color: var(--muted); margin-left: 4px; }
.sched-count { font-size: 12px; color: var(--muted); align-self: center; }
.sched-lock {
  display: flex; align-items: center; gap: 9px;
  padding: 16px 14px; border-radius: 10px;
  background: var(--sunk); color: var(--muted); font-size: 13px;
}
.sched-lock b { color: var(--gold); }

/* 表格下面那一列動作按鈕。以前是「新增來源」用的,那個功能已經移除
   (來源清單由訊號中心決定),現在只剩自動排程的「＋ 新增排程」在用。 */
.src-add { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; align-items: center; }

.ladder-foot {
  display: flex; gap: 22px; flex-wrap: wrap;
  padding-top: 14px; border-top: 1px solid var(--hair);
}
.ladder-foot div { min-width: 0; }
.ladder-foot dt { font-size: 11px; color: var(--muted); margin-bottom: 2px; }
.ladder-foot dd { margin: 0; font-size: 17px; font-weight: 600; }

/* 主數字：整頁只有這一個 48px 以上的數字 */
.figure { display: flex; flex-direction: column; justify-content: center; }
.figure-value {
  margin: 2px 0 0; font-family: var(--display);
  font-size: 56px; line-height: 1.02; font-weight: 800;
  letter-spacing: -0.045em; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.figure-sub { margin: 10px 0 0; font-size: 13px; color: var(--ink-2); }
.figure-facts {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 14px; margin: 18px 0 0; padding-top: 16px; border-top: 1px solid var(--hair);
}
.figure-facts dt { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.figure-facts dd { margin: 0; font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }

.up   { color: var(--win); }
.down { color: var(--loss); }
.flat { color: var(--ink-2); }

/* ----------------------------------------------------------------- tiles */
.tiles {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(212px, 1fr)); gap: 12px;
}
.tile { background: var(--card); border: 1px solid var(--hair); border-radius: 11px; padding: 14px 16px; }
.tile dt { font-size: 11.5px; color: var(--muted); margin-bottom: 5px; }
.tile dd {
  margin: 0; font-family: var(--display);
  font-size: 27px; font-weight: 800; letter-spacing: -.035em; line-height: 1.05;
  font-variant-numeric: tabular-nums;
}
.tile small { display: block; margin-top: 5px; font-size: 11.5px; color: var(--muted); }

/* 勝率量表：填色是勝、軌道是敗，兩段都有文字標籤 */
/* 勝率條：底色用中性的 --sunk。以前借 --loss-wash，翻轉配色後會變成「紅底藍條」，
   看起來像在強調虧損。底色本來就只是軌道，不該帶語意。 */
.meter { height: 7px; border-radius: 999px; background: var(--sunk); margin-top: 9px; overflow: hidden; }
.meter span { display: block; height: 100%; border-radius: 999px; background: var(--win); transition: width .6s cubic-bezier(.2,.7,.3,1); }

/* --------------------------------------------------------------- filters */
.filters {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  padding: 10px 14px; margin-bottom: 14px;
  background: var(--card); border: 1px solid var(--hair); border-radius: 10px;
}
/* 分頁列：切換「訊號跟單」/「趨勢線策略」等完全獨立的策略視圖，
   比 .pillset 大一號、更顯眼——這是切換整個畫面內容的動作，不是篩選 */
.view-tabs { display: flex; gap: 6px; margin-bottom: 18px; }
.view-tab {
  position: relative;
  font: inherit; font-family: var(--display);
  font-size: 14px; font-weight: 700; padding: 10px 20px;
  border-radius: 10px; border: 1px solid transparent; background: transparent;
  color: var(--muted); cursor: pointer;
  transition: color .18s, background-color .18s, border-color .18s;
}
.view-tab:hover { color: var(--ink); background: var(--sunk); }
.view-tab.is-on {
  color: var(--gold-mark); background: var(--card); border-color: var(--hair);
}
/* 底部那道金線是「你在這裡」的訊號。整塊塗金會蓋掉旁邊的內容。 */
.view-tab.is-on::after {
  content: ''; position: absolute; inset: auto 20px -1px;
  height: 2px; border-radius: 2px;
  background: linear-gradient(90deg, transparent, var(--gold-lit), transparent);
}
.filters .label { font-size: 11.5px; color: var(--muted); letter-spacing: .06em; }
.pillset { display: flex; gap: 4px; background: var(--sunk); padding: 3px; border-radius: 8px; }
.pill {
  font: inherit; font-size: 12.5px; padding: 4px 11px; border-radius: 6px;
  border: none; background: transparent; color: var(--ink-2); cursor: pointer;
}
.pill:hover { color: var(--ink); }
.pill.is-on { background: var(--card); color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.07); }
.filters select, .daterange input {
  font: inherit; font-size: 12.5px; padding: 5px 9px; border-radius: 7px;
  border: 1px solid var(--rule); background: var(--card); color: var(--ink); max-width: 240px;
}
.daterange { display: flex; align-items: center; gap: 7px; }
.daterange[hidden] { display: none; }
.filters .count { margin-left: auto; font-size: 12px; color: var(--muted); text-align: right; }

/* ---------------------------------------------------------------- charts */
/* align-items:start —— 來源少的時候右邊那張卡貼齊內容，不要撐出一大片空白 */
.chart-row { display: grid; grid-template-columns: minmax(0,1.55fr) minmax(0,1fr); gap: 14px; align-items: start; }
@media (max-width: 980px) { .chart-row { grid-template-columns: 1fr; } }
.chart-wrap { position: relative; }
.chart-wrap svg { display: block; width: 100%; overflow: visible; }
.grid-line { stroke: var(--hair); stroke-width: 1; }
.axis-line { stroke: var(--rule); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 10.5px; font-variant-numeric: tabular-nums; }
.tick-x { fill: var(--muted); font-size: 10.5px; }
.end-label { font-size: 12px; font-weight: 600; }

.tip {
  position: absolute; pointer-events: none; z-index: 20;
  padding: 8px 11px; border-radius: 8px; min-width: 130px;
  background: var(--card); border: 1px solid var(--rule);
  box-shadow: 0 6px 22px -8px rgba(26,20,16,.4);
  font-size: 12px; opacity: 0; transition: opacity .12s;
}
.tip.is-on { opacity: 1; }
.tip-h { font-family: var(--mono); font-size: 10.5px; color: var(--muted); margin-bottom: 4px; }
.tip-row { display: flex; justify-content: space-between; gap: 14px; }
.tip-row b { font-variant-numeric: tabular-nums; }

.legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 12px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.key { width: 11px; height: 11px; border-radius: 3px; flex: none; }
.key-win { background: var(--win); }
.key-loss { background: var(--loss); }

/* 各來源績效：小倍數卡片，一個來源一張，點了就把下方全部篩選到那個來源 */
.source-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(258px, 1fr)); gap: 12px; }
.source-card {
  font: inherit; text-align: left; cursor: pointer;
  background: var(--card); border: 1px solid var(--hair); border-radius: 12px;
  padding: 14px 16px; box-shadow: var(--shadow);
  display: flex; flex-direction: column; gap: 8px;
  transition: border-color .15s, transform .08s;
}
.source-card:hover { border-color: var(--rule); }
.source-card:active { transform: translateY(1px); }
.source-card.is-on { border-color: var(--gold-mark); box-shadow: 0 0 0 1px var(--gold-mark), var(--shadow); }
.sc-head { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.sc-name { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-net { font-size: 27px; font-weight: 650; letter-spacing: -.02em; line-height: 1.1; }
.sc-chart { height: 54px; }
.sc-blank { height: 54px; display: grid; place-items: center; font-size: 11.5px; color: var(--muted); }
.sc-stats {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0;
  padding-top: 10px; border-top: 1px solid var(--hair);
}
.sc-stats dt { font-size: 10.5px; color: var(--muted); margin-bottom: 2px; white-space: nowrap; }
.sc-stats dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }

/* ---------------------------------------------------------------- tables */
.table-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  position: sticky; top: 0; z-index: 1;
  text-align: left; font-size: 11px; font-weight: 600; letter-spacing: .07em;
  text-transform: uppercase; color: var(--muted);
  padding: 8px 12px; background: var(--card); border-bottom: 1px solid var(--hair);
  white-space: nowrap;
}
tbody td { padding: 9px 12px; border-bottom: 1px solid var(--hair); white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--sunk); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.mono { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.tag {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 700; letter-spacing: .04em;
}
.tag-win  { background: var(--win-wash);  color: var(--win); }
.tag-loss { background: var(--loss-wash); color: var(--loss); }
.tag-warn { background: var(--sunk); color: var(--gold); border: 1px solid var(--gold-mark); }
.tag-lv {
  background: var(--sunk); color: var(--gold); border: 1px solid var(--hair);
  font-variant-numeric: tabular-nums;
}
.side-buy  { color: var(--win); font-weight: 600; }
.side-sell { color: var(--loss); font-weight: 600; }
/* 倒數用金色而非紅綠——紅綠在這頁專門表示賺賠，不能拿去講「快到期」 */
.empty {
  padding: 34px 20px; text-align: center; color: var(--muted); font-size: 13px;
}
.empty b { display: block; color: var(--ink-2); font-size: 14px; margin-bottom: 5px; }

/* ------------------------------------------------------------- log & set */
.logs {
  height: 190px; overflow: auto; white-space: pre-wrap; word-break: break-all;
  background: var(--sunk); border-radius: 9px; padding: 12px 14px;
  font-family: var(--mono); font-size: 12px; line-height: 1.65; color: var(--ink-2);
}
.settings[hidden] { display: none; }
.field-group + .field-group { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--hair); }
.field-group h3 { font-size: 12px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin-bottom: 12px; }
.field-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px 20px; }
.field-grid label { display: grid; grid-template-columns: 130px 1fr; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-2); }
.field-grid label.switch { grid-template-columns: 130px auto; justify-content: start; }
.field-grid input, .field-grid select, .field-grid textarea {
  font: inherit; font-size: 13px; padding: 7px 10px; min-width: 0;
  border: 1px solid var(--rule); border-radius: 7px;
  background: var(--paper); color: var(--ink);
}
.field-grid textarea { min-height: 150px; resize: vertical; font-family: var(--mono); line-height: 1.45; }
.field-grid label.field-wide { grid-column: 1 / -1; align-items: start; }
.field-grid input[type="checkbox"] { width: 17px; height: 17px; accent-color: var(--gold-mark); }
.hint { margin: 14px 0 0; font-size: 12.5px; color: var(--muted); }
.inline-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.inline-actions .hint { margin: 0; }
.settings-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--hair); }
/* 自動儲存狀態:一眼看出「有沒有存到、生不生效」，取代原本要自己記得按儲存的心智負擔 */
.save-status { margin-right: auto; font-size: 13px; color: var(--muted); min-height: 18px; transition: color .2s; }
.save-status.is-pending { color: var(--gold); }
.save-status.is-saving  { color: var(--muted); }
.save-status.is-ok      { color: var(--win); }
.save-status.is-err     { color: var(--loss); font-weight: 600; }

/* ── 會員權益：方案比較表(跟官網一致)+ 聯絡 + 頁尾免責 ───────────── */
.benefits-card { padding: 0; overflow: hidden; }
.cmp-scroll { overflow-x: auto; }
.cmp-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 640px; }
.cmp-table th, .cmp-table td { padding: 12px 14px; border-bottom: 1px solid var(--hair); text-align: center; vertical-align: middle; }
.cmp-table thead th { position: sticky; top: 0; background: var(--card); z-index: 1; }
.cmp-table .cmp-feat { text-align: left; }
.cmp-table th.cmp-feat b, .cmp-table td .cmp-feat b { display: block; font-weight: 650; color: var(--ink); }
.cmp-feat b { display: block; font-weight: 650; color: var(--ink); }
.cmp-feat span { display: block; margin-top: 2px; font-size: 11.5px; color: var(--muted); font-weight: 400; }
.cmp-plan b { display: block; font-size: 14px; color: var(--ink); }
.cmp-plan span { display: block; margin-top: 2px; font-size: 11px; color: var(--muted); }
.cmp-plan .cmp-you { display: inline-block; margin-top: 5px; padding: 1px 8px; border-radius: 999px; font-size: 10.5px; font-style: normal; background: var(--gold); color: #1a1400; font-weight: 700; }
.cmp-table td b { font-weight: 600; color: var(--ink-2); }
.cmp-table td small { display: block; margin-top: 2px; font-size: 11px; color: var(--muted); }
.cmp-table .is-cur { background: rgba(212, 160, 23, .07); }
.cmp-table thead .is-cur { background: rgba(212, 160, 23, .13); }
.cmp-table tbody tr:hover td { background: rgba(255,255,255,.02); }
.mk { display: inline-block; font-size: 15px; line-height: 1; }
.mk--y { color: var(--win); font-weight: 700; }
.mk--n { color: var(--muted); }
.cmp-note { margin: 0; padding: 12px 16px; font-size: 11.5px; color: var(--muted); border-top: 1px solid var(--hair); }
.benefits-ig { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  padding: 16px; border-top: 1px solid var(--hair); background: linear-gradient(180deg, transparent, rgba(212,160,23,.05)); }
.benefits-ig .bi-h { margin: 0; font-weight: 650; color: var(--ink); }
.benefits-ig .bi-b { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); }
.benefits-ig .bi-acts { display: flex; gap: 8px; flex-wrap: wrap; }
/* LINE 用品牌綠。跟 IG 那顆金色主按鈕並排時,顏色本身就說明了是哪個 App。 */
.btn-line { background: #06C755; border-color: #06C755; color: #fff; }
.btn-line:hover { background: #05A948; border-color: #05A948; color: #fff; }

.app-foot { margin: 26px 0 12px; padding-top: 18px; border-top: 1px solid var(--hair); color: var(--muted); }
.app-foot .foot-tag { margin: 0 0 10px; font-size: 12.5px; }
.app-foot a, .auth-foot a { color: var(--gold); text-decoration: none; }
.app-foot a:hover, .auth-foot a:hover { text-decoration: underline; }
.foot-legal summary { cursor: pointer; font-size: 12.5px; color: var(--ink-2); user-select: none; }
.foot-legal .legal-body { margin-top: 10px; display: grid; gap: 8px; }
.foot-legal .legal-body p { margin: 0; font-size: 11.5px; line-height: 1.6; color: var(--muted); }
.foot-legal .legal-body b { color: var(--ink-2); }
.app-foot .foot-copy { margin: 12px 0 0; font-size: 11.5px; }
.app-foot .foot-copy b { color: var(--gold); }

.notice {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 12px 15px; border-radius: 10px; margin-bottom: 14px;
  background: var(--sunk); border: 1px solid var(--hair); font-size: 13px; color: var(--ink-2);
}
.notice b { color: var(--ink); }

body[data-role="central"] .client-only { display: none; }
body[data-role="client"]  .central-only { display: none; }

/* 時區註記：表格裡的時間是「券商牆上時間」，不是使用者電腦的時間。
   兩者可能差好幾個小時（本機是 GMT+8、券商 GMT+3，差 5 小時），
   不標出來的話「我記得是下午一點成交的，怎麼寫八點」會一直被問。 */
.tz-note {
  display: inline-block; margin-left: 8px; padding: 1px 7px;
  border-radius: 999px; background: var(--sunk); border: 1px solid var(--hair);
  font-size: 11px; color: var(--muted); white-space: nowrap;
}
.tz-note:empty { display: none; }

/* ── 會員管理 (只有訊號中心看得到) ───────────────────────────────────── */
.mbr-toolbar {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px;
}
.mbr-toolbar input[type="search"] {
  flex: 1 1 200px; min-width: 160px; padding: 7px 11px; font: inherit; font-size: 13px;
  border: 1px solid var(--rule); border-radius: 7px;
  background: var(--paper); color: var(--ink);
}
.mbr-count { font-size: 12px; color: var(--muted); margin-left: auto; }

/* 新增會員的表單，預設收起 —— 平常在看的是名單，不是一直在開帳號 */
#mbrNewForm { margin-bottom: 16px; }
#mbrNewForm[hidden] { display: none; }
.mbr-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin-bottom: 12px;
}
.mbr-grid label { display: block; font-size: 12px; color: var(--ink-2); }
.mbr-grid input, .mbr-grid select {
  width: 100%; margin-top: 4px; padding: 7px 9px; font: inherit; font-size: 13px;
  border: 1px solid var(--rule); border-radius: 6px;
  background: var(--paper); color: var(--ink);
}

/* 開通成功後把帳密攤出來 —— 密碼只有這一次拿得到，不能只用 alert 閃過去 */
.mbr-issued {
  margin-bottom: 16px; padding: 14px 16px; border-radius: 10px;
  background: var(--ok-wash); border: 1px solid var(--ok);
}
.mbr-issued h4 { margin: 0 0 8px; font-size: 13px; color: var(--ok); }
.mbr-cred {
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  font-family: var(--mono); font-size: 14px; margin-bottom: 8px;
}
.mbr-cred b { font-size: 15px; }
.mbr-issued p { margin: 0; font-size: 11.5px; color: var(--ink-2); }

.mbr-tag {
  display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.mbr-tag.t-trial    { background: var(--sunk);      color: var(--ink-2); }
.mbr-tag.t-basic    { background: var(--sunk);      color: var(--ink); }
.mbr-tag.t-advanced { background: var(--win-wash);  color: var(--win); }
.mbr-tag.t-flagship { background: var(--gold-hi);   color: #2a1d05; }
.mbr-state.ok   { color: var(--ok); }
/* 快到期的要跳出來 —— 這正是該主動聯繫續費的名單。只換色不夠：--gold 在
   奶油底上偏棕，跟 --ok 的墨綠遠看分不出來，所以再加粗。 */
.mbr-state.warn { color: var(--gold); font-weight: 600; }
.mbr-state.bad  { color: var(--loss); font-weight: 600; }
.mbr-acts { display: flex; gap: 5px; flex-wrap: wrap; }
.mbr-acts .btn { padding: 3px 8px; font-size: 11px; }
.mbr-online { color: var(--ok); }
.mbr-offline { color: var(--muted); }
/* 表格在窄視窗要能自己捲，不要把整頁撐橫 */
.mbr-scroll { overflow-x: auto; }

/* ── 會員登入 ─────────────────────────────────────────────────────────
   整頁的門。沒登入時面板是 display:none 而不是模糊 —— 模糊擋不住截圖，
   而且「看得到卻不能動」會讓人以為程式當掉。這裡要像一道乾淨的前門。 */
#authGate {
  position: fixed; inset: 0; z-index: 900;
  display: none; align-items: center; justify-content: center;
  padding: 24px; background: var(--paper);
  /* 頂部一抹金 —— 跟金條標記同一組色，讓這頁一眼就是同一套產品 */
  background-image: radial-gradient(120% 62% at 50% -18%,
                    color-mix(in srgb, var(--gold-lit) 16%, transparent) 0%, transparent 68%);
}
#authGate.is-on { display: flex; }
body.auth-locked > *:not(#authGate) { display: none; }

.auth-card {
  width: 100%; max-width: 372px;
  background: var(--card); border: 1px solid var(--hair);
  border-radius: 14px; padding: 32px 30px 24px; box-shadow: var(--shadow);
}
.auth-brand { display: flex; align-items: center; gap: 12px; }
.auth-brand h2 { font-size: 17px; }
.auth-brand .eyebrow { margin: 2px 0 0; }
.auth-lede {
  margin: 18px 0 22px; padding-top: 16px; border-top: 1px solid var(--hair);
  font-size: 12.5px; color: var(--ink-2);
}

.auth-field { display: block; margin-bottom: 13px; }
/* 用明確 class 而不是 `.auth-field > span` —— 後者權重比 .auth-hint 高，
   會把本該隱藏的 Caps Lock 提示強制顯示出來，也會把輸入框的包裝層
   一起套上標籤字體。 */
.auth-label {
  display: block; margin-bottom: 5px;
  font-size: 11px; font-weight: 600; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted);
}
.auth-input { display: block; position: relative; }
.auth-input input {
  width: 100%; padding: 10px 12px; font: inherit; font-size: 14px;
  border: 1px solid var(--rule); border-radius: 7px;
  background: var(--paper); color: var(--ink);
  transition: border-color .15s, background .15s;
}
.auth-input input:hover { border-color: var(--gold-mark); }
.auth-input input:focus {
  outline: 2px solid var(--gold-mark); outline-offset: 1px;
  border-color: transparent; background: var(--card);
}
#authPass { padding-right: 46px; }         /* 讓出「顯示」按鈕的位置 */
.auth-peek {
  position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
  padding: 4px 8px; font-size: 11px;
}
.auth-hint { margin-top: 5px; font-size: 11px; color: var(--gold); display: none; }
.auth-hint.is-on { display: block; }

#authSubmit { width: 100%; margin-top: 6px; padding: 11px; font-size: 14px; }
#authSubmit[disabled] { opacity: .6; cursor: progress; }

.auth-msg {
  display: none; gap: 8px; align-items: flex-start;
  margin-top: 14px; padding: 10px 12px; border-radius: 8px;
  font-size: 12.5px; line-height: 1.55;
  background: var(--loss-wash); color: var(--loss);
}
.auth-msg.is-on { display: flex; }
.auth-msg::before { content: "!"; font-weight: 700; flex: none; }

.auth-foot {
  margin: 20px 0 0; padding-top: 14px; border-top: 1px solid var(--hair);
  font-size: 11.5px; color: var(--muted); line-height: 1.75;
}
.auth-foot code { font-family: var(--mono); font-size: 11px; color: var(--ink-2); }
/* 登入頁沒有頂列，主題切換另外擺一顆，否則夜間使用者被鎖在亮底 */
#authTheme { position: fixed; top: 16px; right: 18px; z-index: 901; }

/* 已登入後顯示在頂列的身分徽章 */
.auth-badge {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 3px 5px 3px 11px; border-radius: 999px;
  background: var(--sunk); border: 1px solid var(--hair);
  font-size: 12px; color: var(--ink-2); white-space: nowrap;
}
.auth-badge b { font-weight: 600; color: var(--ink); }
.auth-badge .tier { font-weight: 600; color: var(--gold); }
.auth-badge .exp { color: var(--muted); }
/* 剩不到一週就轉紅，會員自己看得到該續期了 */
.auth-badge.is-soon { border-color: var(--loss); background: var(--loss-wash); }
.auth-badge.is-soon .exp { color: var(--loss); font-weight: 600; }
#authLogout, #authChangePw { padding: 2px 9px; font-size: 11px; }

/* 修改密碼對話框 —— 沿用登入卡的骨架，只是浮在面板上而不是佔滿整頁 */
#pwModal {
  position: fixed; inset: 0; z-index: 950;
  display: none; align-items: center; justify-content: center;
  padding: 24px; background: rgba(26, 20, 16, .45);
}
#pwModal.is-on { display: flex; }
:root[data-theme="dark"] #pwModal { background: rgba(0, 0, 0, .6); }
#pwModal .auth-card { max-width: 360px; }
.pw-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
.pw-head h3 { font-size: 15px; }
.pw-head span { font-size: 12px; color: var(--muted); }
.pw-actions { display: flex; gap: 8px; margin-top: 4px; }
.pw-actions .btn { flex: 1; padding: 9px; }
.auth-msg.is-ok { background: var(--ok-wash); color: var(--ok); }
.auth-msg.is-ok::before { content: "✓"; }

/* ── 窄螢幕 ──────────────────────────────────────────────────
   這個介面主要跑在會員自己的電腦上（旁邊開著 MT5），但手機看狀態
   也要能用。原則：橫排的東西改成可橫捲，多欄網格降欄，不要撐破畫面。 */
@media (max-width: 900px) {
  /* 側欄的 248px 在手機上會把主內容擠到剩一百出頭，整頁跟著爆版。
     這裡讓它退回文件流，堆在內容上方。 */
  body[data-role="client"]:not(.auth-locked) .shell {
    grid-template-columns: minmax(0, 1fr);
  }
  .side { position: static; top: auto; }
  .side-nav { display: none; }   /* 單欄堆疊後，錨點導航沒有意義 */

  .dash-grid--2, .dash-grid--3 { grid-template-columns: minmax(0, 1fr); }
  .dash-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dash-grid--3 .table-scroll, .dash-grid--2 .table-scroll { max-height: 320px; }
}

@media (max-width: 640px) {
  .wrap { padding-inline: 14px; }
  .shell { gap: 18px; }
  .dash-stats { grid-template-columns: minmax(0, 1fr); }

  /* 這幾排本來會撐破畫面，改成橫捲；捲軸藏起來，靠慣性滑 */
  /* min-width: 0 是關鍵 —— flex item 預設 min-width:auto 會被內容撐開，
     那樣 overflow-x 根本不會觸發 */
  .view-tabs, .pillset {
    overflow-x: auto; -webkit-overflow-scrolling: touch;
    scrollbar-width: none; flex-wrap: nowrap;
    min-width: 0; max-width: 100%;
  }
  .view-tabs::-webkit-scrollbar, .pillset::-webkit-scrollbar { display: none; }
  .view-tab, .pill { flex: 0 0 auto; white-space: nowrap; }
  .filters { align-items: stretch; }
  .filters .count { margin-left: 0; text-align: left; }
  .filters select, .daterange input { min-width: 0; }

  .donut-wrap { justify-content: center; }
  /* 底部狀態列在手機上會擋住內容，收掉 */
  #statusBar { display: none; }
}

@media (max-width: 420px) {
  .auth-card { padding: 26px 20px 20px; border-radius: 12px; }
}

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
</style>
</head>
<body data-role="__ROLE__" class="__BODY_CLASS__">

<div id="authGate" class="client-only __GATE_CLASS__">
  <button class="btn btn-quiet" id="authTheme" type="button" title="日夜切換">☾</button>
  <form class="auth-card" id="authForm" autocomplete="on">
    <div class="auth-brand">
      <span class="bullion" aria-hidden="true"></span>
      <div>
        <h2>__TITLE__</h2>
        <p class="eyebrow">MEMBER ACCESS</p>
      </div>
    </div>

    <p class="auth-lede">請以會員帳號登入，登入後才會開始接收訊號。</p>

    <label class="auth-field">
      <span class="auth-label">帳號</span>
      <span class="auth-input">
        <input id="authUser" name="username" autocomplete="username"
               autocapitalize="off" autocorrect="off" spellcheck="false" required />
      </span>
    </label>

    <label class="auth-field">
      <span class="auth-label">密碼</span>
      <span class="auth-input">
        <input id="authPass" name="password" type="password"
               autocomplete="current-password" required />
        <button class="btn btn-quiet auth-peek" id="authPeek" type="button"
                tabindex="-1" aria-label="顯示密碼">顯示</button>
      </span>
      <span class="auth-hint" id="authCaps">Caps Lock 已開啟</span>
    </label>

    <button class="btn btn-go" id="authSubmit" type="submit">登 入</button>
    <div class="auth-msg" id="authMsg" role="alert"></div>

    <p class="auth-foot">
      一組帳號同時只能在一台電腦使用；在別台登入會把這台登出。<br />
      忘記密碼或需要續期，請聯繫管理員：IG <b>@goldyoung0927</b>　/　LINE <b>qazasd96225</b><br />
      <a href="https://gold-young.com/" target="_blank" rel="noopener noreferrer">前往官方網站 gold-young.com ↗</a>
    </p>
  </form>
</div>

<div id="pwModal" class="client-only">
  <form class="auth-card" id="pwForm" autocomplete="off">
    <div class="pw-head">
      <h3>修改密碼</h3>
      <span id="pwWho"></span>
    </div>
    <p class="auth-lede">改完不需要重新登入，這台會繼續跟單。</p>

    <label class="auth-field">
      <span class="auth-label">目前密碼</span>
      <span class="auth-input">
        <input id="pwOld" type="password" autocomplete="current-password" required />
      </span>
    </label>
    <label class="auth-field">
      <span class="auth-label">新密碼</span>
      <span class="auth-input">
        <input id="pwNew" type="password" autocomplete="new-password" required />
        <button class="btn btn-quiet auth-peek" id="pwPeek" type="button"
                tabindex="-1" aria-label="顯示密碼">顯示</button>
      </span>
      <span class="auth-hint" id="pwCaps">Caps Lock 已開啟</span>
    </label>
    <label class="auth-field">
      <span class="auth-label">再輸入一次</span>
      <span class="auth-input">
        <input id="pwNew2" type="password" autocomplete="new-password" required />
      </span>
    </label>

    <div class="pw-actions">
      <button class="btn btn-quiet" id="pwCancel" type="button">取消</button>
      <button class="btn btn-go" id="pwSubmit" type="submit">確定變更</button>
    </div>
    <div class="auth-msg" id="pwMsg" role="alert"></div>
    <p class="auth-foot">至少 __PWMIN__ 個字元。忘記目前密碼請聯繫管理員重設。</p>
  </form>
</div>

<header class="rail">
  <div class="rail-id">
    <span class="bullion" aria-hidden="true"></span>
    <div>
      <h1>__TITLE__</h1>
      <p class="rail-sub">__SUBTITLE__</p>
    </div>
  </div>
  <nav class="rail-nav client-only" id="railNav" aria-label="主導覽">
    <a href="#top"         data-nav="top">總覽</a>
    <a href="#secSources"  data-nav="secSources">策略跟單</a>
    <a href="#secChart"    data-nav="secChart">圖表分析</a>
    <a href="#secPerf"     data-nav="secPerf">報表中心</a>
    <a href="#secTrades"   data-nav="secTrades">歷史訂單</a>
    <a href="#settings"    data-nav="settings">設定</a>
    <a href="#secBenefits" data-nav="secBenefits">會員權益</a>
    <!-- 官網是外部連結，不進 bindRailNav 的錨點捲動（那支只認 data-nav） -->
    <a class="rail-out" href="https://gold-young.com/" target="_blank" rel="noopener noreferrer">官方網站 ↗</a>
  </nav>
  <div class="rail-state">
    <span class="auth-badge client-only" id="authBadge" hidden>
      <b id="authBadgeUser"></b>
      <span class="tier" id="authBadgeTier"></span>
      <span class="exp" id="authBadgeExp"></span>
      <button class="btn" id="authChangePw" type="button">改密碼</button>
      <button class="btn" id="authLogout" type="button">登出</button>
    </span>
    <span class="chip is-off" id="chipService"><span class="dot"></span><span>載入中</span></span>
    <span class="chip is-off client-only" id="chipMt5"><span class="dot"></span><span>MT5</span></span>
    <span class="chip is-off" id="chipHub"><span class="dot"></span><span>Hub</span></span>
    <!-- 掛單即時狀態，讓會員一眼看到有沒有單在等進場 -->
    <a href="#secPending" class="chip is-off client-only" id="chipPending"><span class="dot"></span><span>掛單</span></a>
  </div>
  <div class="rail-actions">
    <button class="btn btn-go" id="start"><span class="client-only">開始跟單</span><span class="central-only">開始發布</span></button>
    <button class="btn" id="stop">停止</button>
    __EXTRA_BUTTON__
    <button class="btn btn-quiet" id="themeToggle" title="切換日夜模式" aria-label="切換日夜模式">☾</button>
    <button class="btn" id="toggleSettings" aria-expanded="false">設定</button>
  </div>
</header>

<main>
  <div id="notice"></div>

  <div class="shell">

  <!-- 左側欄。只有會員端有 —— 訊號中心沒有等級也沒有這些區塊。
       這裡不放任何「還沒做出來的功能」：導覽連到的每個錨點都真的存在，
       方案功能列出的每一項都直接對應後端 entitlements 的欄位。 -->
  <aside class="side client-only" id="sideBar" hidden>
   <div class="side-sticky">

    <div class="side-card">
      <p class="side-tier"><b id="sideTier">—</b><span>會員</span></p>
      <p class="side-exp"><span id="sideExpLabel">方案到期日</span>　<b id="sideExp">—</b></p>
      <div class="side-meter" id="sideMeterWrap" hidden><i id="sideMeter"></i></div>
      <p class="side-left" id="sideLeft"></p>
      <!-- 進階版以上:非開盤/未跟單自動暫停計時的狀態列 -->
      <p class="side-pause" id="sidePause" hidden></p>
    </div>


    <!-- 升級入口。方案異動一律私訊處理（跟官網的作法一致），所以這裡
         只給聯絡方式，不做線上結帳。已經是最高等級就整塊收起來。 -->
    <div class="side-card side-up" id="secUpgrade" hidden>
      <p class="side-up-h">升級方案</p>
      <p class="side-up-b" id="upgradeBody">解鎖更多訊號來源與策略設定</p>
      <p class="side-up-ig">IG<b>@goldyoung0927</b></p>
      <p class="side-up-ig">LINE<b>qazasd96225</b></p>
      <a class="side-up-site" href="https://gold-young.com/" target="_blank" rel="noopener noreferrer">看完整方案內容 ↗</a>
    </div>

    <!-- 市場總覽。資料來自 EA 的 watchlist.json；舊版 EA 沒寫這個檔，
         renderWatchlist() 會整塊 hidden 起來，不會留一個空殼在那。 -->
    <div class="side-feats" id="secWatch" hidden>
      <p class="side-head">市場總覽</p>
      <ul class="wl" id="watchlist"></ul>
      <p class="wl-foot">更新時間<span id="watchTime">—</span></p>
    </div>

    <div class="side-feats" id="secEnt">
      <p class="side-head">方案功能</p>
      <ul id="sideEnt"></ul>
      <p class="side-legend">
        <span class="lg"><i class="dot dot--on"></i>可使用</span>
        <span class="lg"><i class="lockmark"></i>需更高等級</span>
      </p>
    </div>

   </div><!-- /side-sticky -->
  </aside>

  <div class="shell-main">

  <!-- 只有設定過「其他策略(EA)」才出現；沒用這功能的人畫面完全不變 -->
  <div class="view-tabs" id="viewTabs" hidden>
    <button type="button" class="view-tab is-on" data-view="signals">__TAB1__</button>
    <button type="button" class="view-tab client-only" data-view="ea">趨勢線策略</button>
    <button type="button" class="view-tab central-only" data-view="members">會員管理</button>
  </div>

<div id="viewSignals">

  <!-- 訊號中心專屬。它沒有 MT5、沒有持倉，需要看的是「訊號有沒有發出去、
       會員連不連得到我」，所以放的是服務狀態與 Hub 位址。 -->
  <div class="central-only">
    <dl class="dash-stats" id="centralStats"></dl>

    <div class="dash-grid dash-grid--2">
      <section class="panel">
        <div class="panel-head">
          <h2>發布目標</h2>
          <p class="spacer">會員端要填的位址</p>
        </div>
        <div class="panel-body">
          <div class="mock" style="box-shadow:none">
            <div class="mock-row"><span class="k">模式</span><span class="v" id="cenMode">—</span></div>
            <div class="mock-row"><span class="k">Hub 位址</span><span class="v" id="cenHub">—</span></div>
            <div class="mock-row"><span class="k">本機 IP</span><span class="v" id="cenLan">—</span></div>
          </div>
          <p class="hint" id="hint" style="margin-top:14px"></p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>LINE 資料來源</h2>
          <p class="spacer">加密資料庫中的聊天室</p>
        </div>
        <div class="panel-body">
          <div id="cenChats" class="mock" style="box-shadow:none"></div>
        </div>
      </section>
    </div>
  </div>

  <!-- 以下全是會員端專屬：持倉、成交、馬丁、績效都是 MT5 的概念，
       訊號中心沒有 MT5 也沒有交易紀錄，顯示出來會是一堆永遠空的面板。 -->
  <div class="client-only">

  <!-- ① 頂部統計卡列。內容由 renderDashStats() 依實際資料產生。 -->
  <dl class="dash-stats" id="dashStats"></dl>

  <!-- ② 掛單與持倉 —— 移到最上面，讓會員一進來就即時看到有沒有單在等進場、目前持倉。
       這兩區是當下狀態，不吃下方的期間篩選。 -->
  <div class="dash-grid dash-grid--2">
    <section class="panel" id="secPending">
      <div class="panel-head">
        <h2>待成交掛單</h2>
        <p class="spacer" id="pendingSummary">—</p>
        <span class="tz-note" id="tzNotePending"></span>
      </div>
      <div class="panel-body panel-body--flush"><div id="pending"></div></div>
    </section>

    <section class="panel" id="secPositions">
      <div class="panel-head">
        <h2>目前持倉</h2>
        <p class="spacer" id="posSummary">—</p>
      </div>
      <div class="panel-body panel-body--flush"><div id="positions"></div></div>
    </section>
  </div>

  <!-- ③ 篩選列：期間與來源。下方所有圖表與表格共用這一組。 -->
    <div class="filters">
      <span class="label">期間</span>
      <div class="pillset" id="filterPeriod">
        <button class="pill is-on" data-period="today">今日</button>
        <button class="pill" data-period="week">本週</button>
        <button class="pill" data-period="lastweek">上週</button>
        <button class="pill" data-period="month">本月</button>
        <button class="pill" data-period="lastmonth">上月</button>
        <button class="pill" data-period="all">全部</button>
        <button class="pill" data-period="custom">自訂</button>
      </div>
      <div class="daterange" id="dateRange" hidden>
        <input type="date" id="dateFrom" aria-label="起始日期" />
        <span class="label">至</span>
        <input type="date" id="dateTo" aria-label="結束日期" />
      </div>
      <span class="label">來源</span>
      <select id="filterSource"><option value="all">全部來源</option></select>
      <span class="count" id="filterCount"></span>
    </div>

  <!-- ③ 主視覺：K 線圖 + 跟單策略。
       K 線的資料是會員自己那台 MT5 的券商報價（EA 寫出 rates_*.json），
       不是外部行情源 —— 圖上看到的價格就是他實際成交的價格。 -->
  <div class="dash-grid dash-grid--hero">
    <section class="panel panel--chart" id="secChart">
      <div class="chart-bar">
        <div class="chart-sym">
          <b id="kSymbol">XAUUSD</b>
          <span class="chart-tf-cur" id="kTfLabel">15 分鐘</span>
          <span class="chart-feed" id="kFeed">券商即時報價</span>
        </div>
        <div class="pillset chart-tfs" id="kTfs"></div>
        <div class="chart-toggles">
          <button type="button" class="ind is-on" data-ma="5">MA5</button>
          <button type="button" class="ind is-on" data-ma="20">MA20</button>
          <button type="button" class="ind is-on" data-ma="60">MA60</button>
          <button type="button" class="ind is-on" data-vol="1">成交量</button>
        </div>
      </div>
      <div class="chart-read" id="kRead"></div>
      <div class="kline-wrap" id="klineWrap">
        <svg id="klineChart" role="img" aria-label="價格走勢 K 線圖"></svg>
        <div class="tip" id="klineTip"></div>
        <div class="kline-empty" id="klineEmpty">等待 MT5 行情資料</div>
      </div>
    </section>

    <section class="panel" id="secSources">
      <div class="panel-head">
        <h2>跟單策略</h2>
        <p class="spacer">點卡片可篩選</p>
      </div>
      <div class="panel-body panel-body--tight">
        <div class="source-grid" id="sourcePerf"></div>
      </div>
    </section>
  </div>

  <!-- ④ 損益曲線。整列寬，這是會員最常盯的一張圖。 -->
  <div class="dash-grid">
    <section class="panel" id="secCurve">
      <div class="panel-head">
        <h2>累計損益曲線</h2>
        <p>每一筆平倉後的累積結果，單位 <span id="curCode">USD</span></p>
      </div>
      <div class="panel-body">
        <div class="card">
          <div class="chart-wrap" id="equityWrap">
            <svg id="equityChart" role="img" aria-label="累計損益曲線"></svg>
            <div class="tip" id="equityTip"></div>
          </div>
        </div>
      </div>
    </section>
  </div>

  <!-- ④ 三欄：馬丁階梯 / 績效圓環 / 交易紀錄 -->
  <div class="dash-grid dash-grid--3">
  <section class="card hero" id="secLadder">
    <div class="ladder client-only">
      <div class="ladder-head">
        <h2 id="ladderTitle">馬丁階梯</h2>
        <span class="ladder-level" id="ladderLevel">—</span>
      </div>
      <div class="ladder-tabs" id="ladderTabs"></div>
      <div class="rungs" id="rungs"></div>
      <div class="flat-note" id="flatNote" hidden></div>
      <dl class="ladder-foot">
        <div><dt>下一筆手數</dt><dd id="nextLot">—</dd></div>
        <div><dt>連續虧損</dt><dd id="consecLoss">—</dd></div>
        <div><dt>本回合已投入</dt><dd id="openCycle">—</dd></div>
      </dl>
    </div>
    <div class="figure">
      <p class="eyebrow">累計已實現損益</p>
      <p class="figure-value" id="heroNet">—</p>
      <p class="figure-sub" id="heroSub">等待交易資料</p>
      <dl class="figure-facts client-only">
        <div><dt>帳戶淨值</dt><dd id="factEquity">—</dd></div>
        <div><dt>餘額</dt><dd id="factBalance">—</dd></div>
        <div><dt>浮動損益</dt><dd id="factFloating">—</dd></div>
      </dl>
    </div>
  </section>

    <section class="panel">
      <div class="panel-head"><h2>績效統計</h2></div>
      <div class="panel-body">
        <div class="donut-wrap">
          <div class="donut" id="donut"></div>
          <dl class="donut-legend" id="donutLegend"></dl>
        </div>
        <div class="mock" id="donutExtra" style="margin-top:18px"></div>
      </div>
    </section>

    <section class="panel" id="secTrades">
      <div class="panel-head">
        <h2>交易紀錄</h2>
        <p class="spacer">每一筆的完整數字</p>
        <span class="tz-note" id="tzNoteRecords"></span>
      </div>
      <div class="panel-body panel-body--flush">
    <div class="card" style="padding:0">
      <div class="table-scroll" id="records"></div>
    </div>
      </div>
    </section>
  </div>

  <!-- ⑥ 完整績效指標與每筆損益 -->
  <div class="section-head" id="secPerf">
    <h2>績效分析</h2>
    <p>下方全部圖表與表格共用同一組篩選</p>
  </div>
  <dl class="tiles" id="tiles"></dl>

    <div class="card" style="margin-top:14px">
      <p class="eyebrow">每筆損益</p>
      <div class="chart-wrap" id="barsWrap">
        <svg id="tradeBars" role="img" aria-label="每筆交易損益"></svg>
        <div class="tip" id="barsTip"></div>
      </div>
      <div class="legend">
        <span><i class="key key-win"></i>贏（獲利）</span>
        <span><i class="key key-loss"></i>輸（虧損）</span>
        <span style="color:var(--muted)">數值見下方交易紀錄表</span>
      </div>
    </div>

  </div><!-- /client-only -->

</div><!-- /viewSignals -->

<div id="viewEA" class="client-only" hidden>
  <section class="card hero">
    <div class="figure" style="grid-column:1/-1">
      <p class="eyebrow">累計已實現損益 · 趨勢線策略</p>
      <p class="figure-value" id="eaHeroNet">—</p>
      <p class="figure-sub" id="eaHeroSub">等待交易資料</p>
    </div>
  </section>

  <div class="section-head">
    <h2>目前持倉</h2>
    <p id="eaPosSummary">—</p>
  </div>
  <div class="card" style="padding:0"><div id="eaPositions"></div></div>

  <div class="section-head">
    <h2>績效分析</h2>
    <p>只計算「其他策略」自己下的單，跟訊號跟單完全分開算</p>
  </div>

  <div class="filters">
    <span class="label">期間</span>
    <div class="pillset" id="eaFilterPeriod">
      <button class="pill is-on" data-period="today">今日</button>
      <button class="pill" data-period="week">本週</button>
      <button class="pill" data-period="lastweek">上週</button>
      <button class="pill" data-period="month">本月</button>
      <button class="pill" data-period="lastmonth">上月</button>
      <button class="pill" data-period="all">全部</button>
      <button class="pill" data-period="custom">自訂</button>
    </div>
    <div class="daterange" id="eaDateRange" hidden>
      <input type="date" id="eaDateFrom" aria-label="起始日期" />
      <span class="label">至</span>
      <input type="date" id="eaDateTo" aria-label="結束日期" />
    </div>
    <span class="count" id="eaFilterCount"></span>
  </div>

  <dl class="tiles" id="eaTiles"></dl>

  <div class="section-head">
    <h2>累計損益曲線</h2>
    <p>每一筆平倉後的累積結果，單位 <span id="eaCurCode">USD</span></p>
  </div>
  <div class="card">
    <div class="chart-wrap" id="eaEquityWrap">
      <svg id="eaEquityChart" role="img" aria-label="趨勢線策略累計損益曲線"></svg>
      <div class="tip" id="eaEquityTip"></div>
    </div>
  </div>

  <div class="section-head">
    <h2>訊號類型績效</h2>
    <p>這顆 EA 有好幾種進場邏輯，分開看才知道哪種訊號在賺、哪種在虧</p>
  </div>
  <div class="source-grid" id="eaBreakdown"></div>

  <div class="section-head">
    <h2>交易紀錄</h2>
    <p>每一筆的完整數字，也是上方圖表的表格版</p>
  </div>
  <div class="card" style="padding:0">
    <div class="table-scroll" id="eaRecords"></div>
  </div>
</div><!-- /viewEA -->

<div id="viewMembers" class="central-only" hidden>
  <div class="section-head">
    <h2>會員管理</h2>
    <p id="mbrSubtitle">從雲端 Hub 讀取</p>
    <span class="spacer"></span>
    <button class="btn" id="mbrRefresh" type="button">重新整理</button>
    <button class="btn btn-go" id="mbrNewToggle" type="button">＋ 開通會員</button>
  </div>

  <div class="card" id="mbrNewForm" hidden>
    <p class="eyebrow">開通新會員</p>
    <div class="mbr-grid">
      <label>帳號<input id="mbrUser" autocapitalize="off" spellcheck="false"
                       placeholder="例 wang001" /></label>
      <label>等級<select id="mbrTier"></select></label>
      <label>天數<input id="mbrDays" type="number" min="1" placeholder="留空 = 該等級預設" /></label>
      <label>備註<input id="mbrNote" placeholder="LINE / 收款方式" /></label>
    </div>
    <button class="btn btn-go" id="mbrCreate" type="button">建立</button>
    <button class="btn btn-quiet" id="mbrCancel" type="button">取消</button>
  </div>

  <div class="mbr-issued" id="mbrIssued" hidden>
    <h4>已開通 — 密碼只顯示這一次</h4>
    <div class="mbr-cred">
      <span>帳號 <b id="mbrIssuedUser"></b></span>
      <span>密碼 <b id="mbrIssuedPass"></b></span>
      <button class="btn" id="mbrCopy" type="button">複製帳密</button>
      <button class="btn btn-quiet" id="mbrIssuedClose" type="button">知道了</button>
    </div>
    <p>伺服器只存雜湊，關掉之後就撈不回來了；忘記只能重設密碼。</p>
  </div>

  <div class="card">
    <div class="mbr-toolbar">
      <input type="search" id="mbrSearch" placeholder="搜尋帳號或備註…" />
      <span class="mbr-count" id="mbrCount"></span>
    </div>
    <div class="mbr-scroll">
      <table>
        <thead><tr>
          <th>帳號</th><th>等級</th><th>狀態</th><th>到期</th>
          <th>線上</th><th>最後上線</th><th>備註</th><th>操作</th>
        </tr></thead>
        <tbody id="mbrRows"></tbody>
      </table>
    </div>
  </div>

  <div class="section-head">
    <h2>登入紀錄</h2>
    <p>最近 60 筆，含失敗嘗試</p>
  </div>
  <div class="card mbr-scroll">
    <table>
      <thead><tr><th>時間</th><th>帳號</th><th>結果</th><th>裝置</th><th>IP</th></tr></thead>
      <tbody id="mbrLoginRows"></tbody>
    </table>
  </div>
</div><!-- /viewMembers -->

  <!-- 狀態紀錄只留給訊號中心。那是給維運看的東西（LINE DB 連線、發布狀況），
       會員看到只會困惑或誤判。元素保留在 DOM 裡而不是整段拿掉 —— paintStatus()
       每秒都會寫 #logs / #uptime，拿掉要在好幾處加防呆，用 central-only 把它
       對會員隱藏起來就夠了，也跟這頁其他角色差異的做法一致。 -->
  <div class="section-head central-only" id="secLogs">
    <h2>狀態紀錄</h2>
    <p id="uptime"></p>
  </div>
  <div class="card central-only"><div class="logs" id="logs"></div></div>

  <!-- ⑧ 設定。會員端一律攤開（會員權益上方）——「改設定」是每天都會做的事，
       藏在一顆按鈕後面只是多一步。訊號中心維持收合。 -->
  <section class="card settings" id="settings" __SETTINGS_HIDDEN__ style="margin-top:14px">
    <div class="section-head" style="margin:0 0 16px">
      <h2>設定</h2>
      <p>改任何設定都會<b>自動儲存</b>，不用記得按儲存；狀態顯示在下方。</p>
    </div>
    __FIELDS__
    <div class="settings-actions">
      <span class="save-status" id="saveStatus"></span>
      <button class="btn central-only" id="restartApply" hidden>重新啟動並套用</button>
      <button class="btn" id="save">立即儲存</button>
      <button class="btn central-only" id="closeSettings">收起</button>
    </div>
  </section>

  <!-- ⑨ 會員權益：跟官網一致的方案比較表 + 聯絡方式。
       刻意排在最後、只在免責聲明上面 —— 這是「想升級時才會看」的內容，
       每天要用的持倉/報表/設定都應該排在它前面。 -->
  <div class="client-only">
    <div class="section-head" id="secBenefits">
      <h2>會員權益</h2>
      <p>不同等級能用的功能一項一項對給你看。你目前的方案會標亮；想解鎖更多，私訊我們升級。</p>
    </div>
    <div class="card benefits-card">
      <div class="cmp-scroll"><table class="cmp-table" id="benefitsTable"></table></div>
      <p class="cmp-note">所有方案價格皆以美金（USD）計價；本公司保留方案內容與價格調整之權利。交易涉及風險，請詳閱頁尾免責聲明。</p>
      <div class="benefits-ig">
        <div>
          <p class="bi-h">需要升級方案，或任何服務？</p>
          <p class="bi-b">直接私訊我們的 Instagram 或 LINE，幫你開通、續期、解答問題。</p>
        </div>
        <div class="bi-acts">
          <a class="btn btn-go" href="https://instagram.com/goldyoung0927" target="_blank" rel="noopener noreferrer">IG @goldyoung0927</a>
          <a class="btn btn-line" href="https://line.me/ti/p/~qazasd96225" target="_blank" rel="noopener noreferrer">LINE qazasd96225</a>
          <a class="btn" href="https://gold-young.com/" target="_blank" rel="noopener noreferrer">官方網站 ↗</a>
        </div>
      </div>
    </div>
  </div>

  <!-- 頁尾免責聲明：跟官網一致，會員端每一頁底部都在。 -->
  <footer class="app-foot client-only" id="appFoot">
    <p class="foot-tag">XAUUSD 黃金訊號自動跟單。專業團隊發訊號，系統幫你解析、控風險、下單。</p>
    <details class="foot-legal">
      <summary>免責聲明（點開閱讀完整條款）</summary>
      <div class="legal-body">
        <p><b>非投資建議。</b>本系統所提供之一切內容，純屬技術研究與資訊分享，不構成任何投資建議、財務建議、招攬或買賣要約。本公司並非證券投資顧問事業、期貨顧問事業或任何形式之受監理金融機構，不提供個別化投資推薦，亦不代客操作或代為管理資金。</p>
        <p><b>交易風險。</b>外匯及貴金屬保證金交易屬高槓桿商品，具有高度風險，可能導致您損失全部本金甚至超過本金，並不適合所有投資人。在決定參與交易前，請確實評估自身投資目的、經驗程度與風險承受能力，必要時應諮詢獨立且合格的專業顧問。</p>
        <p><b>馬丁格爾策略之特殊風險。</b>本系統支援之馬丁格爾（Martingale）加碼策略，在連續虧損時會逐關放大部位，於行情單邊延伸時可能在極短時間內造成重大虧損，或因保證金不足而遭強制平倉。系統提供之最大層數、每層手數與每日虧損上限僅為風險控制工具，並非任何形式之損失保證。</p>
        <p><b>過往績效不代表未來表現。</b>任何績效數據、勝率、報酬率、回撤或交易紀錄，均可能為示範資料、歷史回測或特定期間之結果，不保證未來可重現，亦不代表個別使用者之實際結果。</p>
        <p><b>訊號來源為獨立第三方。</b>本系統負責訊號之擷取、解析與訂單執行，對訊號內容本身之正確性、及時性、完整性或獲利能力，不作任何明示或默示之保證，亦不對依該訊號所生之任何損益負責。</p>
        <p><b>本系統為訂單執行工具。</b>所有下單指令均在使用者自己的電腦上產生，並送往使用者自行開立之券商 MetaTrader 5 帳戶執行。本公司不經手、不保管、不轉移使用者之任何資金。</p>
        <p><b>技術性中斷風險。</b>網路或電力中斷、作業系統或 MetaTrader 5 異常、券商伺服器延遲、報價跳空、流動性不足或滑價等，均可能導致訂單延遲、未成交、部分成交或以非預期價格成交，本公司對此類損失不負賠償責任。</p>
        <p><b>使用者責任。</b>所有交易決策、參數設定、資金配置與風險承擔，均由使用者自行判斷並自負盈虧。使用本系統即表示您已充分理解上述風險，並同意自行承擔一切交易結果。</p>
      </div>
    </details>
    <p class="foot-copy">官方網站 <a href="https://gold-young.com/" target="_blank" rel="noopener noreferrer">gold-young.com</a>　·　需要任何服務請私訊 IG <b>@goldyoung0927</b> 或 LINE <a href="https://line.me/ti/p/~qazasd96225" target="_blank" rel="noopener noreferrer"><b>qazasd96225</b></a>。交易涉及風險，使用本系統即表示您已理解並同意自負盈虧。</p>
  </footer>

  </div><!-- /shell-main -->
  </div><!-- /shell -->

<!-- 底部狀態列。只有會員端有 —— 訊號中心沒有 MT5 也沒有風險額度。 -->
<div class="statusbar client-only" id="statusBar">
  <span class="sb" id="sbRun">—</span>
  <span class="sb" id="sbTime">—</span>
  <span class="sb" id="sbLink">—</span>
  <span class="spacer"></span>
  <span class="sb" id="sbRisk">—</span>
  <span class="sb-meter" id="sbMeter"><i style="width:0"></i></span>
</div>
</main>

<script>
"use strict";
const ROLE = __ROLE_JSON__;
const IS_CLIENT = ROLE !== "central";
/* 密碼最短長度由後端的 membership.MIN_PASSWORD_LENGTH 帶過來，
   前後端才不會各自寫死一個數字然後慢慢對不上。 */
const PW_MIN = __PWMIN__;
const S = { status: null, stats: null, period: "today", source: "all", from: "", to: "", ladderSource: "",
            filled: false, heroShown: null };
// 趨勢線策略分頁的獨立篩選狀態——跟 S 分開，兩個分頁想看不同期間互不影響
const SE = { period: "today", source: "all", from: "", to: "", heroShown: null };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

/* 來源顯示名稱：面板上一律用交易頻率描述，不出現提供者的群組名或暱稱。
   對應表只影響「顯示」——篩選、設定、統計仍然用原始 source 字串當 key，
   所以改這裡不會動到任何資料或比對邏輯。找不到對應就原樣顯示。 */
const SOURCE_ALIAS = {
  __HIGH_FREQ_SOURCE_JSON__: "高頻交易",
  __MID_FREQ_SOURCE_JSON__: "中頻交易",
  __ULTRA_HIGH_FREQ_SOURCE_JSON__: "超高頻交易",
  __LOW_FREQ_SOURCE_JSON__: "低頻交易",
};
const srcName = (s) => SOURCE_ALIAS[s] || (s || "未標記來源");

/* entitlements 回來的是原始群組名，這裡從別名表反查，
   避免把群組名寫死在兩個地方。 */
const HIGH_SOURCE = Object.keys(SOURCE_ALIAS).find((k) => SOURCE_ALIAS[k] === "高頻交易") || "";
const MID_SOURCE  = Object.keys(SOURCE_ALIAS).find((k) => SOURCE_ALIAS[k] === "中頻交易") || "";
const ULTRA_SOURCE = Object.keys(SOURCE_ALIAS).find((k) => SOURCE_ALIAS[k] === "超高頻交易") || "";
const LOW_SOURCE  = Object.keys(SOURCE_ALIAS).find((k) => SOURCE_ALIAS[k] === "低頻交易") || "";

/* 四個頻率的固定排序 + 每一個需要哪個等級。來源表一律把這四列都畫出來，
   會員沒授權的就鎖起來 —— 「看得到、知道升級能解鎖什麼」是刻意的，
   而不是等收到第一筆訊號才讓那一列冒出來。 */
const FREQ_SOURCES = [
  { key: LOW_SOURCE,   need: "flagship" },
  { key: MID_SOURCE,   need: "trial" },
  { key: HIGH_SOURCE,  need: "advanced" },
  { key: ULTRA_SOURCE, need: "flagship" },
].filter((f) => f.key);
const TIER_LABELS = { trial: "體驗版", basic: "基礎版", advanced: "進階版", flagship: "旗艦版" };

/* 目前登入者的額度。paintAuth 每次輪詢都會更新，設定表與排程表都讀這一份，
   不各自從 auth 裡再挖一次。沒登入 = 全部沒有。 */
function noEntitlements() {
  return { sources: [], max_lot: 0.01, martingale: false, partial_close: false,
           breakeven: false, mobile_notify: false, time_pause: false,
           schedule: false, plan_days: 30 };
}
let ENT = noEntitlements();
let TIER = "";
const entHas = (name) => !!name && (ENT.sources || []).indexOf(name) !== -1;


function ids() {
  return ROLE === "central"
    ? ["line_database_path", "line_keychain_service", "line_chats", "market_mt5_files_dir",
       "ultra_strategy_enabled", "ultra_max_signals_per_day", "ultra_cooldown_seconds",
       "ultra_pending_expiry_seconds", "ultra_max_spread", "ultra_min_h1_atr", "ultra_max_h1_atr",
       "hub_url", "host", "port", "token", "interval", "shadow_mode", "cloudflare_tunnel", "cloudflared_path", "auto_start"]
    // 會員端刻意不含 hub_url / token：那兩個不該讓會員看到，也不該由前端回送
    // （token 是管理權限的通行證，送到瀏覽器等於直接把付費牆拆了）
    // 會員端只剩「訊號來源設定」一個地方要填。連線(MT5路徑/輪詢/Shadow/自動開始)與
    // 其他策略(EA)全移除,不給會員看:MT5 路徑自動偵測、auto_start 預設開、shadow 關,
    // 這些值後端各自保留,不從前端回送(少送的欄位 save_settings 會保住)。
    : ["source_profiles", "auto_schedules"];
}
function collect() {
  const out = {};
  for (const id of ids()) {
    const el = $(id);
    if (!el) continue;
    out[id] = el.type === "checkbox" ? (el.checked ? "true" : "false") : el.value;
  }
  return out;
}
function fill(settings) {
  for (const id of ids()) {
    const el = $(id);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = ["true", "1", "yes", "on"].includes(String(settings[id] || "").toLowerCase());
    else el.value = settings[id] == null ? "" : settings[id];
  }
}
async function post(path, data = {}) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || "request failed");
  return json;
}

/* ------------------------------------------------------------ formatting */
let CURRENCY = "USD";
const nf = (min, max) => new Intl.NumberFormat("zh-Hant", { minimumFractionDigits: min, maximumFractionDigits: max });
const n0 = nf(0, 0), n2 = nf(2, 2), n1 = nf(1, 1);

function money(v, opts = {}) {
  if (v == null || !isFinite(v)) return "—";
  const abs = Math.abs(v);
  const digits = opts.compact && abs >= 1000 ? 0 : 2;
  const body = (digits ? n2 : n0).format(abs);
  const sign = opts.signed ? (v >= 0 ? "+" : "-") : (v < 0 ? "-" : "");
  return sign + "$" + body;
}
function lots(v) { return v == null ? "—" : (Math.abs(v) >= 100 ? n0 : n2).format(v); }
function pct(v) { return v == null || !isFinite(v) ? "—" : n1.format(v) + "%"; }
function toneClass(v) { return v > 0 ? "up" : v < 0 ? "down" : "flat"; }
function arrow(v) { return v > 0 ? "▲" : v < 0 ? "▼" : "·"; }
function shortTime(s) { return String(s || "").replace(/^\d{4}[.\-]/, "").replace(/[.]/g, "/"); }
function dayLabel(s) { return shortTime(s).split(" ")[0]; }   // 2026.07.16 15:41 -> 07/16

/* ------------------------------------------------------------ hero number */
function setHero(value, elId, state) {
  state = state || S;
  const el = $(elId || "heroNet");
  el.className = "figure-value " + toneClass(value);
  const from = state.heroShown;
  state.heroShown = value;
  if (REDUCED || from == null || from === value) { el.textContent = money(value, { signed: true }); return; }
  const t0 = performance.now(), span = 520;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / span);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = money(from + (value - from) * eased, { signed: true });
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ------------------------------------------------------------- filtering */
/* 日期一律走「券商牆上時間」。EA 寫的 close_timestamp 用 UTC 讀出來，剛好就是表格裡
   顯示的那個時間，所以區間邊界也用 UTC getter 算——不然會出現「選了今日、但這筆
   寫著昨天」這種對不起來的狀況。 */
function brokerNow() {
  const offset = (S.stats && S.stats.account && S.stats.account.gmt_offset) || 0;
  return Math.floor(Date.now() / 1000) + offset;
}

/* 時區註記。表格顯示的是券商牆上時間，跟使用者電腦的時間差可能好幾小時
   （這台是 GMT+8、券商 GMT+3，差 5 小時）。偏移一律由 account_info 的
   gmt_offset 動態算，不寫死 —— 不同券商不一樣，同一個券商還會有夏令時間。 */
function brokerTzNote() {
  const acc = (S.stats && S.stats.account) || null;
  if (!acc || acc.gmt_offset == null) return "";
  const off = Number(acc.gmt_offset) || 0;
  // gmt_offset 是 EA 用秒回報的，實測會有 10799 這種差一秒的值，四捨五入到整點
  const hrs = Math.round(off / 3600);
  const tz = "GMT" + (hrs < 0 ? "-" : "+") + Math.abs(hrs);
  const localHrs = Math.round(-new Date().getTimezoneOffset() / 60);
  const diff = localHrs - hrs;
  if (!diff) return "時間為券商時間 " + tz;
  return "時間為券商時間 " + tz + "，比你的電腦"
       + (diff > 0 ? "慢 " : "快 ") + Math.abs(diff) + " 小時";
}
function paintTzNotes() {
  const text = brokerTzNote();
  ["tzNoteRecords", "tzNotePending"].forEach((id) => {
    const el = $(id);
    if (el) el.textContent = text;
  });
}
function dayStart(seconds) {
  const d = new Date(seconds * 1000);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000;
}
function addDays(seconds, days) { return seconds + days * 86400; }
function monthStart(seconds, monthDelta) {
  const d = new Date(seconds * 1000);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + (monthDelta || 0), 1) / 1000;
}
function weekStart(seconds) {           // 週一為一週之始
  const today = dayStart(seconds);
  const weekday = new Date(today * 1000).getUTCDay();   // 0 = 週日
  return addDays(today, -((weekday + 6) % 7));
}
function parseDateInput(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return null;
  const [y, m, d] = value.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 1000;
}
/* 回傳 [from, to)，null 代表該端無限制。
   state 預設 S（訊號跟單分頁）；趨勢線策略分頁有自己獨立的期間篩選狀態 SE，
   兩個分頁想看不同期間互不影響（例如訊號看今日、EA 策略看全部）。 */
function activeRange(state) {
  state = state || S;
  const now = brokerNow();
  switch (state.period) {
    case "today":     return [dayStart(now), null];
    case "week":      return [weekStart(now), null];
    case "lastweek":  return [addDays(weekStart(now), -7), weekStart(now)];
    case "month":     return [monthStart(now, 0), null];
    case "lastmonth": return [monthStart(now, -1), monthStart(now, 0)];
    case "custom": {
      const from = parseDateInput(state.from);
      const to = parseDateInput(state.to);
      return [from, to == null ? null : addDays(to, 1)];   // 結束日含當天
    }
    default:          return [null, null];
  }
}
function stampLabel(seconds) {
  const d = new Date(seconds * 1000);
  const pad = (v) => String(v).padStart(2, "0");
  return d.getUTCFullYear() + "/" + pad(d.getUTCMonth() + 1) + "/" + pad(d.getUTCDate());
}
function isoDate(seconds) { return new Date(seconds * 1000).toISOString().slice(0, 10); }
function rangeLabel(state) {
  const [from, to] = activeRange(state);
  if (from == null && to == null) return "全部期間";
  if (from != null && to == null) return stampLabel(from) + " 起";
  if (from == null) return stampLabel(to - 1) + " 以前";
  return stampLabel(from) + " – " + stampLabel(to - 1);
}
/* wantEaNative=false（訊號跟單分頁）只看我們自己送出的訊號單；
   wantEaNative=true（趨勢線策略分頁）只看別的 EA 自己下的單。
   兩個分頁務必互斥，不然同一筆交易會被兩邊重複算進累計損益。 */
function filtered(state, wantEaNative) {
  state = state || S;
  const all = (S.stats && S.stats.trades) || [];
  const [from, to] = activeRange(state);
  return all.filter((t) => {
    if (!!wantEaNative !== (t.mode === "ea_native")) return false;
    const stamp = t.close_timestamp || 0;
    if (from != null && stamp < from) return false;
    if (to != null && stamp >= to) return false;
    if (state.source !== "all" && (t.source || "未標記來源") !== state.source) return false;
    return true;
  });
}
function summarise(trades) {
  const wins = trades.filter((t) => t.is_win);
  const losses = trades.filter((t) => !t.is_win);
  const gw = wins.reduce((a, t) => a + t.profit, 0);
  const gl = losses.reduce((a, t) => a + t.profit, 0);
  let cum = 0, peak = 0, dd = 0;
  let ls = 0, maxLs = 0, ws = 0, maxWs = 0;   // 連敗 / 連勝
  for (const t of trades) {
    cum += t.profit; peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum);
    if (t.is_win) { ls = 0; ws += 1; maxWs = Math.max(maxWs, ws); }
    else          { ws = 0; ls += 1; maxLs = Math.max(maxLs, ls); }
  }
  return {
    total: trades.length, wins: wins.length, losses: losses.length,
    win_rate: trades.length ? (wins.length / trades.length) * 100 : 0,
    net: gw + gl, gross_win: gw, gross_loss: gl,
    profit_factor: gl ? gw / Math.abs(gl) : null,
    avg_win: wins.length ? gw / wins.length : 0,
    avg_loss: losses.length ? gl / losses.length : 0,
    best:  trades.length ? trades.reduce((a, t) => Math.max(a, t.profit), trades[0].profit) : 0,
    worst: trades.length ? trades.reduce((a, t) => Math.min(a, t.profit), trades[0].profit) : 0,
    max_dd: dd, max_loss_streak: maxLs, max_win_streak: maxWs,
    volume: trades.reduce((a, t) => a + t.volume, 0),
  };
}

/* ---------------------------------------------------------------- charts */
const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) if (attrs[k] != null) node.setAttribute(k, attrs[k]);
  return node;
}
function niceTicks(min, max, count) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 0.001; v += step) out.push(Number(v.toFixed(6)));
  return out;
}
function axisLabel(v) {
  const abs = Math.abs(v);
  if (abs >= 1000000) return (v / 1000000).toFixed(abs % 1000000 ? 1 : 0) + "M";
  if (abs >= 1000) return (v / 1000).toFixed(abs % 1000 ? 1 : 0) + "k";
  return n0.format(v);
}

function renderEquity(trades, ids, state) {
  ids = ids || { svg: "equityChart", wrap: "equityWrap", tip: "equityTip" };
  state = state || S;
  const svg = $(ids.svg);
  const wrap = $(ids.wrap);
  svg.textContent = "";
  // 儀表板面板裡給高一點，讓圖填滿卡片；趨勢線分頁還是舊版單欄排版，維持 268。
  const inPanel = !!wrap.closest(".panel-body");
  const W = Math.max(320, wrap.clientWidth), H = inPanel ? 340 : 268;
  const pad = { t: 18, r: 74, b: 30, l: 58 };
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("height", H);

  if (!trades.length) {
    wrap.querySelector(".tip").classList.remove("is-on");
    svg.appendChild(Object.assign(svgEl("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "tick" }),
      { textContent: rangeLabel(state) + " 沒有已平倉交易" }));
    return;
  }

  const pts = [];
  let cum = 0;
  trades.forEach((t, i) => { cum += t.profit; pts.push({ i, cum, t }); });

  const lo = Math.min(0, ...pts.map((p) => p.cum));
  const hi = Math.max(0, ...pts.map((p) => p.cum));
  const ticks = niceTicks(lo, hi, 4);
  const yLo = Math.min(lo, ticks[0]), yHi = Math.max(hi, ticks[ticks.length - 1]);
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const X = (i) => pad.l + (pts.length === 1 ? plotW / 2 : (i / (pts.length - 1)) * plotW);
  const Y = (v) => pad.t + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;

  for (const tick of ticks) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: pad.l + plotW, y1: Y(tick), y2: Y(tick), class: "grid-line" }));
    const label = svgEl("text", { x: pad.l - 9, y: Y(tick) + 3.5, "text-anchor": "end", class: "tick" });
    label.textContent = axisLabel(tick);
    svg.appendChild(label);
  }
  const zeroY = Y(0);
  svg.appendChild(svgEl("line", { x1: pad.l, x2: pad.l + plotW, y1: zeroY, y2: zeroY, class: "axis-line" }));

  // 面積在 0 以上塗獲利色、以下塗虧損色；用 clipPath 沿 0 軸切開
  const defs = svgEl("defs", {});
  const mk = (id, y, h) => {
    const cp = svgEl("clipPath", { id });
    cp.appendChild(svgEl("rect", { x: pad.l, y, width: plotW, height: Math.max(0, h) }));
    return cp;
  };
  defs.appendChild(mk("clipUp", pad.t, zeroY - pad.t));
  defs.appendChild(mk("clipDown", zeroY, pad.t + plotH - zeroY));
  svg.appendChild(defs);

  const line = pts.map((p, i) => (i ? "L" : "M") + X(p.i) + " " + Y(p.cum)).join(" ");
  const area = line + " L" + X(pts[pts.length - 1].i) + " " + zeroY + " L" + X(0) + " " + zeroY + " Z";
  svg.appendChild(svgEl("path", { d: area, fill: "var(--win)", "fill-opacity": ".10", "clip-path": "url(#clipUp)" }));
  svg.appendChild(svgEl("path", { d: area, fill: "var(--loss)", "fill-opacity": ".10", "clip-path": "url(#clipDown)" }));

  const final = pts[pts.length - 1].cum;
  const stroke = final >= 0 ? "var(--win)" : "var(--loss)";
  const path = svgEl("path", { d: line, fill: "none", stroke, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" });
  svg.appendChild(path);
  if (!REDUCED && path.getTotalLength) {
    const len = path.getTotalLength();
    path.style.strokeDasharray = len;
    path.style.strokeDashoffset = len;
    path.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: 720, easing: "cubic-bezier(.3,.8,.4,1)", fill: "forwards" });
  }

  // 每一筆平倉都是一個點位；點太密就退回只畫線，滑鼠移過去還是讀得到每一點
  const gap = pts.length > 1 ? plotW / (pts.length - 1) : plotW;
  if (gap >= 9) {
    for (const p of pts) {
      svg.appendChild(svgEl("circle", {
        cx: X(p.i), cy: Y(p.cum), r: 4,
        fill: p.t.is_win ? "var(--win)" : "var(--loss)",
        stroke: "var(--card)", "stroke-width": 2,
      }));
    }
  }

  const last = pts[pts.length - 1];
  svg.appendChild(svgEl("circle", { cx: X(last.i), cy: Y(last.cum), r: 5, fill: stroke, stroke: "var(--card)", "stroke-width": 2 }));
  const endLabel = svgEl("text", { x: X(last.i) + 11, y: Y(last.cum) + 4, class: "end-label", fill: "var(--ink)" });
  endLabel.textContent = money(last.cum, { signed: true, compact: true });
  svg.appendChild(endLabel);

  // 日期軸：每換一天標一次，標在該日第一筆上，所以每個標籤都對得到具體的點。
  // 天數多到擠不下就等距抽稀，寧可少標也不要疊字。
  const dayFirst = [];
  let seenDay = null;
  pts.forEach((p) => {
    const day = dayLabel(p.t.close_time);
    if (day !== seenDay) { dayFirst.push({ i: p.i, day }); seenDay = day; }
  });
  const thin = Math.max(1, Math.ceil(dayFirst.length / Math.max(2, Math.floor(plotW / 68))));
  let lastX = -Infinity;
  dayFirst.filter((_, n) => n % thin === 0).forEach((mark) => {
    const x = X(mark.i);
    if (x - lastX < 52) return;               // 還是太近就跳過這一個
    lastX = x;
    const label = svgEl("text", {
      x, y: H - 10, class: "tick-x",
      "text-anchor": mark.i === 0 ? "start" : "middle",
    });
    label.textContent = mark.day;
    svg.appendChild(label);
  });

  const cursor = svgEl("line", { y1: pad.t, y2: pad.t + plotH, class: "axis-line", opacity: 0 });
  const marker = svgEl("circle", { r: 5, fill: stroke, stroke: "var(--card)", "stroke-width": 2, opacity: 0 });
  svg.appendChild(cursor); svg.appendChild(marker);

  const tip = $(ids.tip);
  const hit = svgEl("rect", { x: pad.l, y: pad.t, width: plotW, height: plotH, fill: "transparent", style: "cursor:crosshair" });
  svg.appendChild(hit);
  const show = (evt) => {
    const box = svg.getBoundingClientRect();
    const px = ((evt.clientX - box.left) / box.width) * W;
    let best = pts[0], bestGap = Infinity;
    for (const p of pts) { const gap = Math.abs(X(p.i) - px); if (gap < bestGap) { best = p; bestGap = gap; } }
    cursor.setAttribute("x1", X(best.i)); cursor.setAttribute("x2", X(best.i)); cursor.setAttribute("opacity", 1);
    marker.setAttribute("cx", X(best.i)); marker.setAttribute("cy", Y(best.cum)); marker.setAttribute("opacity", 1);
    // 均注/EA自動的單沒有馬丁關卡（level 是 null），null+1 在 JS 會變成 1，
    // 會誤顯示「第 1 關」，所以要明確判斷
    const lvText = best.t.mode === "flat" ? "均注" : best.t.mode === "ea_native" ? "EA 自動"
      : "第 " + (best.t.level + 1) + " 關";
    tip.innerHTML =
      '<div class="tip-h">' + esc(best.t.close_time) + "</div>" +
      '<div class="tip-row"><span>本筆</span><b class="' + toneClass(best.t.profit) + '">' +
        arrow(best.t.profit) + " " + money(best.t.profit, { signed: true }) + "</b></div>" +
      '<div class="tip-row"><span>累計</span><b class="' + toneClass(best.cum) + '">' + money(best.cum, { signed: true }) + "</b></div>" +
      '<div class="tip-row"><span>手數</span><b>' + lots(best.t.volume) + " · " + lvText + "</b></div>";
    tip.classList.add("is-on");
    const left = Math.min(Math.max(0, (X(best.i) / W) * box.width - 70), box.width - 155);
    tip.style.left = left + "px";
    tip.style.top = Math.max(0, (Y(best.cum) / H) * box.height - 78) + "px";
  };
  hit.addEventListener("mousemove", show);
  hit.addEventListener("mouseleave", () => {
    tip.classList.remove("is-on"); cursor.setAttribute("opacity", 0); marker.setAttribute("opacity", 0);
  });
}

function renderBars(trades) {
  const svg = $("tradeBars");
  const wrap = $("barsWrap");
  svg.textContent = "";
  const W = Math.max(280, wrap.clientWidth), H = 196;
  const pad = { t: 14, r: 10, b: 26, l: 54 };
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("height", H);
  if (!trades.length) {
    const t = svgEl("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "tick" });
    t.textContent = rangeLabel() + " 沒有資料";
    svg.appendChild(t); return;
  }

  const values = trades.map((t) => t.profit);
  const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values), 3);
  const yLo = Math.min(0, ...values, ticks[0]), yHi = Math.max(0, ...values, ticks[ticks.length - 1]);
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const Y = (v) => pad.t + plotH - ((v - yLo) / (yHi - yLo || 1)) * plotH;
  const band = plotW / trades.length;
  const barW = Math.max(2, Math.min(24, band - 2));   // 2px 留白由 surface 負責分隔

  for (const tick of ticks) {
    svg.appendChild(svgEl("line", { x1: pad.l, x2: pad.l + plotW, y1: Y(tick), y2: Y(tick), class: "grid-line" }));
    const label = svgEl("text", { x: pad.l - 8, y: Y(tick) + 3.5, "text-anchor": "end", class: "tick" });
    label.textContent = axisLabel(tick);
    svg.appendChild(label);
  }
  const zeroY = Y(0);
  svg.appendChild(svgEl("line", { x1: pad.l, x2: pad.l + plotW, y1: zeroY, y2: zeroY, class: "axis-line" }));

  const tip = $("barsTip");
  trades.forEach((t, i) => {
    const x = pad.l + i * band + (band - barW) / 2;
    const top = Math.min(Y(t.profit), zeroY);
    const h = Math.max(1.5, Math.abs(Y(t.profit) - zeroY));
    const color = t.is_win ? "var(--win)" : "var(--loss)";
    // 資料端 4px 圓角、基線端切齊
    const r = Math.min(4, barW / 2, h);
    const up = t.profit >= 0;
    const d = up
      ? "M" + x + " " + (top + h) + " V" + (top + r) + " Q" + x + " " + top + " " + (x + r) + " " + top +
        " H" + (x + barW - r) + " Q" + (x + barW) + " " + top + " " + (x + barW) + " " + (top + r) + " V" + (top + h) + " Z"
      : "M" + x + " " + top + " V" + (top + h - r) + " Q" + x + " " + (top + h) + " " + (x + r) + " " + (top + h) +
        " H" + (x + barW - r) + " Q" + (x + barW) + " " + (top + h) + " " + (x + barW) + " " + (top + h - r) + " V" + top + " Z";
    const bar = svgEl("path", { d, fill: color });
    svg.appendChild(bar);

    const hit = svgEl("rect", { x: pad.l + i * band, y: pad.t, width: band, height: plotH, fill: "transparent" });
    hit.addEventListener("mouseenter", () => {
      tip.innerHTML =
        '<div class="tip-h">' + esc(t.close_time) + "</div>" +
        '<div class="tip-row"><span>' + (t.is_win ? "贏" : "輸") + "</span><b class=\"" + toneClass(t.profit) + '">' +
          arrow(t.profit) + " " + money(t.profit, { signed: true }) + "</b></div>" +
        '<div class="tip-row"><span>手數</span><b>' + lots(t.volume) + "</b></div>" +
        '<div class="tip-row"><span>關卡</span><b>' +
          (t.mode === "flat" ? "均注" : t.mode === "ea_native" ? "EA 自動" : "第 " + (t.level + 1) + " 關") +
        "</b></div>";
      tip.classList.add("is-on");
      const box = svg.getBoundingClientRect();
      tip.style.left = Math.min(Math.max(0, ((pad.l + i * band) / W) * box.width - 66), box.width - 150) + "px";
      tip.style.top = "0px";
    });
    hit.addEventListener("mouseleave", () => tip.classList.remove("is-on"));
    svg.appendChild(hit);
  });
}

/* 各來源績效：小倍數。每個來源一張卡，各自獨立算損益曲線與統計。
   刻意不做成「一張圖多條彩色線」——來源識別色會跟藍賺紅賠的語意打架
   （驗證器實測橘色距離贏紅只有 ΔE 12，會被讀成獲利色）。分開畫就沒這問題。 */
function miniCurve(trades, width, height) {
  if (!trades.length) return "";
  const pad = 4;
  const pts = [];
  let cum = 0;
  trades.forEach((t, i) => { cum += t.profit; pts.push({ i, cum }); });
  const lo = Math.min(0, ...pts.map((p) => p.cum));
  const hi = Math.max(0, ...pts.map((p) => p.cum));
  const span = (hi - lo) || 1;
  const X = (i) => pad + (pts.length === 1 ? (width - pad * 2) / 2 : (i / (pts.length - 1)) * (width - pad * 2));
  const Y = (v) => pad + (height - pad * 2) - ((v - lo) / span) * (height - pad * 2);
  const line = pts.map((p, i) => (i ? "L" : "M") + X(p.i).toFixed(1) + " " + Y(p.cum).toFixed(1)).join(" ");
  const zero = Y(0).toFixed(1);
  const color = pts[pts.length - 1].cum >= 0 ? "var(--win)" : "var(--loss)";
  return '<svg viewBox="0 0 ' + width + " " + height + '" width="100%" height="' + height +
      '" preserveAspectRatio="none" aria-hidden="true">' +
    '<path d="' + line + " L" + X(pts.length - 1).toFixed(1) + " " + zero + " L" + X(0).toFixed(1) + " " + zero +
      ' Z" fill="' + color + '" fill-opacity=".10" />' +
    '<line x1="' + pad + '" x2="' + (width - pad) + '" y1="' + zero + '" y2="' + zero +
      '" stroke="var(--rule)" stroke-width="1" />' +
    '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />' +
  "</svg>";
}

function renderSourcePerformance(trades, sourceRows) {
  const box = $("sourcePerf");
  const groups = new Map();
  for (const t of trades) {
    const name = t.source || "未標記來源";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(t);
  }
  // 已設定但這區間還沒成交的來源也要列出來，否則會以為它不存在。
  // 但只算「有在跟單」的 —— 停用的來源(含歷史遺留的測試來源)不該佔版面，
  // 也不該讓下面的「單一來源」判斷失效。
  for (const row of sourceRows || []) {
    if (row.enabled && !groups.has(row.source)) groups.set(row.source, []);
  }
  // 一個實例只跟一個來源時，「各來源績效」跟整頁統計是同一份資料，擺著只是重複。
  // 連同上方的區塊標題與來源篩選一起收起來，版面才乾淨。多來源時自動恢復。
  const single = groups.size <= 1;
  const perfHead = box.previousElementSibling;
  if (perfHead && perfHead.classList.contains("section-head")) {
    perfHead.style.display = single ? "none" : "";
  }
  const srcFilter = $("filterSource");
  if (srcFilter) srcFilter.style.display = single ? "none" : "";
  if (single) { box.innerHTML = ""; return; }

  if (!groups.size) {
    box.innerHTML = '<div class="card"><div class="empty">' + esc(rangeLabel()) + " 沒有任何來源的交易</div></div>";
    return;
  }
  const cfgOf = {};
  for (const row of sourceRows || []) cfgOf[row.source] = row;

  box.innerHTML = [...groups.entries()]
    .sort((a, b) => b[1].reduce((s, t) => s + t.profit, 0) - a[1].reduce((s, t) => s + t.profit, 0))
    .map(([name, list]) => {
      const sum = summarise(list);
      const cfg = cfgOf[name] || {};
      // EA 自己下的單不在 source_settings 裡（那張表只管我們自己送出的訊號單），
      // 徽章改看交易本身的 mode——同一個來源的交易 mode 一定一致
      const isEaNative = !cfg.mode && list[0] && list[0].mode === "ea_native";
      const badge = cfg.mode === "flat" ? "均注" : cfg.mode === "martingale" ? "馬丁" : (isEaNative ? "EA 自動" : "");
      // 報酬率用帳戶餘額當分母。沒有「每個來源分配多少資金」這種設定，
      // 硬掰一個數字出來會比不顯示更糟。
      const bal = Number((S.stats && S.stats.account && S.stats.account.balance) || 0);
      const roi = bal > 0 ? (sum.net / bal) * 100 : null;
      const running = cfg.enabled !== false && cfg.configured;

      return '<div class="source-card' + (S.source === name ? " is-on" : "") +
          '" role="button" tabindex="0" data-pick-source="' + esc(name) + '">' +
        '<div class="sc-head"><span class="sc-name">' + esc(srcName(name)) + "</span>" +
          (badge ? '<span class="tag tag-lv">' + badge + "</span>" : "") +
          '<span class="sc-run ' + (running ? "on" : "off") + '">' +
            '<i class="dot"></i>' + (running ? "運行中" : "已停止") + "</span>" +
        "</div>" +
        '<div class="sc-net ' + toneClass(sum.net) + '">' +
          (list.length ? money(sum.net, { signed: true, compact: true }) : "—") + "</div>" +
        '<div class="sc-chart">' +
          (list.length ? miniCurve(list, 260, 54) : '<div class="sc-blank">此區間無成交</div>') + "</div>" +
        '<dl class="sc-stats">' +
          "<div><dt>勝率</dt><dd>" + (list.length ? pct(sum.win_rate) : "—") + "</dd></div>" +
          '<div><dt>報酬率</dt><dd class="' + toneClass(sum.net) + '">' +
            (roi == null || !list.length ? "—"
              : (roi > 0 ? "+" : "") + roi.toFixed(2) + "%") + "</dd></div>" +
          "<div><dt>基準手數</dt><dd>" + (cfg.base_lot ? lots(cfg.base_lot) : "—") + "</dd></div>" +
          '<div><dt>贏 / 輸</dt><dd><span class="up">' + sum.wins + '</span> / <span class="down">' + sum.losses + "</span></dd></div>" +
        "</dl>" +
        '<div class="sc-foot">' +
          '<span class="sc-meta">' + sum.total + " 筆 · 最大連敗 " + sum.max_loss_streak + "</span>" +
          '<button type="button" class="btn sc-manage" data-manage="' + esc(name) + '">管理</button>' +
        "</div>" +
      "</div>";
    }).join("");
}

/* 趨勢線策略分頁的「訊號類型績效」：EA 一顆策略裡有好幾種進場邏輯
   （強多頭多單1／多單回踩／多單重入…各自獨立算勝率），跟「各來源績效」外觀一致，
   但分組依據是 comment 去掉結尾的 K 棒序號（"強多頭多單1_1234" -> "強多頭多單1"），
   不是訊號來源——這裡本來就只有一個來源，再用來源分組沒有意義。
   純唯讀卡片，不能點來篩選：這些名稱不是 SE.source 的合法值（SE.source 篩的是
   「哪個 EA」，不是「哪種訊號」），點了也篩不出東西，所以故意用 <div> 不用 <button>。 */
function eaSignalType(signalId) {
  return String(signalId || "").replace(/_\d+$/, "") || "未分類";
}
function renderEaBreakdown(trades, elId) {
  const box = $(elId || "eaBreakdown");
  const groups = new Map();
  for (const t of trades) {
    const name = eaSignalType(t.signal_id);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(t);
  }
  if (!groups.size) {
    box.innerHTML = '<div class="card"><div class="empty">' + esc(rangeLabel(SE)) + " 沒有任何訊號類型的交易</div></div>";
    return;
  }
  box.innerHTML = [...groups.entries()]
    .sort((a, b) => b[1].reduce((s, t) => s + t.profit, 0) - a[1].reduce((s, t) => s + t.profit, 0))
    .map(([name, list]) => {
      const sum = summarise(list);
      return '<div class="source-card">' +
        '<div class="sc-head"><span class="sc-name">' + esc(name) + "</span></div>" +
        '<div class="sc-net ' + toneClass(sum.net) + '">' + money(sum.net, { signed: true, compact: true }) + "</div>" +
        '<div class="sc-chart">' + miniCurve(list, 260, 54) + "</div>" +
        '<dl class="sc-stats">' +
          "<div><dt>筆數</dt><dd>" + sum.total + "</dd></div>" +
          "<div><dt>勝率</dt><dd>" + pct(sum.win_rate) + "</dd></div>" +
          '<div><dt>贏 / 輸</dt><dd><span class="up">' + sum.wins + '</span> / <span class="down">' + sum.losses + "</span></dd></div>" +
          "<div><dt>最大連敗</dt><dd>" + sum.max_loss_streak + "</dd></div>" +
        "</dl>" +
      "</div>";
    }).join("");
}

/* ----------------------------------------------------------- table & co. */
function renderTiles(sum, cycles, elId) {
  const pf = sum.profit_factor;
  const tiles = [
    { dt: "單筆勝率", dd: pct(sum.win_rate), meter: sum.win_rate,
      small: "贏 " + sum.wins + " · 輸 " + sum.losses },
  ];
  // 「回合」是馬丁格爾的概念（連續虧損直到一次獲利），EA 自己算風險倉位、
  // 沒有這個概念，cycles 傳 null 時整格跳過，不要放一個「—」在那裡佔位。
  if (cycles !== undefined) {
    tiles.push({ dt: "已完成回合", dd: cycles ? cycles.completed : "—",
      small: cycles ? ("獲利回合 " + cycles.profitable + " · 回合勝率 " + pct(cycles.rate)) : "" });
  }
  tiles.push(
    { dt: "獲利因子", dd: pf == null ? "—" : n2.format(pf),
      small: pf == null ? "尚無虧損單" : (pf >= 1 ? "每虧 $1 賺 $" + n2.format(pf) : "低於 1 代表淨虧損"), tone: pf == null ? "" : (pf >= 1 ? "up" : "down") },
    { dt: "最大回撤", dd: money(-sum.max_dd, { compact: true }), tone: sum.max_dd ? "down" : "",
      small: "從高點下來最深的一段" },
    { dt: "最大連敗", dd: sum.max_loss_streak + " 筆", small: "連續輸最多的一段" },
    { dt: "平均獲利", dd: money(sum.avg_win, { compact: true }), tone: "up", small: "每筆贏單平均" },
    { dt: "平均虧損", dd: money(sum.avg_loss, { compact: true }), tone: "down", small: "每筆輸單平均" },
    { dt: "累計手數", dd: lots(sum.volume) + " 手", small: sum.total + " 筆已平倉" },
  );
  $(elId || "tiles").innerHTML = tiles.map((t) => '' +
    '<div class="tile">' +
      "<dt>" + t.dt + "</dt>" +
      '<dd class="' + (t.tone || "") + '">' + t.dd + "</dd>" +
      (t.meter != null ? '<div class="meter"><span style="width:' + Math.max(0, Math.min(100, t.meter)) + '%"></span></div>' : "") +
      (t.small ? "<small>" + t.small + "</small>" : "") +
    "</div>").join("");
}

function renderRecords(trades, ids, state, repaint) {
  ids = ids || { box: "records" };
  state = state || S;
  repaint = repaint || paintStats;
  const box = $(ids.box);
  if (!trades.length) {
    const wantEa = ids.box === "eaRecords";
    const hasAny = ((S.stats && S.stats.trades) || []).some((t) => (t.mode === "ea_native") === wantEa);
    box.innerHTML = '<div class="empty"><b>' + esc(rangeLabel(state)) + ' 沒有已平倉交易</b>' +
      (hasAny
        ? '這個期間還沒有成交<br><button class="btn" id="' + ids.box + 'JumpAll" style="margin-top:12px">看全部歷史戰績</button>'
        : "收到訊號並平倉後，紀錄會出現在這裡") +
      "</div>";
    const jump = $(ids.box + "JumpAll");
    if (jump) jump.onclick = () => selectPeriod(state, ids.periodIds, "all", repaint);
    return;
  }
  // 累計欄要在「目前這個分頁看得到的清單」裡重算，不能直接用後端的 t.cumulative——
  // 那是訊號單跟 EA 單混在一起、依時間排序的全域累計，拆成兩個分頁各自看會對不起來。
  let cum = 0;
  const withCum = trades.map((t) => { cum += t.profit; return { t, cum }; });
  const rows = withCum.slice().reverse().map(({ t, cum }) => '' +
    "<tr>" +
      '<td class="mono">' + esc(t.close_time) + "</td>" +
      '<td><span class="tag ' + (t.is_win ? "tag-win" : "tag-loss") + '">' + (t.is_win ? "▲ 贏" : "▼ 輸") + "</span></td>" +
      '<td class="' + (t.side === "buy" ? "side-buy" : "side-sell") + '">' + (t.side === "buy" ? "買進" : "賣出") + "</td>" +
      '<td class="num mono">' + lots(t.volume) + "</td>" +
      // 均注來源沒有關卡；EA 自己下的單也沒有——那不是我們的馬丁層級，顯示「第 N 關」會誤導
      '<td><span class="tag tag-lv">' +
        (t.mode === "flat" ? "均注" : t.mode === "ea_native" ? "EA 自動" : "第 " + (t.level + 1) + " 關") +
        (t.parts > 1 ? " · 分 " + t.parts + " 段" : "") + "</span></td>" +
      '<td class="num mono">' + n2.format(t.entry_price) + "</td>" +
      '<td class="num mono">' + n2.format(t.exit_price) + "</td>" +
      '<td class="num ' + toneClass(t.profit) + '" style="font-weight:600">' + money(t.profit, { signed: true }) + "</td>" +
      '<td class="num ' + toneClass(cum) + ' mono">' + money(cum, { signed: true, compact: true }) + "</td>" +
      "<td>" + esc(t.source ? srcName(t.source) : "—") + "</td>" +
      // 分批平倉時每段各有成交編號，用 position_id 才是穩定的那一張單
      '<td class="mono">' + esc(t.position_id || t.ticket) + "</td>" +
    "</tr>").join("");
  box.innerHTML =
    "<table><thead><tr>" +
      "<th>平倉時間</th><th>結果</th><th>方向</th><th class=\"num\">手數</th><th>關卡</th>" +
      "<th class=\"num\">進場</th><th class=\"num\">出場</th><th class=\"num\">損益</th>" +
      "<th class=\"num\">累計</th><th>訊號來源</th><th>單號</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table>";
}

function durationText(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + " 小時 " + String(m).padStart(2, "0") + " 分";
  if (m) return m + " 分 " + String(sec).padStart(2, "0") + " 秒";
  return sec + " 秒";
}
function cancelStateTag(p) {
  const state = String(p.cancel_state || "none");
  if (state === "requested") return '<span class="tag tag-warn">等待單號</span>';
  if (state === "command_sent") return '<span class="tag tag-warn">撤單確認中</span>';
  if (state === "failed_retry") return '<span class="tag tag-warn">撤單重試中</span>';
  return '<span class="muted">—</span>';
}
function paintPendingChip(n, running) {
  const chip = $("chipPending");
  if (!chip) return;
  chip.className = "chip " + (!running ? "is-off" : n ? "is-warn" : "is-live");
  chip.lastElementChild.textContent = !running ? "掛單 —" : n ? ("待成交 " + n + " 筆") : "無待成交";
}
function renderPending(pending, running) {
  const box = $("pending");
  const n = pending.length;
  // 狀態一句話帶過(不重複撤單規則);規則只在下方空狀態講一次。
  $("pendingSummary").textContent = n
    ? n + " 筆等待進場"
    : (running ? "沒有等待中的掛單" : "服務未啟動");
  paintPendingChip(n, running);

  if (!n) {
    box.innerHTML = '<div class="empty"><b>' +
      (running ? "目前沒有單在等進場" : "服務未啟動，看不到掛單") + "</b>" +
      (running ? "撤單依 LINE 引用回覆指定的原始報單，或掛單逾 4 小時未成交自動撤" : "按上方「開始跟單」後，等待進場的單會出現在這裡") + "</div>";
    return;
  }
  const rows = pending.map((p) => {
    const tracking = p.tracked === false
      ? '<span class="tag tag-warn">未追蹤</span>'
      : '<span class="tag tag-win">已追蹤</span>';
    return "<tr>" +
      '<td class="' + (p.side === "buy" ? "side-buy" : "side-sell") + '">' + (p.side === "buy" ? "買進" : "賣出") + "</td>" +
      "<td>" + esc(p.symbol || "XAUUSD") + "</td>" +
      '<td class="num mono">' + (p.entry_price ? n2.format(p.entry_price) : "—") + "</td>" +
      '<td class="num mono">' + (p.sl ? n2.format(p.sl) : "—") + "</td>" +
      '<td class="num mono">' + (p.tp ? n2.format(p.tp) : "—") + "</td>" +
      '<td class="mono">' + (p.elapsed_seconds == null ? esc(p.setup_time || "—") : durationText(p.elapsed_seconds)) + "</td>" +
      "<td>" + tracking + "</td>" +
      "<td>" + cancelStateTag(p) + "</td>" +
      "<td>" + esc(p.source ? srcName(p.source) : "—") + "</td>" +
      '<td class="mono">' + esc(p.ticket == null ? "尚未取得" : p.ticket) + "</td>" +
    "</tr>";
  }).join("");
  const untracked = pending.filter((p) => p.tracked === false).length;
  box.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
      "<th>方向</th><th>商品</th><th class=\"num\">掛單價</th><th class=\"num\">停損</th>" +
      "<th class=\"num\">停利</th><th>已等待</th><th>追蹤狀態</th><th>撤單狀態</th>" +
      "<th>訊號來源</th><th>單號</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
    (untracked
      ? '<div class="notice" style="margin:12px 14px 14px">' +
        "<div><b>有 " + untracked + " 張單在 MT5 上，但會員端沒有在管</b>" +
        "這些單目前無法被 LINE 引用撤單命中，成交後的輸贏也不會計入馬丁層級。按「停止」再「開始跟單」" +
        "會重新認領它們。</div></div>"
      : "");
}

function renderPositions(positions, currency, ids) {
  ids = ids || { box: "positions", summary: "posSummary" };
  const box = $(ids.box);
  const floating = positions.reduce((a, p) => a + p.profit, 0);
  $(ids.summary).innerHTML = positions.length
    ? positions.length + " 筆持倉中 · 浮動 <b class=\"" + toneClass(floating) + "\">" + money(floating, { signed: true }) + "</b>"
    : "目前沒有持倉";
  if (!positions.length) {
    // 上方 summary 已顯示「目前沒有持倉」,這裡不重複,只給提示。
    box.innerHTML = '<div class="empty"><b>還沒有進行中的部位</b>收到訊號並成交後會出現在這裡</div>';
    return;
  }
  const rows = positions.map((p) => '' +
    "<tr>" +
      '<td class="' + (p.side === "buy" ? "side-buy" : "side-sell") + '">' + (p.side === "buy" ? "買進" : "賣出") + "</td>" +
      "<td>" + esc(p.symbol) + "</td>" +
      '<td class="num mono">' + lots(p.volume) + "</td>" +
      '<td class="num mono">' + n2.format(p.entry_price) + "</td>" +
      '<td class="num mono">' + (p.current_price ? n2.format(p.current_price) : "—") + "</td>" +
      '<td class="num mono">' + (p.sl ? n2.format(p.sl) : "—") + "</td>" +
      '<td class="num mono">' + (p.tp ? n2.format(p.tp) : "—") + "</td>" +
      '<td class="num ' + toneClass(p.profit) + '" style="font-weight:600">' + arrow(p.profit) + " " + money(p.profit, { signed: true }) + "</td>" +
      "<td>" + esc(p.source ? srcName(p.source) : "—") + "</td>" +
    "</tr>").join("");
  box.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
      "<th>方向</th><th>商品</th><th class=\"num\">手數</th><th class=\"num\">進場</th>" +
      "<th class=\"num\">現價</th><th class=\"num\">停損</th><th class=\"num\">停利</th>" +
      "<th class=\"num\">浮動損益</th><th>訊號來源</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>";
}

/* 有設定每群模式時，階梯改成一個來源一組，用上方的來源鈕切換。
   預設顯示層級最高的那一群——會員最該先看到的就是壓最深的那一關。 */
function ladderForSource(row) {
  const lots = [];
  const max = Math.max(1, Math.min(row.max_level || 5, 12));
  for (let i = 0; i < max; i++) lots.push(round2(row.base_lot * Math.pow(row.multiplier || 2, i)));
  return lots;
}
function round2(v) { return Math.round(v * 100) / 100; }

function renderLadder(mg, cycles, sources) {
  // 只列「有在跟單」的來源。停用的(含歷史遺留的 test / TEST-* / 鄭…)列出來只是
  // 一長串「此來源已停用，不跟單」的雜訊，對操作沒有任何幫助。
  const configured = (sources || []).filter((s) => s.configured && s.enabled);
  const tabs = $("ladderTabs");

  if (!configured.length) {                       // 沒設定每群模式 → 維持單一全域階梯
    tabs.innerHTML = "";
    $("ladderTitle").textContent = "馬丁階梯";
    $("flatNote").hidden = true;
    $("rungs").hidden = false;
    return renderRungs((mg && mg.ladder) || [], (mg && mg.level) || 0, mg, cycles);
  }

  if (!configured.some((s) => s.source === S.ladderSource)) {
    const martins = configured.filter((s) => s.mode === "martingale");
    const pick = martins.slice().sort((a, b) => b.level - a.level)[0] || configured[0];
    S.ladderSource = pick.source;
  }
  const row = configured.find((s) => s.source === S.ladderSource);

  // 只有一個來源就不必畫分頁 —— 沒有東西可切換
  if (configured.length <= 1) {
    tabs.innerHTML = "";
  } else {
  tabs.innerHTML = configured.map((s) => {
    const on = s.source === S.ladderSource ? "is-on" : "";
    const off = s.enabled ? "" : "is-off";
    const badge = s.mode === "flat" ? "均注" : "第 " + (s.level + 1) + " 關";
    return '<button type="button" class="src-tab ' + on + " " + off + '" data-src="' + esc(s.source) + '">' +
      esc(srcName(s.source)) + '<span class="badge">' + badge + "</span></button>";
  }).join("");
  }

  $("ladderTitle").textContent = row.mode === "flat" ? "均注模式" : "馬丁階梯";
  if (row.mode === "flat") {
    $("rungs").hidden = true;
    const note = $("flatNote");
    note.hidden = false;
    note.innerHTML =
      "<div><div class=\"big\">" + lots(row.base_lot) + " 手</div>" +
      "<p>這個來源每一筆都下固定手數，輸贏都不加碼、不進關卡。</p></div>";
    $("ladderLevel").textContent = row.enabled ? "固定手數，不進關" : "此來源已停用，不跟單";
    $("nextLot").textContent = lots(row.base_lot) + " 手";
    $("consecLoss").textContent = "—";
    $("openCycle").textContent = "均注無回合";
    return;
  }
  $("flatNote").hidden = true;
  $("rungs").hidden = false;
  renderRungs(ladderForSource(row), row.level, {
    enabled: true, multiplier: row.multiplier,
    next_lot: ladderForSource(row)[Math.min(row.level, row.max_level - 1)],
    consecutive_losses: row.losses,
  }, cycles, row.enabled ? "" : "此來源已停用，不跟單");
}

function renderRungs(ladder, level, mg, cycles, overrideNote) {
  const box = $("rungs");
  if (!ladder.length) { box.innerHTML = ""; return; }
  // 手數是每關 ×2 的指數序列，所以階高走對數刻度：一階 = 一次加倍。
  // 用線性畫的話第一關會縮成一條細線，「目前在哪一關」反而看不見。
  const base = ladder[0] > 0 ? ladder[0] : 1;
  let steps = ladder.map((lot) => (lot > 0 ? Math.log2(lot / base) + 1 : 1));
  if (!steps.every((v) => isFinite(v) && v > 0)) steps = ladder.map((lot) => lot);  // 自訂手數不成等比時退回線性
  const maxStep = Math.max(...steps);
  box.innerHTML = ladder.map((lot, i) => {
    const height = 16 + (steps[i] / maxStep) * 84;
    const lit = i <= level ? "is-lit" : "";  // 含目前這關：金色代表「已經押上去的」
    const current = i === level ? "is-current" : "";
    return '<div class="rung ' + lit + " " + current + '">' +
      '<div class="rung-bar" style="height:' + height + '%"></div>' +
      '<div class="rung-lot">' + lots(lot) + "</div>" +
      '<div class="rung-tag">第 ' + (i + 1) + " 關</div>" +
    "</div>";
  }).join("");

  $("ladderLevel").textContent = overrideNote ? overrideNote : (mg.enabled
    ? "第 " + (level + 1) + " 關 / 共 " + ladder.length + " 關 · 倍數 ×" + n1.format(mg.multiplier)
    : "馬丁格爾已關閉，固定手數");
  $("nextLot").textContent = lots(mg.next_lot) + " 手";
  $("consecLoss").textContent = (mg.consecutive_losses || 0) + " 筆";
  const openEl = $("openCycle");
  if (cycles && cycles.open_trades) {
    openEl.innerHTML = cycles.open_trades + " 筆 · <span class=\"" + toneClass(cycles.open_profit) + "\">" +
      money(cycles.open_profit, { signed: true, compact: true }) + "</span>";
  } else {
    openEl.textContent = "回合已結清";
  }
}

/* 每群下單設定表。

   四個頻率(低頻/中頻/高頻/超高頻)一律整列畫出來，不管這台機器收過訊號沒有 ——
   會員買到哪裡、再上去能解鎖什麼，寫在他每天都會看的地方，比等第一筆訊號進來
   才讓那一列冒出來清楚。沒授權的整列鎖住：所有欄位 disabled、標出需要的等級，
   點不動也存不進去(syncSourceProfiles 一律把鎖住的來源寫成 enabled:false)。

   這四列以外，過去收過訊號的來源仍然照舊列在後面，不受鎖定影響。

   刻意沒有「新增來源」：來源清單完全由訊號中心決定(會員收得到什麼，Hub 說了算)，
   讓會員自己打群組名只會打錯字 —— 打錯的名字不會對到任何訊號，那一列從此靜靜
   躺在表上什麼也不做，而使用者會以為自己設定好了。 */

/* 這個來源可以選哪些「止盈處理」。中頻訊號一單只有一個止盈，分批平倉根本沒有
   東西可以分，所以中頻不給那個選項。ok=false 的會 render 成 disabled 的灰選項，
   讓會員看得到「升級可以解鎖什麼」，但選不下去。 */
function tpOptions(source) {
  const partial   = { v: "partial",   label: "分批平倉", ok: !!ENT.partial_close, need: "advanced" };
  const breakeven = { v: "breakeven", label: "保本移損", ok: !!ENT.breakeven,     need: "advanced" };
  const single    = { v: "single",    label: "單一點位", ok: true,                need: "trial" };
  return source === MID_SOURCE ? [single, breakeven] : [partial, breakeven, single];
}
function optionHtml(opt, current) {
  const label = opt.ok ? opt.label : opt.label + "（需" + (TIER_LABELS[opt.need] || "更高等級") + "）";
  return '<option value="' + opt.v + '"' + (opt.v === current ? " selected" : "") +
    (opt.ok ? "" : " disabled") + ">" + esc(label) + "</option>";
}
/* 把後端存著的 tp_mode 收斂成「這個來源、這個等級真的選得到」的值。
   例：中頻存著舊的 partial → 顯示成單一點位；沒有保本移損權益 → 退回單一點位。
   選不到就取第一個可用的，絕不讓下拉停在一個 disabled 的選項上。 */
function pickTpMode(source, stored) {
  const opts = tpOptions(source);
  const hit = opts.find((o) => o.v === stored && o.ok);
  if (hit) return hit.v;
  const first = opts.find((o) => o.ok);
  return first ? first.v : "single";
}

function blankSourceRow(name) {
  return { source: name, trades: 0, configured: false, enabled: false, mode: "flat",
           tp_mode: name === MID_SOURCE ? "single" : "partial",
           base_lot: 0.01, multiplier: 2, max_level: 5, partial_ratios: [],
           // 預設 3 美元:黃金的停損多半抓 6~10 美元，走一半再保本是常見做法。
           // 預設 0 等於「選了保本移損卻永遠不保本」，那是最糟的預設值。
           breakeven_distance: 3, max_daily_profit: 0, max_daily_loss: 0 };
}

function sourceRowHtml(r, meta, locked, need) {
  const dis = locked ? " disabled" : "";
  const mgOk = !!ENT.martingale;
  const mode = mgOk ? (r.mode === "martingale" ? "martingale" : "flat") : "flat";
  const tp = pickTpMode(r.source, r.tp_mode);
  const maxLot = ENT.max_lot;
  const lotMax = (maxLot == null || !(maxLot > 0)) ? "" : ' max="' + maxLot + '"';
  return '<tr data-source-row="' + esc(r.source) + '"' +
      (locked ? ' data-locked="1" class="is-locked"' : "") + ">" +
    '<td><div class="src-name">' + esc(srcName(r.source)) + "</div>" +
      '<div class="src-meta">' + esc(meta) + "</div>" +
      (locked ? '<div class="src-lock"><i class="lockmark"></i>需' +
                esc(TIER_LABELS[need] || "更高等級") + "</div>" : "") +
    "</td>" +
    '<td><input type="checkbox" class="sp-enabled"' +
      (r.enabled && !locked ? " checked" : "") + dis + " /></td>" +
    '<td><select class="sp-mode"' + dis + ">" +
      '<option value="martingale"' + (mode === "martingale" ? " selected" : "") +
        (mgOk ? "" : " disabled") + ">馬丁" + (mgOk ? "" : "（需進階版）") + "</option>" +
      '<option value="flat"' + (mode === "flat" ? " selected" : "") + ">均注</option>" +
    "</select></td>" +
    '<td><input type="number" class="sp-base" step="0.01" min="0.01"' + lotMax +
      ' value="' + r.base_lot + '"' + dis + " /></td>" +
    '<td><input type="number" class="sp-mult" step="0.1" min="1" value="' + r.multiplier + '"' + dis + " /></td>" +
    '<td><input type="number" class="sp-max" step="1" min="1" max="12" value="' + r.max_level + '"' + dis + " /></td>" +
    '<td class="sp-tp-cell"><select class="sp-tpmode"' + dis + ">" +
      tpOptions(r.source).map((o) => optionHtml(o, tp)).join("") +
    "</select>" +
    '<input type="text" class="sp-lots" value="' + lotsToText(r.partial_ratios, r.base_lot) + '"' + dis +
      ' title="每個止盈平多少手，例 0.01/0.01/0.01（加總 = 這單總手數）" />' +
    '<label class="sp-be-wrap"><span>保本距離（美元）</span>' +
      '<input type="number" class="sp-be" step="0.1" min="0"' + dis +
      ' value="' + (Number(r.breakeven_distance) || 0) + '"' +
      ' title="價格朝有利方向走到「進場價 ± 這個美元價差」時，把停損移到進場價保本。0 = 不啟動距離保本" /></label>' +
    '<span class="sp-ratio-note"></span></td>' +
    '<td><input type="number" class="sp-profit" step="1" min="0" value="' + (r.max_daily_profit || 0) + '"' + dis +
      ' title="當日該來源獲利達此金額就今日停跟；0 = 不限" /></td>' +
    '<td><input type="number" class="sp-loss" step="1" min="0" value="' + (r.max_daily_loss || 0) + '"' + dis +
      ' title="當日該來源虧損達此金額就今日停跟；0 = 不限" /></td>' +
  "</tr>";
}

function renderSourceSettings(rows) {
  const box = $("sourceSettings");
  const byName = {};
  rows.forEach((r) => { byName[r.source] = r; });

  // 先四個頻率(固定順序)，再接上其他收過訊號/手動新增的來源
  const ordered = [];
  const claimed = {};
  for (const f of FREQ_SOURCES) {
    claimed[f.key] = 1;
    const r = byName[f.key] || blankSourceRow(f.key);
    const locked = !entHas(f.key);
    let meta;
    if (f.key === LOW_SOURCE) {
      // 低頻的訊號源還在建置，先把位置與權益顯示出來，不假裝它已經在發訊號
      meta = "訊號源建置中，開放後自動生效";
    } else if (byName[f.key]) {
      meta = "已成交 " + r.trades + " 筆" + (r.configured ? "" : " · 尚未個別設定（用預設）");
    } else {
      meta = "尚未收過訊號";
    }
    ordered.push({ r, meta, locked, need: f.need });
  }
  for (const r of rows) {
    if (claimed[r.source]) continue;
    // 這四個頻率以外的來源(早期手動新增、或收過訊號的舊群組名)不做等級鎖 ——
    // 真正擋得住的是 Hub，它根本不會把未授權來源的訊號送下來。
    ordered.push({ r, locked: false, need: null,
      meta: "已成交 " + r.trades + " 筆" + (r.configured ? "" : " · 尚未個別設定（用預設）") });
  }

  box.innerHTML =
    '<table class="src-table"><thead><tr>' +
      "<th>訊號來源</th><th>跟單</th><th>模式</th><th>基礎手數</th><th>馬丁倍數</th><th>關卡數</th><th>止盈處理</th>" +
      "<th>每日止盈</th><th>每日止損</th>" +
    "</tr></thead><tbody>" +
    ordered.map((o) => sourceRowHtml(o.r, o.meta, o.locked, o.need)).join("") +
    "</tbody></table>";

  // box 是持久元素(每次只換 innerHTML),事件監聽器只綁一次,否則每次重繪都疊加、
  // syncSourceProfiles 會被同一個事件呼叫好幾次(記憶體與重複觸發)。用委派 + 旗標守門。
  if (!box.dataset.bound) {
    box.addEventListener("change", syncSourceProfiles);
    box.addEventListener("input", syncSourceProfiles);
    box.dataset.bound = "1";
  }
  syncSourceProfiles();
}


/* ── 分批手數 ────────────────────────────────────────────────────────
   分批平倉直接讓會員填「每段實際手數」(例如 0.01/0.01/0.01),不再用比例。
   三段加起來就是這一單的總手數 —— 基礎手數會自動 = 總和。每段送到 MT5 都必須
   ≥ 0.01 手(券商最低)。內部換算成佔比(手數/總和)存起來,馬丁加碼時比例才會跟著放大。 */
// toFixed(2) 才跟後端 Python round(x,2) 一致(Math.round 會把 0.015 進成 0.02)。
function pround2(x) { return Number(x.toFixed(2)); }
// 把存起來的佔比 × 基礎手數還原成「每段手數」字串,給既有來源顯示用。
function lotsToText(ratios, base) {
  base = pround2(base || 0);
  if (Array.isArray(ratios) && ratios.length >= 2 && base >= 0.02) {
    const c0 = pround2(base * ratios[0]);
    const c1 = pround2(base * ratios[1]);
    const tail = pround2(base - c0 - c1);
    if (c0 >= 0.01 && c1 >= 0.01 && tail >= 0.01) return c0 + "/" + c1 + "/" + tail;
  }
  return "0.01/0.01/0.01";
}
function parseLots(text) {
  const parts = String(text || "").replace(/，/g, ",").split(/[\/,\s]+/).map((s) => s.trim()).filter(Boolean);
  const nums = parts.map(Number);
  if (nums.length < 2 || nums.some((n) => !isFinite(n) || n <= 0)) return null;
  return nums.map((n) => pround2(n));
}
/* 檢查每個「分批平倉」來源:每段手數都要 ≥ 0.01(MT5 最低);基礎手數自動 = 三段總和。
   回傳擋存錯誤字串陣列(空=可儲存)。鎖住的來源不檢查 —— 它根本不會跟單。 */
function validateSourceLots() {
  const errors = [];
  for (const row of document.querySelectorAll("[data-source-row]")) {
    const note = row.querySelector(".sp-ratio-note");
    const mode = row.querySelector(".sp-tpmode").value;
    if (row.dataset.locked === "1" || mode !== "partial") {
      if (note) {
        // 保本移損把觸發條件講白:價格走到「進場價 ± 保本距離」就把停損移到進場價。
        // 沒填距離(0)= 距離觸發沒開,提醒他填,不要讓人以為選了就會保本。
        const be = row.querySelector(".sp-be");
        const d = mode === "breakeven" ? (Number(be && be.value) || 0) : 0;
        if (row.dataset.locked === "1" || mode !== "breakeven") {
          note.textContent = ""; note.className = "sp-ratio-note";
        } else if (d > 0) {
          note.textContent = "價格觸及保本距離（進場後 " + d + " 美元）→ 停損移到進場價";
          note.className = "sp-ratio-note is-ok";
        } else {
          note.textContent = "未設保本距離：退回「觸及第一個止盈才保本」";
          note.className = "sp-ratio-note is-warn";
        }
      }
      continue;
    }
    const name = row.dataset.sourceRow;
    const lots = parseLots(row.querySelector(".sp-lots").value);
    if (!lots) {
      if (note) { note.textContent = "格式錯誤，請填像 0.01/0.01/0.01"; note.className = "sp-ratio-note is-bad"; }
      errors.push(`「${srcName(name)}」的分批手數格式不對，請填像 0.01/0.01/0.01`);
      continue;
    }
    const bad = lots.filter((l) => l < 0.01).length;
    if (bad) {
      if (note) { note.textContent = "每段都要 ≥ 0.01 手（MT5 最低）"; note.className = "sp-ratio-note is-bad"; }
      errors.push(`「${srcName(name)}」分批每段都要 ≥ 0.01 手，目前有 ${bad} 段低於 0.01`);
      continue;
    }
    const sum = pround2(lots.reduce((a, b) => a + b, 0));
    if (note) { note.textContent = `每單 ${sum} 手 → 分 ${lots.join(" / ")} 手 ✓`; note.className = "sp-ratio-note is-ok"; }
  }
  return errors;
}

function syncSourceProfiles() {
  const out = {};
  for (const row of document.querySelectorAll("[data-source-row]")) {
    // 鎖住的來源(等級沒買到)一律寫成不跟單,而且每個欄位都維持 disabled。
    // 前端擋只是體驗;真正的閘門在 Hub —— 它根本不會送那些訊號下來。
    const locked = row.dataset.locked === "1";
    const mode = row.querySelector(".sp-mode").value;
    const martingale = mode === "martingale";
    // 均注沒有倍數與關卡可言，把欄位鎖住比留著讓人填了沒作用好
    row.querySelector(".sp-mult").disabled = locked || !martingale;
    row.querySelector(".sp-max").disabled = locked || !martingale;
    // 分批手數只有「分批平倉」用得到,保本距離只有「保本移損」用得到,其餘藏起來。
    // 分批時基礎手數 = 三段總和,自動帶入且不讓改(會員只填分批手數);內部存成佔比,
    // 馬丁加碼時比例才會跟著放大。
    const tpMode = row.querySelector(".sp-tpmode").value;
    const partial = tpMode === "partial";
    const lotsInput = row.querySelector(".sp-lots");
    const beWrap = row.querySelector(".sp-be-wrap");
    const beInput = row.querySelector(".sp-be");
    const spBase = row.querySelector(".sp-base");
    if (lotsInput) lotsInput.style.display = partial ? "" : "none";
    if (beWrap) beWrap.style.display = tpMode === "breakeven" ? "" : "none";
    const entry = {
      enabled: !locked && row.querySelector(".sp-enabled").checked,
      mode,
      tp_mode: tpMode,
      // 距離一律存著,不因為切走模式就把會員填過的數字丟掉
      breakeven_distance: Math.max(0, parseFloat(beInput && beInput.value) || 0),
      max_daily_loss: parseFloat(row.querySelector(".sp-loss").value) || 0,
      max_daily_profit: parseFloat(row.querySelector(".sp-profit").value) || 0,
    };
    if (partial && lotsInput) {
      const lots = parseLots(lotsInput.value);
      if (lots && lots.every((l) => l >= 0.01)) {
        const sum = pround2(lots.reduce((a, b) => a + b, 0));
        entry.base_lot = sum;
        entry.partial_ratios = lots.map((l) => l / sum);
        spBase.value = sum;
        spBase.disabled = true;             // 基礎手數自動 = 三段總和
      } else {
        entry.base_lot = parseFloat(spBase.value) || 0.01;   // 無效手數由 validate 擋存
        spBase.disabled = true;
      }
    } else {
      spBase.disabled = locked;
      entry.base_lot = parseFloat(spBase.value) || 0.01;
    }
    if (martingale) {
      entry.multiplier = parseFloat(row.querySelector(".sp-mult").value) || 2;
      entry.max_level = parseInt(row.querySelector(".sp-max").value, 10) || 5;
    }
    out[row.dataset.sourceRow] = entry;
  }
  $("source_profiles").value = JSON.stringify(out);
  validateSourceLots();   // 更新每列的最低手數提示(紅/綠)
}

/* ── 自動排程 ────────────────────────────────────────────────────────
   每天幾點開始跟單、幾點停。跨午夜(21:00→02:00)是合法的 —— 黃金本來就是通宵盤,
   只支援 start < end 的話「晚上開盤跟到凌晨」根本設不出來。

   等級只決定「有沒有這個功能」(進階版以上才有),不再分單一/多組/進階 —— 有的話
   幾組、能不能挑星期都一樣。SCHEDULE_LIMIT 是面板的實用上限,不是等級差異。
   後端 active_schedules() 會用同一套規則再箝制一次 —— 前端灰掉只是體驗,不是閘門。 */
const SCHEDULE_LIMIT = __SCHEDULE_LIMIT__;
const DAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];   // 對齊 Python weekday(): 週一=0

function readSchedules() {
  try {
    const raw = JSON.parse($("auto_schedules").value || "[]");
    return Array.isArray(raw) ? raw.filter((x) => x && typeof x === "object") : [];
  } catch (e) { return []; }
}
function scheduleSignature(list) {
  return JSON.stringify(list) + "|" + (ENT.schedule ? 1 : 0);
}
function schedRowHtml(s, i) {
  const days = Array.isArray(s.days) ? s.days : [];
  const on = (d) => !days.length || days.indexOf(d) !== -1;   // 空陣列 = 每天
  return '<tr data-sched="' + i + '">' +
    '<td><input type="checkbox" class="sc-on"' + (s.enabled === false ? "" : " checked") + " /></td>" +
    '<td><input type="time" class="sc-start" value="' + esc(s.start || "09:00") + '" /></td>' +
    '<td><input type="time" class="sc-end" value="' + esc(s.end || "23:30") + '" /></td>' +
    '<td class="sc-days">' +
      DAY_LABELS.map((label, d) =>
        '<button type="button" class="sc-day' + (on(d) ? " is-on" : "") + '" data-day="' + d + '"' +
        ' aria-pressed="' + (on(d) ? "true" : "false") + '">' + label + "</button>").join("") +
    "</td>" +
    '<td><button type="button" class="btn btn-quiet sc-del">刪除</button></td>' +
  "</tr>";
}
function renderSchedules() {
  const box = $("scheduleBox");
  if (!box) return;
  const list = readSchedules();
  const sig = scheduleSignature(list);
  // 每秒輪詢都會叫到這裡,內容沒變就不重畫 —— 否則會把正在填的時間欄位洗掉
  if (box.dataset.signature === sig) return;
  box.dataset.signature = sig;

  if (!ENT.schedule) {
    box.innerHTML = '<div class="sched-lock"><i class="lockmark"></i>' +
      "自動排程需<b>進階版</b>以上。升級後可以設定每天自動開始／停止跟單的時間。</div>";
    return;
  }

  const rows = list.slice(0, SCHEDULE_LIMIT);
  const full = rows.length >= SCHEDULE_LIMIT;
  box.innerHTML =
    '<table class="sched-table"><thead><tr>' +
      "<th>啟用</th><th>開始跟單</th><th>停止跟單</th><th>星期</th><th></th>" +
    "</tr></thead><tbody>" +
    (rows.length
      ? rows.map((s, i) => schedRowHtml(s, i)).join("")
      : '<tr class="sched-empty"><td colspan="5">還沒有排程 —— 目前完全手動。按下面的「新增排程」加一組。</td></tr>') +
    "</tbody></table>" +
    '<div class="src-add">' +
      '<button type="button" class="btn" id="addSchedule"' + (full ? " disabled" : "") +
        ">＋ 新增排程</button>" +
      (full ? '<span class="sched-count">已達上限 ' + SCHEDULE_LIMIT + " 組</span>" : "") +
    "</div>";

  if (!box.dataset.bound) {
    box.dataset.bound = "1";
    // 按鈕的 click 不會冒出 input/change,所以按鈕這條路要自己叫一次自動儲存
    box.addEventListener("click", (evt) => {
      const day = evt.target.closest(".sc-day");
      if (day && !day.disabled) {
        const nowOn = !day.classList.contains("is-on");
        day.classList.toggle("is-on", nowOn);
        day.setAttribute("aria-pressed", nowOn ? "true" : "false");
        syncSchedules(); autoSaveSoon(); return;
      }
      const del = evt.target.closest(".sc-del");
      if (del) {
        const row = del.closest("[data-sched]");
        if (row) row.remove();
        syncSchedules(); autoSaveSoon(); return;
      }
      if (evt.target.closest("#addSchedule")) addSchedule();
    });
    box.addEventListener("change", syncSchedules);
    box.addEventListener("input", syncSchedules);
  }
}
function collectSchedules() {
  const out = [];
  for (const row of document.querySelectorAll("[data-sched]")) {
    const days = [...row.querySelectorAll(".sc-day.is-on")].map((b) => Number(b.dataset.day));
    out.push({
      enabled: row.querySelector(".sc-on").checked,
      start: row.querySelector(".sc-start").value || "",
      end: row.querySelector(".sc-end").value || "",
      days: days.length === 7 ? [] : days,      // 七天全勾 = 每天
    });
  }
  return out;
}
function syncSchedules() {
  if (!ENT.schedule) return;                        // 沒這功能就別去動已經存著的值
  const list = collectSchedules();
  $("auto_schedules").value = JSON.stringify(list);
  $("scheduleBox").dataset.signature = scheduleSignature(list);
}
function addSchedule() {
  const list = collectSchedules();
  if (!ENT.schedule || list.length >= SCHEDULE_LIMIT) return;
  // 預設帶「早上九點到晚上十一點半」,不是空白 —— 要會員自己從零填兩個時間
  // 才看得到東西,第一次用一定卡住。
  list.push({ enabled: true, start: "09:00", end: "23:30", days: [] });
  $("auto_schedules").value = JSON.stringify(list);
  $("scheduleBox").dataset.signature = "";       // 強制重畫
  renderSchedules();
  autoSaveSoon();
}
/* 排程說明列。每秒都更新,但它跟表格是分開的元素,不會打斷正在填的欄位。 */
function paintScheduleHint(snap) {
  const hint = $("scheduleHint");
  if (!hint) return;
  if (!ENT.schedule) {
    hint.textContent = "目前方案沒有自動排程：跟單的開始與停止完全由你手動控制。";
    return;
  }
  const active = snap ? snap.schedule_active : null;
  if (active == null) {
    hint.textContent = "還沒有生效中的排程，跟單維持手動控制。時間用這台電腦的本機時間；"
      + "跨午夜（例如 21:00 → 02:00）是可以的。";
    return;
  }
  hint.textContent = "排程生效中 · 現在" + (active ? "在跟單時段內" : "不在跟單時段內")
    + "。時段開始會自動開始跟單、時段結束會自動停止；你在時段內手動按了停止就會維持停止，"
    + "不會被自動拉回來。時間用這台電腦的本機時間。";
}

/* -------------------------------------------------------------- painting */
/* ------------------------------------------------------------- dashboard */

/* 小折線。給統計卡用，沒有座標軸、沒有互動，只表達走勢方向。
   series 少於兩點就回傳空字串 —— 一個點連不成線，硬畫會是一條假的水平線。 */
function sparkPath(series, w, h, pad) {
  if (!series || series.length < 2) return null;
  let lo = Infinity, hi = -Infinity;
  for (const v of series) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (lo === hi) { lo -= 1; hi += 1; }
  const n = series.length;
  const x = (i) => (i / (n - 1)) * w;
  const y = (v) => pad + (1 - (v - lo) / (hi - lo)) * (h - pad * 2);
  let d = `M${x(0).toFixed(1)},${y(series[0]).toFixed(1)}`;
  for (let i = 1; i < n; i++) d += ` L${x(i).toFixed(1)},${y(series[i]).toFixed(1)}`;
  return { line: d, area: d + ` L${w},${h} L0,${h} Z` };
}

function sparkSvg(series, tone) {
  const W = 240, H = 42;
  const p = sparkPath(series, W, H, 6);
  if (!p) return "";
  const stroke = {
    down: "var(--loss)", gold: "var(--gold-lit)", up: "var(--win)",
    cyan: "var(--accent-cyan)", violet: "var(--accent-violet)", blue: "var(--accent-blue)",
  }[tone] || "var(--win)";
  const gid = "sg" + Math.random().toString(36).slice(2, 8);
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${stroke}" stop-opacity=".28"/>
      <stop offset="1" stop-color="${stroke}" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${p.area}" fill="url(#${gid})"/>
    <path d="${p.line}" fill="none" stroke="${stroke}" stroke-width="1.6"
          stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

/* 頂部五張統計卡。
   每個數字與每條 sparkline 都是從實際成交紀錄算出來的，沒有裝飾用的假資料；
   算不出序列的那張（跟單來源）就不畫線。 */
function renderDashStats(sum, account, trades, srcRows) {
  const host = $("dashStats");
  if (!host) return;

  // 由舊到新的累積損益。統計卡的三條線都從這裡衍生。
  const cum = [];
  let run = 0;
  for (const t of trades) { run += t.profit; cum.push(round2(run)); }

  // 資產曲線 = 期末淨值往回推。沒有帳戶資料就不畫。
  let equitySeries = null;
  if (account && cum.length > 1) {
    const end = account.equity;
    equitySeries = cum.map((c) => round2(end - (cum[cum.length - 1] - c)));
  }

  // 滾動勝率：每一筆之後重算一次到目前為止的勝率
  let wr = null;
  if (trades.length > 1) {
    let w = 0;
    wr = trades.map((t, i) => { if (t.is_win) w += 1; return (w / (i + 1)) * 100; });
  }

  // 回撤序列：距離歷史高點的差距，畫成往下的走勢
  let ddSeries = null;
  if (cum.length > 1) {
    let peak = 0;
    ddSeries = cum.map((c) => { peak = Math.max(peak, c); return -(peak - c); });
  }

  const active = (srcRows || []).filter((r) => r.enabled !== false).length;
  const total = (srcRows || []).length;

  const cards = [
    { k: "總資產", v: account ? money(account.equity, { compact: true }) : "—",
      sub: account ? "餘額 " + money(account.balance, { compact: true }) : "尚未連上 MT5",
      series: equitySeries, tone: "cyan", accent: "cyan" },
    { k: "已實現損益", v: money(sum.net, { signed: true, compact: true }),
      sub: sum.total + " 筆已平倉", cls: toneClass(sum.net),
      series: cum.length > 1 ? cum : null, tone: sum.net >= 0 ? "up" : "down",
      accent: sum.net >= 0 ? "win" : "loss" },
    { k: "跟單來源", v: String(active),
      sub: total ? "共 " + total + " 組 · 停用 " + (total - active) : "尚未設定",
      series: null, accent: "violet" },
    { k: "勝率", v: sum.total ? pct(sum.win_rate) : "—",
      sub: sum.total ? "贏 " + sum.wins + " · 輸 " + sum.losses : "尚無平倉紀錄",
      cls: "gold", series: wr, tone: "gold", accent: "gold" },
    { k: "最大回撤", v: sum.max_dd ? "-" + money(sum.max_dd, { compact: true }).replace("$", "$") : "—",
      sub: "從高點下來最深的一段", cls: sum.max_dd ? "down" : "",
      series: ddSeries, tone: "down", accent: "loss" },
  ];

  host.innerHTML = cards.map((c) => `
    <div class="dstat dstat--${c.accent || "gold"}">
      <dt>${esc(c.k)}</dt>
      <dd class="${c.cls || ""}">${esc(c.v)}</dd>
      <span class="sub">${esc(c.sub)}</span>
      ${c.series ? sparkSvg(c.series, c.tone) : '<span class="spark"></span>'}
    </div>`).join("");
}

/* 勝負圓環。用兩段 stroke-dasharray 畫，不需要任何圖表函式庫。 */
function renderDonut(sum) {
  const host = $("donut");
  const legend = $("donutLegend");
  if (!host || !legend) return;

  const R = 58, C = 2 * Math.PI * R, SW = 16;
  const total = sum.total || 0;
  const winFrac = total ? sum.wins / total : 0;

  host.innerHTML = `<svg width="146" height="146" viewBox="0 0 146 146" role="img"
      aria-label="勝負比例：贏 ${sum.wins} 筆、輸 ${sum.losses} 筆">
    <circle cx="73" cy="73" r="${R}" fill="none" stroke="var(--sunk)" stroke-width="${SW}"/>
    ${total ? `
    <circle cx="73" cy="73" r="${R}" fill="none" stroke="var(--loss)" stroke-width="${SW}"
            stroke-dasharray="${C}" stroke-dashoffset="0"/>
    <circle cx="73" cy="73" r="${R}" fill="none" stroke="var(--win)" stroke-width="${SW}"
            stroke-dasharray="${(C * winFrac).toFixed(2)} ${C}" stroke-linecap="butt"/>` : ""}
  </svg>
  <div class="donut-mid"><b>${total}</b><span>總交易</span></div>`;

  const pctOf = (n) => total ? ((n / total) * 100).toFixed(1) + "%" : "—";
  legend.innerHTML = `
    <div><i style="background:var(--win)"></i><div>
      <dt>獲利交易</dt><dd>${sum.wins}<em>${pctOf(sum.wins)}</em></dd>
    </div></div>
    <div><i style="background:var(--loss)"></i><div>
      <dt>虧損交易</dt><dd>${sum.losses}<em>${pctOf(sum.losses)}</em></dd>
    </div></div>
    <div><i style="background:var(--gold-lit)"></i><div>
      <dt>盈虧比</dt><dd>${sum.profit_factor == null ? "—" : sum.profit_factor.toFixed(2)}</dd>
    </div></div>`;

  // 甜甜圈下方補幾個「績效分析」區塊沒列的極值，順便把這格的空白填掉
  const extra = $("donutExtra");
  if (extra) {
    const money = (n) => (n == null || !isFinite(n)) ? "—"
      : (n > 0 ? "+" : n < 0 ? "-" : "") + "$" + Math.abs(n).toFixed(2);
    const rows = [
      ["最佳單筆", money(sum.best), sum.best > 0 ? "up" : ""],
      ["最差單筆", money(sum.worst), sum.worst < 0 ? "down" : ""],
      ["最長連勝", (sum.max_win_streak || 0) + " 筆", ""],
      ["最長連敗", (sum.max_loss_streak || 0) + " 筆", ""],
    ];
    extra.innerHTML = rows.map(([k, v, cls]) => `
      <div class="mock-row">
        <span class="k">${k}</span>
        <span class="v ${cls}">${esc(v)}</span>
      </div>`).join("");
  }
}

/* ================================================================== kline
   K 線圖。資料來自會員自己那台 MT5（EA 寫的 rates_*.json），經 /api/market
   進來。自己畫的原因見 CSS 那段註解。

   佈局：上面 72% 畫價格，下面 22% 畫成交量，中間留 6% 當間隙。
   ================================================================== */
const K = {
  tf: "M15",
  data: null,          // { bars, digits, symbol, available, source }
  ma: { 5: true, 20: true, 60: true },
  vol: true,
  hover: null,         // 滑鼠停在第幾根
  loading: false,
};

const TF_LABEL = { M1: "1 分鐘", M5: "5 分鐘", M15: "15 分鐘",
                   H1: "1 小時", H4: "4 小時", D1: "日線" };

/* 移動平均。前 n-1 根沒有值，回 null 讓線段斷開，不要硬畫成 0。 */
function movingAvg(bars, n) {
  const out = new Array(bars.length).fill(null);
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].c;
    if (i >= n) sum -= bars[i - n].c;
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/* 價格軸的刻度：抓一個「好看的」間隔（1/2/2.5/5 × 10^n），
   不然會出現 2337.183 這種刻度。 */
function niceTicks(lo, hi, target) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const raw = span / Math.max(2, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(v);
  return out;
}

function fmtBarTime(t, tf) {
  const d = new Date(t * 1000);
  const p2 = (n) => String(n).padStart(2, "0");
  if (tf === "D1") return d.getFullYear() + "/" + p2(d.getMonth() + 1) + "/" + p2(d.getDate());
  if (tf === "H4" || tf === "H1")
    return p2(d.getMonth() + 1) + "/" + p2(d.getDate()) + " " + p2(d.getHours()) + ":" + p2(d.getMinutes());
  return p2(d.getHours()) + ":" + p2(d.getMinutes());
}

function renderKline() {
  const wrap = $("klineWrap"), svg = $("klineChart");
  if (!wrap || !svg) return;

  const d = K.data;
  let bars = (d && d.bars) || [];
  wrap.classList.toggle("is-empty", bars.length < 2);
  if (bars.length < 2) { svg.innerHTML = ""; renderKlineRead(); return; }

  const box = wrap.getBoundingClientRect();
  const W = Math.max(320, Math.round(box.width));
  const H = Math.max(300, Math.round(box.height));
  const padR = 62, padL = 8, padT = 10, padB = 22;
  const plotW = W - padL - padR;

  const showVol = K.vol;
  const volH = showVol ? Math.round((H - padT - padB) * 0.22) : 0;
  const gap = showVol ? Math.round((H - padT - padB) * 0.06) : 0;
  const priceH = H - padT - padB - volH - gap;

  // 價格範圍要把畫出來的 MA 也包進去，不然線會跑到框外
  let lo = Infinity, hi = -Infinity;
  for (const b of bars) { if (b.l < lo) lo = b.l; if (b.h > hi) hi = b.h; }
  const mas = {};
  for (const n of [5, 20, 60]) {
    if (!K.ma[n] || bars.length < n) continue;
    mas[n] = movingAvg(bars, n);
    for (const v of mas[n]) { if (v == null) continue; if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  if (!(hi > lo)) { hi = lo + 1; lo -= 1; }
  const padPx = (hi - lo) * 0.06;
  lo -= padPx; hi += padPx;

  // 只畫塞得下的最後幾根。每根低於 ~4px 就糊成一片色塊，不如少畫一點。
  const fit = Math.max(30, Math.min(bars.length, Math.floor(plotW / 7)));
  bars = bars.slice(-fit);

  const n = bars.length;
  const slot = plotW / n;                     // 每根佔的寬度（含間隙）
  const bw = Math.max(1, Math.min(14, slot * 0.68));
  const X = (i) => padL + slot * (i + 0.5);
  const Y = (v) => padT + priceH - ((v - lo) / (hi - lo)) * priceH;

  let volMax = 0;
  if (showVol) for (const b of bars) if (b.v > volMax) volMax = b.v;
  const volTop = padT + priceH + gap;
  const VY = (v) => volTop + volH - (volMax > 0 ? (v / volMax) * volH : 0);

  const dg = (d && d.digits) || 2;
  const fx = (v) => v.toFixed(dg);
  const parts = [];

  // ── 格線與價格刻度 ──
  for (const v of niceTicks(lo, hi, 5)) {
    const y = Y(v);
    if (y < padT - 1 || y > padT + priceH + 1) continue;
    parts.push(`<line x1="${padL}" y1="${y.toFixed(1)}" x2="${padL + plotW}" y2="${y.toFixed(1)}" class="k-grid"/>`);
    parts.push(`<text x="${W - padR + 7}" y="${(y + 3.5).toFixed(1)}" class="k-axis">${fx(v)}</text>`);
  }

  // ── 成交量 ──
  if (showVol) {
    for (let i = 0; i < n; i++) {
      const b = bars[i];
      const up = b.c >= b.o;
      const y = VY(b.v), h = Math.max(0.6, volTop + volH - y);
      parts.push(`<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" class="k-vol ${up ? "up" : "down"}"/>`);
    }
  }

  // ── 蠟燭 ──
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    const up = b.c >= b.o;
    const x = X(i);
    const yo = Y(b.o), yc = Y(b.c);
    const top = Math.min(yo, yc);
    const bh = Math.max(1, Math.abs(yc - yo));   // 十字線也要看得見，至少 1px
    parts.push(`<line x1="${x.toFixed(1)}" y1="${Y(b.h).toFixed(1)}" x2="${x.toFixed(1)}" y2="${Y(b.l).toFixed(1)}" class="k-wick ${up ? "up" : "down"}"/>`);
    parts.push(`<rect x="${(x - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" class="k-body ${up ? "up" : "down"}"/>`);
  }

  // ── 均線 ──
  for (const nn of [5, 20, 60]) {
    const series = mas[nn];
    if (!series) continue;
    let dpath = "", pen = false;
    for (let i = 0; i < n; i++) {
      const v = series[i];
      if (v == null) { pen = false; continue; }
      dpath += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
      pen = true;
    }
    if (dpath) parts.push(`<path d="${dpath.trim()}" class="k-ma" style="stroke:var(--ma-${nn})"/>`);
  }

  // ── 最新價：右側標籤 + 貫穿虛線 ──
  const last = bars[n - 1];
  const ly = Y(last.c);
  const lastUp = last.c >= last.o;
  parts.push(`<line x1="${padL}" y1="${ly.toFixed(1)}" x2="${padL + plotW}" y2="${ly.toFixed(1)}" class="k-lastline ${lastUp ? "up" : "down"}"/>`);
  parts.push(`<rect x="${(W - padR + 2).toFixed(1)}" y="${(ly - 9).toFixed(1)}" width="${(padR - 4).toFixed(1)}" height="18" rx="3" class="k-lastbg ${lastUp ? "up" : "down"}"/>`);
  parts.push(`<text x="${W - padR + 7}" y="${(ly + 3.5).toFixed(1)}" class="k-lasttxt">${fx(last.c)}</text>`);

  // ── 時間軸 ──
  const every = Math.max(1, Math.round(n / Math.max(3, Math.floor(plotW / 92))));
  for (let i = n - 1; i >= 0; i -= every) {
    const x = X(i);
    if (x < padL + 18 || x > padL + plotW - 18) continue;
    parts.push(`<text x="${x.toFixed(1)}" y="${H - 6}" class="k-axis k-axis--x">${esc(fmtBarTime(bars[i].t, K.tf))}</text>`);
  }

  // ── 十字準星（滑鼠移到才顯示）──
  parts.push(`<line id="kCrossV" class="k-cross" x1="0" y1="${padT}" x2="0" y2="${padT + priceH + gap + volH}" opacity="0"/>`);

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.innerHTML = parts.join("");

  // 命中測試用的幾何資料，交給滑鼠事件
  K.geom = { X, Y, padL, plotW, slot, n, W, H };
  K.view = bars;   // 實際畫出來的那幾根，讀數列與 hover 都對這份
  renderKlineRead();
}

/* OHLC 讀數列。停在哪根顯示哪根，沒停就顯示最新那根。 */
function renderKlineRead() {
  const host = $("kRead");
  if (!host) return;
  const d = K.data, bars = K.view || (d && d.bars) || [];
  if (!bars.length) { host.innerHTML = ""; return; }

  const i = (K.hover != null && bars[K.hover]) ? K.hover : bars.length - 1;
  const b = bars[i];
  const prev = bars[i - 1];
  const dg = (d && d.digits) || 2;
  const fx = (v) => v.toFixed(dg);

  const base = prev ? prev.c : b.o;
  const diff = b.c - base;
  const pct = base ? (diff / base) * 100 : 0;
  const cls = diff > 0 ? "up" : diff < 0 ? "down" : "";
  const sign = diff > 0 ? "+" : "";

  let maHtml = "";
  for (const nn of [5, 20, 60]) {
    if (!K.ma[nn] || bars.length < nn) continue;
    const v = movingAvg(bars, nn)[i];
    maHtml += `<i style="color:var(--ma-${nn})">MA${nn} ${v == null ? "—" : fx(v)}</i>`;
  }

  host.innerHTML =
    `<div class="ohlc">` +
      `<span>開<b>${fx(b.o)}</b></span>` +
      `<span>高<b>${fx(b.h)}</b></span>` +
      `<span>低<b>${fx(b.l)}</b></span>` +
      `<span>收<b class="${cls}">${fx(b.c)}</b></span>` +
      `<span><b class="${cls}">${sign}${fx(diff)} (${sign}${pct.toFixed(2)}%)</b></span>` +
    `</div>` +
    (maHtml ? `<div class="mas">${maHtml}</div>` : "");
}

/* 滑鼠互動：十字準星 + 浮動提示 */
function bindKline() {
  const wrap = $("klineWrap");
  if (!wrap || wrap.dataset.bound) return;
  wrap.dataset.bound = "1";

  const tip = $("klineTip");
  wrap.addEventListener("pointermove", (e) => {
    const g = K.geom, bars = K.view || [];
    if (!g || !bars.length) return;
    const box = wrap.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * g.W;
    let i = Math.round((px - g.padL) / g.slot - 0.5);
    i = Math.max(0, Math.min(bars.length - 1, i));
    if (i === K.hover) return;
    K.hover = i;

    const line = $("kCrossV");
    if (line) { line.setAttribute("x1", g.X(i)); line.setAttribute("x2", g.X(i)); line.setAttribute("opacity", 1); }
    renderKlineRead();

    if (tip) {
      const b = bars[i];
      const dg = (K.data && K.data.digits) || 2;
      tip.innerHTML =
        `<div class="tip-h">${esc(fmtBarTime(b.t, K.tf))}</div>` +
        `<div class="tip-row"><span>開</span><b>${b.o.toFixed(dg)}</b></div>` +
        `<div class="tip-row"><span>高</span><b>${b.h.toFixed(dg)}</b></div>` +
        `<div class="tip-row"><span>低</span><b>${b.l.toFixed(dg)}</b></div>` +
        `<div class="tip-row"><span>收</span><b class="${b.c >= b.o ? "up" : "down"}">${b.c.toFixed(dg)}</b></div>` +
        `<div class="tip-row"><span>量</span><b>${b.v}</b></div>`;
      tip.style.opacity = 1;
      const left = Math.min(Math.max(0, (g.X(i) / g.W) * box.width - 68), box.width - 150);
      tip.style.left = left + "px";
      tip.style.top = "12px";
    }
  });

  wrap.addEventListener("pointerleave", () => {
    K.hover = null;
    const line = $("kCrossV");
    if (line) line.setAttribute("opacity", 0);
    if (tip) tip.style.opacity = 0;
    renderKlineRead();
  });
}

/* 週期切換鈕。只列後端說「有足夠根數」的那些。 */
function renderTfPills() {
  const host = $("kTfs");
  if (!host) return;
  const avail = (K.data && K.data.available) || ["M1", "M5", "M15", "H1", "H4", "D1"];
  host.innerHTML = avail.map((tf) =>
    `<button type="button" class="pill ${tf === K.tf ? "is-on" : ""}" data-tf="${tf}">${tf}</button>`
  ).join("");
  const label = $("kTfLabel");
  if (label) label.textContent = TF_LABEL[K.tf] || K.tf;
}

/* 頂部導覽：點了平滑捲動，捲動時高亮目前所在的區塊。

   用 scroll 事件而不是 IntersectionObserver——區塊高矮差很多，
   observer 的門檻值很難調到每個區塊都合理，直接比座標反而穩。 */
function bindRailNav() {
  const nav = $("railNav");
  if (!nav || nav.dataset.bound) return;
  nav.dataset.bound = "1";

  const links = [...nav.querySelectorAll("a")];

  nav.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    // 沒有 data-nav 的是外部連結(官網)，讓瀏覽器自己處理 ——
    // 這裡如果照樣 preventDefault，那顆連結會變成按了完全沒反應。
    if (!a.dataset.nav) return;
    e.preventDefault();
    const id = a.dataset.nav;
    if (id === "top") {
      scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
      return;
    }
    const el = $(id);
    if (!el) return;
    // 頂欄是固定的，捲到定位要扣掉它的高度，不然標題會被蓋住
    const rail = document.querySelector(".rail");
    const offset = (rail ? rail.getBoundingClientRect().height : 0) + 16;
    scrollTo({ top: el.getBoundingClientRect().top + scrollY - offset,
               behavior: REDUCED ? "auto" : "smooth" });
  });

  let ticking = false;
  const mark = () => {
    ticking = false;
    const line = innerHeight * 0.3;      // 視窗上緣往下三成當判定線
    let active = "top";
    for (const a of links) {
      const id = a.dataset.nav;
      if (!id || id === "top") continue;
      const el = $(id);
      if (!el || el.hidden) continue;
      if (el.getBoundingClientRect().top <= line) active = id;
    }
    if (scrollY < 40) active = "top";
    for (const a of links) a.classList.toggle("is-on", a.dataset.nav === active);
  };
  addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(mark);
  }, { passive: true });
  mark();
}

/* 即時報價灌進「形成中的那根」。

   K 線的骨架 5 秒才重抓一次，但 /api/status 每秒都在跑而且順便帶了 bid。
   真實交易軟體上，最後一根會隨著每個 tick 動——這裡做的就是這件事：
   把最新價當成那根的收盤，順便把高低推開。下一次 /api/market 回來時，
   券商算的真實數字會直接覆蓋掉這個推估值。 */
function applyTick(tick) {
  if (!tick || !K.data || !K.data.bars || !K.data.bars.length) return;
  const px = Number(tick.bid);
  if (!isFinite(px) || px <= 0) return;

  const bars = K.data.bars;
  const last = bars[bars.length - 1];
  if (last.c === px) return;             // 沒動就不重畫

  last.c = px;
  if (px > last.h) last.h = px;
  if (px < last.l) last.l = px;

  // 報價徽章一起更新，讓人看得出來畫面是活的
  const feed = $("kFeed");
  if (feed && tick.stale) feed.textContent = "報價已停止更新";

  renderKline();
}

async function refreshMarket() {
  if (K.loading) return;
  K.loading = true;
  try {
    const r = await fetch("/api/market?tf=" + encodeURIComponent(K.tf), { cache: "no-store" });
    const j = await r.json();
    if (!j.ok || !j.market) return;
    K.data = j.market;

    const sym = $("kSymbol");
    if (sym) sym.textContent = j.market.symbol || "XAUUSD";
    const feed = $("kFeed");
    if (feed) {
      // 聚合出來的要講清楚，不要讓人以為是券商原生週期
      feed.textContent = j.market.source === "aggregated"
        ? "由 M1 合成" : "券商即時報價";
    }
    renderTfPills();
    renderKline();
    renderWatchlist();
  } catch (e) {
    /* 網路斷了就維持上一次的畫面，下一輪再試 */
  } finally {
    K.loading = false;
  }
}

/* 側欄的市場總覽 */
function renderWatchlist() {
  const host = $("watchlist");
  if (!host) return;
  const rows = (K.data && K.data.watchlist) || [];
  const card = $("secWatch");
  if (card) card.hidden = rows.length === 0;
  if (!rows.length) { host.innerHTML = ""; return; }

  host.innerHTML = rows.map((w) => {
    const up = w.change_pct > 0, down = w.change_pct < 0;
    const cls = up ? "up" : down ? "down" : "";
    const sign = up ? "+" : "";
    return `<li>
      <div class="wl-sym">
        <b>${esc(w.symbol)}</b>
        <span>${esc(SYM_NAME[w.symbol] || "")}</span>
      </div>
      <div class="wl-px">
        <b>${w.bid.toLocaleString("en-US", { minimumFractionDigits: w.digits, maximumFractionDigits: w.digits })}</b>
        <span class="${cls}">${sign}${w.change_pct.toFixed(2)}%</span>
      </div>
    </li>`;
  }).join("");

  const t = $("watchTime");
  if (t) {
    const d = new Date();
    const p2 = (x) => String(x).padStart(2, "0");
    t.textContent = p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds());
  }
}

const SYM_NAME = {
  XAUUSD: "黃金/美元", XAGUSD: "白銀/美元", USOIL: "原油",
  BTCUSD: "比特幣/美元", ETHUSD: "以太幣/美元",
  EURUSD: "歐元/美元", GBPUSD: "英鎊/美元", USDJPY: "美元/日圓",
};

/* 週期鈕與指標開關 */
document.addEventListener("click", (e) => {
  const tfBtn = e.target.closest("#kTfs .pill");
  if (tfBtn) {
    K.tf = tfBtn.dataset.tf;
    K.hover = null;
    renderTfPills();
    refreshMarket();
    return;
  }
  const chip = e.target.closest(".chart-toggles .ind");
  if (chip) {
    if (chip.dataset.ma) {
      const n = Number(chip.dataset.ma);
      K.ma[n] = !K.ma[n];
      chip.classList.toggle("is-on", K.ma[n]);
    } else {
      K.vol = !K.vol;
      chip.classList.toggle("is-on", K.vol);
    }
    renderKline();
  }
});

/* 底部狀態列。全部是既有的狀態資料，只是搬到隨時看得到的位置。 */
function renderStatusBar(stats, sum) {
  const bar = $("statusBar");
  if (!bar) return;

  const running = !!(S.status && S.status.running);
  const account = stats.account;

  // 今日風險 = 今天已實現的虧損 ÷ 帳戶淨值。沒有設上限就只顯示數字不畫條。
  const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0);
  const todayLoss = (stats.trades || [])
    .filter((t) => t.close_timestamp * 1000 >= dayStart.getTime() && t.profit < 0)
    .reduce((a, t) => a + Math.abs(t.profit), 0);
  const cap = Number((S.status && S.status.settings && S.status.settings.max_daily_loss) || 0);
  const equity = account ? account.equity : 0;
  const usedPct = cap ? (todayLoss / cap) * 100 : (equity ? (todayLoss / equity) * 100 : 0);
  const meterCls = usedPct >= 100 ? "is-over" : usedPct >= 60 ? "is-warn" : "";

  $("sbRun").innerHTML = `<span class="dot" style="background:${running ? "var(--ok)" : "var(--muted)"}"></span>
    系統狀態 <b>${running ? "運行中" : "已停止"}</b>`;
  $("sbTime").innerHTML = `伺服器時間 <span class="mono">${esc(
    (account && account.server_time) || "—")}</span>`;
  $("sbLink").innerHTML = `<span class="dot" style="background:${stats.connected ? "var(--ok)" : "var(--gold-lit)"}"></span>
    MT5 <b>${stats.connected ? "已連線" : "未更新"}</b>`;
  $("sbRisk").innerHTML = `今日虧損 <span class="mono">${money(todayLoss, { compact: true })}</span>` +
    (cap ? ` <span class="mono">/ ${money(cap, { compact: true })}</span>` : "");
  const meter = $("sbMeter");
  meter.className = "sb-meter " + meterCls;
  meter.firstElementChild.style.width = Math.min(100, Math.max(0, usedPct)) + "%";
  meter.hidden = !cap && !equity;
}

function paintStats() {
  const stats = S.stats;
  if (!stats) return;
  CURRENCY = (stats.account && stats.account.currency) || "USD";
  $("curCode").textContent = CURRENCY;

  const chip = $("chipMt5");
  chip.className = "chip client-only " + (stats.connected ? "is-live" : "is-warn");
  chip.lastElementChild.textContent = stats.connected
    ? "MT5 已連線"
    : (stats.account ? "MT5 資料未更新" : "MT5 未連線");

  const account = stats.account;
  $("factEquity").textContent = account ? money(account.equity, { compact: true }) : "—";
  $("factBalance").textContent = account ? money(account.balance, { compact: true }) : "—";
  const floatingEl = $("factFloating");
  if (account) {
    floatingEl.textContent = money(account.floating, { signed: true, compact: true });
    floatingEl.className = toneClass(account.floating);
  } else { floatingEl.textContent = "—"; }

  // 來源下拉：保留使用者選擇，來源清單變動時才重建。
  // 清單要跟「各來源績效」一致 —— 用自動發現的完整來源，不能只列有成交紀錄的，
  // 否則剛設定好、還沒平倉的群組會整個不見。EA 自己下的單有自己的分頁，
  // 這裡（訊號跟單分頁）不列。
  const select = $("filterSource");
  const names = [...new Set(
    (stats.source_settings || []).map((r) => r.source)
      .concat((stats.trades || []).filter((t) => t.mode !== "ea_native").map((t) => t.source || "未標記來源"))
  )];
  const signature = names.join("|");
  if (select.dataset.signature !== signature) {
    select.dataset.signature = signature;
    select.innerHTML = '<option value="all">全部來源</option>' +
      names.map((n) => '<option value="' + esc(n) + '">' + esc(srcName(n)) + "</option>").join("");
    select.value = names.includes(S.source) ? S.source : "all";
    S.source = select.value;
  }

  const allSignalTrades = (stats.trades || []).filter((t) => t.mode !== "ea_native");
  const allEaTrades = (stats.trades || []).filter((t) => t.mode === "ea_native");
  const signalPositions = (stats.positions || []).filter((p) => p.mode !== "ea_native");
  const eaPositions = (stats.positions || []).filter((p) => p.mode === "ea_native");

  const trades = filtered(S, false);
  const sum = summarise(trades);
  const isAll = S.period === "all" && S.source === "all";

  $("filterCount").textContent = rangeLabel() + " · " + trades.length + " / " + allSignalTrades.length + " 筆";
  setHero(sum.net);
  $("heroSub").innerHTML = trades.length
    ? sum.total + " 筆已平倉 · 勝率 " + pct(sum.win_rate) + (isAll ? "" : " · " + esc(rangeLabel()))
    : (stats.connected ? esc(rangeLabel()) + " 尚無平倉紀錄" : "等待 MT5 回報交易資料");

  // 設定表只在來源清單變動時重建，否則每 3 秒輪詢會把使用者正在打的字洗掉
  const srcRows = stats.source_settings || [];
  // 等級也要進 signature：登入/升級之後鎖定狀態會變，只比來源名稱的話
  // 整張表會停在登入前那份「全部鎖住」的樣子。
  const srcSig = srcRows.map((r) => r.source).join("|") + "@" + TIER;
  if ($("sourceSettings").dataset.signature !== srcSig) {
    $("sourceSettings").dataset.signature = srcSig;
    renderSourceSettings(srcRows);
  }

  renderLadder(stats.martingale || {}, isAll ? stats.cycles : null, srcRows);
  paintTzNotes();
  renderPending(stats.pending || [], !!(S.status && S.status.running));
  renderPositions(signalPositions, CURRENCY);
  renderTiles(sum, isAll ? stats.cycles : null);
  renderEquity(trades);
  renderBars(trades);
  renderSourcePerformance(trades, srcRows);
  renderRecords(trades);
  renderDashStats(sum, account, trades, srcRows);
  renderDonut(sum);
  renderStatusBar(stats, sum);

  // 分頁列：完全沒設定過其他 EA 就不出現，訊號跟單畫面跟以前一模一樣
  const hasEa = (stats.ea_sources || []).length > 0;
  $("viewTabs").hidden = !hasEa;
  if (hasEa) paintEA(allEaTrades, eaPositions);
}

/* 趨勢線策略分頁：跟訊號跟單分頁共用同一份 /api/stats 輪詢結果，只是把
   trades/positions 換成 mode==="ea_native" 的子集，用自己的一組期間篩選狀態 SE。
   沒有馬丁階梯（EA 自己算風險倉位）、沒有待成交掛單（這顆 EA 只下市價單）、
   沒有下單控制表（手數不是我們調的）。 */
function paintEA(allEaTrades, eaPositions) {
  $("eaCurCode").textContent = CURRENCY;
  const trades = filtered(SE, true);
  const sum = summarise(trades);
  const isAll = SE.period === "all";

  $("eaFilterCount").textContent = rangeLabel(SE) + " · " + trades.length + " / " + allEaTrades.length + " 筆";
  setHero(sum.net, "eaHeroNet", SE);
  $("eaHeroSub").textContent = trades.length
    ? sum.total + " 筆已平倉 · 勝率 " + pct(sum.win_rate) + (isAll ? "" : " · " + rangeLabel(SE))
    : (S.stats && S.stats.connected ? rangeLabel(SE) + " 尚無平倉紀錄" : "等待 MT5 回報交易資料");

  renderPositions(eaPositions, CURRENCY, { box: "eaPositions", summary: "eaPosSummary" });
  renderTiles(sum, undefined, "eaTiles");
  renderEquity(trades, { svg: "eaEquityChart", wrap: "eaEquityWrap", tip: "eaEquityTip" }, SE);
  renderEaBreakdown(trades, "eaBreakdown");
  renderRecords(trades, { box: "eaRecords", periodIds: EA_PERIOD_IDS }, SE, () => paintEA(allEaTrades, eaPositions));
}

function paintStatus() {
  const snap = S.status;
  if (!snap) return;
  const chip = $("chipService");
  chip.className = "chip " + (snap.running ? "is-live" : "is-off");
  chip.lastElementChild.textContent = snap.running
    ? (snap.role === "central" ? "訊號發布中" : "跟單運轉中")
    : snap.status;

  const hub = $("chipHub");
  // 中央機顯示主機名（管理者要確認連對地方）；會員端只顯示連線與否 ——
  // 會員沒有需要知道伺服器在哪，講出來只是給人探測的線索。
  const hubUrl = String((snap.settings && snap.settings.hub_url) || "").trim();
  const linked = IS_CLIENT ? Boolean(snap.hub_configured) : Boolean(hubUrl);
  hub.className = "chip " + (linked ? "is-live" : "is-warn");
  hub.lastElementChild.textContent = IS_CLIENT
    ? (linked ? "訊號伺服器" : "未連線")
    : (hubUrl ? hubUrl.replace(/^https?:\/\//, "") : "未設定 Hub");

  $("start").disabled = !!snap.running;
  $("stop").disabled = !snap.running;
  $("uptime").textContent = snap.running
    ? "已運轉 " + Math.floor(snap.uptime_seconds / 60) + " 分 " + (snap.uptime_seconds % 60) + " 秒"
    : "服務未啟動";

  const logs = $("logs");
  const atBottom = logs.scrollHeight - logs.scrollTop - logs.clientHeight < 40;
  logs.textContent = (snap.logs || []).join("\n");
  if (atBottom) logs.scrollTop = logs.scrollHeight;

  if (ROLE === "central") paintCentralHint(snap);
}

/* 訊號中心的儀表板。它沒有 MT5、沒有成交紀錄，能講的是
   「服務有沒有在跑、訊號發到哪、會員連不連得到我」。 */
function paintCentralDash(snap) {
  if (IS_CLIENT) return;
  const settings = snap.settings || {};
  const running = !!snap.running;

  // 運行時間
  const up = Number(snap.uptime_seconds || 0);
  const upText = up
    ? (up >= 86400 ? Math.floor(up / 86400) + " 天 " + Math.floor((up % 86400) / 3600) + " 小時"
       : up >= 3600 ? Math.floor(up / 3600) + " 小時 " + Math.floor((up % 3600) / 60) + " 分"
       : Math.floor(up / 60) + " 分")
    : "—";

  // 發布模式：填了雲端 Hub URL 就是雲端模式，否則是本機自架
  const remoteHub = String(settings.hub_url || "").trim();
  const mode = remoteHub ? "雲端 Hub" : "本機自架";
  const port = settings.port || "8765";
  const hubAddr = remoteHub || snap.cloudflare_url ||
    ("http://" + (snap.lan_ip || "127.0.0.1") + ":" + port);

  const chats = Array.isArray(snap.line_chats) ? snap.line_chats : [];

  const cards = [
    { k: "服務狀態", v: running ? "發布中" : "已停止",
      sub: running ? "LINE DB 監看中" : "按上方「開始發布」啟動",
      cls: running ? "up" : "", accent: running ? "win" : "violet" },
    { k: "運行時間", v: upText, sub: running ? "自本次啟動起" : "尚未啟動", accent: "cyan" },
    { k: "發布模式", v: mode,
      sub: remoteHub ? "訊號送往雲端" : "會員直連這台", accent: "gold" },
    { k: "LINE 聊天室", v: String(chats.length || "—"),
      sub: chats.length ? "個聊天室監看中" : "尚未設定聊天室", accent: "violet" },
    { k: "Hub 連線", v: snap.hub_configured || remoteHub ? "已設定" : "未設定",
      sub: snap.cloudflare_url ? "Cloudflare Tunnel 已啟用" : "見下方發布目標",
      cls: (snap.hub_configured || remoteHub) ? "up" : "down",
      accent: (snap.hub_configured || remoteHub) ? "win" : "loss" },
  ];

  const host = $("centralStats");
  if (host) {
    host.innerHTML = cards.map((c) => `
      <div class="dstat dstat--${c.accent}">
        <dt>${esc(c.k)}</dt>
        <dd class="${c.cls || ""}">${esc(c.v)}</dd>
        <span class="sub">${esc(c.sub)}</span>
      </div>`).join("");
  }

  const set = (id, text) => { const el = $(id); if (el) el.textContent = text; };
  set("cenMode", mode);
  set("cenHub", hubAddr);
  set("cenLan", (snap.lan_ip || "—") + ":" + port);

  const wrap = $("cenChats");
  if (wrap) {
    wrap.innerHTML = chats.length
      ? chats.map((chat) => `
          <div class="mock-row">
            <span class="dot ${running ? "dot--on" : "dot--off"}"></span>
            <span>${esc(chat)}</span>
          </div>`).join("")
      : '<div class="mock-row"><span class="k">尚未設定 LINE 資料庫聊天室</span></div>';
  }
}

function paintCentralHint(snap) {
  paintCentralDash(snap);
  const settings = snap.settings || {};
  const remoteHub = String(settings.hub_url || "").trim();
  const port = settings.port || "8765";
  const host = String(settings.host || "").trim();
  const loopback = ["127.0.0.1", "localhost", "::1"].includes(host);
  const hint = $("hint");
  const notice = $("notice");
  let text = "";
  let warn = false;
  if (remoteHub) text = "雲端 Hub 模式：訊號發布到 " + remoteHub + "；會員端 Hub URL 也填這個。";
  else if (snap.cloudflare_url) text = "會員端 Hub URL：" + snap.cloudflare_url;
  else if (["true", "1", "yes", "on"].includes(String(settings.cloudflare_tunnel || "").toLowerCase()))
    text = "Cloudflare Tunnel 啟動中；公開 Hub URL 會出現在狀態紀錄。";
  else if (loopback) { text = "Hub 只監聽本機，會員端連不進來——把「Hub 監聽 IP」改成 0.0.0.0，或勾選 Cloudflare Tunnel。"; warn = true; }
  else text = "會員端 Hub URL 可填：http://" + snap.lan_ip + ":" + port + "（限同一區網）";
  if (hint) hint.textContent = text;
  notice.innerHTML = warn ? '<div class="notice"><b>會員端連不進來</b>' + esc(text) + "</div>" : "";
}

/* ------------------------------------------------------------- polling */
/* ----------------------------------------------------------------- auth */
/* 登入閘門。後端每次 /api/status 都會回目前的登入狀態，前端只是照著畫；
   session token 一律留在後端，不會出現在瀏覽器裡。 */
/* ------------------------------------------------------------------ side */
/* 側欄的等級卡與方案功能清單。
   每一項都直接讀 auth.entitlements 的欄位，不自己推斷、也不列出後端沒有的東西——
   側欄上寫「可使用」但實際上被擋掉，比不顯示還糟。 */
const TIER_ORDER = ["trial", "basic", "advanced", "flagship"];
/* 到期進度條的滿格基準 = 這個方案「一期是幾天」，由後端 entitlements.plan_days
   帶過來（體驗版 7 天、其餘 30 天）。以前寫死 30 天，於是剛開通的體驗版帳號
   一進來進度條只有 23%，看起來像快到期了。買一年的人會直接滿格，那是對的
   ——剩很多就是剩很多。後端沒回（舊 Hub）就照等級猜一個保守值。 */
/* 方案時間的警示等級。門檻用「還剩幾天」而不是百分比 —— 見 .side-meter 的註解。
   ok  >14 天 · mid 8~14 · warn 4~7 · crit 1~3 · out 已到期／額度用盡 */
function planLevel(daysLeft) {
  if (!(daysLeft > 0)) return "out";
  if (daysLeft > 14) return "ok";
  if (daysLeft > 7) return "mid";
  if (daysLeft > 3) return "warn";
  return "crit";
}
const PLAN_LEVELS = ["lv-ok", "lv-mid", "lv-warn", "lv-crit", "lv-out"];
/* 進度條、剩餘天數、倒數文字三個地方一起換色。只換一個的話，另外兩個看起來
   像壞掉；而且 6px 的細線單獨變色很容易被滑過去。 */
function applyPlanLevel(daysLeft) {
  const cls = "lv-" + planLevel(daysLeft);
  for (const id of ["sideMeterWrap", "sideExp", "sideLeft"]) {
    const el = $(id);
    if (!el) continue;
    el.classList.remove(...PLAN_LEVELS);
    el.classList.add(cls);
  }
}

/* 這個帳號還剩幾天。用量制(進階版以上)看使用額度、日曆制看到期日；
   無期限回 null。以前好幾個地方各自讀 a.expires_at —— 用量制的 expires_at 是
   null，於是頂欄一律顯示「無期限」、側欄的續期提示永遠不會出現。 */
function planDaysLeft(a) {
  const u = (a && a.usage && typeof a.usage === "object") ? a.usage : null;
  if (u && u.seconds_left != null) return Number(u.seconds_left) / 86400;
  const exp = Number((a && a.expires_at) || 0);
  return exp ? (exp * 1000 - Date.now()) / 86400000 : null;
}

function planFullDays(a) {
  const days = Number(((a && a.entitlements) || {}).plan_days);
  if (days > 0) return days;
  return String((a && a.tier) || "").toLowerCase() === "trial" ? 7 : 30;
}

function paintSide(a) {
  const bar = $("sideBar");
  if (!bar) return;
  if (!a || !a.logged_in) { bar.hidden = true; return; }
  bar.hidden = false;
  const TIER_FULL_DAYS = planFullDays(a);

  $("sideTier").textContent = a.tier_label || a.tier || "—";

  // 升級卡：已經是旗艦版就不用勸他升級了；快到期的話改成續期文案
  const up = $("secUpgrade");
  if (up) {
    const tier = String(a.tier || "").toLowerCase();
    const top = tier === "flagship";
    const days = planDaysLeft(a);
    const soon = days != null && days <= 14;
    up.hidden = top && !soon;
    const body = $("upgradeBody");
    if (body) {
      body.textContent = soon
        ? (days >= 0 ? `方案剩 ${days} 天，私訊即可續期` : "方案已到期，私訊即可續期")
        : "解鎖更多訊號來源與策略設定";
    }
    up.querySelector(".side-up-h").textContent = soon ? "續期" : "升級方案";
  }

  const wrap = $("sideMeterWrap");
  const lbl = $("sideExpLabel");
  const pauseEl = $("sidePause");
  const tp = !!a.time_pause;
  const usage = (tp && a.usage && typeof a.usage === "object") ? a.usage : null;

  if (usage && usage.seconds_left != null) {
    // 用量制(進階版以上):方案時間是一份「使用額度」,只有開盤且跟單時才扣。
    // 倒數顯示剩餘額度,非開盤/未跟單時凍結,不燒方案時間。
    if (lbl) lbl.textContent = "使用額度";
    window.__memberExpAt = 0;                  // 關掉日曆倒數分支
    // 只有 Hub 回傳的額度真的變了才重設本地錨點, 否則節流(每 10 秒才寫一次)
    // 會讓平滑的本地倒數每次輪詢就被拉回去、看起來卡住或倒退。
    const newSecs = Number(usage.seconds_left);
    if (!window.__usage || window.__usage.seconds_left !== newSecs
        || window.__usage.market_open !== usage.market_open) {
      window.__usage = { seconds_left: newSecs, market_open: !!usage.market_open };
      window.__usageAt = Date.now();
    }
    const days = newSecs / 86400;
    // 進度條的滿格 = 這個帳號「當初拿到多少額度」(Hub 帶下來的 seconds_total)，
    // 不是等級的預設天數。7 天試用開在進階版上，用 30 天當分母第一天就只有 23%，
    // 看起來像快到期 —— 而且會讓人誤以為滿格是 30 天。舊 Hub 沒回這欄就退回等級預設。
    const grantDays = Number(usage.seconds_total) / 86400;
    const fullDays = grantDays > 0 ? Math.max(grantDays, days) : TIER_FULL_DAYS;
    $("sideExp").textContent = (days >= 1
      ? "約 " + Math.floor(days) + " 天"
      : "約 " + Math.max(0, Math.floor(newSecs / 3600)) + " 小時")
      + (grantDays > 0 ? " / 共 " + Math.round(fullDays) + " 天" : "");
    wrap.hidden = false;
    $("sideMeter").style.width = Math.max(0, Math.min(100, (days / fullDays) * 100)) + "%";
    applyPlanLevel(days);
    if (pauseEl) pauseEl.hidden = false;
    startExpTicker();
  } else if (tp && usage) {
    // 進階版但無期限(額度 = null): 永久帳號, 不顯示倒數
    if (lbl) lbl.textContent = "方案";
    $("sideExp").textContent = "無期限";
    window.__memberExpAt = 0; window.__usage = null;
    wrap.hidden = true; $("sideLeft").textContent = "";
    applyPlanLevel(Infinity);
    if (pauseEl) pauseEl.hidden = true;
    stopExpTicker();
  } else {
    // 日曆制(體驗/基礎版):維持原本 expires_at 到期倒數,完全不受用量制影響。
    if (lbl) lbl.textContent = "方案到期日";
    window.__usage = null;
    if (pauseEl) pauseEl.hidden = true;
    const exp = Number(a.expires_at || 0);
    window.__memberExpAt = exp;                 // 給每秒倒數用
    if (exp) {
      const d = new Date(exp * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      // 到期「日期＋時間」都顯示，配合下面的天時分秒倒數
      $("sideExp").textContent =
        `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      const days = (exp * 1000 - Date.now()) / 86400000;
      wrap.hidden = false;
      $("sideMeter").style.width = Math.max(0, Math.min(100, (days / TIER_FULL_DAYS) * 100)) + "%";
      applyPlanLevel(days);
      startExpTicker();                          // 啟動/重啟每秒倒數
    } else {
      $("sideExp").textContent = "無期限";
      wrap.hidden = true;
      $("sideLeft").textContent = "";
      applyPlanLevel(Infinity);
      stopExpTicker();
    }
  }

  const e = a.entitlements || {};
  const srcs = Array.isArray(e.sources) ? e.sources : [];
  const has = (name) => !!name && srcs.indexOf(name) !== -1;
  const maxLot = e.max_lot;

  // 完整列出方案所有功能(跟下方會員權益比較表逐項對得起來)。會員沒有的鎖起來、
  // 標出需要的等級,讓低階會員看見上面能解鎖什麼。訊號源/手數/馬丁/分批/保本/排程
  // 一律看後端 entitlements 的欄位,不自己依等級推 —— 推錯就會出現「側欄說有、
  // 實際被擋掉」。只有沒有對應欄位的(勝率分析、真人建議…)才用等級判定。
  const curIdx = TIER_ORDER.indexOf(String(a.tier || "").toLowerCase());
  const atLeast = (t) => curIdx >= 0 && curIdx >= TIER_ORDER.indexOf(t);
  const rows = [
    { label: "低頻訊號跟單",       on: has(LOW_SOURCE),   need: "flagship" },
    { label: "中頻訊號跟單",       on: has(MID_SOURCE),   need: "trial" },
    { label: "高頻訊號跟單",       on: has(HIGH_SOURCE),  need: "advanced" },
    { label: "超高頻訊號跟單",     on: has(ULTRA_SOURCE), need: "flagship" },
    { label: "不限次數跟單",       on: atLeast("basic"),  need: "basic" },
    { label: "跟單手數上限",       on: true, need: "trial",
      value: maxLot == null ? "不限" : lots(maxLot) + " 手" },
    { label: "馬丁策略設定",       on: !!e.martingale,    need: "advanced" },
    { label: "分批止盈設定",       on: !!e.partial_close, need: "advanced" },
    { label: "保本移損設定",       on: !!e.breakeven,     need: "advanced" },
    { label: "每日止盈 / 止損",    on: true, need: "trial" },
    { label: "績效報表",           on: true, need: "trial" },
    { label: "各頻率勝率分析",     on: atLeast("advanced"), need: "advanced" },
    { label: "手機跟單通知",       on: !!e.mobile_notify, need: "advanced" },
    { label: "非開盤自動暫停計時", on: !!e.time_pause,    need: "advanced" },
    { label: "本金比例自動調手數", on: atLeast("flagship"), need: "flagship" },
    { label: "自動排程",           on: !!e.schedule,      need: "advanced" },
    { label: "真人分析建議",       on: atLeast("flagship"), need: "flagship" },
  ];

  $("sideEnt").innerHTML = rows.map((r) => `
    <li class="${r.on ? "" : "is-off"}">
      ${r.on ? '<i class="dot dot--on"></i>' : '<i class="lockmark"></i>'}
      <span>${esc(r.label)}</span>
      ${r.on
        ? (r.value ? `<span class="val">${esc(r.value)}</span>` : "")
        : `<span class="val val--lock">需${esc(TIER_LABELS[r.need] || "更高等級")}</span>`}
    </li>`).join("");
}

/* 到期倒數：天時分秒，每秒跳一次。expires_at 存在 window.__memberExpAt，
   paintSide 每次刷新會更新它，這裡只管把它算成人看得懂的字。 */
let __expTimer = null;
function fmtDHMS(r) {
  const d = Math.floor(r / 86400); r -= d * 86400;
  const h = Math.floor(r / 3600);  r -= h * 3600;
  const m = Math.floor(r / 60);    const s = r - m * 60;
  return `${d} 天 ${h} 時 ${m} 分 ${s} 秒`;
}
function tickExp() {
  const left = $("sideLeft");
  if (!left) return;

  // 用量制(進階版以上):倒數走「使用額度」,只有開盤+跟單時才本地遞減,其餘凍結。
  const u = window.__usage;
  if (u && u.seconds_left != null) {
    const pauseEl = $("sidePause");
    const following = !!(typeof S !== "undefined" && S.status && S.status.running);
    const consuming = following && !!u.market_open;
    let secs = Number(u.seconds_left);
    if (consuming) secs -= (Date.now() - (window.__usageAt || Date.now())) / 1000;
    secs = Math.max(0, Math.floor(secs));
    if (secs <= 0) {
      left.textContent = "額度已用完，請私訊續期（LINE qazasd96225）";
      applyPlanLevel(0);
      if (pauseEl) { pauseEl.hidden = false; pauseEl.className = "side-pause is-paused"; pauseEl.textContent = "⏸ 額度已用完"; }
      return;
    }
    left.textContent = `還可使用 ${fmtDHMS(secs)}`;
    applyPlanLevel(secs / 86400);
    if (pauseEl) {
      pauseEl.hidden = false;
      if (consuming) { pauseEl.className = "side-pause is-live"; pauseEl.textContent = "⏱ 計時中"; }
      else if (!u.market_open) { pauseEl.className = "side-pause is-paused"; pauseEl.textContent = "⏸ 已暫停 · 非開盤"; }
      else { pauseEl.className = "side-pause is-paused"; pauseEl.textContent = "⏸ 已暫停 · 未跟單"; }
    }
    return;
  }

  // 日曆制(體驗/基礎版):到期倒數,原本邏輯不變。
  const exp = Number(window.__memberExpAt || 0);
  if (!exp) { left.textContent = ""; return; }
  let r = Math.floor(exp - Date.now() / 1000);
  if (r <= 0) { left.textContent = "已到期，請私訊續期（LINE qazasd96225）"; applyPlanLevel(0); return; }
  left.textContent = `還剩 ${fmtDHMS(r)}`;
  applyPlanLevel(r / 86400);
}
function startExpTicker() { tickExp(); if (!__expTimer) __expTimer = setInterval(tickExp, 1000); }
function stopExpTicker() { if (__expTimer) { clearInterval(__expTimer); __expTimer = null; } }

/* 會員權益方案比較表 —— 內容跟官網 /pricing 一模一樣(13 項功能 × 四個方案),
   依會員目前等級標亮。改這裡就要一起改 website/pricing/index.html 與 i18n。
   側欄「方案功能」列的每一項也必須對得起來(見 paintSide)。
   每格:"y"=有、"n"=無、字串=單行文字、[主, 副]=兩行文字。 */
const BENEFIT_COLS = [
  { key: "trial",    name: "體驗版", en: "FREE",    price: "US$0" },
  { key: "basic",    name: "基礎版", en: "PLUS",    price: "US$49" },
  { key: "advanced", name: "進階版", en: "PRO",     price: "US$99" },
  { key: "flagship", name: "旗艦版", en: "PREMIUM", price: "US$149" },
];
const BENEFIT_ROWS = [
  ["使用策略", "能跟哪些頻率的策略", ["中頻策略", "每日 1 筆訊號"], ["中頻策略", "完整跟單"], ["中頻 + 高頻策略", "完整跟單"], ["低頻 + 中頻 + 高頻 + 超高頻策略", "完整跟單"]],
  ["每日跟單限制", "每天最多跟幾筆中頻訊號", "每日最多 1 筆", "不限次數", "不限次數", "不限次數"],
  ["馬丁設定", "馬丁的倍數與層數能不能自己設", "n", "n", ["基礎馬丁", "倍數、最大層數限制"], ["完整馬丁", "倍數、層數、最大風險控制"]],
  ["手數設定", "每筆跟單的手數能調到多細", ["基礎範圍", "有限制"], ["自己設", "標準範圍"], ["自己設", "+ 分批平倉 / 保本移損"], ["自己設", "+ 分批平倉 / 保本移損 + 動態自動調整手數"]],
  ["績效報表", "今天 / 這週 / 這個月的報表", "y", "y", "y", "y"],
  ["各頻率勝率", "各頻率的勝率與績效分開看", "n", "n", "y", "y"],
  ["每日虧損上限", "每天最多虧多少，金額或比例", "y", "y", "y", "y"],
  ["手機跟單通知", "手機收跟單訊號與系統通知", "n", "n", "y", "y"],
  ["分批止盈 / 保本移損", "分批止盈的手數分配，或走滿設定價差就把停損移到進場價", "n", "n", "y", "y"],
  ["本金比例自動調整手數", "手數跟著本金比例自動調整", "n", "n", "n", "y"],
  ["非開盤自動暫停計時", "非開盤/停止跟單時方案時間自動暫停,只在開盤跟單時計算", "n", "n", "y", "y"],
  ["自動排程", "每天自動開始 / 停止的時間", "n", "n", "y", "y"],
  ["真人分析建議", "每月真人幫你看報表、給調整建議", "n", "n", "n", ["每月提供", "真人訊息分析與調整建議"]],
];
function benefitCell(cell) {
  if (cell === "y") return '<span class="mk mk--y" title="有">✓</span>';
  if (cell === "n") return '<span class="mk mk--n" title="無">—</span>';
  if (Array.isArray(cell)) return "<b>" + esc(cell[0]) + "</b>" + (cell[1] ? "<small>" + esc(cell[1]) + "</small>" : "");
  return "<b>" + esc(cell) + "</b>";
}
function renderBenefits(a) {
  const table = $("benefitsTable");
  if (!table) return;
  const cur = (a && a.logged_in) ? BENEFIT_COLS.findIndex((c) => c.key === String(a.tier || "").toLowerCase()) : -1;
  let head = '<thead><tr><th class="cmp-feat"></th>';
  BENEFIT_COLS.forEach((c, i) => {
    head += '<th class="cmp-plan' + (i === cur ? " is-cur" : "") + '">' +
      "<b>" + esc(c.name) + "</b><span>" + esc(c.en) + " · " + esc(c.price) + "</span>" +
      (i === cur ? '<em class="cmp-you">你的方案</em>' : "") + "</th>";
  });
  head += "</tr></thead>";
  let body = "<tbody>";
  BENEFIT_ROWS.forEach((row) => {
    body += '<tr><th scope="row" class="cmp-feat"><b>' + esc(row[0]) + "</b><span>" + esc(row[1]) + "</span></th>";
    for (let i = 0; i < 4; i++) body += '<td class="' + (i === cur ? "is-cur" : "") + '">' + benefitCell(row[i + 2]) + "</td>";
    body += "</tr>";
  });
  body += "</tbody>";
  table.innerHTML = head + body;
}

function paintAuth(snap) {
  if (!IS_CLIENT) return;
  const a = snap.auth || { logged_in: false };
  // 額度先更新,再畫任何東西 —— 來源設定表與排程表都讀 ENT,晚一拍就會閃過
  // 一次「全部鎖住」的畫面。沒登入 = 全部沒有。
  ENT = a.logged_in ? Object.assign(noEntitlements(), a.entitlements || {})
                    : noEntitlements();
  TIER = a.logged_in ? String(a.tier || "").toLowerCase() : "";
  paintSide(a);
  renderBenefits(a);
  renderSchedules();
  const gate = $("authGate");
  const locked = !a.logged_in;
  gate.classList.toggle("is-on", locked);
  document.body.classList.toggle("auth-locked", locked);

  if (locked) {
    // 被踢下線 / 到期 / 停權時，後端會把原因放在 auth.error
    if (a.error) showAuthMsg(a.error);
    $("authBadge").hidden = true;
    // 自動聚焦只做一次,而且只在「登入卡片裡完全沒有東西被聚焦」時做。
    //
    // 原本的條件是 activeElement !== authUser && !authPass.value —— 使用者點進
    // 密碼欄、還沒開始打字的那一刻兩個條件同時成立,而 paintAuth 每秒都會被
    // /api/status 叫一次,於是游標每秒被搶回帳號欄,密碼根本打不進去。
    const gate = $("authGate");
    const u = $("authUser");
    const active = document.activeElement;
    const insideGate = !!(active && gate && gate.contains(active));
    if (u && !insideGate && !gate.dataset.focused) {
      gate.dataset.focused = "1";
      u.focus();
    }
    return;
  }

  // 登出後閘門會再打開,那時要能重新自動聚焦一次
  const gateEl = $("authGate");
  if (gateEl) delete gateEl.dataset.focused;

  const badge = $("authBadge");
  badge.hidden = false;
  $("authBadgeUser").textContent = a.username || "";
  $("authBadgeTier").textContent = a.tier_label || "";
  const left = planDaysLeft(a);
  if (left != null) {
    const days = Math.floor(left);
    $("authBadgeExp").textContent = days >= 0 ? `剩 ${days} 天` : "已到期";
    // 剩不到一週就標紅，讓會員自己看得到該續費了
    badge.classList.toggle("is-soon", days <= 7);
  } else {
    $("authBadgeExp").textContent = "無期限";
    badge.classList.remove("is-soon");
  }
}
function showAuthMsg(text) {
  const el = $("authMsg");
  el.textContent = text || "";
  el.classList.toggle("is-on", Boolean(text));
}
if (IS_CLIENT) {
  $("authTheme").onclick = () =>
    applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");

  // 顯示/隱藏密碼。自動產生的密碼夾雜大小寫，看不到字很容易打錯。
  $("authPeek").onclick = () => {
    const el = $("authPass");
    const shown = el.type === "text";
    el.type = shown ? "password" : "text";
    $("authPeek").textContent = shown ? "顯示" : "隱藏";
    el.focus();
  };

  // Caps Lock 提示 —— 密碼是自動產生的大小寫混合字串，開著大寫鎖必錯，
  // 而且錯了只會看到「帳號或密碼錯誤」，根本猜不到原因。
  const capsWatch = (evt) => {
    if (typeof evt.getModifierState !== "function") return;
    $("authCaps").classList.toggle("is-on", evt.getModifierState("CapsLock"));
  };
  $("authPass").addEventListener("keydown", capsWatch);
  $("authPass").addEventListener("keyup", capsWatch);
  $("authPass").addEventListener("blur", () => $("authCaps").classList.remove("is-on"));

  $("authForm").addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const btn = $("authSubmit");
    const user = $("authUser").value.trim();
    const pass = $("authPass").value;
    if (!user || !pass) { showAuthMsg("請輸入帳號與密碼"); return; }
    btn.disabled = true; btn.textContent = "登入中…"; showAuthMsg("");
    try {
      const res = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user, password: pass }),
      });
      const data = await res.json();
      if (data.ok) {
        // 清掉密碼並把「顯示」狀態收回去，否則下次登出再進來密碼是明文欄位
        $("authPass").value = "";
        $("authPass").type = "password";
        $("authPeek").textContent = "顯示";
        $("authCaps").classList.remove("is-on");
        await refreshStatus();
      } else {
        showAuthMsg(data.error || "登入失敗");
        $("authPass").select();
      }
    } catch (e) {
      showAuthMsg("連不上本機服務，請確認程式仍在執行");
    } finally {
      btn.disabled = false; btn.textContent = "登 入";
    }
  });
  $("authLogout").onclick = async () => {
    if (!confirm("登出後會停止跟單，確定嗎？")) return;
    await fetch("/api/logout", { method: "POST" });
    await refreshStatus();
  };

  /* ---------------------------------------------------------- 修改密碼 */
  const pwMsg = (text, ok) => {
    const el = $("pwMsg");
    el.textContent = text || "";
    el.classList.toggle("is-on", Boolean(text));
    el.classList.toggle("is-ok", Boolean(ok));
  };
  const pwClose = () => {
    $("pwModal").classList.remove("is-on");
    ["pwOld", "pwNew", "pwNew2"].forEach((id) => { $(id).value = ""; $(id).type = "password"; });
    $("pwPeek").textContent = "顯示";
    $("pwCaps").classList.remove("is-on");
    pwMsg("");
  };
  $("authChangePw").onclick = () => {
    const a = (S.status && S.status.auth) || {};
    $("pwWho").textContent = a.username || "";
    $("pwModal").classList.add("is-on");
    $("pwOld").focus();
  };
  $("pwCancel").onclick = pwClose;
  // 點灰色背景或按 Esc 都能關 —— 這是個可以隨時放棄的操作
  $("pwModal").addEventListener("click", (e) => { if (e.target === $("pwModal")) pwClose(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("pwModal").classList.contains("is-on")) pwClose();
  });
  $("pwPeek").onclick = () => {
    const shown = $("pwNew").type === "text";
    ["pwNew", "pwNew2"].forEach((id) => { $(id).type = shown ? "password" : "text"; });
    $("pwPeek").textContent = shown ? "顯示" : "隱藏";
    $("pwNew").focus();
  };
  const pwCapsWatch = (evt) => {
    if (typeof evt.getModifierState !== "function") return;
    $("pwCaps").classList.toggle("is-on", evt.getModifierState("CapsLock"));
  };
  ["pwOld", "pwNew", "pwNew2"].forEach((id) => {
    $(id).addEventListener("keydown", pwCapsWatch);
    $(id).addEventListener("keyup", pwCapsWatch);
  });

  $("pwForm").addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const oldPw = $("pwOld").value, a = $("pwNew").value, b = $("pwNew2").value;
    // 兩次不一致要在本地就擋掉 —— 送到伺服器只會白跑一趟 PBKDF2 (要 0.4 秒)
    if (a !== b) { pwMsg("兩次輸入的新密碼不一致"); $("pwNew2").select(); return; }
    if (a.length < PW_MIN) { pwMsg(`新密碼至少要 ${PW_MIN} 個字元`); $("pwNew").select(); return; }
    if (a === oldPw) { pwMsg("新密碼不能跟目前的一樣"); $("pwNew").select(); return; }

    const btn = $("pwSubmit");
    btn.disabled = true; btn.textContent = "變更中…"; pwMsg("");
    try {
      const res = await fetch("/api/change-password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_password: oldPw, new_password: a }),
      });
      const data = await res.json();
      if (data.ok) {
        pwMsg("密碼已更新，下次登入請用新密碼", true);
        ["pwOld", "pwNew", "pwNew2"].forEach((id) => ($(id).value = ""));
        setTimeout(pwClose, 1800);
      } else {
        pwMsg(data.error || "變更失敗");
        if (/目前密碼/.test(data.error || "")) $("pwOld").select();
      }
    } catch (e) {
      pwMsg("連不上本機服務，請確認程式仍在執行");
    } finally { btn.disabled = false; btn.textContent = "確定變更"; }
  });
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    const snap = await res.json();
    if (!snap.ok) return;
    S.status = snap;
    if (!S.filled) { fill(snap.settings); S.filled = true; }
    paintAuth(snap);
    paintScheduleHint(snap);
    paintStatus();
    applyTick(snap.tick);      // 每秒把最新價灌進形成中的那根
  } catch (e) { /* 網頁還開著、服務暫時沒回應時不要洗版 */ }
}
async function refreshStats() {
  if (!IS_CLIENT) return;
  try {
    const res = await fetch("/api/stats");
    const payload = await res.json();
    if (!payload.ok) return;
    S.stats = payload.stats;
    paintStats();
  } catch (e) { /* 同上 */ }
}

/* ---------------------------------------------------------------- theme */
const THEME_KEY = "gold-copy-theme";
function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  const glyph = mode === "dark" ? "☀" : "☾";
  $("themeToggle").textContent = glyph;
  // 登入頁沒有頂列，那顆切換鈕要一起同步，否則圖示會跟實際主題相反
  const at = $("authTheme");
  if (at) at.textContent = glyph;
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* 無痕模式 */ }
}
let savedTheme = "dark";
try { if (localStorage.getItem(THEME_KEY) === "light") savedTheme = "light"; } catch (e) { /* 同上 */ }
applyTheme(savedTheme);
$("themeToggle").onclick = () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");

/* --------------------------------------------------------------- events */
/* 通用期間篩選：state 是要寫入的篩選狀態（S 或 SE），ids 是這個分頁對應的
   DOM id 組，repaint 是選完之後要重畫哪個分頁。兩個分頁的期間選單長得一樣，
   邏輯共用，只有「要動誰的狀態、畫誰的畫面」不同。 */
function selectPeriod(state, ids, key, repaint) {
  state.period = key;
  [...$(ids.pillset).children].forEach((c) => c.classList.toggle("is-on", c.dataset.period === key));
  const custom = key === "custom";
  $(ids.range).hidden = !custom;
  if (custom && !state.from && !state.to) {
    // 自訂預設帶出本月，使用者不用從空白開始填
    const now = brokerNow();
    $(ids.from).value = state.from = isoDate(monthStart(now, 0));
    $(ids.to).value = state.to = isoDate(now);
  }
  repaint();
}
const SIGNAL_PERIOD_IDS = { pillset: "filterPeriod", range: "dateRange", from: "dateFrom", to: "dateTo" };
const EA_PERIOD_IDS = { pillset: "eaFilterPeriod", range: "eaDateRange", from: "eaDateFrom", to: "eaDateTo" };
$("filterPeriod").addEventListener("click", (evt) => {
  const btn = evt.target.closest("[data-period]");
  if (btn) selectPeriod(S, SIGNAL_PERIOD_IDS, btn.dataset.period, paintStats);
});
$("eaFilterPeriod").addEventListener("click", (evt) => {
  const btn = evt.target.closest("[data-period]");
  if (btn) selectPeriod(SE, EA_PERIOD_IDS, btn.dataset.period, paintEA);
});
for (const id of ["dateFrom", "dateTo"]) {
  $(id).addEventListener("change", () => {
    S.from = $("dateFrom").value;
    S.to = $("dateTo").value;
    paintStats();
  });
}
for (const id of ["eaDateFrom", "eaDateTo"]) {
  $(id).addEventListener("change", () => {
    SE.from = $("eaDateFrom").value;
    SE.to = $("eaDateTo").value;
    paintEA();
  });
}
$("filterSource").addEventListener("change", (evt) => { S.source = evt.target.value; paintStats(); });
/* 點來源卡片 = 把下方全部圖表與表格篩選到那個來源；再點一次取消 */
$("sourcePerf").addEventListener("click", (evt) => {
  // 「管理」直接開設定面板。它在卡片裡面，要先攔下來不然會連帶觸發篩選。
  const manage = evt.target.closest("[data-manage]");
  if (manage) {
    evt.stopPropagation();
    const panel = $("toggleSettings");
    if (panel) panel.click();
    const sec = $("settings") || $("settingsPanel");
    if (sec) sec.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
    return;
  }
  const card = evt.target.closest("[data-pick-source]");
  if (!card) return;
  const name = card.dataset.pickSource;
  S.source = S.source === name ? "all" : name;
  const select = $("filterSource");
  if ([...select.options].some((o) => o.value === S.source)) select.value = S.source;
  paintStats();
});
// 卡片從 button 改成 div 後，Enter / Space 要自己接回來
$("sourcePerf").addEventListener("keydown", (evt) => {
  if (evt.key !== "Enter" && evt.key !== " ") return;
  const card = evt.target.closest("[data-pick-source]");
  if (!card || evt.target.closest("[data-manage]")) return;
  evt.preventDefault();
  card.click();
});
$("ladderTabs").addEventListener("click", (evt) => {
  const btn = evt.target.closest("[data-src]");
  if (!btn) return;
  S.ladderSource = btn.dataset.src;
  paintStats();
});
$("viewTabs").addEventListener("click", (evt) => {
  const btn = evt.target.closest("[data-view]");
  if (!btn) return;
  const view = btn.dataset.view;
  [...$("viewTabs").children].forEach((c) => c.classList.toggle("is-on", c === btn));
  $("viewSignals").hidden = view !== "signals";
  $("viewEA").hidden = view !== "ea";
  $("viewMembers").hidden = view !== "members";
  if (view === "members") loadMembers();
});

/* ------------------------------------------------------------- 會員管理 */
/* 只有訊號中心有這一區。瀏覽器不直接打 Hub —— 一律經過本機控制台的
   /api/admin/* 代理，管理 token 才不會落到前端 JS 裡。 */
const MBR = { list: [], tiers: [], filter: "" };

async function adminGet(path) {
  const res = await fetch("/api/admin" + path);
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "讀取失敗");
  return data;
}
async function adminPost(path, body) {
  const res = await fetch("/api/admin" + path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || "操作失敗");
  return data;
}

function expiryText(m) {
  // 進階版以上是用量制:顯示剩餘「使用額度」而非日曆到期日(expires_at 為 null)。
  if (m && m.time_pause && m.usage_seconds_left != null) {
    const secs = Number(m.usage_seconds_left);
    const days = secs / 86400;
    const show = days >= 1 ? `${days.toFixed(1)} 天` : `${Math.max(0, Math.floor(secs / 3600))} 小時`;
    if (secs <= 0) return { text: "額度用盡", cls: "bad" };
    return { text: `額度 ${show}`, cls: days <= 3 ? "warn" : "ok" };
  }
  const ts = m && m.expires_at;
  if (!ts) return { text: "無期限", cls: "ok" };
  const days = Math.floor((ts * 1000 - Date.now()) / 86400000);
  const stamp = new Date(ts * 1000).toLocaleDateString("zh-TW");
  if (days < 0) return { text: `${stamp}（已過期）`, cls: "bad" };
  // 剩不到一週標黃：這是該主動聯繫續費的名單
  return { text: `${stamp}（剩 ${days} 天）`, cls: days <= 7 ? "warn" : "ok" };
}
function seenText(ts) {
  if (!ts) return "—";
  const mins = Math.floor((Date.now() - ts * 1000) / 60000);
  if (mins < 2) return "剛剛";
  if (mins < 60) return `${mins} 分鐘前`;
  if (mins < 1440) return `${Math.floor(mins / 60)} 小時前`;
  return `${Math.floor(mins / 1440)} 天前`;
}

function renderMembers() {
  const q = MBR.filter.trim().toLowerCase();
  const rows = MBR.list.filter((m) =>
    !q || m.username.toLowerCase().includes(q) || (m.note || "").toLowerCase().includes(q));
  $("mbrCount").textContent =
    `共 ${MBR.list.length} 位` + (q ? `，符合 ${rows.length} 位` : "") +
    `　線上 ${MBR.list.filter((m) => m.online).length}`;

  if (!rows.length) {
    $("mbrRows").innerHTML =
      '<tr><td colspan="8" class="muted" style="padding:22px;text-align:center">' +
      (MBR.list.length ? "沒有符合的會員" : "還沒有任何會員，按右上角「開通會員」新增") +
      "</td></tr>";
    return;
  }
  $("mbrRows").innerHTML = rows.map((m) => {
    const exp = expiryText(m);
    const suspended = m.status !== "active";
    const state = suspended
      ? '<span class="mbr-state bad">停權</span>'
      : (m.expired ? '<span class="mbr-state bad">過期</span>'
                   : '<span class="mbr-state ok">正常</span>');
    const u = esc(m.username);
    return "<tr>" +
      `<td class="mono"><b>${u}</b></td>` +
      `<td><span class="mbr-tag t-${esc(m.tier)}">${esc(m.tier_label)}</span></td>` +
      `<td>${state}</td>` +
      `<td class="mbr-state ${exp.cls}">${esc(exp.text)}</td>` +
      `<td class="${m.online ? "mbr-online" : "mbr-offline"}">${m.online ? "● 在線" : "○"}</td>` +
      `<td class="muted">${esc(seenText(m.last_seen_at))}</td>` +
      `<td class="muted">${esc(m.note || "—")}</td>` +
      '<td><div class="mbr-acts">' +
        `<button class="btn" data-act="extend" data-u="${u}">續期</button>` +
        `<button class="btn" data-act="tier" data-u="${u}">改等級</button>` +
        `<button class="btn" data-act="${suspended ? "resume" : "suspend"}" data-u="${u}">` +
          (suspended ? "解除停權" : "停權") + "</button>" +
        `<button class="btn" data-act="passwd" data-u="${u}">重設密碼</button>` +
        (m.online ? `<button class="btn" data-act="kick" data-u="${u}">踢下線</button>` : "") +
        `<button class="btn" data-act="delete" data-u="${u}">刪除</button>` +
      "</div></td></tr>";
  }).join("");
}

function renderLogins(rows) {
  if (!rows.length) {
    $("mbrLoginRows").innerHTML =
      '<tr><td colspan="5" class="muted" style="padding:18px;text-align:center">尚無紀錄</td></tr>';
    return;
  }
  $("mbrLoginRows").innerHTML = rows.map((r) =>
    "<tr>" +
      `<td class="mono muted">${esc(new Date(r.at * 1000).toLocaleString("zh-TW"))}</td>` +
      `<td class="mono">${esc(r.username)}</td>` +
      `<td class="${r.ok ? "mbr-state ok" : "mbr-state bad"}">` +
        (r.ok ? (r.detail === "kicked_previous" ? "成功（踢掉前一台）" : "成功")
              : `失敗 · ${esc(r.detail)}`) + "</td>" +
      `<td class="muted">${esc(r.device || "—")}</td>` +
      `<td class="mono muted">${esc(r.ip || "—")}</td>` +
    "</tr>").join("");
}

async function loadMembers() {
  try {
    if (!MBR.tiers.length) {
      MBR.tiers = (await adminGet("/tiers")).tiers;
      $("mbrTier").innerHTML = MBR.tiers.map((t) =>
        `<option value="${esc(t.key)}">${esc(t.label)}（${t.default_days} 天）</option>`).join("");
      const adv = MBR.tiers.findIndex((t) => t.key === "basic");
      if (adv >= 0) $("mbrTier").selectedIndex = adv;
    }
    MBR.list = (await adminGet("/members")).members;
    renderMembers();
    renderLogins((await adminGet("/logins?limit=60")).logins);
    $("mbrSubtitle").textContent = "已連上 Hub";
  } catch (e) {
    $("mbrSubtitle").textContent = "讀取失敗：" + e.message;
    $("mbrRows").innerHTML =
      '<tr><td colspan="8" class="mbr-state bad" style="padding:22px;text-align:center">' +
      esc(e.message) + "</td></tr>";
  }
}

async function memberAction(act, user) {
  const m = MBR.list.find((x) => x.username === user);
  try {
    if (act === "extend") {
      const d = prompt(`「${user}」要續期幾天？`, "30");
      if (!d) return;
      await adminPost("/members/extend", { username: user, days: Number(d) });
    } else if (act === "tier") {
      const opts = MBR.tiers.map((t, i) => `${i + 1}. ${t.label}`).join("\n");
      const pick = prompt(`「${user}」改成哪個等級？\n${opts}`, "");
      const idx = Number(pick) - 1;
      if (!MBR.tiers[idx]) return;
      await adminPost("/members/update", { username: user, tier: MBR.tiers[idx].key });
    } else if (act === "suspend" || act === "resume") {
      const on = act === "suspend";
      if (on && !confirm(`停權「${user}」？他會立刻斷線且無法登入。`)) return;
      await adminPost("/members/update",
                      { username: user, status: on ? "suspended" : "active" });
    } else if (act === "passwd") {
      if (!confirm(`重設「${user}」的密碼？舊密碼與已登入的裝置會立刻失效。`)) return;
      const r = await adminPost("/members/reset-password", { username: user });
      showIssued(user, r.result.password);
    } else if (act === "kick") {
      await adminPost("/members/kick", { username: user });
    } else if (act === "delete") {
      if (prompt(`刪除「${user}」無法復原。請輸入帳號再確認一次：`) !== user) return;
      await adminPost("/members/delete", { username: user });
    }
    await loadMembers();
  } catch (e) { alert("操作失敗：" + e.message); }
}

function showIssued(user, pass) {
  $("mbrIssuedUser").textContent = user;
  $("mbrIssuedPass").textContent = pass;
  $("mbrIssued").hidden = false;
  $("mbrIssued").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

if (!IS_CLIENT) {
  // 訊號中心固定就是「訊號發布 / 會員管理」兩個分頁。會員端那邊是看有沒有
  // 設定第二顆 EA 才決定要不要出現分頁列，中央機沒有那個條件。
  $("viewTabs").hidden = false;
  $("mbrRefresh").onclick = loadMembers;
  $("mbrSearch").oninput = (e) => { MBR.filter = e.target.value; renderMembers(); };
  $("mbrNewToggle").onclick = () => {
    const f = $("mbrNewForm");
    f.hidden = !f.hidden;
    if (!f.hidden) $("mbrUser").focus();
  };
  $("mbrCancel").onclick = () => { $("mbrNewForm").hidden = true; };
  $("mbrIssuedClose").onclick = () => { $("mbrIssued").hidden = true; };
  $("mbrCopy").onclick = () => {
    const txt = `帳號 ${$("mbrIssuedUser").textContent}\n密碼 ${$("mbrIssuedPass").textContent}`;
    navigator.clipboard.writeText(txt)
      .then(() => { $("mbrCopy").textContent = "已複製";
                    setTimeout(() => ($("mbrCopy").textContent = "複製帳密"), 1500); })
      .catch(() => alert(txt));
  };
  $("mbrCreate").onclick = async () => {
    const user = $("mbrUser").value.trim();
    if (!user) { alert("請輸入帳號"); $("mbrUser").focus(); return; }
    const body = { username: user, tier: $("mbrTier").value, note: $("mbrNote").value.trim() };
    const days = $("mbrDays").value.trim();
    if (days) body.days = Number(days);
    $("mbrCreate").disabled = true;
    try {
      const r = await adminPost("/members", body);
      $("mbrUser").value = ""; $("mbrNote").value = ""; $("mbrDays").value = "";
      $("mbrNewForm").hidden = true;
      showIssued(r.result.username, r.result.password);
      await loadMembers();
    } catch (e) { alert("開通失敗：" + e.message); }
    finally { $("mbrCreate").disabled = false; }
  };
  $("mbrRows").addEventListener("click", (evt) => {
    const b = evt.target.closest("[data-act]");
    if (b) memberAction(b.dataset.act, b.dataset.u);
  });
}

$("start").onclick = () => post("/api/start", collect()).then(refreshStatus).catch((e) => alert(e.message));
$("stop").onclick = () => post("/api/stop").then(refreshStatus).catch((e) => alert(e.message));
// ── 自動儲存 ────────────────────────────────────────────────────────
// 改任何設定就自動存,不用記得按儲存,也不用猜要不要停止/重啟。存完直接告訴你:
// 會員端 → 即時生效(不用停、不用重啟);訊號中心 → 需重新啟動才生效(給一顆重啟鈕)。
let __saveTimer = null, __saveSeq = 0;
function setSaveStatus(text, kind) {
  const el = $("saveStatus");
  if (el) { el.textContent = text || ""; el.className = "save-status" + (kind ? " is-" + kind : ""); }
}
function validateSettingsInputs() {
  // 純文字 JSON 欄位(訊號中心才有)打錯字會被後端默默丟掉,存前先擋一次。
  const eaField = $("ea_sources");
  if (eaField && eaField.value.trim()) {
    try {
      const p = JSON.parse(eaField.value);
      if (typeof p !== "object" || Array.isArray(p) || p === null) throw new Error("需要是物件");
    } catch (e) {
      setSaveStatus("「其他策略」JSON 格式錯誤,尚未儲存：" + e.message, "err");
      return false;
    }
  }
  return true;
}
function doSaveSettings() {
  if (!validateSettingsInputs()) return Promise.reject(new Error("invalid"));
  // 分批平倉手數不夠拆就擋下來,不存 —— 免得存了才發現分批默默失效。
  if (IS_CLIENT) {
    const lotErrors = validateSourceLots();
    if (lotErrors.length) {
      setSaveStatus("✗ 未儲存：" + lotErrors[0], "err");
      return Promise.reject(new Error("lot"));
    }
  }
  const seq = ++__saveSeq;
  setSaveStatus("儲存中…", "saving");
  return post("/api/settings", collect()).then(() => {
    if (seq !== __saveSeq) return;                 // 有更新的儲存蓋過來,別覆寫狀態
    const running = !!(S.status && S.status.running);
    if (IS_CLIENT) {
      setSaveStatus(running ? "✓ 已自動儲存 · 即時生效（不用停止或重啟）" : "✓ 已自動儲存（開始跟單後套用）", "ok");
      if ($("restartApply")) $("restartApply").hidden = true;
    } else {
      setSaveStatus(running ? "✓ 已自動儲存 —— 需重新啟動才生效" : "✓ 已自動儲存", "ok");
      if ($("restartApply")) $("restartApply").hidden = !running;
    }
  }).catch((e) => { if (seq === __saveSeq) setSaveStatus("✗ 儲存失敗：" + e.message, "err"); throw e; });
}
function autoSaveSoon() {
  clearTimeout(__saveTimer);
  setSaveStatus("有變更，自動儲存中…", "pending");
  __saveTimer = setTimeout(() => { doSaveSettings().catch(() => {}); }, 600);
}
// 設定面板內任何輸入/勾選/下拉改動 → 自動儲存。來源表格的 syncSourceProfiles 會先在
// 內層跑完(事件冒泡),加上 600ms debounce,collect() 一定讀到最新的 source_profiles。
// 程式化 populate 欄位不會觸發 input/change,所以每秒刷新狀態不會誤觸自動儲存。
["input", "change"].forEach((ev) => $("settings").addEventListener(ev, autoSaveSoon));
$("save").onclick = () => { clearTimeout(__saveTimer); doSaveSettings().catch((e) => setSaveStatus("✗ " + e.message, "err")); };
if ($("restartApply")) {
  $("restartApply").onclick = () => {
    setSaveStatus("重新啟動中…", "saving");
    post("/api/stop").then(() => post("/api/start", collect())).then(refreshStatus)
      .then(() => { setSaveStatus("✓ 已重新啟動，設定生效", "ok"); $("restartApply").hidden = true; })
      .catch((e) => setSaveStatus("✗ 重啟失敗：" + e.message, "err"));
  };
}
const toggle = $("toggleSettings");
toggle.onclick = () => {
  const panel = $("settings");
  // 會員端的設定永遠是開的（就排在會員權益上面），這顆按鈕只負責捲過去。
  // 訊號中心維持收合／展開。
  if (IS_CLIENT) {
    panel.hidden = false;
    panel.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
    return;
  }
  const open = panel.hidden;
  panel.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  if (open) panel.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
};
$("closeSettings").onclick = () => { $("settings").hidden = true; toggle.setAttribute("aria-expanded", "false"); };

if ($("testLineDatabase")) {
  $("testLineDatabase").onclick = async () => {
    const button = $("testLineDatabase");
    const result = $("lineDatabaseResult");
    button.disabled = true;
    result.textContent = "測試中…";
    try {
      const response = await post("/api/test-line-database", collect());
      const info = response.line_database || {};
      const chats = Array.isArray(info.chats) ? info.chats : [];
      result.textContent = "連線成功 · integrity=" + (info.integrity_check || "unknown") + " · " + chats.length + " 個聊天室";
      await refreshStatus();
    } catch (e) {
      result.textContent = "連線失敗：" + e.message;
    } finally {
      button.disabled = false;
    }
  };
}

if ($("findLineDatabase")) {
  $("findLineDatabase").onclick = async () => {
    const button = $("findLineDatabase");
    const result = $("lineDatabaseResult");
    button.disabled = true;
    result.textContent = "搜尋中…";
    try {
      const response = await post("/api/find-line-databases", {});
      const info = response.line_databases || {};
      const candidates = Array.isArray(info.candidates) ? info.candidates : [];
      if (info.recommended) {
        $("line_database_path").value = info.recommended;
        result.textContent = "找到並已填入資料庫 · 共 " + candidates.length + " 個候選";
      } else if (candidates.length) {
        result.textContent = "找到 " + candidates.length + " 個候選，無法唯一判定；請依 Windows 文件驗證後填入。";
      } else {
        result.textContent = "未在已知位置找到 .edb；請依 Windows 文件手動檢查。";
      }
    } catch (e) {
      result.textContent = "搜尋失敗：" + e.message;
    } finally {
      button.disabled = false;
    }
  };
}

if ($("openHub")) {
  $("openHub").onclick = () => {
    const s = collect();
    let remote = String(s.hub_url || "").trim();
    if (remote.endsWith("/")) remote = remote.slice(0, -1);
    const base = remote || "http://127.0.0.1:" + (s.port || "8765");
    window.open(base + "/?token=" + encodeURIComponent(s.token || ""), "_blank");
  };
}

let resizeTimer = null;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (S.stats) paintStats(); renderKline(); }, 140);
});

refreshStatus();
refreshStats();
bindKline();
bindRailNav();
refreshMarket();
setInterval(refreshStatus, 1000);
setInterval(refreshStats, 3000);
setInterval(refreshMarket, 5000);
</script>
</body>
</html>"""


def render(state: Any) -> str:
    """把 LauncherState 塞進樣板。state 需要 role 與 title。"""
    is_central = getattr(state, "role", "client") == "central"
    # 會員端不放「測試 Hub」與「關閉程式」：連線狀態上方的狀態列已經在顯示，
    # 而關程式走視窗關閉即可，避免會員誤按停掉跟單。
    extra_button = '<button class="btn" id="openHub">開啟 Hub 頁面</button>' if is_central else ""
    subtitle = "訊號發布中心" if is_central else "XAUUSD · 訊號自動跟單"

    # 初始鎖定狀態由伺服器決定，不要等前端第一次 /api/status 回來才蓋上去 ——
    # 那之間會閃過一眼交易面板，未登入的人會以為程式壞了。
    locked = (not is_central) and not getattr(state, "auth", None)

    return (
        PAGE.replace("__TITLE__", str(getattr(state, "title", "黃金跟單")))
        .replace("__BODY_CLASS__", "auth-locked" if locked else "")
        .replace("__GATE_CLASS__", "is-on" if locked else "")
        .replace("__SUBTITLE__", subtitle)
        .replace("__ROLE_JSON__", json.dumps(getattr(state, "role", "client")))
        .replace("__ROLE__", str(getattr(state, "role", "client")))
        .replace("__FIELDS__", CENTRAL_FIELDS if is_central else CLIENT_FIELDS)
        .replace("__EXTRA_BUTTON__", extra_button)
        .replace("__HIGH_FREQ_SOURCE_JSON__", json.dumps(HIGH_FREQ, ensure_ascii=False))
        .replace("__MID_FREQ_SOURCE_JSON__", json.dumps(MID_FREQ, ensure_ascii=False))
        .replace("__ULTRA_HIGH_FREQ_SOURCE_JSON__", json.dumps(ULTRA_HIGH_FREQ, ensure_ascii=False))
        .replace("__LOW_FREQ_SOURCE_JSON__", json.dumps(LOW_FREQ, ensure_ascii=False))
        .replace("__SCHEDULE_LIMIT__", str(SCHEDULE_LIMIT))
        # 會員端的設定一律攤開來（在會員權益上方），不用先按「設定」才看得到；
        # 訊號中心維持收合 —— 那邊的欄位多半是裝好一次就不再動的連線設定。
        .replace("__SETTINGS_HIDDEN__", "hidden" if is_central else "")
        .replace("__TAB1__", "訊號發布" if is_central else "訊號跟單")
        .replace("__PWMIN__", str(MIN_PASSWORD_LENGTH))
    )
