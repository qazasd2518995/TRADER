"""會員的手機控制台（Hub 直接吐的一頁 HTML）。

為什麼是手寫的一頁：Hub 的映像檔只複製 copy_trader/central 且不裝任何第三方
套件（見 Dockerfile），所以沒有樣板引擎、沒有前端框架、也不能外連 CDN。

功能對齊電腦版會員端（webui.py）：帳戶淨值與績效、持倉與掛單、跟單開關、
每個訊號來源各自的策略、自動排程、方案與到期倒數。但排版是重做的 ——
電腦版是三欄面板加表格，手機上照搬會變成一堆要橫向捲的小字。內容太多，
所以切成四個分頁，底部固定導覽列（拇指構得到的位置）。

  總覽   跟單開關、淨值、今日損益、持倉、掛單、績效
  策略   每個來源獨立設定；沒授權的顯示成鎖住而不是消失
  排程   自動跟單時段
  帳號   會員名稱、方案、到期／額度倒數、權益清單

**沒有任何全域交易設定。** 電腦版面板一個都沒有（default_lot_size /
use_martingale / martingale_* / partial_close_ratios 在 webui.py 裡出現 0 次），
那些值只是「某來源沒設定時的預設種子」。手機上多做一個全域手數會讓人以為
它跟來源設定是兩套東西 —— 實際上來源的 base_lot 永遠說了算。

**能改的只有設定，不能平倉也不能下單。** 一旦開放，產品就從「跟單工具」變成
「交易終端」，責任層級完全不同，而且 MT5 官方 app 本來就能做。

介面上最重要的一條規矩：**誠實顯示這是在控制會員自己家裡那台電腦，不是雲端
服務。** 掛機端沒上線時按什麼都不會發生，畫面必須講出來。所以每一項可改的
東西都有「已套用／套用中」，只有掛機端真的回報過相同的值才算已套用。
"""
from __future__ import annotations

_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1216">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>黃金跟單 · 控制台</title>
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
.grid3 .k,.g2 .k{font-size:11px;color:var(--faint);letter-spacing:.05em}
.grid3 .v,.g2 .v{font-size:16px;font-weight:600;margin-top:3px}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:13px 10px}

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
  border:1px solid var(--line);color:var(--faint);white-space:nowrap}
.pill.ok{color:var(--up);border-color:#20503c}
.pill.wait{color:var(--warn);border-color:#4a3c1f}
.pill.lock{color:var(--faint)}

.fld{margin-top:14px;padding-top:14px;border-top:1px solid var(--line2)}
.fld:first-child{margin-top:0;padding-top:0;border-top:0}
.fld .lbl{font-size:15px;font-weight:600}
.fld .st{font-size:12px;margin-top:4px}
.fld .hint{font-size:11.5px;color:var(--faint);margin-top:5px;line-height:1.45}
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
button.act{border:0;border-radius:10px;background:var(--gold);color:#1a1408;
  font-size:15px;font-weight:600;padding:11px 17px;cursor:pointer;flex:0 0 auto}
button.act:disabled{opacity:.4}
button.wide{width:100%;padding:14px;border:0;border-radius:10px;
  background:var(--gold);color:#1a1408;font-size:15px;font-weight:600;cursor:pointer}
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

.bene{display:flex;align-items:center;gap:9px;padding:8px 0;font-size:14px}
.bene+.bene{border-top:1px solid var(--line2)}
.bene i{font-style:normal;font-size:13px;width:16px;flex:0 0 auto;text-align:center}
.bene.no{color:var(--faint)}

.banner{border-radius:12px;padding:11px 13px;font-size:13px;margin-bottom:12px;
  border:1px solid;line-height:1.45}
.banner.bad{background:#241618;border-color:#48242a;color:#ffb3ae}
.banner.warn{background:#241f14;border-color:#463b20;color:#f5d79c}
.err{color:#ff9d99;font-size:12.5px;margin-top:9px;min-height:16px;line-height:1.45}
.muted{color:var(--faint);font-size:12.5px;margin-top:12px;line-height:1.5}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.dot.on{background:var(--up)} .dot.off{background:var(--down)}
.bar{height:5px;border-radius:999px;background:#242a32;overflow:hidden;margin-top:9px}
.bar i{display:block;height:100%;border-radius:999px;background:var(--gold)}

/* 條件顯示的子區塊 */
.sub-fld{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line2)}
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
.two input{flex:1}
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
        <div class="spark" id="sparkWrap"><svg id="spark" viewBox="0 0 300 44"
          preserveAspectRatio="none" aria-label="累計損益走勢"></svg></div>
        <div id="recent"></div>
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

var STALE=60;
function live(v){ return !!v.agent_reported_at && (Date.now()/1000-v.agent_reported_at)<STALE; }

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

/* ── 策略：全部設定都在這裡，跟電腦版一樣以來源為單位 ───────────────── */
var MODES={flat:"均注（每單固定手數）",martingale:"馬丁加倍（虧損後加碼）",
           risk_percent:"本金比例（依風險%計算）"};
var TPS={single:"單一點位（只掛第一個止盈）",breakeven:"保本移損（觸及後把停損拉到成本）",
         partial:"分批平倉（各止盈分段出場）"};
function opts(map,cur,lock){
  var h="";
  for(var k in map)
    h+='<option value="'+k+'"'+(cur===k?" selected":"")+((lock&&lock[k])?" disabled":"")+
       '>'+map[k]+((lock&&lock[k])?"（方案未含）":"")+'</option>';
  return h;
}
function fldNum(label,key,val,ph,hint){
  return '<div class="sub-fld"><div class="k" style="font-size:12px;color:var(--dim)">'+label+'</div>'+
    '<div class="inline"><input type="number" inputmode="decimal" data-f="'+key+'" '+
    'step="0.01" placeholder="'+(ph||"")+'" value="'+(val!=null?val:"")+'"></div>'+
    (hint?'<div class="hint">'+hint+'</div>':'')+'</div>';
}
function renderSources(v){
  var ent=v.entitlements||{}, allowed=(ent.sources||[]),
      d=(v.desired||{}).source_profiles||{}, a=(v.applied||{}).source_profiles||{},
      list=v.all_sources||[], h="";
  for(var i=0;i<list.length;i++){
    var name=list[i].name, label=list[i].label, ok=allowed.indexOf(name)>=0;
    var p={}, k;
    for(k in (a[name]||{})) p[k]=a[name][k];
    for(k in (d[name]||{})) p[k]=d[name][k];
    var on=!!p.enabled, mode=p.mode||"flat", tp=p.tp_mode||"single";
    h+='<div class="src'+(ok&&on?"":" off")+'" data-src="'+esc(name)+'">'+
       '<div class="top"><div><div class="nm">'+esc(label)+
       (ok?"":' <span class="pill lock">未包含在方案</span>')+'</div>'+
       '<div class="sub">'+esc(name)+'</div></div>'+
       '<label class="sw"><input type="checkbox" data-f="enabled"'+
       (on?" checked":"")+(ok?"":" disabled")+'><i></i></label></div>';
    if(ok){
      h+='<div class="body">'+
         '<div class="fld"><div class="lbl" style="font-size:14px">下單方式</div>'+
         '<div class="inline"><select data-f="mode" data-re="1">'+
         opts(MODES,mode,{martingale:!ent.martingale,risk_percent:!ent.dynamic_lot})+
         '</select></div>';
      if(mode==="risk_percent")
        h+=fldNum("每單風險（本金 %）","risk_percent",p.risk_percent,"0.5",
                  "依帳戶淨值與停損距離反推手數，風險固定、手數浮動。");
      else
        h+=fldNum("基礎手數","base_lot",p.base_lot,"0.01",
                  ent.max_lot?("方案上限 "+ent.max_lot+" 手"):"");
      if(mode==="martingale")
        h+='<div class="sub-fld"><div class="k" style="font-size:12px;color:var(--dim)">加倍倍數 / 最大層數</div>'+
           '<div class="two" style="margin-top:9px">'+
           '<input type="number" inputmode="decimal" data-f="multiplier" step="0.1" min="1" max="10" '+
           'placeholder="倍數" value="'+(p.multiplier!=null?p.multiplier:"")+'">'+
           '<input type="number" inputmode="numeric" data-f="max_level" step="1" min="1" max="10" '+
           'placeholder="層數" value="'+(p.max_level!=null?p.max_level:"")+'"></div>'+
           '<div class="hint">層數越深，一次反向走勢的傷害越大。</div></div>';
      h+='</div>'+
         '<div class="fld"><div class="lbl" style="font-size:14px">止盈處理</div>'+
         '<div class="inline"><select data-f="tp_mode" data-re="1">'+
         opts(TPS,tp,{partial:!ent.partial_close,breakeven:!ent.breakeven})+'</select></div>';
      if(tp==="breakeven")
        h+=fldNum("保本觸發距離（美元）","breakeven_distance",p.breakeven_distance,"0",
                  "0 = 觸及第一個止盈之後才把停損拉到成本。");
      if(tp==="partial")
        h+='<div class="sub-fld"><div class="k" style="font-size:12px;color:var(--dim)">各止盈出場比例</div>'+
           '<div class="inline"><input type="text" inputmode="decimal" data-f="partial_ratios" '+
           'placeholder="0.5,0.3,0.2" value="'+
           ((p.partial_ratios||[]).map(function(x){return Math.round(x*100)/100;}).join(",")||"")+'"></div>'+
           '<div class="hint">填 50,30,20 或 0.5,0.3,0.2 都可以，會自動換算成總和 100%。</div></div>';
      h+='</div>'+
         '<div class="fld"><div class="lbl" style="font-size:14px">風險控管 <span class="dim" style="font-weight:400;font-size:12px">（0 = 不限）</span></div>'+
         '<div class="two" style="margin-top:9px">'+
         '<input type="number" inputmode="decimal" data-f="max_daily_loss" placeholder="單日虧損上限" '+
         'value="'+(p.max_daily_loss!=null?p.max_daily_loss:"")+'">'+
         '<input type="number" inputmode="decimal" data-f="max_daily_profit" placeholder="單日獲利上限" '+
         'value="'+(p.max_daily_profit!=null?p.max_daily_profit:"")+'"></div>'+
         '<div class="two" style="margin-top:8px">'+
         '<input type="number" inputmode="numeric" data-f="max_active_orders" placeholder="同時持有上限" '+
         'value="'+(p.max_active_orders!=null?p.max_active_orders:"")+'">'+
         '<input type="number" inputmode="numeric" data-f="max_daily_trades" placeholder="單日筆數上限" '+
         'value="'+(p.max_daily_trades!=null?p.max_daily_trades:"")+'"></div>'+
         '<div class="hint">達到就停止這個來源的新單，隔日重置。</div></div>'+
         '<button class="wide act" data-save="1" style="margin-top:14px">套用這個來源</button>'+
         '<div class="err" data-err="1"></div></div>';
    }
    h+='</div>';
  }
  $("srcList").innerHTML=h;
  bindSources();
}
function bindSources(){
  var cards=document.querySelectorAll(".src");
  for(var i=0;i<cards.length;i++) (function(card){
    var name=card.getAttribute("data-src");
    var sw=card.querySelector('input[data-f="enabled"]');
    if(sw&&!sw.disabled) sw.onchange=function(){
      var pt={}; pt[name]={enabled:this.checked}; push({source_profiles:pt});
    };
    // 下單方式／止盈處理一改就要重畫（顯示的子欄位不一樣），先存起來再重繪
    var sels=card.querySelectorAll("[data-re]");
    for(var j=0;j<sels.length;j++) sels[j].onchange=function(){
      var pt={}; pt[name]={}; pt[name][this.getAttribute("data-f")]=this.value;
      push({source_profiles:pt});
    };
    var btn=card.querySelector("[data-save]");
    if(btn) btn.onclick=function(){
      var one={}, fs=card.querySelectorAll("[data-f]");
      for(var k=0;k<fs.length;k++){
        var f=fs[k], key=f.getAttribute("data-f");
        if(key==="enabled") continue;
        var val=String(f.value).trim();
        if(val==="") continue;
        one[key]=(f.tagName==="SELECT"||key==="partial_ratios")?val:Number(val);
      }
      var pt={}; pt[name]=one;
      push({source_profiles:pt}, card.querySelector("[data-err]"));
    };
  })(cards[i]);
}

/* ── 排程 ───────────────────────────────────────────────────────────── */
var DAYS=["一","二","三","四","五","六","日"];
function renderSchedules(v){
  var ent=v.entitlements||{}, ok=!!ent.schedule;
  $("schedLock").textContent=ok?"":"方案未包含";
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
  sparkline(s.curve);
  var rc=s.recent||[], rh="";
  for(var i=0;i<rc.length;i++)
    rh+='<div class="row"><span class="k">'+esc(rc[i].close_time)+'　'+esc(rc[i].symbol)+
        '</span><span class="v num '+cls(Number(rc[i].profit))+'">'+money(rc[i].profit)+'</span></div>';
  $("recent").innerHTML=rh;

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
    if(errEl) errEl.textContent=explain(r.body.rejected||[]);
    render(r.body);
    if(tab==="src") renderSources(r.body);
    if(tab==="sched") renderSchedules(r.body);
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
