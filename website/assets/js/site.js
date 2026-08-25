/* ============================================================================
   site — 導覽、語言選單、手風琴、捲動進場、數字跑動、卡片光暈
   全部是漸進增強：JS 不執行時頁面仍然完整可讀可操作。
   ========================================================================== */

(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $  = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ------------------------------------------------------------ 導覽列 --- */
  function initNav() {
    var nav = $('.nav');
    if (!nav) return;

    var onScroll = function () { nav.classList.toggle('is-stuck', window.scrollY > 24); };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    var burger = $('.nav-burger');
    var links = $('.nav-links');
    if (burger && links) {
      burger.addEventListener('click', function () {
        var open = burger.getAttribute('aria-expanded') === 'true';
        burger.setAttribute('aria-expanded', String(!open));
        links.classList.toggle('is-open', !open);
      });
      // 點了選單項目就收起來
      links.addEventListener('click', function (e) {
        if (e.target.closest('a')) {
          burger.setAttribute('aria-expanded', 'false');
          links.classList.remove('is-open');
        }
      });
    }
  }

  /* -------------------------------------------------------- 語言切換 ---- */
  function initLang() {
    var btn = $('.lang-btn');
    var menu = $('.lang-menu');
    if (!btn || !menu) return;

    var close = function () {
      btn.setAttribute('aria-expanded', 'false');
      menu.classList.remove('is-open');
    };

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!open));
      menu.classList.toggle('is-open', !open);
    });

    document.addEventListener('click', function (e) {
      if (!menu.contains(e.target) && !btn.contains(e.target)) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { close(); btn.focus(); }
    });

    $$('button[data-lang]', menu).forEach(function (item) {
      item.addEventListener('click', function () {
        window.I18N.set(item.getAttribute('data-lang'));
        close();
      });
    });

    // 選單裡的勾選狀態與按鈕上的語言名稱要跟著實際語言走
    var sync = function (lang) {
      $$('button[data-lang]', menu).forEach(function (item) {
        item.setAttribute('aria-selected', String(item.getAttribute('data-lang') === lang));
      });
      var label = $('.lang-current', btn);
      if (label) label.textContent = lang === 'en' ? 'EN' : '繁中';
    };
    document.addEventListener('i18n:changed', function (e) { sync(e.detail.lang); });
    if (window.I18N) sync(window.I18N.lang);
  }

  /* ---------------------------------------------------------- 手風琴 ---- */
  function initFaq() {
    $$('.faq-q').forEach(function (q) {
      q.addEventListener('click', function () {
        var open = q.getAttribute('aria-expanded') === 'true';
        // 一次只開一個，讀起來比較不亂
        $$('.faq-q').forEach(function (o) { o.setAttribute('aria-expanded', 'false'); });
        q.setAttribute('aria-expanded', String(!open));
      });
    });
  }

  /* ------------------------------------------------------ 捲動進場 ------ */
  function initReveal() {
    var targets = $$('[data-reveal]');
    if (!targets.length) return;

    // reduced-motion 時直接顯示，不加會產生位移的 class
    if (reduceMotion || !('IntersectionObserver' in window)) {
      targets.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }

    targets.forEach(function (el) { el.classList.add('reveal'); });

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        var delay = parseInt(el.getAttribute('data-reveal') || '0', 10);
        setTimeout(function () { el.classList.add('is-in'); }, delay);
        io.unobserve(el);       // 只跑一次，不重播
      });
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.12 });

    targets.forEach(function (el) { io.observe(el); });
  }

  /* ------------------------------------------------------ 數字跑動 ------ */
  function initCounters() {
    var els = $$('[data-count]');
    if (!els.length) return;

    var render = function (el, v) {
      var dec = parseInt(el.getAttribute('data-decimals') || '0', 10);
      var pre = el.getAttribute('data-prefix') || '';
      var suf = el.getAttribute('data-suffix') || '';
      el.textContent = pre + v.toFixed(dec).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + suf;
    };

    if (reduceMotion || !('IntersectionObserver' in window)) {
      els.forEach(function (el) { render(el, parseFloat(el.getAttribute('data-count'))); });
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        io.unobserve(el);

        var target = parseFloat(el.getAttribute('data-count'));
        var dur = 1100;
        var t0 = performance.now();

        var tick = function (now) {
          var p = Math.min((now - t0) / dur, 1);
          // easeOutExpo：一開始快、尾巴慢，讀起來像在「定格」
          var e = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
          render(el, target * e);
          if (p < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    }, { threshold: 0.4 });

    els.forEach(function (el) { render(el, 0); io.observe(el); });
  }

  /* -------------------------------------------------- 卡片游標光暈 ------ */
  function initTiles() {
    if (reduceMotion) return;
    $$('.tile').forEach(function (tile) {
      tile.addEventListener('pointermove', function (e) {
        var r = tile.getBoundingClientRect();
        tile.style.setProperty('--mx', (e.clientX - r.left) + 'px');
        tile.style.setProperty('--my', (e.clientY - r.top) + 'px');
      });
    });
  }

  /* ---------------------------------------------- 從設定檔填入數值 ------ */
  function initConfig() {
    var cfg = window.SITE_CONFIG;
    if (!cfg) return;

    // 績效區塊：關掉就整段移除，不留空殼
    var statsSection = $('[data-section="stats"]');
    if (statsSection && cfg.showStats === false) {
      statsSection.remove();
    } else if (statsSection && cfg.stats) {
      Object.keys(cfg.stats).forEach(function (k) {
        var el = $('[data-stat="' + k + '"]');
        if (!el) return;
        var s = cfg.stats[k];
        el.setAttribute('data-count', s.value);
        el.setAttribute('data-decimals', s.decimals || 0);
        if (s.prefix) el.setAttribute('data-prefix', s.prefix);
        if (s.suffix) el.setAttribute('data-suffix', s.suffix);
      });
    }

    // 方案價格
    if (cfg.pricing && cfg.pricing.monthly) {
      Object.keys(cfg.pricing.monthly).forEach(function (k) {
        var el = $('[data-price="' + k + '"]');
        if (el) el.textContent = cfg.pricing.monthly[k];
      });
      $$('[data-currency]').forEach(function (el) { el.textContent = cfg.pricing.currency; });
    }

  }


  /* -------------------------------------------------- 月 / 年計費切換 ---- */
  /* 年付不打折，而是「付 12 個月拿 14 個月的可使用時間」——
     所以月費數字不會變，變的是旁邊那個徽章。這跟一般 SaaS 的年繳折扣不同，
     要讓人一眼看出差別在哪。 */
  function initBilling() {
    var group = $('.billing');
    if (!group) return;
    var note = $('#billingNote');

    $$('button[data-billing]', group).forEach(function (btn) {
      btn.addEventListener('click', function () {
        $$('button[data-billing]', group).forEach(function (b) {
          b.setAttribute('aria-selected', String(b === btn));
        });
        if (note) note.classList.toggle('is-on', btn.getAttribute('data-billing') === 'yearly');
      });
    });
  }

  /* ---------------------------------------------- 比較表「顯示所有功能」 -- */
  function initCompare() {
    var btn = $('#cmpToggle');
    var wrap = $('#cmpWrap');
    if (!btn || !wrap) return;

    btn.addEventListener('click', function () {
      var open = wrap.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', String(open));
      var label = $('span', btn);
      if (label && window.I18N) {
        label.textContent = window.I18N.t(open ? 'pricing.showLess' : 'pricing.showAll');
        // 換語言時要記得目前是展開還是收起，不能一律填回「顯示所有功能」
        label.setAttribute('data-i18n', open ? 'pricing.showLess' : 'pricing.showAll');
      }
      var icon = $('svg', btn);
      if (icon) icon.style.transform = open ? 'rotate(180deg)' : '';
      if (!open) wrap.scrollIntoView({ block: 'nearest' });
    });
  }


  /* ------------------------------------------------ 下載頁的系統偵測 ---- */
  /* 把使用者當下系統那張卡排到前面並標起來。純粹是省一次判斷，
     兩個平台都還是看得到 —— 偵測錯的時候不能讓人找不到另一個。
     這裡不再處理下載連結：安裝包一律私訊索取，頁面上沒有下載按鈕。 */
  function initDownload() {
    var cards = $$('.dl-card[data-os]');
    if (!cards.length) return;

    var ua = navigator.userAgent;
    var platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    var guess = '';
    if (/Win/i.test(platform) || /Windows/i.test(ua)) guess = 'windows';
    else if (/Mac/i.test(platform) || /Mac OS X/i.test(ua)) guess = 'macos';

    cards.forEach(function (c) {
      c.classList.toggle('is-you', !!guess && c.getAttribute('data-os') === guess);
    });
  }

  /* ------------------------------------------------------------ 啟動 ---- */
  function boot() {
    initNav();
    initLang();
    initFaq();
    initConfig();     // 要在 counters 之前，data-count 才有值
    initReveal();
    initCounters();
    initTiles();
    initBilling();
    initCompare();
    initDownload();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
