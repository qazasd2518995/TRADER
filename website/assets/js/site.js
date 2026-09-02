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

    // 方案幣別。實際月／年價格由 initBilling 統一渲染，不能在這裡先填月費，
    // 否則年繳預設頁面會先閃一下月價才跳成年價。
    if (cfg.pricing && cfg.pricing.monthly) {
      $$('[data-currency]').forEach(function (el) { el.textContent = cfg.pricing.currency; });
    }

  }


  /* -------------------------------------------------- 月 / 年計費切換 ---- */
  /* 年繳顯示採成熟 SaaS 常見的三層資訊：
       1. 主數字 = 真正會收取的年費
       2. 原年價刪除線 + 省幾個月
       3. 平均每月 + 折扣百分比
     所有數字只從 site-config 的月費、使用月數、付費月數推導，避免文案與價格漂移。 */
  function priceText(value) {
    var n = Number(value || 0);
    if (Math.abs(n - Math.round(n)) < 0.000001) return Math.round(n).toLocaleString('en-US');
    return n.toFixed(2).replace(/\.00$/, '');
  }

  function billingMath(key, pricing) {
    var monthly = Number(pricing.monthly[key] || 0);
    var yearly = pricing.yearly || {};
    var serviceMonths = Math.max(1, Number(yearly.serviceMonths || 12));
    var paidMonths = Math.max(1, Math.min(serviceMonths, Number(yearly.paidMonths || serviceMonths)));
    var original = monthly * serviceMonths;
    var total = monthly * paidMonths;
    var saved = original - total;
    var discount = original > 0 ? Math.round((saved / original) * 100) : 0;
    return {
      monthly: monthly,
      serviceMonths: serviceMonths,
      paidMonths: paidMonths,
      savedMonths: serviceMonths - paidMonths,
      original: original,
      total: total,
      saved: saved,
      average: serviceMonths > 0 ? total / serviceMonths : 0,
      discount: discount
    };
  }

  function ensureBillingDetail(plan) {
    var detail = $('.plan-billing-detail', plan);
    if (detail) return detail;
    detail = document.createElement('div');
    detail.className = 'plan-billing-detail';
    detail.setAttribute('aria-live', 'polite');
    var price = $('.plan-price', plan);
    if (price) price.insertAdjacentElement('afterend', detail);
    return detail;
  }

  /* 原價刪除線要放在大數字「上面」才有折扣感——把它插在 .plan-price 之前，
     而不是塞進下方的小字收據裡。月繳時保留空行，卡片高度才不會跳。 */
  function ensureStrikeRow(plan) {
    var row = $('.plan-strike', plan);
    if (row) return row;
    var price = $('.plan-price', plan);
    if (!price) return null;
    row = document.createElement('div');
    row.className = 'plan-strike';
    row.setAttribute('aria-live', 'polite');
    price.insertAdjacentElement('beforebegin', row);
    return row;
  }

  function renderPlanDetail(plan, key, mode, pricing) {
    var detail = ensureBillingDetail(plan);
    if (!detail) return;
    var strike = ensureStrikeRow(plan);
    var calc = billingMath(key, pricing);
    var currency = pricing.currency || 'US$';
    var t = function (key, fallback) {
      return window.I18N ? window.I18N.t(key, fallback) : fallback;
    };
    detail.replaceChildren();
    if (strike) strike.replaceChildren();
    detail.classList.toggle('is-yearly', mode === 'yearly');
    var showDeal = mode === 'yearly' && calc.monthly > 0;
    if (strike) strike.classList.toggle('is-on', showDeal);

    if (calc.monthly <= 0) {
      var free = document.createElement('span');
      free.className = 'plan-billing-free';
      free.textContent = t('pricing.freeBilling', '永久免費，不分月繳年繳');
      detail.appendChild(free);
      return;
    }

    if (mode !== 'yearly') {
      var monthHint = document.createElement('span');
      monthHint.className = 'plan-billing-monthly';
      monthHint.textContent = t('pricing.monthlyHint', '按月付款，保留最大彈性');
      detail.appendChild(monthHint);
      return;
    }

    /* 大數字上方：原價劃掉 + 折扣紅標，一眼看到「588 變 441」。 */
    if (strike) {
      var original = document.createElement('s');
      original.className = 'plan-strike-old';
      original.textContent = currency + priceText(calc.original);
      original.setAttribute('aria-label',
        t('pricing.regularAnnual', '原價') + ' ' + currency + priceText(calc.original));
      var off = document.createElement('b');
      off.className = 'plan-strike-off';
      off.textContent = '−' + calc.discount + '%';
      strike.append(original, off);
    }

    /* 大數字下方：省幾個月 + 平均每月，讓人自己核對划算在哪。 */
    var bottom = document.createElement('div');
    bottom.className = 'annual-deal-bottom';
    var saved = document.createElement('strong');
    saved.className = 'annual-save-pill';
    saved.textContent = t('pricing.saveMonths', '省 {months} 個月')
      .replace('{months}', String(calc.savedMonths));
    var average = document.createElement('span');
    average.textContent = t('pricing.averageMonthly', '平均每月') +
      ' ' + currency + priceText(calc.average);
    bottom.append(saved, average);
    detail.append(bottom);
  }

  function initBilling() {
    var groups = $$('.billing');
    var pricing = window.SITE_CONFIG && window.SITE_CONFIG.pricing;
    if (!groups.length || !pricing || !pricing.monthly) return;
    var valid = ['monthly', 'yearly'];
    var mode = valid.indexOf(pricing.defaultBilling) !== -1 ? pricing.defaultBilling : 'monthly';

    var render = function (nextMode) {
      mode = valid.indexOf(nextMode) !== -1 ? nextMode : 'monthly';
      document.documentElement.setAttribute('data-billing-mode', mode);
      $$('button[data-billing]').forEach(function (button) {
        button.setAttribute('aria-selected', String(button.getAttribute('data-billing') === mode));
      });
      $$('[data-billing-note]').forEach(function (note) {
        note.classList.toggle('is-on', mode === 'yearly');
        note.setAttribute('aria-hidden', String(mode !== 'yearly'));
      });

      $$('[data-price]').forEach(function (el) {
        var key = el.getAttribute('data-price');
        if (!Object.prototype.hasOwnProperty.call(pricing.monthly, key)) return;
        var calc = billingMath(key, pricing);
        el.textContent = priceText(mode === 'yearly' ? calc.total : calc.monthly);
      });
      $$('[data-price-period]').forEach(function (el) {
        var key = mode === 'yearly' ? 'plans.perYear' : 'plans.perMonth';
        var translated = mode === 'yearly'
          ? (window.I18N ? window.I18N.t('plans.perYear', '/年') : '/年')
          : (window.I18N ? window.I18N.t('plans.perMonth', '/月') : '/月');
        el.setAttribute('data-i18n', key);
        el.textContent = translated;
      });
      $$('.plan').forEach(function (plan) {
        var amount = $('[data-price]', plan);
        if (amount) renderPlanDetail(plan, amount.getAttribute('data-price'), mode, pricing);
      });
    };

    groups.forEach(function (group) {
      $$('button[data-billing]', group).forEach(function (btn) {
        btn.addEventListener('click', function () { render(btn.getAttribute('data-billing')); });
        btn.addEventListener('keydown', function (event) {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          var next = btn.getAttribute('data-billing') === 'monthly' ? 'yearly' : 'monthly';
          render(next);
          var target = $('button[data-billing="' + next + '"]', group);
          if (target) target.focus();
        });
      });
    });

    document.addEventListener('i18n:changed', function () { render(mode); });
    render(mode);
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
