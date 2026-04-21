// Meta Pixel — loaded on every page. Pixel ID is fetched from
// /api/v1/public-config so we don't have to redeploy the static HTML
// every time the Pixel ID changes (or when first wiring it up).
//
// Why we don't inline the ID here: the frontend is bundled at deploy
// time. The Pixel ID isn't known until Sergio creates the dataset in
// Meta Events Manager. Fetching at runtime means he can paste the ID
// into EasyPanel env vars and it goes live instantly.
//
// Event dedup with CAPI: every conversion event the backend mirrors
// uses an event_id we control. The pages that fire conversion events
// (precios → InitiateCheckout, gracias-* → Purchase) read that event_id
// from a shared source and pass it as `eventID` here. If the dedup
// fails, Meta double-counts; if Pixel fails (ad-blocker), CAPI alone
// covers it. Both paths fire = best of both worlds.
(function () {
  'use strict';

  // Fetch + cache config so multiple page-loads in the same session don't
  // re-hit the endpoint. sessionStorage is per-tab — fine here, refresh
  // re-fetches but a deploy-window mismatch is the only risk.
  function getPixelId(cb) {
    const cached = sessionStorage.getItem('ph_pixel_id');
    if (cached !== null) {
      cb(cached);
      return;
    }
    fetch('/api/v1/public-config')
      .then((r) => (r.ok ? r.json() : { meta_pixel_id: '' }))
      .then((c) => {
        const id = c.meta_pixel_id || '';
        sessionStorage.setItem('ph_pixel_id', id);
        cb(id);
      })
      .catch(() => cb(''));
  }

  // Standard Meta snippet, no functional changes — just wrapped so we
  // only init it when we actually have a Pixel ID. Without the wrap
  // the snippet inits with `undefined` ID and Meta logs warnings.
  function init(pixelId) {
    if (!pixelId) return; // CAPI-only mode (early stage of BM warmup)
    !(function (f, b, e, v, n, t, s) {
      if (f.fbq) return;
      n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) f._fbq = n;
      n.push = n;
      n.loaded = !0;
      n.version = '2.0';
      n.queue = [];
      t = b.createElement(e);
      t.async = !0;
      t.src = v;
      s = b.getElementsByTagName(e)[0];
      s.parentNode.insertBefore(t, s);
    })(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');

    fbq('init', pixelId);
    fbq('track', 'PageView');
  }

  getPixelId(init);

  // ── Helpers exposed to page-specific scripts ──────────────────────
  // Read the _fbp cookie (Meta drops it after Pixel init). Used by the
  // checkout request to forward to CAPI for matching.
  window.getFbp = function () {
    const m = document.cookie.match(/(?:^|;\s*)_fbp=([^;]+)/);
    return m ? m[1] : '';
  };

  // Build the _fbc value from a ?fbclid= URL param. Meta wants format
  // `fb.1.<ts>.<fbclid>`. The cookie also captures this but landing
  // pages that come from a click sometimes don't have the cookie set
  // yet on first paint, so reading the URL is more reliable.
  window.getFbc = function () {
    const fromCookie = document.cookie.match(/(?:^|;\s*)_fbc=([^;]+)/);
    if (fromCookie) return fromCookie[1];
    const urlClick = new URLSearchParams(window.location.search).get('fbclid');
    if (urlClick) {
      return 'fb.1.' + Date.now() + '.' + urlClick;
    }
    return '';
  };

  // Track a Meta standard event with auto event_id forwarded to CAPI.
  // Page code calls `phTrack('InitiateCheckout', { value: 11990, currency: 'COP' }, eventId)`.
  window.phTrack = function (eventName, params, eventId) {
    if (typeof fbq !== 'function') return; // pixel still loading or disabled
    if (eventId) {
      fbq('track', eventName, params || {}, { eventID: eventId });
    } else {
      fbq('track', eventName, params || {});
    }
  };
})();
