"""會員的手機控制台（Hub 直接吐的一頁 HTML）。

為什麼是手寫的一頁：Hub 的映像檔只複製 copy_trader/central 且不裝任何第三方
套件（見 Dockerfile），所以沒有樣板引擎、沒有前端框架、也不能外連 CDN。
一頁自己包好最省事，也最不容易在 256MB 的機器上出事。

資訊架構對齊電腦版會員端（webui.py）：淨值與損益擺最上面、接著掛單與持倉、
再來績效統計。但排版是重做的 —— 電腦版是三欄面板加表格，手機上照搬會變成
一堆要橫向捲動的小字。這裡改成單欄卡片、大字級數字、成對的鍵值列。

**能控制的只有兩件事：跟單開/暫停、基礎手數。** 刻意不做平倉與下單 —— 一旦
開放，產品就從「跟單工具」變成「交易終端」，責任層級完全不同，而且 MT5 官方
app 本來就能做。其餘全是唯讀。

介面上最重要的一條規矩：**誠實顯示這是在控制會員自己家裡那台電腦，不是雲端
服務。** 掛機端沒上線時按什麼都不會發生，畫面必須講出來，不能讓他以為按了
暫停就安全了。所以每一項可控項目都有「已套用 / 套用中」兩種狀態，只有掛機端
真的回報過相同的值才算已套用。
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
  --bg:#0f1216; --card:#161a20; --card2:#1b2027; --line:#252b33;
  --fg:#e9edf2; --dim:#8b96a5; --faint:#5c6672;
  --gold:#d8b25f; --up:#3ecf8e; --down:#f0655f; --warn:#d9a441;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;
  padding-bottom:calc(env(safe-area-inset-bottom) + 28px)}
.wrap{max-width:520px;margin:0 auto;padding:0 14px}
.num{font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.up{color:var(--up)} .down{color:var(--down)} .dim{color:var(--dim)}

/* 頁首 */
header{padding:calc(env(safe-area-inset-top) + 14px) 14px 10px;
  display:flex;align-items:center;justify-content:space-between}
.brand{font-size:15px;font-weight:600;letter-spacing:.03em}
.brand span{color:var(--gold)}
.who{font-size:12px;color:var(--dim)}

/* 卡片 */
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:16px;margin-bottom:12px}
.card h2{font-size:13px;font-weight:600;color:var(--dim);margin:0 0 12px;
  letter-spacing:.06em}
.card h2 em{font-style:normal;color:var(--faint);font-weight:400;margin-left:6px}

/* 淨值主視覺 */
.hero .eq{font-size:34px;font-weight:600;line-height:1.15;margin:2px 0 4px}
.hero .cur{font-size:15px;color:var(--dim);font-weight:400;margin-left:4px}
.hero .today{font-size:15px;font-weight:600}
.hero .sub{display:flex;gap:18px;margin-top:14px;padding-top:14px;
  border-top:1px solid var(--line)}
.hero .sub div{flex:1}
.hero .sub .k{font-size:11px;color:var(--faint);letter-spacing:.05em}
.hero .sub .v{font-size:16px;font-weight:600;margin-top:3px}

/* 鍵值列 */
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;
  min-height:40px}
.row + .row{border-top:1px solid var(--line)}
.row .k{font-size:14px;color:var(--dim)}
.row .v{font-size:14px;text-align:right}

/* 開關 */
.ctl{display:flex;align-items:center;justify-content:space-between;gap:14px}
.ctl .lbl{font-size:16px;font-weight:600}
.ctl .st{font-size:12px;margin-top:4px}
.sw{position:relative;width:60px;height:33px;flex:0 0 auto}
.sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;z-index:2}
.sw i{position:absolute;inset:0;background:#2b323b;border-radius:999px;transition:.18s}
.sw i:after{content:"";position:absolute;top:4px;left:4px;width:25px;height:25px;
  border-radius:50%;background:#8b96a5;transition:.18s}
.sw input:checked + i{background:#1d4736}
.sw input:checked + i:after{transform:translateX(27px);background:var(--up)}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;
  border:1px solid var(--line);color:var(--faint);white-space:nowrap}
.pill.ok{color:var(--up);border-color:#20503c}
.pill.wait{color:var(--warn);border-color:#4a3c1f}

/* 手數 */
.lot{display:flex;gap:10px;margin-top:12px;padding-top:14px;
  border-top:1px solid var(--line)}
.lot .fld{flex:1}
.lot .fld .lbl{font-size:16px;font-weight:600}
.lot .fld .st{font-size:12px;margin-top:4px}
.lotin{display:flex;gap:8px;margin-top:9px}
input[type=number],input[type=text],input[type=password]{
  width:100%;padding:12px 13px;border-radius:10px;border:1px solid var(--line);
  background:#0c0f13;color:var(--fg);font-size:16px}
input:focus{outline:none;border-color:var(--gold)}
button{border:0;border-radius:10px;background:var(--gold);color:#1a1408;
  font-size:15px;font-weight:600;padding:12px 18px;cursor:pointer}
button:disabled{opacity:.45}
button.wide{width:100%;padding:14px}
button.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);
  font-weight:400}

/* 部位／掛單清單 */
.item{display:flex;align-items:center;gap:10px;padding:11px 0}
.item + .item{border-top:1px solid var(--line)}
.side{font-size:11px;font-weight:700;padding:3px 7px;border-radius:6px;
  letter-spacing:.06em;flex:0 0 auto}
.side.buy{background:#123528;color:var(--up)}
.side.sell{background:#38191a;color:var(--down)}
.item .mid{flex:1;min-width:0}
.item .sym{font-size:14px;font-weight:600}
.item .meta{font-size:12px;color:var(--faint);margin-top:2px}
.item .pl{font-size:15px;font-weight:600;text-align:right;flex:0 0 auto}
.empty{font-size:13px;color:var(--faint);padding:6px 0}

/* 績效 */
.perf{display:flex;align-items:center;gap:16px}
.ring{width:88px;height:88px;border-radius:50%;flex:0 0 auto;position:relative;
  display:grid;place-items:center}
.ring:after{content:"";position:absolute;inset:9px;border-radius:50%;
  background:var(--card)}
.ring .txt{position:relative;z-index:1;text-align:center}
.ring .txt b{display:block;font-size:19px;font-weight:600}
.ring .txt small{font-size:10px;color:var(--faint);letter-spacing:.05em}
.perf .side2{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:12px 10px}
.perf .side2 .k{font-size:11px;color:var(--faint);letter-spacing:.05em}
.perf .side2 .v{font-size:15px;font-weight:600;margin-top:2px}
.spark{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.spark svg{width:100%;height:46px;display:block}

/* 提示條 */
.banner{border-radius:12px;padding:11px 13px;font-size:13px;margin-bottom:12px;
  border:1px solid;line-height:1.45}
.banner.bad{background:#241618;border-color:#48242a;color:#ffb3ae}
.banner.warn{background:#241f14;border-color:#463b20;color:#f5d79c}
.err{color:#ff9d99;font-size:13px;margin-top:10px;min-height:17px}
.muted{color:var(--faint);font-size:12.5px;margin-top:12px;line-height:1.5}
.hide{display:none}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.dot.on{background:var(--up)} .dot.off{background:var(--down)}
</style>
</head>
<body>

<div id="loginView">
  <header><div class="brand">黃金<span>跟單</span> · 控制台</div></header>
  <div class="wrap">
    <div class="card">
      <label class="k dim" for="u" style="font-size:13px">帳號</label>
      <input id="u" type="text" autocomplete="username" autocapitalize="none"
             spellcheck="false" style="margin-top:6px">
      <div style="height:12px"></div>
      <label class="k dim" for="p" style="font-size:13px">密碼</label>
      <input id="p" type="password" autocomplete="current-password" style="margin-top:6px">
      <div style="height:16px"></div>
      <button class="wide" id="loginBtn">登入</button>
      <div class="err" id="loginErr"></div>
      <p class="muted">這裡控制的是你自己電腦上的跟單程式。電腦沒開機的話，
      在這裡的操作會先記著，等電腦上線才生效。</p>
    </div>
  </div>
</div>

<div id="mainView" class="hide">
  <header>
    <div class="brand">黃金<span>跟單</span></div>
    <div class="who" id="who">—</div>
  </header>
  <div class="wrap">
    <div id="banner"></div>

    <div class="card hero">
      <h2>帳戶淨值</h2>
      <div class="eq num" id="eq">—</div>
      <div class="today num" id="todayPl">—</div>
      <div class="sub">
        <div><div class="k">結餘</div><div class="v num" id="bal">—</div></div>
        <div><div class="k">已用保證金</div><div class="v num" id="marg">—</div></div>
        <div><div class="k">浮動損益</div><div class="v num" id="float">—</div></div>
      </div>
    </div>

    <div class="card">
      <h2>跟單控制</h2>
      <div class="ctl">
        <div>
          <div class="lbl">自動跟單</div>
          <div class="st" id="followState">—</div>
        </div>
        <label class="sw"><input type="checkbox" id="follow"><i></i></label>
      </div>
      <div class="lot">
        <div class="fld">
          <div class="lbl">基礎手數</div>
          <div class="st" id="lotState">—</div>
          <div class="lotin">
            <input id="lot" type="number" inputmode="decimal" step="0.01" min="0.01">
            <button id="lotBtn">套用</button>
          </div>
        </div>
      </div>
      <div class="err" id="err"></div>
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
        <div class="ring" id="ring"><div class="txt"><b id="wr">—</b><small>勝率</small></div></div>
        <div class="side2">
          <div><div class="k">今日</div><div class="v num" id="pToday">—</div></div>
          <div><div class="k">近 7 日</div><div class="v num" id="pWeek">—</div></div>
          <div><div class="k">累計</div><div class="v num" id="pAll">—</div></div>
          <div><div class="k">筆數</div><div class="v num" id="pN">—</div></div>
        </div>
      </div>
      <div class="spark" id="sparkWrap"><svg id="spark" viewBox="0 0 300 46"
        preserveAspectRatio="none" aria-label="累計損益走勢"></svg></div>
      <div id="recent" style="margin-top:6px"></div>
    </div>

    <div class="card">
      <h2>連線與方案</h2>
      <div class="row"><span class="k">掛機端</span><span class="v" id="agent">—</span></div>
      <div class="row"><span class="k">MT5 帳號</span><span class="v num" id="acct">—</span></div>
      <div class="row"><span class="k">方案</span><span class="v" id="tier">—</span></div>
      <div class="row"><span class="k" id="expK">到期</span><span class="v" id="exp">—</span></div>
    </div>

    <button class="wide ghost" id="logoutBtn">登出</button>
  </div>
</div>

<script>
"use strict";
var TK=null, timer=null, dirty=0;
function $(i){ return document.getElementById(i); }
function api(path, body){
  var o={headers:{"Content-Type":"application/json"}};
  if(TK) o.headers["Authorization"]="Bearer "+TK;
  if(body!==undefined){ o.method="POST"; o.body=JSON.stringify(body); }
  return fetch(path,o).then(function(r){
    return r.json().catch(function(){return {};}).then(function(j){
      return {status:r.status, body:j};
    });
  });
}
function ago(ts){
  if(!ts) return "從未";
  var s=Math.max(0,Math.floor(Date.now()/1000-ts));
  if(s<60) return s+" 秒前";
  if(s<3600) return Math.floor(s/60)+" 分前";
  if(s<86400) return Math.floor(s/3600)+" 小時前";
  return Math.floor(s/86400)+" 天前";
}
function n(v,d){ return (v===null||v===undefined||v==="")?"—":Number(v).toFixed(d===undefined?2:d); }
function money(v,d){
  if(v===null||v===undefined||v==="") return "—";
  var x=Number(v); return (x>0?"+":"")+x.toFixed(d===undefined?2:d);
}
function cls(v){ return (v>0)?"up":((v<0)?"down":"dim"); }
function put(id,v,d){
  var e=$(id); e.textContent=money(v,d);
  e.className="v num "+cls(Number(v));
}

var STALE=60;   // 掛機端每 10 秒報一次；60 秒沒消息才算離線，很寬鬆了
function live(v){ return !!v.agent_reported_at && (Date.now()/1000-v.agent_reported_at)<STALE; }

function sparkline(curve){
  var el=$("spark");
  if(!curve || curve.length<2){ $("sparkWrap").className="spark hide"; return; }
  $("sparkWrap").className="spark";
  var lo=Math.min.apply(null,curve), hi=Math.max.apply(null,curve);
  if(hi===lo){ hi=lo+1; }
  var W=300,H=46,pad=3, step=W/(curve.length-1), pts=[];
  for(var i=0;i<curve.length;i++){
    var y=pad+(H-2*pad)*(1-(curve[i]-lo)/(hi-lo));
    pts.push((i*step).toFixed(1)+","+y.toFixed(1));
  }
  var last=curve[curve.length-1];
  var col = last>0 ? "#3ecf8e" : (last<0 ? "#f0655f" : "#8b96a5");
  el.innerHTML =
    '<polyline fill="none" stroke="'+col+'" stroke-width="1.6" '+
    'stroke-linejoin="round" stroke-linecap="round" points="'+pts.join(" ")+'"></polyline>';
}

function listItems(rows, kind){
  if(!rows || !rows.length) return '<div class="empty">目前沒有</div>';
  var h="";
  for(var i=0;i<rows.length;i++){
    var r=rows[i], side=(r.type||"").toLowerCase();
    var isBuy = side.indexOf("buy")>=0;
    var lab = isBuy ? "買" : "賣";
    var meta, right;
    if(kind==="pos"){
      meta = n(r.volume,2)+" 手 · 進 "+n(r.price_open,2)+" · 現 "+n(r.price_current,2);
      right = '<div class="pl '+cls(Number(r.profit))+'">'+money(r.profit)+'</div>';
    } else {
      meta = n(r.volume,2)+" 手 · 掛 "+n(r.price,2)+
             " · 損 "+n(r.sl,2)+" / 盈 "+n(r.tp,2);
      right = '<div class="pl dim" style="font-size:12px">等待成交</div>';
    }
    h += '<div class="item"><span class="side '+(isBuy?"buy":"sell")+'">'+lab+'</span>'+
         '<div class="mid"><div class="sym">'+(r.symbol||"—")+'</div>'+
         '<div class="meta num">'+meta+'</div></div>'+right+'</div>';
  }
  return h;
}

function render(v){
  var L=live(v), d=v.desired||{}, a=v.applied||{}, acc=v.account||{}, s=v.stats||{};

  $("who").textContent = v.username || "";

  var b="";
  if(!L){
    b='<div class="banner bad">掛機端離線（最後回報：'+ago(v.agent_reported_at)+
      '）。在這裡改的設定會先記著，等你的電腦上線才會生效。</div>';
  } else if(v.mt5_stale){
    b='<div class="banner warn">掛機端在線，但連不上 MT5。目前不會下單。</div>';
  }
  $("banner").innerHTML=b;

  // 淨值
  $("eq").innerHTML = (acc.equity!=null ? Number(acc.equity).toFixed(2) : "—") +
    '<span class="cur">'+(acc.currency||"")+'</span>';
  var t=s.profit_today;
  $("todayPl").innerHTML = (t==null) ? '<span class="dim">今日 —</span>'
    : '<span class="'+cls(t)+'">今日 '+money(t)+'</span>';
  put("bal", acc.balance); put("marg", acc.margin); put("float", acc.profit);
  $("bal").className="v num"; $("marg").className="v num";

  // 跟單開關
  if(!dirty) $("follow").checked = ("following" in d) ? !!d.following : !!a.following;
  var fOk = ("following" in d) && ("following" in a) && a.following===d.following;
  $("followState").innerHTML = !("following" in d)
    ? '<span class="dim">'+(("following" in a)?(a.following?"跟單中":"已停止"):"—")+'</span>'
    : ((fOk&&L) ? '<span class="pill ok">已套用 · '+(d.following?"跟單中":"已停止")+'</span>'
                : '<span class="pill wait">套用中 · 等掛機端確認</span>');

  // 手數
  var le=$("lot");
  if(document.activeElement!==le && !dirty){
    var c = ("lot_size" in d)?d.lot_size:a.lot_size;
    if(c!==undefined&&c!==null) le.value=c;
  }
  var lOk = ("lot_size" in d)&&("lot_size" in a)&&Math.abs(a.lot_size-d.lot_size)<1e-9;
  var mx=(v.entitlements||{}).max_lot;
  var cap = mx ? '　<span class="dim">上限 '+mx+'</span>' : '　<span class="dim">無上限</span>';
  $("lotState").innerHTML = !("lot_size" in d)
    ? '<span class="dim">目前 '+n(a.lot_size,2)+'</span>'+cap
    : ((lOk&&L) ? '<span class="pill ok">已套用 · '+n(d.lot_size,2)+'</span>'+cap
                : '<span class="pill wait">套用中 · '+n(d.lot_size,2)+'</span>'+cap);

  // 持倉／掛單
  $("posN").textContent = (v.positions_count!=null)?("("+v.positions_count+")"):"";
  $("ordN").textContent = (v.orders_count!=null)?("("+v.orders_count+")"):"";
  $("posList").innerHTML = listItems(v.positions, "pos");
  $("ordList").innerHTML = listItems(v.orders, "ord");

  // 績效
  var wr = s.win_rate;
  $("wr").textContent = (wr==null)?"—":(wr+"%");
  var deg = (wr==null)?0:(wr*3.6);
  $("ring").style.background = (wr==null)
    ? "conic-gradient(#252b33 0deg 360deg)"
    : "conic-gradient(var(--up) 0deg "+deg+"deg, #38191a "+deg+"deg 360deg)";
  put("pToday", s.profit_today); put("pWeek", s.profit_week); put("pAll", s.profit_total);
  $("pN").textContent = (s.total==null)?"—":s.total;
  $("pN").className="v num";
  sparkline(s.curve);

  var rc=s.recent||[], rh="";
  for(var i=0;i<rc.length;i++){
    rh += '<div class="row"><span class="k">'+(rc[i].close_time||"")+'　'+
          (rc[i].symbol||"")+'</span><span class="v num '+cls(Number(rc[i].profit))+'">'+
          money(rc[i].profit)+'</span></div>';
  }
  $("recent").innerHTML = rh;

  // 連線與方案
  $("agent").innerHTML='<span class="dot '+(L?"on":"off")+'"></span>'+
    (L?"在線":"離線")+' <span class="dim">'+ago(v.agent_reported_at)+'</span>';
  $("acct").textContent = acc.login ? (acc.login+" · "+(acc.server||"")) : "—";
  $("tier").textContent = v.tier_label||v.tier||"—";
  var u=v.usage;
  if(u && u.time_pause && u.seconds_left!=null){
    $("expK").textContent="剩餘額度";
    var hrs=u.seconds_left/3600;
    $("exp").textContent = (hrs>=24 ? (hrs/24).toFixed(1)+" 天" : hrs.toFixed(1)+" 小時") +
      (u.market_open ? "" : "（休市中，不計時）");
  } else {
    $("expK").textContent="到期";
    $("exp").textContent = v.expires_at
      ? new Date(v.expires_at*1000).toLocaleDateString() : "無期限";
  }
}

function refresh(){
  return api("/console/settings").then(function(r){
    if(r.status===401){ logout(); return; }
    if(r.body&&r.body.ok) render(r.body);
  }).catch(function(){});
}
function push(patch){
  dirty=1; $("err").textContent="";
  return api("/console/settings",{settings:patch}).then(function(r){
    dirty=0;
    if(r.status===401){ logout(); return; }
    if(!r.body||!r.body.ok){ $("err").textContent="沒有送出去，請再試一次"; return; }
    var rj=r.body.rejected||[];
    if(rj.indexOf("lot_size:capped_by_tier")>=0)
      $("err").textContent="手數超過方案上限，已自動調整為上限值";
    else if(rj.length) $("err").textContent="部分設定未生效："+rj.join("、");
    render(r.body);
  }).catch(function(){ dirty=0; $("err").textContent="連不上伺服器"; });
}
function showMain(){
  $("loginView").className="hide"; $("mainView").className="";
  refresh();
  if(timer) clearInterval(timer);
  timer=setInterval(refresh,5000);
}
function logout(){
  if(timer) clearInterval(timer);
  timer=null; TK=null;
  try{ localStorage.removeItem("gc_token"); }catch(e){}
  $("mainView").className="hide"; $("loginView").className="";
}

$("loginBtn").onclick=function(){
  var b=this; b.disabled=true; $("loginErr").textContent="";
  api("/auth/login",{
    username:$("u").value.trim(), password:$("p").value,
    device:(navigator.userAgent.indexOf("iPhone")>=0?"iPhone":
            navigator.userAgent.indexOf("Android")>=0?"Android":"瀏覽器"),
    scope: "console"
  }).then(function(r){
    b.disabled=false;
    if(r.body&&r.body.ok&&r.body.member){
      TK=r.body.member.session_token;
      try{ localStorage.setItem("gc_token",TK); }catch(e){}
      $("p").value=""; showMain();
    }else{
      var c=(r.body&&r.body.error)||"unknown";
      $("loginErr").textContent={
        bad_credentials:"帳號或密碼錯誤",
        suspended:"帳號已停用，請聯繫管理員",
        expired:"方案已到期，請聯繫管理員續約"
      }[c]||("登入失敗（"+c+"）");
    }
  }).catch(function(){ b.disabled=false; $("loginErr").textContent="連不上伺服器"; });
};
$("p").addEventListener("keydown",function(e){ if(e.key==="Enter") $("loginBtn").click(); });
$("follow").onchange=function(){ push({following:this.checked}); };
$("lotBtn").onclick=function(){
  var v=parseFloat($("lot").value);
  if(!(v>0)){ $("err").textContent="請輸入有效的手數"; return; }
  push({lot_size:v});
};
$("logoutBtn").onclick=function(){ api("/auth/logout",{}).then(logout).catch(logout); };

// 切回前景立刻刷新 —— 手機切回來卻看到十分鐘前的數字很容易誤判
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
