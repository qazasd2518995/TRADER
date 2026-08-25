/* ============================================================================
   tape — 頂端的即時行情條。

   用 TradingView 公開提供的免費嵌入式 Widget。它是第三方腳本，隨時可能被
   擋掉（廣告攔截器、公司防火牆、對方服務中斷），所以這裡不假設它一定會成功：
   給它 6 秒，沒長出 iframe 就切成靜態退回列，版面不會塌一塊空白。
   ========================================================================== */

(function () {
  'use strict';

  var WIDGET_SRC = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js';
  var TIMEOUT_MS = 6000;

  var tape = document.getElementById('tape');
  if (!tape) return;

  var container = tape.querySelector('.tradingview-widget-container');
  if (!container) return;

  /* 目前掛載的狀態。
     locale：已經掛上去的語系，用來擋掉重複掛載。
     script：那次掛載插入的 <script>。
     done：那支 script 是否已經跑完。

     為什麼要記 done —— widget 的腳本在執行時會回頭找自己的 parentNode。
     如果這時候我們已經把 container.innerHTML 清掉（例如使用者切了語言，
     或啟動時不小心掛了兩次），它拿到的 parentNode 是 null，就會丟
     "Cannot read properties of null (reading 'querySelector')"。
     所以還在飛的時候不能動 DOM，要等它落地。 */
  var current = null;
  var timer = null;

  function fallback() {
    tape.classList.add('is-fallback');
  }

  function localeFor(lang) { return lang === 'en' ? 'en' : 'zh_TW'; }

  function mount(locale) {
    if (current && current.locale === locale) return;      // 已經是這個語系了

    // 前一支還在執行 —— 等它跑完再換，不然會把執行中的 script 拔掉
    if (current && !current.done) {
      var again = function () { mount(locale); };
      current.script.addEventListener('load', again, { once: true });
      current.script.addEventListener('error', again, { once: true });
      return;
    }

    clearTimeout(timer);
    tape.classList.remove('is-fallback');
    container.innerHTML = '<div class="tradingview-widget-container__widget"></div>';

    var cfg = {
      symbols: [
        { proName: 'OANDA:XAUUSD',    title: locale === 'zh_TW' ? '黃金/美元' : 'Gold' },
        { proName: 'OANDA:XAGUSD',    title: locale === 'zh_TW' ? '白銀/美元' : 'Silver' },
        { proName: 'TVC:USOIL',       title: locale === 'zh_TW' ? '原油'     : 'Crude Oil' },
        { proName: 'BITSTAMP:BTCUSD', title: 'BTC/USD' },
        { proName: 'FX:EURUSD',       title: 'EUR/USD' }
      ],
      showSymbolLogo: true,
      // isTransparent: true 會讓 widget 無視 colorTheme 改用亮色渲染 ——
      // 實測在深色頁面上會爆出一條白帶。讓它自己畫深色底才對得上。
      isTransparent: false,
      displayMode: 'adaptive',
      colorTheme: 'dark',
      locale: locale
    };

    var s = document.createElement('script');
    s.src = WIDGET_SRC;
    s.async = true;
    s.type = 'text/javascript';
    s.innerHTML = JSON.stringify(cfg);

    var state = { locale: locale, script: s, done: false };
    s.addEventListener('load',  function () { state.done = true; });
    s.addEventListener('error', function () { state.done = true; fallback(); });

    current = state;
    container.appendChild(s);

    // 腳本 load 不代表 widget 畫出來了 —— 要等它自己插 iframe 進來才算成功
    timer = setTimeout(function () {
      state.done = true;
      if (!container.querySelector('iframe')) fallback();
    }, TIMEOUT_MS);
  }

  // 語言換了就重掛一次，讓商品名稱跟著翻
  document.addEventListener('i18n:changed', function (e) {
    mount(localeFor(e.detail.lang));
  });

  /* 第一次掛載交給 i18n —— 它載完語言檔就會發 i18n:changed。
     但如果語言檔載不到（404、fetch 被擋），那個事件永遠不會來，
     所以補一個保險：等 ready 落地後再確認一次。 */
  if (window.I18N && window.I18N.ready && typeof window.I18N.ready.then === 'function') {
    window.I18N.ready.then(function () {
      mount(localeFor(window.I18N.lang));
    });
  } else {
    mount(localeFor('zh-Hant'));
  }
})();
