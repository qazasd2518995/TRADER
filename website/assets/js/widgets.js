/* ============================================================================
   widgets — TradingView 官方免費嵌入式元件的統一載入器。

   這些是第三方腳本，隨時可能被擋掉（廣告攔截器、公司防火牆、對方服務中斷、
   中國大陸的網路環境），所以每一個都必須有「載不到會怎樣」的答案：
   給它一段時間，沒長出 iframe 就顯示 fallback，版面不會塌一塊空白。

   踩過的坑：isTransparent: true 會讓 widget 無視 colorTheme 改用亮色渲染，
   在深色頁面上會爆出一條白帶。全部設 false，讓它自己畫深色底。
   ========================================================================== */

(function () {
  'use strict';

  var SRC = 'https://s3.tradingview.com/external-embedding/embed-widget-';
  var TIMEOUT_MS = 7000;

  function locale() {
    return (window.I18N && window.I18N.lang === 'en') ? 'en' : 'zh_TW';
  }

  /* 一個 widget 的掛載狀態。done 用來擋「還在飛的時候就把 script 從 DOM 拔掉」——
     widget 腳本執行時會回頭找自己的 parentNode，拔掉會讓它丟 null 錯誤。 */
  function Slot(host) {
    this.host = host;
    this.kind = host.getAttribute('data-widget');
    this.current = null;
    this.timer = null;
  }

  Slot.prototype.fail = function () {
    this.host.classList.add('is-fallback');
  };

  /* 設定裡的 {"$t": "market.metals"} 會在掛載當下換成當前語言的字串。
     widget 的分頁標題、商品顯示名這些是寫在 JSON 設定裡的，不是 DOM 節點，
     沒辦法用 data-i18n 處理 —— 不這樣做的話，切成英文時分頁還是中文。 */
  function resolve(v) {
    if (Array.isArray(v)) return v.map(resolve);
    if (v && typeof v === 'object') {
      if (typeof v.$t === 'string') {
        return window.I18N ? window.I18N.t(v.$t, v.$t) : v.$t;
      }
      var out = {};
      for (var k in v) if (Object.prototype.hasOwnProperty.call(v, k)) out[k] = resolve(v[k]);
      return out;
    }
    return v;
  }

  Slot.prototype.config = function () {
    var raw = this.host.getAttribute('data-config') || '{}';
    var cfg;
    try { cfg = JSON.parse(raw); } catch (e) { cfg = {}; }
    cfg = resolve(cfg);
    cfg.locale = locale();
    cfg.colorTheme = 'dark';
    cfg.isTransparent = false;   // true 會強制亮色渲染，見檔頭說明
    return cfg;
  };

  Slot.prototype.mount = function () {
    var self = this;
    var loc = locale();
    if (this.current && this.current.locale === loc) return;

    // 前一支還在執行 —— 等它落地再換，否則會把執行中的 script 拔掉
    if (this.current && !this.current.done) {
      var again = function () { self.mount(); };
      this.current.script.addEventListener('load', again, { once: true });
      this.current.script.addEventListener('error', again, { once: true });
      return;
    }

    clearTimeout(this.timer);
    this.host.classList.remove('is-fallback');

    var box = this.host.querySelector('.tv-widget');
    if (!box) return;
    box.innerHTML = '<div class="tradingview-widget-container__widget"></div>';

    var s = document.createElement('script');
    s.src = SRC + this.kind + '.js';
    s.async = true;
    s.type = 'text/javascript';
    s.innerHTML = JSON.stringify(this.config());

    var state = { locale: loc, script: s, done: false };
    s.addEventListener('load', function () { state.done = true; });
    s.addEventListener('error', function () { state.done = true; self.fail(); });
    this.current = state;
    box.appendChild(s);

    // 腳本 load 不代表畫出來了 —— 要等它自己插 iframe 進來才算成功
    this.timer = setTimeout(function () {
      state.done = true;
      if (!box.querySelector('iframe')) self.fail();
    }, TIMEOUT_MS);
  };

  var slots = [];

  function init() {
    slots = Array.prototype.map.call(
      document.querySelectorAll('[data-widget]'),
      function (host) { return new Slot(host); }
    );
    if (!slots.length) return;

    var mountAll = function () { slots.forEach(function (s) { s.mount(); }); };

    // 語言換了就重掛，商品名稱與介面文字跟著翻
    document.addEventListener('i18n:changed', mountAll);

    /* 第一次掛載交給 i18n —— 它載完語言檔會發 i18n:changed。
       語言檔載不到時那個事件永遠不會來，所以 ready 落地後再確認一次。 */
    if (window.I18N && window.I18N.ready && typeof window.I18N.ready.then === 'function') {
      window.I18N.ready.then(mountAll);
    } else {
      mountAll();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
