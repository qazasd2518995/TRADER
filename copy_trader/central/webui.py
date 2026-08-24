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
from typing import Any

CLIENT_FIELDS = """
      <div class="field-group">
        <h3>連線</h3>
        <div class="field-grid">
          <label>中央 Hub URL<input id="hub_url" placeholder="http://中央電腦IP:8765" /></label>
          <label>Hub 密碼<input id="token" type="password" /></label>
          <label>MT5 Files 路徑<input id="mt5_files_dir" placeholder="可留空自動偵測" /></label>
          <label>輪詢秒數<input id="interval" placeholder="1.0" /></label>
          <label class="switch">開啟程式後自動開始<input id="auto_start" type="checkbox" /></label>
        </div>
      </div>
      <div class="field-group">
        <h3>下單與馬丁</h3>
        <div class="field-grid">
          <label>基礎手數<input id="default_lot_size" placeholder="0.01" /></label>
          <label class="switch">啟用馬丁格爾<input id="use_martingale" type="checkbox" /></label>
          <label>馬丁倍數<input id="martingale_multiplier" placeholder="2.0（每層 × 倍數）" /></label>
          <label>馬丁最大層數<input id="martingale_max_level" placeholder="5（5 關最大 base×16）" /></label>
          <label>每層自訂手數<input id="martingale_lots" placeholder="留空=用倍數；例 0.01,0.02,0.04,0.08" /></label>
          <label>多 TP 分批平倉<input id="partial_close_ratios" placeholder="例 0.5,0.3,0.2" /></label>
        </div>
      </div>
      <div class="field-group">
        <h3>訊號來源設定</h3>
        <div id="sourceSettings"></div>
        <input type="hidden" id="source_profiles" />
        <p class="hint">每個 LINE 群組可以各自設定。選「均注」= 每筆固定手數、不進關卡；選「馬丁」= 逐關加碼，各群層級獨立計算，互不影響。沒有列出來的來源會套用上方的全域設定。</p>
      </div>
      <div class="field-group">
        <h3>自動刪單</h3>
        <div class="field-grid">
          <label>幾秒未進場刪單<input id="cancel_pending_after_seconds" placeholder="10800（3 小時）；0 = 不刪" /></label>
          <label>價格偏離幾 % 刪單<input id="cancel_if_price_beyond_percent" placeholder="0 = 停用（目前設定）" /></label>
        </div>
        <p class="hint">掛單超過設定時間仍未成交，會員端會自動撤掉並通知 MT5 刪除委託。目前只用逾時這一條規則，價格偏離刪單已停用（填 0）。改動只影響之後送出的新單。</p>
      </div>
      <div class="field-group">
        <h3>其他策略（EA 自動下單）</h3>
        <div class="field-grid">
          <label>魔術編號 → 名稱<input id="ea_sources" placeholder='{"20260503": "趨勢線策略"}' /></label>
        </div>
        <p class="hint">同一個 MT5 帳戶裡，另外掛的、不靠訊號、自己判斷進出場的 EA（例如趨勢線策略）——填「魔術編號: 名稱」讓報表認出這是誰下的單，會併入下面的績效卡片與交易紀錄一起看。這裡純粹是標籤，不會控制那顆 EA 的手數或進出場，手數要調就直接改 MT5 裡 EA 的設定。魔術編號在 MT5 的 EA 輸入參數裡找（通常叫「魔術編號」或 Magic Number）。</p>
      </div>
"""

CENTRAL_FIELDS = """
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
<style>
/* ---------------------------------------------------------------- tokens */
:root {
  color-scheme: light;
  --paper:    #f6f0e3;
  --card:     #fdfaf3;
  --sunk:     #efe8d8;
  --ink:      #1a1410;
  --ink-2:    #5c5348;
  --muted:    #8a8073;
  --hair:     #e8e0cf;
  --rule:     #cdc4b0;
  --gold:     #8a6318;
  --gold-mark:#a8781a;
  --gold-lit: #d4a017;
  --gold-hi:  #ffd86b;
  /* 獲利=藍、虧損=紅。刻意不用台股的紅漲綠跌 —— 做黃金整天看下來滿屏紅字，
     即使是賺的也會有壓迫感。藍色在亮底與暗底都夠清楚，跟紅的色相距離也遠，
     不會像橘/綠那樣在小圖例裡被誤讀。 */
  --win:      #1668dc;
  --loss:     #d42a3f;
  --win-wash: rgba(22, 104, 220, 0.10);
  --loss-wash:rgba(212, 42, 63, 0.10);
  /* 連線/存活指示燈用的綠 —— 以前借用 --loss，翻轉配色後會變成紅燈，語意相反。 */
  --ok:       #0e7c5a;
  --ok-wash:  rgba(14, 124, 90, 0.10);
  --shadow:   0 1px 2px rgba(26,20,16,.05), 0 8px 24px -16px rgba(26,20,16,.30);

  --sans: system-ui, -apple-system, "Segoe UI", "PingFang TC", "Noto Sans TC",
          "Microsoft JhengHei", sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
/* 亮色是預設，不跟隨系統的深色偏好——會員端要的就是明亮。
   夜間可用右上角的日夜切換，選擇會記在 localStorage。 */
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper:#100d0a; --card:#17130f; --sunk:#1f1a15;
  --ink:#f4efe4; --ink-2:#a89c8a; --muted:#8f8474;
  --hair:#2a231c; --rule:#3a3128;
  --gold:#f0c65c; --gold-mark:#e0b23f; --gold-lit:#e8b93f; --gold-hi:#ffe08a;
  --win:#4d9bff; --loss:#fa3a46;
  --win-wash:rgba(77,155,255,.16); --loss-wash:rgba(250,58,70,.14);
  --ok:#1e9c80; --ok-wash:rgba(30,156,128,.14);
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
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
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; }
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
.rail-id { display: flex; align-items: center; gap: 12px; margin-right: auto; }
.rail-id h1 { font-size: 17px; }
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
  font: inherit; font-size: 13px; font-weight: 500;
  padding: 7px 14px; border-radius: 7px;
  border: 1px solid var(--rule); background: var(--card); color: var(--ink);
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

/* ------------------------------------------------------------------ main */
main { max-width: 1240px; margin: 0 auto; padding: 22px 24px 72px; }
.card {
  background: var(--card); border: 1px solid var(--hair);
  border-radius: 12px; padding: 18px 20px; box-shadow: var(--shadow);
}
.eyebrow {
  margin: 0 0 6px; font-size: 11px; font-weight: 600;
  letter-spacing: .13em; text-transform: uppercase; color: var(--muted);
}
.section-head {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin: 30px 0 12px;
}
.section-head h2 { font-size: 15px; }
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
.flat-note .big { font-size: 34px; font-weight: 650; letter-spacing: -.02em; }
.flat-note p { margin: 4px 0 0; font-size: 12.5px; color: var(--muted); max-width: 30ch; }

/* 每群下單設定表 */
.src-table { width: 100%; border-collapse: collapse; font-size: 13px; }
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
.src-name { font-weight: 600; white-space: nowrap; }
.src-meta { font-size: 11px; color: var(--muted); font-weight: 400; }
.src-add { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.src-add input {
  font: inherit; font-size: 12.5px; padding: 6px 10px; flex: 1 1 280px; min-width: 0;
  border: 1px solid var(--rule); border-radius: 7px; background: var(--paper); color: var(--ink);
}

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
  margin: 2px 0 0; font-size: 52px; line-height: 1.05; font-weight: 650;
  letter-spacing: -0.035em; white-space: nowrap;
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
.tile dd { margin: 0; font-size: 25px; font-weight: 620; letter-spacing: -.02em; line-height: 1.1; }
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
  font: inherit; font-size: 13.5px; font-weight: 500; padding: 9px 18px;
  border-radius: 9px; border: 1px solid var(--hair); background: var(--card);
  color: var(--ink-2); cursor: pointer; box-shadow: var(--shadow);
}
.view-tab:hover { color: var(--ink); border-color: var(--rule); }
.view-tab.is-on {
  color: #2a1d05; font-weight: 600; border-color: transparent;
  background: linear-gradient(158deg, var(--gold-hi), var(--gold-lit) 60%, var(--gold-mark));
  box-shadow: inset 0 1px 0 rgba(255,255,255,.5);
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
.countdown { font-variant-numeric: tabular-nums; color: var(--ink-2); }
.countdown.urgent { color: var(--gold); font-weight: 700; }
/* 掛單期間最接近過的差距，比目前更近時附註 */
.near { color: var(--muted); font-size: 11px; }

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
.field-grid input, .field-grid select {
  font: inherit; font-size: 13px; padding: 7px 10px; min-width: 0;
  border: 1px solid var(--rule); border-radius: 7px;
  background: var(--paper); color: var(--ink);
}
.field-grid input[type="checkbox"] { width: 17px; height: 17px; accent-color: var(--gold-mark); }
.hint { margin: 14px 0 0; font-size: 12.5px; color: var(--muted); }
.settings-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--hair); }

.notice {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 12px 15px; border-radius: 10px; margin-bottom: 14px;
  background: var(--sunk); border: 1px solid var(--hair); font-size: 13px; color: var(--ink-2);
}
.notice b { color: var(--ink); }

body[data-role="central"] .client-only { display: none; }

/* ── 會員登入 ─────────────────────────────────────────────────────────
   蓋在整個面板之上。沒登入前不讓人看到任何交易畫面, 也就不會有「以為在
   跟單、其實沒登入」的誤會。 */
#authGate {
  position: fixed; inset: 0; z-index: 900;
  display: none; align-items: center; justify-content: center;
  background: var(--bg); padding: 24px;
}
#authGate.is-on { display: flex; }
body.auth-locked > *:not(#authGate) { filter: blur(3px); pointer-events: none; user-select: none; }
.auth-card {
  width: 100%; max-width: 380px;
  background: var(--card); border: 1px solid var(--hair); border-radius: 16px;
  padding: 30px 28px 26px; box-shadow: 0 18px 50px rgba(0,0,0,.16);
}
.auth-brand { display: flex; align-items: center; gap: 11px; margin-bottom: 6px; }
.auth-brand h2 { font-size: 18px; margin: 0; letter-spacing: .3px; }
.auth-sub { color: var(--ink-2); font-size: 12.5px; margin: 0 0 22px; }
.auth-field { display: block; margin-bottom: 14px; }
.auth-field span { display: block; font-size: 12px; color: var(--ink-2); margin-bottom: 5px; }
.auth-field input {
  width: 100%; box-sizing: border-box; padding: 10px 12px; font-size: 14px;
  border: 1px solid var(--hair); border-radius: 9px;
  background: var(--sunk); color: var(--ink); font-family: inherit;
}
.auth-field input:focus { outline: 2px solid var(--win); outline-offset: 1px; border-color: transparent; }
#authSubmit { width: 100%; margin-top: 8px; padding: 11px; font-size: 14.5px; font-weight: 600; }
#authSubmit[disabled] { opacity: .55; cursor: progress; }
.auth-msg {
  margin-top: 14px; padding: 10px 12px; border-radius: 9px; font-size: 12.5px;
  background: var(--loss-wash); color: var(--loss); border: 1px solid transparent;
  display: none;
}
.auth-msg.is-on { display: block; }
.auth-foot { margin-top: 18px; font-size: 11.5px; color: var(--ink-3, var(--ink-2)); line-height: 1.7; }
/* 已登入時顯示在頂列的身分徽章 */
.auth-badge {
  display: inline-flex; align-items: center; gap: 7px; padding: 3px 10px;
  border-radius: 999px; background: var(--sunk); border: 1px solid var(--hair);
  font-size: 12px; color: var(--ink-2);
}
.auth-badge b { color: var(--ink); font-weight: 600; }
.auth-badge .tier { color: var(--win); font-weight: 600; }
.auth-badge.is-soon { border-color: var(--loss); }
.auth-badge.is-soon .exp { color: var(--loss); font-weight: 600; }
#authLogout { margin-left: 2px; padding: 1px 7px; font-size: 11px; }

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; }
}
</style>
</head>
<body data-role="__ROLE__">

<div id="authGate" class="client-only">
  <form class="auth-card" id="authForm" autocomplete="on">
    <div class="auth-brand">
      <span class="bullion" aria-hidden="true"></span>
      <h2>黃金跟單會員端</h2>
    </div>
    <p class="auth-sub">請以會員帳號登入後開始跟單</p>

    <label class="auth-field">
      <span>帳號</span>
      <input id="authUser" name="username" autocomplete="username"
             autocapitalize="off" spellcheck="false" required />
    </label>
    <label class="auth-field">
      <span>密碼</span>
      <input id="authPass" name="password" type="password"
             autocomplete="current-password" required />
    </label>

    <button class="btn btn-go" id="authSubmit" type="submit">登 入</button>
    <div class="auth-msg" id="authMsg"></div>

    <p class="auth-foot">
      一組帳號同時只能在一台電腦使用；在別台登入會把這台登出。<br />
      忘記密碼或需要續期，請聯繫管理員。
    </p>
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
  <div class="rail-state">
    <span class="auth-badge client-only" id="authBadge" hidden>
      <b id="authBadgeUser"></b>
      <span class="tier" id="authBadgeTier"></span>
      <span class="exp" id="authBadgeExp"></span>
      <button class="btn" id="authLogout" type="button">登出</button>
    </span>
    <span class="chip is-off" id="chipService"><span class="dot"></span><span>載入中</span></span>
    <span class="chip is-off client-only" id="chipMt5"><span class="dot"></span><span>MT5</span></span>
    <span class="chip is-off" id="chipHub"><span class="dot"></span><span>Hub</span></span>
  </div>
  <div class="rail-actions">
    <button class="btn btn-go" id="start">開始跟單</button>
    <button class="btn" id="stop">停止</button>
    __EXTRA_BUTTON__
    <button class="btn btn-quiet" id="themeToggle" title="切換日夜模式" aria-label="切換日夜模式">☾</button>
    <button class="btn" id="toggleSettings" aria-expanded="false">設定</button>
  </div>
</header>

<main>
  <div id="notice"></div>

  <!-- 只有設定過「其他策略(EA)」才出現；沒用這功能的人畫面完全不變 -->
  <div class="view-tabs client-only" id="viewTabs" hidden>
    <button type="button" class="view-tab is-on" data-view="signals">訊號跟單</button>
    <button type="button" class="view-tab" data-view="ea">趨勢線策略</button>
  </div>

<div id="viewSignals">
  <!-- 現在的狀態：不受下方篩選影響 -->
  <section class="card hero">
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

  <div class="client-only">
    <div class="section-head">
      <h2>待成交掛單</h2>
      <p id="pendingSummary">—</p>
    </div>
    <div class="card" style="padding:0"><div id="pending"></div></div>

    <div class="section-head">
      <h2>目前持倉</h2>
      <p id="posSummary">—</p>
    </div>
    <div class="card" style="padding:0"><div id="positions"></div></div>

    <div class="section-head">
      <h2>績效分析</h2>
      <p>下方全部圖表與表格共用同一組篩選</p>
    </div>

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

    <dl class="tiles" id="tiles"></dl>

    <div class="section-head">
      <h2>累計損益曲線</h2>
      <p>每一筆平倉後的累積結果，單位 <span id="curCode">USD</span></p>
    </div>
    <div class="card">
      <div class="chart-wrap" id="equityWrap">
        <svg id="equityChart" role="img" aria-label="累計損益曲線"></svg>
        <div class="tip" id="equityTip"></div>
      </div>
    </div>

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

    <div class="section-head">
      <h2>各來源績效</h2>
      <p>每個訊號群組各自獨立計算，點卡片可把下方全部篩選到那一個來源</p>
    </div>
    <div class="source-grid" id="sourcePerf"></div>

    <div class="section-head">
      <h2>交易紀錄</h2>
      <p>每一筆的完整數字，也是上方圖表的表格版</p>
    </div>
    <div class="card" style="padding:0">
      <div class="table-scroll" id="records"></div>
    </div>
  </div>
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

  <div class="section-head">
    <h2>狀態紀錄</h2>
    <p id="uptime"></p>
  </div>
  <div class="card"><div class="logs" id="logs"></div></div>

  <section class="card settings" id="settings" hidden style="margin-top:14px">
    <div class="section-head" style="margin:0 0 16px">
      <h2>設定</h2>
      <p>改完按「儲存設定」，或直接按上方「開始跟單」一併套用</p>
    </div>
    __FIELDS__
    <p class="hint" id="hint"></p>
    <div class="settings-actions">
      <button class="btn btn-go" id="save">儲存設定</button>
      <button class="btn" id="closeSettings">收起</button>
    </div>
  </section>
</main>

<script>
"use strict";
const ROLE = __ROLE_JSON__;
const IS_CLIENT = ROLE !== "central";
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
  "焦點利潤(yuyu)": "高頻交易",
  "黃金報單🈲言群": "中頻交易",
};
const srcName = (s) => SOURCE_ALIAS[s] || (s || "未標記來源");

function ids() {
  return ROLE === "central"
    ? ["hub_url", "host", "port", "token", "interval", "cloudflare_tunnel", "cloudflared_path", "auto_start"]
    : ["hub_url", "token", "mt5_files_dir", "interval", "auto_start", "default_lot_size", "use_martingale",
       "martingale_multiplier", "martingale_max_level", "martingale_lots", "partial_close_ratios",
       "cancel_pending_after_seconds", "cancel_if_price_beyond_percent", "source_profiles", "ea_sources"];
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
  let cum = 0, peak = 0, dd = 0, ls = 0, worst = 0;
  for (const t of trades) {
    cum += t.profit; peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum);
    if (t.is_win) ls = 0; else { ls += 1; worst = Math.max(worst, ls); }
  }
  return {
    total: trades.length, wins: wins.length, losses: losses.length,
    win_rate: trades.length ? (wins.length / trades.length) * 100 : 0,
    net: gw + gl, gross_win: gw, gross_loss: gl,
    profit_factor: gl ? gw / Math.abs(gl) : null,
    avg_win: wins.length ? gw / wins.length : 0,
    avg_loss: losses.length ? gl / losses.length : 0,
    best: trades.reduce((a, t) => Math.max(a, t.profit), 0),
    max_dd: dd, max_loss_streak: worst,
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
  const W = Math.max(320, wrap.clientWidth), H = 268;
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
      return '<button type="button" class="source-card' + (S.source === name ? " is-on" : "") +
          '" data-pick-source="' + esc(name) + '">' +
        '<div class="sc-head"><span class="sc-name">' + esc(srcName(name)) + "</span>" +
          (badge ? '<span class="tag tag-lv">' + badge + (cfg.enabled === false ? " · 停用" : "") + "</span>" : "") +
        "</div>" +
        '<div class="sc-net ' + toneClass(sum.net) + '">' +
          (list.length ? money(sum.net, { signed: true, compact: true }) : "—") + "</div>" +
        '<div class="sc-chart">' +
          (list.length ? miniCurve(list, 260, 54) : '<div class="sc-blank">此區間無成交</div>') + "</div>" +
        '<dl class="sc-stats">' +
          "<div><dt>筆數</dt><dd>" + sum.total + "</dd></div>" +
          "<div><dt>勝率</dt><dd>" + (list.length ? pct(sum.win_rate) : "—") + "</dd></div>" +
          '<div><dt>贏 / 輸</dt><dd><span class="up">' + sum.wins + '</span> / <span class="down">' + sum.losses + "</span></dd></div>" +
          "<div><dt>最大連敗</dt><dd>" + sum.max_loss_streak + "</dd></div>" +
        "</dl>" +
      "</button>";
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

/* 待成交掛單：倒數本地每秒自己跑，不等 3 秒一次的輪詢，會員才看得到它真的在動 */
function durationText(seconds) {
  if (seconds == null) return "不自動刪單";
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return h + " 小時 " + String(m).padStart(2, "0") + " 分";
  if (m) return m + " 分 " + String(sec).padStart(2, "0") + " 秒";
  return sec + " 秒";
}
function tickCountdowns() {
  const now = Date.now();
  for (const cell of document.querySelectorAll("[data-deadline]")) {
    const left = (Number(cell.dataset.deadline) - now) / 1000;
    cell.textContent = left <= 0 ? "刪單中…" : durationText(left);
    cell.classList.toggle("urgent", left > 0 && left <= 900);   // 剩不到 15 分鐘
  }
}
function renderPending(pending, running) {
  const box = $("pending");
  const rule = (S.stats && S.stats.cancel_rules) || {};
  const ruleText = rule.after_seconds
    ? "超過 " + durationText(rule.after_seconds) + " 未進場自動刪單"
    : "目前未設定逾時刪單";
  $("pendingSummary").textContent = pending.length
    ? pending.length + " 筆等待進場 · " + ruleText
    : (running ? "沒有等待中的掛單 · " + ruleText : "服務未啟動");

  if (!pending.length) {
    box.innerHTML = '<div class="empty"><b>' +
      (running ? "沒有等待中的掛單" : "服務未啟動，看不到掛單") + "</b>" +
      (running ? esc(ruleText) : "按上方「開始跟單」後，等待進場的單會出現在這裡") + "</div>";
    return;
  }
  const rows = pending.map((p) => {
    const deadline = p.remaining_seconds == null ? null : Date.now() + p.remaining_seconds * 1000;
    // tracked=false 是 MT5 上真的有、但會員端沒在管的單，不能假裝它有倒數
    const countdown = p.tracked === false
      ? '<span class="tag tag-warn">未追蹤</span>'
      : (deadline ? durationText(p.remaining_seconds) : "不自動刪單");
    return "<tr>" +
      '<td class="' + (p.side === "buy" ? "side-buy" : "side-sell") + '">' + (p.side === "buy" ? "買進" : "賣出") + "</td>" +
      "<td>" + esc(p.symbol || "XAUUSD") + "</td>" +
      '<td class="num mono">' + (p.entry_price ? n2.format(p.entry_price) : "—") + "</td>" +
      '<td class="num mono">' + (p.sl ? n2.format(p.sl) : "—") + "</td>" +
      '<td class="num mono">' + (p.tp ? n2.format(p.tp) : "—") + "</td>" +
      '<td class="num mono">' + (p.distance == null ? "—" :
        n2.format(p.distance) + (p.closest_gap != null && p.closest_gap < p.distance
          ? '<span class="near"> ↓' + n2.format(p.closest_gap) + "</span>" : "")) + "</td>" +
      '<td class="mono">' + (p.elapsed_seconds == null ? esc(p.setup_time || "—") : durationText(p.elapsed_seconds)) + "</td>" +
      '<td class="num countdown"' + (deadline ? ' data-deadline="' + deadline + '"' : "") + ">" + countdown + "</td>" +
      "<td>" + esc(p.source ? srcName(p.source) : "—") + "</td>" +
      '<td class="mono">' + esc(p.ticket == null ? "尚未取得" : p.ticket) + "</td>" +
    "</tr>";
  }).join("");
  const untracked = pending.filter((p) => p.tracked === false).length;
  box.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
      "<th>方向</th><th>商品</th><th class=\"num\">掛單價</th><th class=\"num\">停損</th>" +
      "<th class=\"num\">停利</th><th class=\"num\">距成交</th><th>已等待</th><th class=\"num\">距自動刪單</th>" +
      "<th>訊號來源</th><th>單號</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
    (untracked
      ? '<div class="notice" style="margin:12px 14px 14px">' +
        "<div><b>有 " + untracked + " 張單在 MT5 上，但會員端沒有在管</b>" +
        "這些單不會逾時自動刪，成交後的輸贏也不會計入馬丁層級。按「停止」再「開始跟單」" +
        "會重新認領它們。</div></div>"
      : "");
  tickCountdowns();
}

function renderPositions(positions, currency, ids) {
  ids = ids || { box: "positions", summary: "posSummary" };
  const box = $(ids.box);
  const floating = positions.reduce((a, p) => a + p.profit, 0);
  $(ids.summary).innerHTML = positions.length
    ? positions.length + " 筆持倉中 · 浮動 <b class=\"" + toneClass(floating) + "\">" + money(floating, { signed: true }) + "</b>"
    : "目前沒有持倉";
  if (!positions.length) {
    box.innerHTML = '<div class="empty"><b>目前沒有持倉</b>收到訊號並成交後會出現在這裡</div>';
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

/* 每群下單設定表。來源清單是自動發現的（會員端收過的訊號都會列出來），
   使用者不用手打群組名——打錯字只會靜默套用全域設定，很難察覺。 */
function renderSourceSettings(rows) {
  const box = $("sourceSettings");
  if (!rows.length) {
    box.innerHTML = '<div class="empty" style="padding:20px"><b>還沒收過任何訊號來源</b>' +
      "收到第一筆訊號後，來源會自動出現在這裡讓你設定</div>";
    return;
  }
  box.innerHTML =
    '<table class="src-table"><thead><tr>' +
      "<th>訊號來源</th><th>跟單</th><th>模式</th><th>基礎手數</th><th>馬丁倍數</th><th>關卡數</th><th>多 TP 處理</th>" +
    "</tr></thead><tbody>" +
    rows.map((r) => '' +
      '<tr data-source-row="' + esc(r.source) + '">' +
        '<td><div class="src-name">' + esc(srcName(r.source)) + "</div>" +
          '<div class="src-meta">已成交 ' + r.trades + " 筆" +
          (r.configured ? "" : " · 目前套用全域設定") + "</div></td>" +
        '<td><input type="checkbox" class="sp-enabled"' + (r.enabled ? " checked" : "") + " /></td>" +
        '<td><select class="sp-mode">' +
          '<option value="martingale"' + (r.mode === "martingale" ? " selected" : "") + ">馬丁</option>" +
          '<option value="flat"' + (r.mode === "flat" ? " selected" : "") + ">均注</option>" +
        "</select></td>" +
        '<td><input type="number" class="sp-base" step="0.01" min="0.01" value="' + r.base_lot + '" /></td>' +
        '<td><input type="number" class="sp-mult" step="0.1" min="1" value="' + r.multiplier + '" /></td>' +
        '<td><input type="number" class="sp-max" step="1" min="1" max="12" value="' + r.max_level + '" /></td>' +
        '<td><select class="sp-tpmode">' +
          '<option value="partial"' + (r.tp_mode === "partial" ? " selected" : "") + ">分批平倉</option>" +
          '<option value="breakeven"' + (r.tp_mode === "breakeven" ? " selected" : "") + ">保本移損</option>" +
        "</select></td>" +
      "</tr>").join("") +
    "</tbody></table>" +
    '<div class="src-add">' +
      '<input type="text" id="newSourceName" placeholder="群組名稱（要跟訊號中心的顯示名稱完全一致）" />' +
      '<button type="button" class="btn" id="addSource">新增來源</button>' +
    "</div>";

  box.addEventListener("change", syncSourceProfiles);
  box.addEventListener("input", syncSourceProfiles);
  $("addSource").onclick = addSourceRow;
  $("newSourceName").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addSourceRow(); } };
  syncSourceProfiles();
}

/* 手動新增：讓還沒發過訊號的群組可以「先設定好再上線」。
   不然只能等它發第一筆才會出現在表上，而那第一筆已經照全域設定下出去了。 */
function addSourceRow() {
  const input = $("newSourceName");
  const name = (input.value || "").trim();
  if (!name) return;
  if (document.querySelector('[data-source-row="' + CSS.escape(name) + '"]')) {
    alert("「" + name + "」已經在清單裡了");
    return;
  }
  const tr = document.createElement("tr");
  tr.dataset.sourceRow = name;
  tr.innerHTML =
    '<td><div class="src-name">' + esc(srcName(name)) + "</div>" +
      '<div class="src-meta">手動新增 · 尚未收過訊號</div></td>' +
    '<td><input type="checkbox" class="sp-enabled" checked /></td>' +
    '<td><select class="sp-mode"><option value="martingale">馬丁</option>' +
      '<option value="flat" selected>均注</option></select></td>' +
    '<td><input type="number" class="sp-base" step="0.01" min="0.01" value="0.01" /></td>' +
    '<td><input type="number" class="sp-mult" step="0.1" min="1" value="2" /></td>' +
    '<td><input type="number" class="sp-max" step="1" min="1" max="12" value="5" /></td>' +
    '<td><select class="sp-tpmode"><option value="partial">分批平倉</option>' +
      '<option value="breakeven" selected>保本移損</option></select></td>';
  document.querySelector(".src-table tbody").appendChild(tr);
  input.value = "";
  syncSourceProfiles();
}

function syncSourceProfiles() {
  const out = {};
  for (const row of document.querySelectorAll("[data-source-row]")) {
    const mode = row.querySelector(".sp-mode").value;
    const martingale = mode === "martingale";
    // 均注沒有倍數與關卡可言，把欄位鎖住比留著讓人填了沒作用好
    row.querySelector(".sp-mult").disabled = !martingale;
    row.querySelector(".sp-max").disabled = !martingale;
    const entry = {
      enabled: row.querySelector(".sp-enabled").checked,
      mode,
      base_lot: parseFloat(row.querySelector(".sp-base").value) || 0.01,
      tp_mode: row.querySelector(".sp-tpmode").value,
    };
    if (martingale) {
      entry.multiplier = parseFloat(row.querySelector(".sp-mult").value) || 2;
      entry.max_level = parseInt(row.querySelector(".sp-max").value, 10) || 5;
    }
    out[row.dataset.sourceRow] = entry;
  }
  $("source_profiles").value = JSON.stringify(out);
}

/* -------------------------------------------------------------- painting */
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
  const srcSig = srcRows.map((r) => r.source).join("|");
  if ($("sourceSettings").dataset.signature !== srcSig) {
    $("sourceSettings").dataset.signature = srcSig;
    renderSourceSettings(srcRows);
  }

  renderLadder(stats.martingale || {}, isAll ? stats.cycles : null, srcRows);
  renderPending(stats.pending || [], !!(S.status && S.status.running));
  renderPositions(signalPositions, CURRENCY);
  renderTiles(sum, isAll ? stats.cycles : null);
  renderEquity(trades);
  renderBars(trades);
  renderSourcePerformance(trades, srcRows);
  renderRecords(trades);

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
  chip.lastElementChild.textContent = snap.running ? "跟單運轉中" : snap.status;

  const hub = $("chipHub");
  const hubUrl = String((snap.settings && snap.settings.hub_url) || "").trim();
  hub.className = "chip " + (hubUrl ? "is-live" : "is-warn");
  hub.lastElementChild.textContent = hubUrl ? hubUrl.replace(/^https?:\/\//, "") : "未設定 Hub";

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

function paintCentralHint(snap) {
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
function paintAuth(snap) {
  if (!IS_CLIENT) return;
  const a = snap.auth || { logged_in: false };
  const gate = $("authGate");
  const locked = !a.logged_in;
  gate.classList.toggle("is-on", locked);
  document.body.classList.toggle("auth-locked", locked);

  if (locked) {
    // 被踢下線 / 到期 / 停權時，後端會把原因放在 auth.error
    if (a.error) showAuthMsg(a.error);
    const badge = $("authBadge");
    if (badge) badge.hidden = true;
    const u = $("authUser");
    if (u && document.activeElement !== u && !$("authPass").value) u.focus();
    return;
  }

  const badge = $("authBadge");
  badge.hidden = false;
  $("authBadgeUser").textContent = a.username || "";
  $("authBadgeTier").textContent = a.tier_label || "";
  const exp = Number(a.expires_at || 0);
  if (exp) {
    const days = Math.floor((exp * 1000 - Date.now()) / 86400000);
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
        $("authPass").value = "";
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
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    const snap = await res.json();
    if (!snap.ok) return;
    S.status = snap;
    if (!S.filled) { fill(snap.settings); S.filled = true; }
    paintAuth(snap);
    paintStatus();
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
  $("themeToggle").textContent = mode === "dark" ? "☀" : "☾";
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* 無痕模式 */ }
}
let savedTheme = "light";
try { if (localStorage.getItem(THEME_KEY) === "dark") savedTheme = "dark"; } catch (e) { /* 同上 */ }
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
  const card = evt.target.closest("[data-pick-source]");
  if (!card) return;
  const name = card.dataset.pickSource;
  S.source = S.source === name ? "all" : name;
  const select = $("filterSource");
  if ([...select.options].some((o) => o.value === S.source)) select.value = S.source;
  paintStats();
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
});

$("start").onclick = () => post("/api/start", collect()).then(refreshStatus).catch((e) => alert(e.message));
$("stop").onclick = () => post("/api/stop").then(refreshStatus).catch((e) => alert(e.message));
$("save").onclick = (evt) => {
  // 純文字 JSON 欄位打錯字後端會直接當沒設定（回退全域），不是報錯——
  // 存檔前先擋一次，不然使用者不會發現自己輸入的東西被默默丟掉了。
  const eaField = $("ea_sources");
  if (eaField && eaField.value.trim()) {
    try {
      const parsed = JSON.parse(eaField.value);
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error("需要是物件");
    } catch (e) {
      alert("「其他策略」欄位不是合法的 JSON，例如 {\"20260503\": \"趨勢線策略\"}\n\n" + e.message);
      return;
    }
  }
  post("/api/settings", collect()).then(() => {
    const btn = evt.target;
    btn.textContent = "已儲存";
    setTimeout(() => { btn.textContent = "儲存設定"; }, 1400);
  }).catch((e) => alert(e.message));
};
const toggle = $("toggleSettings");
toggle.onclick = () => {
  const panel = $("settings");
  const open = panel.hidden;
  panel.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  if (open) panel.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
};
$("closeSettings").onclick = () => { $("settings").hidden = true; toggle.setAttribute("aria-expanded", "false"); };

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
  resizeTimer = setTimeout(() => { if (S.stats) paintStats(); }, 140);
});

refreshStatus();
refreshStats();
setInterval(refreshStatus, 1000);
setInterval(refreshStats, 3000);
setInterval(tickCountdowns, 1000);
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

    return (
        PAGE.replace("__TITLE__", str(getattr(state, "title", "黃金跟單")))
        .replace("__SUBTITLE__", subtitle)
        .replace("__ROLE_JSON__", json.dumps(getattr(state, "role", "client")))
        .replace("__ROLE__", str(getattr(state, "role", "client")))
        .replace("__FIELDS__", CENTRAL_FIELDS if is_central else CLIENT_FIELDS)
        .replace("__EXTRA_BUTTON__", extra_button)
    )
