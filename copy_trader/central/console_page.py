"""會員的手機控制台（Hub 直接吐的一頁 HTML）。

為什麼是手寫的一頁：Hub 的映像檔只複製 copy_trader/central 且不裝任何第三方
套件（見 Dockerfile），所以沒有樣板引擎、沒有前端框架、也不能外連 CDN。

功能對齊電腦版會員端（webui.py）：帳戶淨值與績效、持倉與掛單、跟單開關、
每個訊號來源各自的策略、自動排程、方案與到期倒數、修改密碼。但排版是重做的
—— 電腦版是三欄面板加表格，手機上照搬會變成一堆要橫向捲的小字。內容太多，
所以切成四個分頁，底部固定導覽列（拇指構得到的位置）。

  總覽   跟單開關、淨值、今日損益、持倉、掛單、績效（指標跟電腦版同一套）
  策略   每個來源獨立設定；欄位、預設值、鎖定規則都照電腦版
  排程   自動跟單時段
  帳號   會員名稱、方案、到期／額度倒數、權益清單、修改密碼

**策略分頁跟電腦版一模一樣的地方，逐條列出來，改任何一邊都要對照另一邊：**

  * 下單方式：均注／馬丁／本金比例。方案沒含的選項留在清單裡但選不到，
    存著的值若是選不到的，顯示成均注（webui pickLotMode）。
  * 基礎手數：本金比例模式下藏起來改顯示「每筆風險 %」。
  * 馬丁：倍數與關卡數只在馬丁模式出現，並且畫出整條階梯（每關實際手數）、
    現在在第幾關、連續虧損幾筆、下一手多少 —— 電腦版的「馬丁階梯」卡。
    階梯上若有一關的手數跟上一關一樣（例如 0.01 × 1.5 = 0.015 被 MT5 的 0.01
    跳動吃掉），直接標出來 —— 2026-09-09 實倉就這樣「馬丁層級升了、手數沒變」。
  * 止盈處理：分批平倉／保本移損／單一點位。中頻一單只有一個止盈，
    只給單一點位與保本移損（webui tpOptions）。
  * 分批平倉是**填每一段的實際手數**（0.01/0.01/0.01），不是比例。
    基礎手數自動 = 各段總和、不能手改；內部存成佔比，馬丁加碼時比例跟著放大。
    每段都要 ≥ 0.01（MT5 最低），不合格擋存（webui validateSourceLots）。
  * 保本距離只在保本移損出現；空白列預設 3 美元，0 會提醒「退回觸及第一個止盈才保本」。
  * 每日止盈／每日止損；0 = 不限。
  * 電腦版沒有的欄位這裡也沒有（max_active_orders / max_daily_trades 是保留欄位，
    面板早就移除）。

**沒有任何全域交易設定。** 電腦版面板一個都沒有（default_lot_size /
use_martingale / martingale_* / partial_close_ratios 在 webui.py 裡出現 0 次），
那些值只是「某來源沒設定時的預設種子」。手機上多做一個全域手數會讓人以為
它跟來源設定是兩套東西 —— 實際上來源的 base_lot 永遠說了算。

**畫面上不出現任何 LINE 聊天室的名字。** 來源對外一律叫交易頻率（低頻／中頻／
高頻／超高頻）。Hub 送來的 name 只拿來當設定的 key 與 DOM 的索引，
不進任何看得到的文字；績效統計裡對不到頻率名稱的來源乾脆不列。

**能改的只有設定，不能平倉也不能下單。** 一旦開放，產品就從「跟單工具」變成
「交易終端」，責任層級完全不同，而且 MT5 官方 app 本來就能做。

介面上最重要的一條規矩：**誠實顯示這是在控制會員自己家裡那台電腦，不是雲端
服務。** 掛機端沒上線時按什麼都不會發生，畫面必須講出來。所以每一項可改的
東西都有「已套用／套用中」，只有掛機端真的回報過相同的值才算已套用。
"""
from __future__ import annotations

# 連結預覽（head 裡的 og / twitter 標籤）
#
# 沒有那些標籤時，LINE 與 Messenger 會自己去抓頁面內文拼一段描述 —— 實際貼出來
# 是「帳號 密碼 登入 這裡控制的是你自己電腦上的跟單程式。電…」，也就是登入框的
# 欄位名跟說明文字被連在一起。這個網址會被貼進會員社群，預覽就是第一印象。
#
# og:image 借官網那張已上線的 og.png。跨網域沒問題，而且省得 Hub 再開一條靜態檔
# 路由 —— Hub 的映像檔刻意只帶標準庫，不該為了一張圖變複雜。
#
# 這段說明放在這裡而不是寫成 HTML 註解：註解會跟著每一次請求送給每一位會員，
# 而這頁的規矩本來就是「整頁自帶、不對外連線、不塞沒有用的位元組」。
_PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1216">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>黃金跟單 · 會員控制台</title>
<meta name="description" content="黃金跟單系統的會員控制台。即時查看帳戶淨值、持倉與跟單績效，並隨時調整手數、跟單時段與訊號來源設定。需會員帳號登入。">
<meta property="og:type" content="website">
<meta property="og:site_name" content="黃金跟單系統">
<meta property="og:title" content="黃金跟單 · 會員控制台">
<meta property="og:description" content="即時查看帳戶淨值、持倉與跟單績效，隨時調整手數、跟單時段與訊號來源設定。需會員帳號登入。">
<meta property="og:image" content="https://gold-young.com/assets/img/og.png">
<meta property="og:url" content="https://gold-signal-hub-tw.fly.dev/console">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="黃金跟單 · 會員控制台">
<meta name="twitter:description" content="即時查看帳戶淨值、持倉與跟單績效，隨時調整手數、跟單時段與訊號來源設定。需會員帳號登入。">
<meta name="twitter:image" content="https://gold-young.com/assets/img/og.png">
<!-- 這是會員專屬的操作介面，不該被搜尋引擎收錄。 -->
<meta name="robots" content="noindex,nofollow">
<style>
:root{
  --bg:#0f1216; --card:#161a20; --line:#252b33; --line2:#1d222a;
  --fg:#e9edf2; --dim:#8b96a5; --faint:#5c6672;
  --gold:#d8b25f; --up:#3ecf8e; --down:#f0655f; --warn:#d9a441;
  --navh:58px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif}
.num{font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.up{color:var(--up)} .down{color:var(--down)} .dim{color:var(--dim)}
.hide{display:none!important}

header{position:sticky;top:0;z-index:20;background:rgba(15,18,22,.94);
  backdrop-filter:saturate(1.4) blur(8px);border-bottom:1px solid var(--line);
  padding:calc(env(safe-area-inset-top) + 12px) 16px 11px;
  display:flex;align-items:center;justify-content:space-between;gap:10px}
.brand{font-size:15px;font-weight:600;letter-spacing:.03em;white-space:nowrap}
.brand span{color:var(--gold)}
.hstat{font-size:12px;color:var(--dim);text-align:right;line-height:1.35}
.hstat b{display:block;font-weight:600;color:var(--fg)}

main{padding:14px 14px calc(var(--navh) + env(safe-area-inset-bottom) + 20px);
  max-width:560px;margin:0 auto}

nav{position:fixed;left:0;right:0;bottom:0;z-index:30;display:flex;
  background:rgba(19,23,28,.97);backdrop-filter:blur(10px);
  border-top:1px solid var(--line);
  padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;background:none;border:0;color:var(--faint);
  height:var(--navh);font-size:11.5px;font-weight:500;display:flex;
  flex-direction:column;align-items:center;justify-content:center;gap:3px;
  cursor:pointer;padding:0}
nav button i{font-style:normal;font-size:17px;line-height:1}
nav button.on{color:var(--gold)}

.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:16px;margin-bottom:12px}
.card h2{font-size:12.5px;font-weight:600;color:var(--dim);margin:0 0 13px;
  letter-spacing:.06em;display:flex;align-items:baseline;gap:7px}
.card h2 em{font-style:normal;color:var(--faint);font-weight:400;font-size:11.5px}

.eq{font-size:33px;font-weight:600;line-height:1.14;margin:2px 0 3px}
.eq small{font-size:15px;color:var(--dim);font-weight:400;margin-left:4px}
.grid3{display:flex;gap:16px;margin-top:14px;padding-top:14px;
  border-top:1px solid var(--line)}
.grid3>div{flex:1;min-width:0}
.grid3 .k,.g2 .k,.tiles .k{font-size:11px;color:var(--faint);letter-spacing:.05em}
.grid3 .v,.g2 .v{font-size:16px;font-weight:600;margin-top:3px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:13px 10px}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;
  padding-top:14px;border-top:1px solid var(--line)}
.tiles>div{background:#12161b;border:1px solid var(--line2);border-radius:11px;padding:10px 12px}
.tiles .v{font-size:16px;font-weight:600;margin-top:2px}
.tiles .s{font-size:11px;color:var(--faint);margin-top:2px}

.row{display:flex;align-items:center;justify-content:space-between;gap:12px;
  min-height:42px}
.row+.row{border-top:1px solid var(--line2)}
.row .k{font-size:14px;color:var(--dim)}
.row .v{font-size:14px;text-align:right}

.ctl{display:flex;align-items:center;justify-content:space-between;gap:14px}
.ctl .lbl{font-size:16px;font-weight:600}
.ctl .st{font-size:12px;margin-top:4px}
.sw{position:relative;width:58px;height:32px;flex:0 0 auto}
.sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;z-index:2}
.sw i{position:absolute;inset:0;background:#2b323b;border-radius:999px;transition:.18s}
.sw i:after{content:"";position:absolute;top:4px;left:4px;width:24px;height:24px;
  border-radius:50%;background:#8b96a5;transition:.18s}
.sw input:checked+i{background:#1d4736}
.sw input:checked+i:after{transform:translateX(26px);background:var(--up)}
.sw input:disabled+i{opacity:.4}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;
  border:1px solid var(--line);color:var(--faint);white-space:nowrap;vertical-align:middle}
.pill.ok{color:var(--up);border-color:#20503c}
.pill.wait{color:var(--warn);border-color:#4a3c1f}
.pill.lock{color:var(--faint)}

.fld{margin-top:14px;padding-top:14px;border-top:1px solid var(--line2)}
.fld:first-child{margin-top:0;padding-top:0;border-top:0}
.fld .lbl{font-size:15px;font-weight:600}
.fld .st{font-size:12px;margin-top:4px}
.fld .hint,.hint{font-size:11.5px;color:var(--faint);margin-top:5px;line-height:1.45}
.inline{display:flex;gap:8px;margin-top:9px}
.inline input,.inline select{flex:1;min-width:0}
input,select{width:100%;padding:11px 12px;border-radius:10px;
  border:1px solid var(--line);background:#0c0f13;color:var(--fg);font-size:16px}
select{appearance:none;background-image:linear-gradient(45deg,transparent 50%,#8b96a5 50%),
  linear-gradient(135deg,#8b96a5 50%,transparent 50%);
  background-position:calc(100% - 17px) 50%,calc(100% - 12px) 50%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:34px}
input:focus,select:focus{outline:none;border-color:var(--gold)}
input:disabled,select:disabled{opacity:.45}
input[readonly]{color:var(--dim);background:#12161b}
button.act{border:0;border-radius:10px;background:var(--gold);color:#1a1408;
  font-size:15px;font-weight:600;padding:11px 17px;cursor:pointer;flex:0 0 auto}
button.act:disabled{opacity:.4}
button.wide{width:100%;padding:14px;border:0;border-radius:10px;
  background:var(--gold);color:#1a1408;font-size:15px;font-weight:600;cursor:pointer}
button.wide:disabled{opacity:.4}
button.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);
  font-weight:400}

.item{display:flex;align-items:center;gap:10px;padding:11px 0}
.item+.item{border-top:1px solid var(--line2)}
.side{font-size:11px;font-weight:700;padding:3px 7px;border-radius:6px;flex:0 0 auto}
.side.buy{background:#123528;color:var(--up)}
.side.sell{background:#38191a;color:var(--down)}
.item .mid{flex:1;min-width:0}
.item .sym{font-size:14px;font-weight:600}
.item .meta{font-size:11.5px;color:var(--faint);margin-top:2px}
.item .pl{font-size:15px;font-weight:600;text-align:right;flex:0 0 auto}
.empty{font-size:13px;color:var(--faint);padding:4px 0}

.perf{display:flex;align-items:center;gap:16px}
.ring{width:86px;height:86px;border-radius:50%;flex:0 0 auto;position:relative;
  display:grid;place-items:center}
.ring:after{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--card)}
.ring .t{position:relative;z-index:1;text-align:center}
.ring .t b{display:block;font-size:19px;font-weight:600}
.ring .t small{font-size:10px;color:var(--faint)}
.spark{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.spark svg{width:100%;height:44px;display:block}

.src{border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:11px;
  background:var(--card)}
.src.off{opacity:.62}
.src .top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.src .nm{font-size:15px;font-weight:600}
.src .sub{font-size:11.5px;color:var(--faint);margin-top:2px}
.src .body{margin-top:13px;padding-top:13px;border-top:1px solid var(--line2)}
.lbl2{font-size:12px;color:var(--dim)}
.sub-fld{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line2)}
.note{font-size:11.5px;margin-top:6px;line-height:1.45;color:var(--faint)}
.note.ok{color:var(--up)} .note.warn{color:var(--warn)} .note.bad{color:var(--down)}
.ladder{display:flex;align-items:flex-end;gap:5px;margin-top:10px;height:64px}
.ladder>div{flex:1;min-width:0;text-align:center;display:flex;flex-direction:column;
  justify-content:flex-end;height:100%}
.ladder i{display:block;border-radius:4px 4px 0 0;background:#2b323b;min-height:4px}
.ladder .lit i{background:var(--gold)}
.ladder .cur i{background:var(--up)}
.ladder .dup i{background:var(--down)}
.ladder b{font-size:10.5px;font-weight:600;margin-top:4px;display:block;
  font-variant-numeric:tabular-nums}
.ladder small{font-size:9.5px;color:var(--faint)}
.state{display:flex;gap:10px;margin-top:10px}
.state>div{flex:1;min-width:0;background:#12161b;border:1px solid var(--line2);
  border-radius:10px;padding:8px 10px}
.state .k{font-size:10.5px;color:var(--faint)}
.state .v{font-size:14px;font-weight:600;margin-top:1px}

.bene{display:flex;align-items:center;gap:9px;padding:8px 0;font-size:14px}
.bene+.bene{border-top:1px solid var(--line2)}
.bene i{font-style:normal;font-size:13px;width:16px;flex:0 0 auto;text-align:center}
.bene.no{color:var(--faint)}

.banner{border-radius:12px;padding:11px 13px;font-size:13px;margin-bottom:12px;
  border:1px solid;line-height:1.45}
.banner.bad{background:#241618;border-color:#48242a;color:#ffb3ae}
.banner.warn{background:#241f14;border-color:#463b20;color:#f5d79c}
.err{color:#ff9d99;font-size:12.5px;margin-top:9px;min-height:16px;line-height:1.45}
.err.good{color:var(--up)}
.muted{color:var(--faint);font-size:12.5px;margin-top:12px;line-height:1.5}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.dot.on{background:var(--up)} .dot.off{background:var(--down)}
.bar{height:5px;border-radius:999px;background:#242a32;overflow:hidden;margin-top:9px}
.bar i{display:block;height:100%;border-radius:999px;background:var(--gold)}

.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
.chip{border:1px solid var(--line);border-radius:999px;padding:6px 12px;
  font-size:13px;color:var(--dim);cursor:pointer;user-select:none;background:transparent}
.chip.on{background:#2a2416;border-color:#4a3f22;color:var(--gold)}
.sched{display:flex;align-items:center;gap:10px;padding:12px 0}
.sched+.sched{border-top:1px solid var(--line2)}
.sched .rng{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.sched .dys{font-size:11.5px;color:var(--faint);margin-top:2px}
.sched button{background:none;border:0;color:var(--down);font-size:13px;
  cursor:pointer;padding:8px;margin-left:auto}
.two{display:flex;gap:8px}
.two input{flex:1;min-width:0}
.two label{flex:1;min-width:0;display:block}
.two label span,.one label span{display:block;font-size:11px;color:var(--faint);margin-bottom:5px}
.one{margin-top:9px}
</style>
</head>
<body>

<div id="loginView">
  <header><div class="brand">黃金<span>跟單</span> · 控制台</div></header>
  <main>
    <div class="card">
      <div class="fld">
        <label class="k dim" for="u" style="font-size:13px">帳號</label>
        <input id="u" type="text" autocomplete="username" autocapitalize="none"
               spellcheck="false" style="margin-top:6px">
      </div>
      <div class="fld" style="border-top:0;padding-top:12px;margin-top:0">
        <label class="k dim" for="p" style="font-size:13px">密碼</label>
        <input id="p" type="password" autocomplete="current-password" style="margin-top:6px">
      </div>
      <div style="height:16px"></div>
      <button class="wide" id="loginBtn">登入</button>
      <div class="err" id="loginErr"></div>
      <p class="muted">這裡控制的是你自己電腦上的跟單程式。電腦沒開機的話，
      在這裡的操作會先記著，等電腦上線才生效。</p>
    </div>
  </main>
</div>

<div id="mainView" class="hide">
  <header>
    <div class="brand">黃金<span>跟單</span></div>
    <div class="hstat"><b id="hName">—</b><span id="hTier">—</span></div>
  </header>
  <main>
    <div id="banner"></div>

    <!-- 總覽 -->
    <section id="tab-home">
      <div class="card">
        <div class="ctl">
          <div><div class="lbl">自動跟單</div><div class="st" id="followState">—</div></div>
          <label class="sw"><input type="checkbox" id="follow"><i></i></label>
        </div>
      </div>

      <div class="card">
        <h2>帳戶淨值</h2>
        <div class="eq num" id="eq">—</div>
        <div class="num" id="todayPl" style="font-size:15px;font-weight:600">—</div>
        <div class="grid3">
          <div><div class="k">結餘</div><div class="v num" id="bal">—</div></div>
          <div><div class="k">保證金</div><div class="v num" id="marg">—</div></div>
          <div><div class="k">浮動</div><div class="v num" id="flt">—</div></div>
        </div>
      </div>

      <div class="card">
        <h2>目前持倉 <em id="posN"></em></h2>
        <div id="posList"><div class="empty">—</div></div>
      </div>

      <div class="card">
        <h2>待成交掛單 <em id="ordN"></em></h2>
        <div id="ordList"><div class="empty">—</div></div>
      </div>

      <div class="card">
        <h2>跟單績效 <em>僅計本系統下的單</em></h2>
        <div class="perf">
          <div class="ring" id="ring"><div class="t"><b id="wr">—</b><small>勝率</small></div></div>
          <div class="g2" style="flex:1">
            <div><div class="k">今日</div><div class="v num" id="pToday">—</div></div>
            <div><div class="k">近 7 日</div><div class="v num" id="pWeek">—</div></div>
            <div><div class="k">累計</div><div class="v num" id="pAll">—</div></div>
            <div><div class="k">筆數</div><div class="v num" id="pN">—</div></div>
          </div>
        </div>
        <div class="tiles" id="tiles"></div>
        <div class="spark" id="sparkWrap"><svg id="spark" viewBox="0 0 300 44"
          preserveAspectRatio="none" aria-label="累計損益走勢"></svg></div>
        <div id="recent"></div>
      </div>

      <div class="card" id="bySrcCard">
        <h2>各來源績效</h2>
        <div id="bySrc"><div class="empty">—</div></div>
      </div>
    </section>

    <!-- 策略 -->
    <section id="tab-src" class="hide">
      <div id="srcList"></div>
      <p class="muted">手數、馬丁、分批平倉都是<b>各來源獨立</b>設定的，跟電腦版一致。
      沒有包含在你方案裡的來源會顯示鎖頭，訊號本身也不會送到你的電腦。</p>
    </section>

    <!-- 排程 -->
    <section id="tab-sched" class="hide">
      <div class="card" id="schedCard">
        <h2>自動跟單時段 <em id="schedLock"></em></h2>
        <div id="schedList"><div class="empty">—</div></div>
        <div class="fld">
          <div class="lbl" style="font-size:14px">新增一段</div>
          <div class="two" style="margin-top:9px">
            <input id="sStart" type="time" value="21:00">
            <input id="sEnd" type="time" value="02:00">
          </div>
          <div class="chips" id="sDays"></div>
          <button class="wide act" id="sAdd" style="margin-top:12px">加入時段</button>
          <div class="hint">不選星期＝每天。可以跨午夜（例如 21:00 → 02:00），
          跨午夜時星期看的是「這段開始的那一天」。</div>
        </div>
        <div class="err" id="schedErr"></div>
      </div>
      <p class="muted">時段結束會自動停止跟單。在時段內自己按停止的話就讓它停著，
      不會每隔幾分鐘被自動打開 —— 「按了沒用」比排程不準更讓人困擾。</p>
    </section>

    <!-- 帳號 -->
    <section id="tab-acct" class="hide">
      <div class="card">
        <h2>方案</h2>
        <div class="row"><span class="k">會員帳號</span><span class="v" id="aName">—</span></div>
        <div class="row"><span class="k">目前方案</span><span class="v" id="aTier">—</span></div>
        <div class="row"><span class="k" id="aExpK">到期</span><span class="v num" id="aExp">—</span></div>
        <div class="row"><span class="k">帳號狀態</span><span class="v" id="aStatus">—</span></div>
        <div class="bar"><i id="aBar" style="width:0"></i></div>
        <div class="hint" id="aExpHint" style="font-size:11.5px;color:var(--faint);margin-top:8px"></div>
      </div>

      <div class="card">
        <h2>方案權益</h2>
        <div id="bene"></div>
      </div>

      <div class="card">
        <h2>連線</h2>
        <div class="row"><span class="k">掛機端</span><span class="v" id="agent">—</span></div>
        <div class="row"><span class="k">裝置</span><span class="v" id="dev">—</span></div>
        <div class="row"><span class="k">MT5 帳號</span><span class="v num" id="acct">—</span></div>
        <div class="row"><span class="k">交易伺服器</span><span class="v" id="srv">—</span></div>
      </div>

      <div class="card">
        <h2>修改密碼</h2>
        <div class="one"><label><span>目前密碼</span>
          <input id="pwOld" type="password" autocomplete="current-password"></label></div>
        <div class="one"><label><span>新密碼</span>
          <input id="pwNew" type="password" autocomplete="new-password"></label></div>
        <div class="one"><label><span>再輸入一次新密碼</span>
          <input id="pwNew2" type="password" autocomplete="new-password"></label></div>
        <button class="wide act" id="pwBtn" style="margin-top:12px">更新密碼</button>
        <div class="err" id="pwMsg"></div>
        <div class="hint" id="pwHint">新密碼至少 8 個字元。改完後電腦上的跟單程式不會被登出，
        下次登入時再用新密碼。</div>
      </div>

      <button class="wide ghost" id="logoutBtn">登出</button>
      <p class="muted">要更改方案或續約，請聯繫管理員。</p>
    </section>
  </main>

  <nav>
    <button data-tab="home" class="on"><i>◎</i>總覽</button>
    <button data-tab="src"><i>⌘</i>策略</button>
    <button data-tab="sched"><i>◷</i>排程</button>
    <button data-tab="acct"><i>◔</i>帳號</button>
  </nav>
</div>

<script>
"use strict";
var TK=null, timer=null, dirty=0, V=null, tab="home", pickDays=[];
function $(i){ return document.getElementById(i); }
function api(p,b){
  var o={headers:{"Content-Type":"application/json"}};
  if(TK) o.headers["Authorization"]="Bearer "+TK;
  if(b!==undefined){ o.method="POST"; o.body=JSON.stringify(b); }
  return fetch(p,o).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){return {status:r.status,body:j};});
  });
}
function ago(t){
  if(!t) return "從未";
  var s=Math.max(0,Math.floor(Date.now()/1000-t));
  if(s<60) return s+" 秒前";
  if(s<3600) return Math.floor(s/60)+" 分前";
  if(s<86400) return Math.floor(s/3600)+" 小時前";
  return Math.floor(s/86400)+" 天前";
}
function n(v,d){ return (v==null||v==="")?"—":Number(v).toFixed(d==null?2:d); }
function money(v,d){ if(v==null||v==="") return "—"; var x=Number(v);
  return (x>0?"+":"")+x.toFixed(d==null?2:d); }
function cls(v){ return v>0?"up":(v<0?"down":"dim"); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
function put(id,v){ var e=$(id); e.textContent=money(v); e.className="v num "+cls(Number(v)); }
// toFixed(2) 才跟後端 Python round(x,2) 一致（Math.round 會把 0.015 進成 0.02）
function r2(x){ return Number(Number(x).toFixed(2)); }

var STALE=60;
function live(v){ return !!v.agent_reported_at && (Date.now()/1000-v.agent_reported_at)<STALE; }

/* ── 來源名稱：只准用頻率標籤 ─────────────────────────────────────────
   Hub 的 all_sources 給 {name,label,need}。name 是聊天室名字，只當 key 用；
   任何會被看到的字都走 label。對不到 label 的來源一律不顯示。 */
var TIER_LABELS={trial:"體驗版",basic:"基礎版",advanced:"進階版",flagship:"旗艦版"};
function srcLabel(name){
  var list=(V&&V.all_sources)||[];
  for(var i=0;i<list.length;i++) if(list[i].name===name) return list[i].label;
  return "";
}
function tierLabel(t){ return ((V&&V.tier_labels)||TIER_LABELS)[t]||TIER_LABELS[t]||"更高等級"; }

function sparkline(c){
  var el=$("spark");
  if(!c||c.length<2){ $("sparkWrap").className="spark hide"; return; }
  $("sparkWrap").className="spark";
  var lo=Math.min.apply(null,c), hi=Math.max.apply(null,c); if(hi===lo) hi=lo+1;
  var W=300,H=44,p=3,st=W/(c.length-1),pts=[];
  for(var i=0;i<c.length;i++)
    pts.push((i*st).toFixed(1)+","+(p+(H-2*p)*(1-(c[i]-lo)/(hi-lo))).toFixed(1));
  var last=c[c.length-1];
  el.innerHTML='<polyline fill="none" stroke="'+(last>0?"#3ecf8e":last<0?"#f0655f":"#8b96a5")+
    '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="'+pts.join(" ")+'"></polyline>';
}
function listItems(rows,kind){
  if(!rows||!rows.length) return '<div class="empty">目前沒有</div>';
  var h="";
  for(var i=0;i<rows.length;i++){
    var r=rows[i], buy=String(r.type||"").toLowerCase().indexOf("buy")>=0, meta, right;
    if(kind==="pos"){
      meta=n(r.volume)+" 手 · 進 "+n(r.price_open)+" · 現 "+n(r.price_current);
      right='<div class="pl '+cls(Number(r.profit))+'">'+money(r.profit)+'</div>';
    }else{
      meta=n(r.volume)+" 手 · 掛 "+n(r.price)+" · 損 "+n(r.sl)+" / 盈 "+n(r.tp);
      right='<div class="pl dim" style="font-size:11.5px">等待成交</div>';
    }
    h+='<div class="item"><span class="side '+(buy?"buy":"sell")+'">'+(buy?"買":"賣")+'</span>'+
       '<div class="mid"><div class="sym">'+esc(r.symbol)+'</div>'+
       '<div class="meta num">'+meta+'</div></div>'+right+'</div>';
  }
  return h;
}

/* 績效指標，跟電腦版 renderTiles 同一組 */
function renderTiles(s){
  var pf=s.profit_factor, t=[];
  t.push(["獲利因子", pf==null?"—":n(pf), pf==null?"尚無虧損單":(pf>=1?"每虧 $1 賺 $"+n(pf):"低於 1 代表淨虧損"), pf==null?"":(pf>=1?"up":"down")]);
  t.push(["最大回撤", s.max_drawdown?("-"+n(s.max_drawdown)):"—", "從高點下來最深的一段", s.max_drawdown?"down":""]);
  t.push(["最大連敗", (s.max_loss_streak||0)+" 筆", "連續輸最多的一段", ""]);
  t.push(["平均獲利", money(s.avg_win), "每筆贏單平均", "up"]);
  t.push(["平均虧損", money(s.avg_loss), "每筆輸單平均", "down"]);
  t.push(["累計手數", n(s.volume)+" 手", (s.total||0)+" 筆已平倉", ""]);
  var h="";
  for(var i=0;i<t.length;i++)
    h+='<div><div class="k">'+t[i][0]+'</div><div class="v num '+t[i][3]+'">'+t[i][1]+
       '</div><div class="s">'+t[i][2]+'</div></div>';
  $("tiles").innerHTML=h;
}
function renderBySource(s){
  var rows=(s&&s.by_source)||[], h="";
  for(var i=0;i<rows.length;i++){
    var label=srcLabel(rows[i].source);
    if(!label) continue;   // 對不到頻率名稱的來源不顯示 —— 畫面上不出現聊天室名字
    h+='<div class="row"><span class="k">'+esc(label)+
       ' <span class="dim" style="font-size:11.5px">'+rows[i].trades+' 筆 · 勝率 '+
       (rows[i].win_rate==null?"—":rows[i].win_rate+"%")+'</span></span>'+
       '<span class="v num '+cls(Number(rows[i].profit))+'">'+money(rows[i].profit)+'</span></div>';
  }
  $("bySrc").innerHTML=h||'<div class="empty">還沒有已平倉的跟單</div>';
}

/* ── 策略：跟電腦版 sourceRowHtml / syncSourceProfiles / validateSourceLots 一致 ── */
function lotModeOptions(ent,isUltra){
  var o=[{v:"flat",label:"均注",ok:true,need:"trial"},
         {v:"martingale",label:"馬丁",ok:!!ent.martingale,need:"advanced"},
         {v:"risk_percent",label:"本金比例",ok:!!ent.dynamic_lot,need:"flagship"}];
  // 「跟隨來源」只有訊號本身帶得出手數的來源能選。超高頻是鏡像別人的帳戶，
  // 看得到真實手數；LINE 報單的訊息裡沒有手數，選了每筆都只會退回基礎手數。
  if(isUltra) o.push({v:"source",label:"跟隨來源",ok:!!ent.dynamic_lot,need:"flagship"});
  return o;
}
function tpOptions(ent,isMid){
  var partial={v:"partial",label:"分批平倉",ok:!!ent.partial_close,need:"advanced"};
  var be={v:"breakeven",label:"保本移損",ok:!!ent.breakeven,need:"advanced"};
  var single={v:"single",label:"單一點位",ok:true,need:"trial"};
  // 中頻一單只有一個止盈，分批根本沒東西可分
  return isMid?[single,be]:[partial,be,single];
}
function pick(opts,stored){
  for(var i=0;i<opts.length;i++) if(opts[i].v===stored&&opts[i].ok) return opts[i].v;
  for(var j=0;j<opts.length;j++) if(opts[j].ok) return opts[j].v;
  return opts[0].v;
}
function optionHtml(o,cur){
  var label=o.ok?o.label:o.label+"（需"+tierLabel(o.need)+"）";
  return '<option value="'+o.v+'"'+(o.v===cur?" selected":"")+(o.ok?"":" disabled")+'>'+esc(label)+'</option>';
}
// 分批：存的是佔比，顯示時 × 基礎手數還原成「每段手數」；還原不出合法值就給預設
function lotsToText(ratios,base){
  base=r2(base||0);
  if(ratios&&ratios.length>=2&&base>=0.02){
    var c0=r2(base*ratios[0]), c1=r2(base*ratios[1]), tail=r2(base-c0-c1);
    if(c0>=0.01&&c1>=0.01&&tail>=0.01) return c0+"/"+c1+"/"+tail;
  }
  return "0.01/0.01/0.01";
}
function parseLots(text){
  var parts=String(text||"").replace(/，/g,",").split(/[\/,\s]+/), out=[];
  for(var i=0;i<parts.length;i++){
    var p=parts[i].trim(); if(!p) continue;
    var x=Number(p); if(!isFinite(x)||x<=0) return null;
    out.push(r2(x));
  }
  return out.length<2?null:out;
}
function ladderFor(base,mult,levels){
  var out=[];
  for(var i=0;i<levels;i++) out.push(r2(base*Math.pow(mult,i)));
  return out;
}
// 空白列的預設值，跟電腦版 blankSourceRow 一樣（保本距離預設 3 美元：黃金停損多半 6~10，走一半保本）
function defaults(isMid){
  return {enabled:false,mode:"flat",tp_mode:isMid?"single":"partial",base_lot:0.01,
          risk_percent:0.5,source_ratio:1,multiplier:2,max_level:5,partial_ratios:[],
          breakeven_distance:3,max_daily_profit:0,max_daily_loss:0};
}
function sameProfile(d,a){
  if(!d) return false;
  for(var k in d){
    var x=d[k], y=(a||{})[k];
    if(JSON.stringify(x)!==JSON.stringify(y)) return false;
  }
  return true;
}
function renderSources(v){
  var ent=v.entitlements||{}, allowed=(ent.sources||[]),
      d=(v.desired||{}).source_profiles||{}, a=(v.applied||{}).source_profiles||{},
      list=v.all_sources||[], stats=(v.stats&&v.stats.by_source)||[],
      state=v.source_state||{}, h="";
  for(var i=0;i<list.length;i++){
    var name=list[i].name, label=list[i].label, need=list[i].need, ok=allowed.indexOf(name)>=0;
    var isMid=label==="中頻交易", isLow=label==="低頻交易", isUltra=label==="超高頻交易";
    var p=defaults(isMid), k;
    for(k in (a[name]||{})) p[k]=a[name][k];
    for(k in (d[name]||{})) p[k]=d[name][k];
    var on=ok&&!!p.enabled;
    var trades=0; for(var t=0;t<stats.length;t++) if(stats[t].source===name) trades=stats[t].trades;
    var meta;
    if(isLow&&!trades) meta="訊號源建置中，開放後自動生效";
    else if(trades) meta="已成交 "+trades+" 筆"+((d[name]||a[name])?"":" · 尚未個別設定（用預設）");
    else meta="尚未收過訊號";
    var pill="";
    if(ok&&d[name]) pill=(sameProfile(d[name],a[name])&&live(v))
      ?'<span class="pill ok">已套用</span>':'<span class="pill wait">套用中</span>';
    h+='<div class="src'+(on?"":" off")+'" data-i="'+i+'">'+
       '<div class="top"><div><div class="nm">'+esc(label)+' '+pill+
       (ok?"":' <span class="pill lock">需'+esc(tierLabel(need))+'</span>')+'</div>'+
       '<div class="sub">'+esc(meta)+'</div></div>'+
       '<label class="sw"><input type="checkbox" data-f="enabled"'+
       (on?" checked":"")+(ok?"":" disabled")+'><i></i></label></div>';
    if(ok){
      var mode=pick(lotModeOptions(ent,isUltra),p.mode), tp=pick(tpOptions(ent,isMid),p.tp_mode);
      var lotMax=(ent.max_lot&&ent.max_lot>0)?' max="'+ent.max_lot+'"':"";
      var st=state[name]||{}, lv=Number(st.level||0), ls=Number(st.losses||0);
      h+='<div class="body">'+
         '<div class="fld"><div class="lbl" style="font-size:14px">下單方式</div>'+
         '<div class="inline"><select data-f="mode">';
      var lm=lotModeOptions(ent,isUltra); for(var m=0;m<lm.length;m++) h+=optionHtml(lm[m],mode);
      h+='</select></div>'+
         '<div class="sub-fld" data-box="base"><div class="lbl2">基礎手數</div>'+
         '<div class="inline"><input type="number" inputmode="decimal" data-f="base_lot" step="0.01" min="0.01"'+lotMax+
         ' value="'+esc(p.base_lot)+'"></div>'+
         '<div class="note" data-note="base">'+(ent.max_lot?"方案上限 "+ent.max_lot+" 手":"")+'</div></div>'+
         '<div class="sub-fld" data-box="risk"><div class="lbl2">每筆風險（本金 %）</div>'+
         '<div class="inline"><input type="number" inputmode="decimal" data-f="risk_percent" step="0.05" min="0.01" max="5"'+
         ' value="'+esc(p.risk_percent)+'"></div>'+
         '<div class="note">以餘額與淨值較低者為本金，依每則訊號的進場價與 SL 即時計算；低於 0.01 手會略過。</div></div>'+
         '<div class="sub-fld" data-box="sratio"><div class="lbl2">來源手數 ×（倍率）</div>'+
         '<div class="inline"><input type="number" inputmode="decimal" data-f="source_ratio" step="0.05" min="0.01" max="1"'+
         ' value="'+esc(p.source_ratio)+'"></div>'+
         '<div class="note" data-note="sratio"></div></div>'+
         '<div class="sub-fld" data-box="mg"><div class="two">'+
         '<label><span>馬丁倍數</span><input type="number" inputmode="decimal" data-f="multiplier" step="0.1" min="1" max="10" value="'+esc(p.multiplier)+'"></label>'+
         '<label><span>關卡數</span><input type="number" inputmode="numeric" data-f="max_level" step="1" min="1" max="10" value="'+esc(p.max_level)+'"></label>'+
         '</div>'+
         '<div class="ladder" data-ladder="1"></div>'+
         '<div class="state"><div><div class="k">目前</div><div class="v" data-st="level">第 '+(lv+1)+' 關</div></div>'+
         '<div><div class="k">連續虧損</div><div class="v" data-st="losses">'+ls+' 筆</div></div>'+
         '<div><div class="k">下一手</div><div class="v num" data-st="next">—</div></div></div>'+
         '<div class="note" data-note="mg"></div></div>'+
         '</div>'+
         '<div class="fld"><div class="lbl" style="font-size:14px">止盈處理</div>'+
         '<div class="inline"><select data-f="tp_mode">';
      var to=tpOptions(ent,isMid); for(var q=0;q<to.length;q++) h+=optionHtml(to[q],tp);
      h+='</select></div>'+
         '<div class="sub-fld" data-box="lots"><div class="lbl2">分批手數（每個止盈平多少手）</div>'+
         '<div class="inline"><input type="text" inputmode="decimal" data-f="partial_lots" placeholder="0.01/0.01/0.01"'+
         ' value="'+esc(lotsToText(p.partial_ratios,p.base_lot))+'"></div>'+
         '<div class="note" data-note="lots"></div></div>'+
         '<div class="sub-fld" data-box="be"><div class="lbl2">保本距離（美元）</div>'+
         '<div class="inline"><input type="number" inputmode="decimal" data-f="breakeven_distance" step="0.1" min="0"'+
         ' value="'+esc(p.breakeven_distance)+'"></div>'+
         '<div class="note" data-note="be"></div></div>'+
         '</div>'+
         '<div class="fld"><div class="lbl" style="font-size:14px">每日上限 <span class="dim" style="font-weight:400;font-size:12px">（美元，0 = 不限）</span></div>'+
         '<div class="two" style="margin-top:9px">'+
         '<label><span>每日止盈</span><input type="number" inputmode="decimal" data-f="max_daily_profit" step="1" min="0" value="'+esc(p.max_daily_profit||0)+'"></label>'+
         '<label><span>每日止損</span><input type="number" inputmode="decimal" data-f="max_daily_loss" step="1" min="0" value="'+esc(p.max_daily_loss||0)+'"></label>'+
         '</div><div class="hint">當日這個來源的獲利／虧損達到金額就今日停跟，隔日重置。</div></div>'+
         '<button class="wide act" data-save="1" style="margin-top:14px">套用這個來源</button>'+
         '<div class="err" data-err="1"></div></div>';
    }
    h+='</div>';
  }
  $("srcList").innerHTML=h;
  bindSources();
}
function q(card,sel){ return card.querySelector(sel); }
function fv(card,key){ var e=q(card,'[data-f="'+key+'"]'); return e?e.value:""; }
/* 依目前選的模式決定哪些欄位要出現，並即時算分批總和／馬丁階梯 —— 電腦版 syncSourceProfiles 的手機版 */
function reflow(card){
  var mode=fv(card,"mode"), tp=fv(card,"tp_mode");
  var mg=mode==="martingale", dyn=mode==="risk_percent", partial=tp==="partial", be=tp==="breakeven";
  // 跟隨來源跟本金比例一樣：總手數不是會員填的，是每筆訊號當場算出來的
  var follow=mode==="source", sized=dyn||follow;
  q(card,'[data-box="base"]').className="sub-fld"+(sized?" hide":"");
  q(card,'[data-box="risk"]').className="sub-fld"+(dyn?"":" hide");
  q(card,'[data-box="sratio"]').className="sub-fld"+(follow?"":" hide");
  q(card,'[data-box="mg"]').className="sub-fld"+(mg?"":" hide");
  q(card,'[data-box="lots"]').className="sub-fld"+((partial&&!sized)?"":" hide");
  q(card,'[data-box="be"]').className="sub-fld"+(be?"":" hide");
  var baseIn=q(card,'[data-f="base_lot"]'), lotsIn=q(card,'[data-f="partial_lots"]');
  var lotsNote=q(card,'[data-note="lots"]'), baseNote=q(card,'[data-note="base"]');
  var ent=(V&&V.entitlements)||{};
  var errs=[];
  // 分批：基礎手數自動 = 各段總和，鎖住不讓改
  if(follow){
    var sr=Number(fv(card,"source_ratio"));
    var srNote=q(card,'[data-note="sratio"]');
    if(!(sr>=0.01&&sr<=1)){ srNote.textContent="倍率要在 0.01 ~ 1 之間（只能縮小）"; srNote.className="note bad";
      errs.push("來源手數倍率要在 0.01 ~ 1 之間"); }
    else{ srNote.textContent="來源下 1 手就跟 "+r2(sr)+" 手；算出來不足 0.01 手會補到 0.01。";
      srNote.className="note ok"; }
  }
  if(partial&&!sized){
    var lots=parseLots(lotsIn.value);
    if(!lots){ lotsNote.textContent="格式錯誤，請填像 0.01/0.01/0.01"; lotsNote.className="note bad";
      errs.push("分批手數格式不對，請填像 0.01/0.01/0.01"); }
    else{
      var bad=0; for(var i=0;i<lots.length;i++) if(lots[i]<0.01) bad++;
      if(bad){ lotsNote.textContent="每段都要 ≥ 0.01 手（MT5 最低）"; lotsNote.className="note bad";
        errs.push("分批每段都要 ≥ 0.01 手，目前有 "+bad+" 段低於 0.01"); }
      else{
        var sum=0; for(var j=0;j<lots.length;j++) sum+=lots[j]; sum=r2(sum);
        baseIn.value=sum;
        lotsNote.textContent="每單 "+sum+" 手 → 分 "+lots.join(" / ")+" 手 ✓"; lotsNote.className="note ok";
        if(ent.max_lot&&sum>ent.max_lot){ lotsNote.textContent="加總 "+sum+" 手超過方案上限 "+ent.max_lot+" 手"; lotsNote.className="note bad";
          errs.push("分批加總超過方案上限 "+ent.max_lot+" 手"); }
      }
    }
    baseIn.readOnly=true; baseNote.textContent="＝ 分批各段的總和，由上面的分批手數決定";
  }else{
    baseIn.readOnly=false; baseNote.textContent=ent.max_lot?"方案上限 "+ent.max_lot+" 手":"";
    var bl=Number(baseIn.value);
    if(!sized&&(!isFinite(bl)||bl<0.01)) errs.push("基礎手數至少 0.01");
    if(!sized&&ent.max_lot&&bl>ent.max_lot) errs.push("基礎手數超過方案上限 "+ent.max_lot+" 手");
  }
  if(partial&&sized){ lotsNote.textContent=""; }
  var beNote=q(card,'[data-note="be"]');
  if(be){
    var dist=Number(fv(card,"breakeven_distance"))||0;
    if(dist>0){ beNote.textContent="價格觸及保本距離（進場後 "+dist+" 美元）→ 停損移到進場價"; beNote.className="note ok"; }
    else{ beNote.textContent="未設保本距離：退回「觸及第一個止盈才保本」"; beNote.className="note warn"; }
  }
  // 馬丁階梯：每關實際手數。跟上一關一樣的標紅 —— 那一關等於沒加碼
  if(mg){
    var base=sized?0:Number(baseIn.value)||0.01, mult=Number(fv(card,"multiplier"))||2,
        lv=Math.max(1,Math.min(10,parseInt(fv(card,"max_level"),10)||5));
    var lad=ladderFor(base,mult,lv), st=((V&&V.source_state)||{})[card._name]||{};
    var cur=Math.min(Number(st.level||0),lv-1), top=Math.max.apply(null,lad)||1, lh="", dup=[];
    for(var r=0;r<lad.length;r++){
      var isDup=r>0&&lad[r]<=lad[r-1];
      if(isDup) dup.push(r+1);
      lh+='<div class="'+(r<=cur?"lit ":"")+(r===cur?"cur ":"")+(isDup?"dup":"")+'">'+
          '<i style="height:'+Math.max(8,Math.round(lad[r]/top*44))+'px"></i><b>'+lad[r]+'</b><small>第 '+(r+1)+' 關</small></div>';
    }
    q(card,'[data-ladder]').innerHTML=lh;
    q(card,'[data-st="level"]').textContent="第 "+(cur+1)+" 關";
    q(card,'[data-st="next"]').textContent=lad[cur]+" 手";
    var mgNote=q(card,'[data-note="mg"]');
    if(dup.length){
      mgNote.className="note bad";
      mgNote.textContent="第 "+dup.join("、")+" 關的手數跟前一關一樣 —— "+base+" × "+mult+" 落不到 MT5 的 0.01 跳動上，那一關等於沒加碼。把倍數調到 2，或把基礎手數加大。";
    }else{ mgNote.className="note"; mgNote.textContent="輸一筆升一關、贏一筆回第 1 關；到頂再輸就重置。"; }
    var mm=Number(fv(card,"multiplier")); if(!(mm>=1)) errs.push("馬丁倍數至少 1");
  }
  return errs;
}
// 組成要送給 Hub 的這一個來源的設定 —— 鍵與電腦版 syncSourceProfiles 完全一樣
function collect(card){
  var mode=fv(card,"mode"), tp=fv(card,"tp_mode"), dyn=mode==="risk_percent", partial=tp==="partial";
  var sized=dyn||mode==="source";
  var one={enabled:!!q(card,'[data-f="enabled"]').checked, mode:mode, tp_mode:tp,
    breakeven_distance:Math.max(0,Number(fv(card,"breakeven_distance"))||0),
    risk_percent:Math.min(5,Math.max(0.01,Number(fv(card,"risk_percent"))||0.5)),
    // 只准縮小 —— 來源的本金跟會員的無關，放大它沒有風控意義（後端再箝制一次）
    source_ratio:Math.min(1,Math.max(0.01,Number(fv(card,"source_ratio"))||1)),
    max_daily_loss:Number(fv(card,"max_daily_loss"))||0,
    max_daily_profit:Number(fv(card,"max_daily_profit"))||0};
  var lots=parseLots(fv(card,"partial_lots"));
  if(partial&&!sized&&lots){
    var sum=0; for(var i=0;i<lots.length;i++) sum+=lots[i]; sum=r2(sum);
    one.base_lot=sum;
    one["partial_ratios"]=lots.map(function(l){return l/sum;});
  }else{
    one.base_lot=Number(fv(card,"base_lot"))||0.01;
    // 本金比例／跟隨來源配分批：沿用欄位裡記著的分配比例，沒設就由後端退回 50/30/20
    if(sized&&lots){ var s2=0; for(var j=0;j<lots.length;j++) s2+=lots[j];
      one["partial_ratios"]=lots.map(function(l){return l/s2;}); }
  }
  if(mode==="martingale"){
    one.multiplier=Number(fv(card,"multiplier"))||2;
    one.max_level=parseInt(fv(card,"max_level"),10)||5;
  }
  return one;
}
function bindSources(){
  var cards=document.querySelectorAll(".src"), list=(V&&V.all_sources)||[];
  for(var i=0;i<cards.length;i++) (function(card){
    var name=(list[Number(card.getAttribute("data-i"))]||{}).name||"";
    card._name=name;
    var sw=q(card,'input[data-f="enabled"]');
    if(sw&&!sw.disabled) sw.onchange=function(){
      var pt={}; pt[name]={enabled:this.checked}; push({source_profiles:pt});
    };
    if(!q(card,".body")) return;
    var inputs=card.querySelectorAll("[data-f]");
    for(var j=0;j<inputs.length;j++){
      if(inputs[j].getAttribute("data-f")==="enabled") continue;
      inputs[j].oninput=inputs[j].onchange=function(){ dirty=1; reflow(card); };
    }
    reflow(card);
    q(card,"[data-save]").onclick=function(){
      var errEl=q(card,"[data-err]"), errs=reflow(card);
      if(errs.length){ errEl.textContent=errs.join("；"); return; }
      var pt={}; pt[name]=collect(card);
      push({source_profiles:pt}, errEl);
    };
  })(cards[i]);
}

/* ── 排程 ───────────────────────────────────────────────────────────── */
var DAYS=["一","二","三","四","五","六","日"];
function renderSchedules(v){
  var ent=v.entitlements||{}, ok=!!ent.schedule;
  $("schedLock").textContent=ok?"":"需"+tierLabel("advanced")+"以上";
  var list=("auto_schedules" in (v.desired||{}))?v.desired.auto_schedules
           :((v.applied||{}).auto_schedules||[]);
  var h="";
  if(!list||!list.length) h='<div class="empty">沒有排程，跟單開關完全由你手動控制。</div>';
  for(var i=0;i<(list||[]).length;i++){
    var s=list[i], dy=(s.days&&s.days.length)?s.days.map(function(d){return "週"+DAYS[d];}).join("、"):"每天";
    h+='<div class="sched"><div><div class="rng">'+esc(s.start)+' → '+esc(s.end)+'</div>'+
       '<div class="dys">'+dy+'</div></div>'+
       (ok?'<button data-del="'+i+'">移除</button>':'')+'</div>';
  }
  $("schedList").innerHTML=h;
  var dels=$("schedList").querySelectorAll("[data-del]");
  for(var j=0;j<dels.length;j++) dels[j].onclick=function(){
    var idx=Number(this.getAttribute("data-del")), out=[];
    for(var k=0;k<list.length;k++) if(k!==idx) out.push(list[k]);
    push({auto_schedules:out}, $("schedErr"));
  };
  var chips="";
  for(var d=0;d<7;d++)
    chips+='<button type="button" class="chip'+(pickDays.indexOf(d)>=0?" on":"")+
           '" data-day="'+d+'">'+DAYS[d]+'</button>';
  $("sDays").innerHTML=chips;
  var cs=$("sDays").querySelectorAll("[data-day]");
  for(var m=0;m<cs.length;m++) cs[m].onclick=function(){
    var d=Number(this.getAttribute("data-day")), at=pickDays.indexOf(d);
    if(at>=0) pickDays.splice(at,1); else pickDays.push(d);
    this.className="chip"+(at>=0?"":" on");
  };
  $("sStart").disabled=$("sEnd").disabled=$("sAdd").disabled=!ok;
  $("sAdd").onclick=function(){
    var cur=list||[];
    var out=cur.slice();
    out.push({start:$("sStart").value,end:$("sEnd").value,days:pickDays.slice()});
    pickDays=[];
    push({auto_schedules:out}, $("schedErr"));
  };
}

function renderBenefits(ent){
  var rows=[["可跟訊號來源",(ent.sources||[]).length+" 個"],
            ["單筆手數上限", ent.max_lot?ent.max_lot+" 手":"不限"],
            ["馬丁加倍", !!ent.martingale],
            ["本金比例動態手數", !!ent.dynamic_lot],
            ["分批平倉", !!ent.partial_close],
            ["保本移損", !!ent.breakeven],
            ["自動排程", !!ent.schedule],
            ["手機跟單通知", !!ent.mobile_notify],
            ["非開盤自動暫停計時", !!ent.time_pause]];
  var h="";
  for(var i=0;i<rows.length;i++){
    var k=rows[i][0], val=rows[i][1];
    h+= (typeof val==="boolean")
      ? '<div class="bene'+(val?"":" no")+'"><i>'+(val?"✓":"×")+'</i>'+k+'</div>'
      : '<div class="bene"><i class="dim">·</i>'+k+
        '<span style="margin-left:auto" class="num dim">'+val+'</span></div>';
  }
  $("bene").innerHTML=h;
}

function render(v){
  V=v;
  var L=live(v), d=v.desired||{}, a=v.applied||{}, acc=v.account||{}, s=v.stats||{},
      ent=v.entitlements||{};
  $("hName").textContent=v.username||"—";
  $("hTier").textContent=v.tier_label||v.tier||"";

  var b="";
  if(!L) b='<div class="banner bad">掛機端離線（最後回報：'+ago(v.agent_reported_at)+
          '）。這裡改的設定會先記著，等你的電腦上線才會生效。</div>';
  else if(v.mt5_stale) b='<div class="banner warn">掛機端在線，但連不上 MT5，目前不會下單。</div>';
  $("banner").innerHTML=b;

  if(!dirty) $("follow").checked=("following" in d)?!!d.following:!!a.following;
  var fOk=("following" in d)&&("following" in a)&&a.following===d.following;
  $("followState").innerHTML=!("following" in d)
    ? '<span class="dim">'+(("following" in a)?(a.following?"跟單中":"已停止"):"—")+'</span>'
    : ((fOk&&L)?'<span class="pill ok">已套用 · '+(d.following?"跟單中":"已停止")+'</span>'
               :'<span class="pill wait">套用中 · 等掛機端確認</span>');

  $("eq").innerHTML=(acc.equity!=null?Number(acc.equity).toFixed(2):"—")+
    '<small>'+esc(acc.currency||"")+'</small>';
  $("todayPl").innerHTML=(s.profit_today==null)?'<span class="dim">今日 —</span>'
    :'<span class="'+cls(s.profit_today)+'">今日 '+money(s.profit_today)+'</span>';
  $("bal").textContent=n(acc.balance); $("marg").textContent=n(acc.margin);
  put("flt",acc.profit);
  $("posN").textContent=v.positions_count!=null?"("+v.positions_count+")":"";
  $("ordN").textContent=v.orders_count!=null?"("+v.orders_count+")":"";
  $("posList").innerHTML=listItems(v.positions,"pos");
  $("ordList").innerHTML=listItems(v.orders,"ord");
  var wr=s.win_rate;
  $("wr").textContent=wr==null?"—":wr+"%";
  $("ring").style.background=wr==null?"conic-gradient(#252b33 0deg 360deg)"
    :"conic-gradient(var(--up) 0deg "+(wr*3.6)+"deg, #38191a "+(wr*3.6)+"deg 360deg)";
  put("pToday",s.profit_today); put("pWeek",s.profit_week); put("pAll",s.profit_total);
  $("pN").textContent=s.total==null?"—":s.total; $("pN").className="v num";
  renderTiles(s);
  sparkline(s.curve);
  var rc=s.recent||[], rh="";
  for(var i=0;i<rc.length;i++)
    rh+='<div class="row"><span class="k">'+esc(rc[i].close_time)+'　'+esc(rc[i].symbol)+
        '</span><span class="v num '+cls(Number(rc[i].profit))+'">'+money(rc[i].profit)+'</span></div>';
  $("recent").innerHTML=rh;
  renderBySource(s);

  if(tab==="src"&&!dirty) renderSources(v);
  if(tab==="sched"&&!dirty) renderSchedules(v);

  $("aName").textContent=v.username||"—";
  $("aTier").textContent=v.tier_label||v.tier||"—";
  $("aStatus").textContent=(v.status==="active")?"正常":(v.status||"—");
  var u=v.usage, pct=0, hint="";
  if(u&&u.time_pause&&u.seconds_left!=null){
    $("aExpK").textContent="剩餘額度";
    var hrs=u.seconds_left/3600;
    $("aExp").textContent=hrs>=24?(hrs/24).toFixed(1)+" 天":hrs.toFixed(1)+" 小時";
    if(u.seconds_total) pct=Math.max(0,Math.min(100,u.seconds_left/u.seconds_total*100));
    hint=u.market_open?"黃金開盤中，跟單時會消耗額度。":"目前休市，不消耗額度。";
  }else{
    $("aExpK").textContent="到期";
    if(v.expires_at){
      var left=(v.expires_at-Date.now()/1000)/86400;
      $("aExp").textContent=new Date(v.expires_at*1000).toLocaleDateString()+
        "（剩 "+Math.max(0,Math.floor(left))+" 天）";
      pct=Math.max(0,Math.min(100,left/(ent.plan_days||30)*100));
      if(left<5) hint="快到期了，記得聯繫管理員續約。";
    }else{ $("aExp").textContent="無期限"; pct=100; }
  }
  $("aBar").style.width=pct+"%";
  $("aBar").style.background=pct<15?"var(--down)":(pct<35?"var(--warn)":"var(--gold)");
  $("aExpHint").textContent=hint;
  renderBenefits(ent);
  $("agent").innerHTML='<span class="dot '+(L?"on":"off")+'"></span>'+(L?"在線":"離線")+
    ' <span class="dim">'+ago(v.agent_reported_at)+'</span>';
  $("dev").textContent=v.agent_device||"—";
  $("acct").textContent=acc.login||"—";
  $("srv").textContent=acc.server||"—";
  if(v.min_password_length) $("pwHint").textContent="新密碼至少 "+v.min_password_length+
    " 個字元。改完後電腦上的跟單程式不會被登出，下次登入時再用新密碼。";
}

function refresh(){
  return api("/console/settings").then(function(r){
    if(r.status===401){ logout(); return; }
    if(r.body&&r.body.ok) render(r.body);
  }).catch(function(){});
}
function push(patch,errEl){
  dirty=1;
  if(errEl) errEl.textContent="";
  return api("/console/settings",{settings:patch}).then(function(r){
    dirty=0;
    if(r.status===401){ logout(); return; }
    if(!r.body||!r.body.ok){ if(errEl) errEl.textContent="沒有送出去，請再試一次"; return; }
    var msg=explain(r.body.rejected||[]);
    render(r.body);
    if(tab==="src") renderSources(r.body);
    if(tab==="sched") renderSchedules(r.body);
    // 重畫完才寫訊息，否則會被重畫洗掉。來源卡片重畫後是新的 DOM，
    // 要用來源名稱重新找到那張卡的訊息欄。
    if(errEl&&patch.source_profiles){
      var which=Object.keys(patch.source_profiles)[0], again=null, cards=document.querySelectorAll(".src");
      for(var i=0;i<cards.length;i++) if(cards[i]._name===which) again=q(cards[i],"[data-err]");
      if(again){ again.textContent=msg||"已送出，等掛機端套用"; again.className="err"+(msg?"":" good"); }
    }else if(errEl) errEl.textContent=msg;
  }).catch(function(){ dirty=0; if(errEl) errEl.textContent="連不上伺服器"; });
}
function explain(rj){
  if(!rj||!rj.length) return "";
  var msg=[];
  for(var i=0;i<rj.length;i++){
    var r=rj[i];
    if(r.indexOf("capped_by_tier")>=0||r.indexOf("lot_capped")>=0) msg.push("手數超過方案上限，已調整為上限值");
    else if(r.indexOf("not_in_tier")>=0) msg.push("這項功能不在你的方案內，已關閉");
    else if(r.indexOf("single_tp_only")>=0) msg.push("中頻訊號只有一個止盈，已改為單一點位");
    else if(r.indexOf("below_min")>=0) msg.push("低於 MT5 最小手數 0.01，已調整");
    else if(r.indexOf("bad_time")>=0) msg.push("時間格式不正確");
    else if(r.indexOf("empty_range")>=0) msg.push("起訖時間不能一樣");
    else if(r.indexOf("bad_")>=0||r.indexOf("format")>=0) msg.push("有欄位格式不正確，該項未生效");
  }
  var uniq=[];
  for(var j=0;j<msg.length;j++) if(uniq.indexOf(msg[j])<0) uniq.push(msg[j]);
  return uniq.join("；");
}

function show(name){
  tab=name;
  var ids=["home","src","sched","acct"];
  for(var i=0;i<ids.length;i++) $("tab-"+ids[i]).className=(ids[i]===name)?"":"hide";
  var bs=document.querySelectorAll("nav button");
  for(var j=0;j<bs.length;j++) bs[j].className=(bs[j].getAttribute("data-tab")===name)?"on":"";
  window.scrollTo(0,0);
  dirty=0;
  if(V){ if(name==="src") renderSources(V); if(name==="sched") renderSchedules(V); }
}
var navBtns=document.querySelectorAll("nav button");
for(var i=0;i<navBtns.length;i++)
  navBtns[i].onclick=function(){ show(this.getAttribute("data-tab")); };

function showMain(){
  $("loginView").className="hide"; $("mainView").className="";
  refresh();
  if(timer) clearInterval(timer);
  timer=setInterval(refresh,5000);
}
function logout(){
  if(timer) clearInterval(timer);
  timer=null; TK=null; V=null;
  try{ localStorage.removeItem("gc_token"); }catch(e){}
  $("mainView").className="hide"; $("loginView").className="";
}

/* ── 修改密碼：走 /auth/change-password，控制台的連線改完不會被踢掉，電腦那格也不會 ── */
$("pwBtn").onclick=function(){
  var old=$("pwOld").value, a=$("pwNew").value, b=$("pwNew2").value, m=$("pwMsg");
  var min=(V&&V.min_password_length)||8;
  m.className="err";
  if(!old){ m.textContent="請輸入目前密碼"; return; }
  if(a!==b){ m.textContent="兩次輸入的新密碼不一致"; return; }
  if(a.length<min){ m.textContent="新密碼至少要 "+min+" 個字元"; return; }
  if(a===old){ m.textContent="新密碼不能跟目前的一樣"; return; }
  var btn=this; btn.disabled=true; m.textContent="";
  api("/auth/change-password",{old_password:old,new_password:a}).then(function(r){
    btn.disabled=false;
    if(r.body&&r.body.ok){
      $("pwOld").value=$("pwNew").value=$("pwNew2").value="";
      m.className="err good"; m.textContent="密碼已更新，下次登入請用新密碼"; return;
    }
    var c=(r.body&&r.body.error)||"unknown";
    m.textContent={bad_old_password:"目前密碼不正確",too_short:"新密碼至少要 "+min+" 個字元",
      same_as_old:"新密碼不能跟目前的一樣",session_invalid:"連線已失效，請重新登入",
      expired:"方案已到期，請聯繫管理員續約",suspended:"帳號已停用，請聯繫管理員"}[c]||("更新失敗（"+c+"）");
  }).catch(function(){ btn.disabled=false; m.textContent="連不上伺服器"; });
};

$("loginBtn").onclick=function(){
  var b=this; b.disabled=true; $("loginErr").textContent="";
  api("/auth/login",{
    username:$("u").value.trim(), password:$("p").value,
    device:(navigator.userAgent.indexOf("iPhone")>=0?"iPhone":
            navigator.userAgent.indexOf("Android")>=0?"Android":"瀏覽器"),
    scope:"console"
  }).then(function(r){
    b.disabled=false;
    if(r.body&&r.body.ok&&r.body.member){
      TK=r.body.member.session_token;
      try{ localStorage.setItem("gc_token",TK); }catch(e){}
      $("p").value=""; show("home"); showMain();
    }else{
      var c=(r.body&&r.body.error)||"unknown";
      $("loginErr").textContent={bad_credentials:"帳號或密碼錯誤",
        suspended:"帳號已停用，請聯繫管理員",
        expired:"方案已到期，請聯繫管理員續約"}[c]||("登入失敗（"+c+"）");
    }
  }).catch(function(){ b.disabled=false; $("loginErr").textContent="連不上伺服器"; });
};
$("p").addEventListener("keydown",function(e){ if(e.key==="Enter") $("loginBtn").click(); });
$("follow").onchange=function(){ push({following:this.checked}); };
$("logoutBtn").onclick=function(){ api("/auth/logout",{}).then(logout).catch(logout); };

document.addEventListener("visibilitychange",function(){
  if(!document.hidden&&TK) refresh();
});

try{ TK=localStorage.getItem("gc_token"); }catch(e){ TK=null; }
if(TK){
  api("/auth/me").then(function(r){
    if(r.status===200&&r.body.ok) showMain(); else logout();
  }).catch(logout);
}
</script>
</body>
</html>
"""


def render() -> str:
    return _PAGE
