/* ============================================================================
   i18n — 執行期字串替換。

   設計取捨：不用函式庫、不做建置期產生、不做每語言一份 HTML。
   HTML 裡先寫好繁中作為 fallback，所以 JS 掛掉或還沒載完時，
   使用者看到的仍然是一個完整可讀的繁中網站，而不是滿頁的 key。

   用法：
     <h1 data-i18n="hero.title1">訊號歸訊號</h1>
     <input data-i18n-attr="placeholder:form.email">
     <a data-i18n-attr="aria-label:nav.menu,title:nav.menu">
   ========================================================================== */

(function () {
  'use strict';

  var SUPPORTED = ['zh-Hant', 'en'];
  var DEFAULT = 'zh-Hant';
  var STORAGE_KEY = 'site_lang';

  var cache = {};
  var current = DEFAULT;

  /* i18n/ 相對於站台根目錄。頁面可能在 /pricing/ 這種子路徑下，
     所以要從 <html data-root> 拿根路徑，預設為 '/'。 */
  function root() {
    return document.documentElement.getAttribute('data-root') || '/';
  }

  function normalize(tag) {
    if (!tag) return null;
    var t = String(tag).toLowerCase();
    if (t.indexOf('zh') === 0) {
      // zh-tw / zh-hk / zh-hant 都給繁中；zh-cn / zh-hans 目前沒有簡中，一律回繁中
      return 'zh-Hant';
    }
    if (t.indexOf('en') === 0) return 'en';
    return null;
  }

  /* 決定順序：?lang= → 使用者上次的選擇 → 繁中。
     
     刻意不看 navigator.language。這個站的客群在台灣，而台灣不少人的
     系統與瀏覽器是設英文的 —— 跟著瀏覽器語言走的話，這些人第一次進來
     會看到英文版，那是錯的對象看到錯的語言。
     
     英文版留給主動切換的人，切了會記在 localStorage，下次直接是英文。
     分享連結時想指定語言就加 ?lang=en。 */
  function detect() {
    var q = new URLSearchParams(location.search).get('lang');
    var fromQuery = normalize(q);
    if (fromQuery) return fromQuery;

    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (SUPPORTED.indexOf(saved) !== -1) return saved;
    } catch (e) { /* 無痕模式會擋 localStorage，忽略 */ }

    return DEFAULT;
  }

  function load(lang) {
    if (cache[lang]) return Promise.resolve(cache[lang]);
    return fetch(root() + 'i18n/' + lang + '.json', { cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('i18n ' + lang + ' HTTP ' + r.status);
        return r.json();
      })
      .then(function (dict) { cache[lang] = dict; return dict; });
  }

  function apply(dict, lang) {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      if (Object.prototype.hasOwnProperty.call(dict, key)) el.textContent = dict[key];
    });

    document.querySelectorAll('[data-i18n-attr]').forEach(function (el) {
      el.getAttribute('data-i18n-attr').split(',').forEach(function (pair) {
        var bits = pair.split(':');
        var attr = (bits[0] || '').trim();
        var key = (bits[1] || '').trim();
        if (attr && Object.prototype.hasOwnProperty.call(dict, key)) {
          el.setAttribute(attr, dict[key]);
        }
      });
    });

    /* 每頁可以用 <html data-meta-title="pricing.metaTitle"> 指定自己的 key，
       沒指定就退回首頁那組。不這樣做的話，子頁切語言時標題會留在中文。 */
    var root = document.documentElement;
    var tKey = root.getAttribute('data-meta-title') || 'meta.title';
    var dKey = root.getAttribute('data-meta-desc') || 'meta.desc';
    if (dict[tKey]) document.title = dict[tKey];
    var desc = document.querySelector('meta[name="description"]');
    if (desc && dict[dKey]) desc.setAttribute('content', dict[dKey]);

    document.documentElement.setAttribute('lang', lang === 'en' ? 'en' : 'zh-Hant');
    current = lang;

    document.dispatchEvent(new CustomEvent('i18n:changed', { detail: { lang: lang, dict: dict } }));
  }

  function setLang(lang, opts) {
    opts = opts || {};
    if (SUPPORTED.indexOf(lang) === -1) lang = DEFAULT;

    return load(lang).then(function (dict) {
      apply(dict, lang);

      try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}

      if (opts.updateUrl !== false) {
        var url = new URL(location.href);
        // 預設語言不留參數，網址乾淨一點
        if (lang === DEFAULT) url.searchParams.delete('lang');
        else url.searchParams.set('lang', lang);
        history.replaceState(null, '', url);
      }
      return dict;
    }).catch(function (err) {
      // 載不到就維持 HTML 裡原本寫死的繁中，網站仍然可讀
      console.warn('[i18n] 載入失敗，維持頁面預設語言：', err.message);
    });
  }

  window.I18N = {
    get lang() { return current; },
    supported: SUPPORTED,
    t: function (key, fallback) {
      var d = cache[current];
      return (d && Object.prototype.hasOwnProperty.call(d, key)) ? d[key]
           : (fallback !== undefined ? fallback : key);
    },
    set: setLang,
    ready: setLang(detect(), { updateUrl: false })
  };
})();
