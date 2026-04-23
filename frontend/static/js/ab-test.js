// PhinodIA A/B testing framework — client-side.
//
// Each test has an id (e.g., 'precios_cta_v1'), variants (a/b/...), weights
// (default 50/50), and TTL. Variant is assigned on first visit, cached in
// cookie, and URL param `?ab_<test_id>=b` overrides for manual testing.
//
// Variant travels with conversion events:
//   - Pixel events use content_category suffix `_AB_<variant>`
//   - /payments/checkout receives ab_variant in POST body → backend appends
//     to reference as `-v<variant>` suffix → webhook parses back → CAPI
//     Purchase gets custom_data.ab_variant. Dashboard aggregates from
//     transactions.plan_name encoded suffix.

(function () {
  'use strict';

  // Registered tests. Add new tests here + implement the UI change where
  // it's applicable. Keeping the config in a single place so bot +
  // dashboard stay in sync.
  const TESTS = {
    precios_cta_v1: {
      description: 'Texto del botón Comprar — control vs. "Quiero empezar"',
      variants: ['a', 'b'],
      weights: [0.5, 0.5],
      ttl_days: 14,
    },
  };

  function _pickVariant(test) {
    const r = Math.random();
    let cum = 0;
    for (let i = 0; i < test.variants.length; i++) {
      cum += test.weights[i];
      if (r < cum) return test.variants[i];
    }
    return test.variants[test.variants.length - 1];
  }

  function _readCookie(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
    return m ? decodeURIComponent(m[1]) : '';
  }

  function _writeCookie(name, value, maxAgeSec) {
    document.cookie =
      name + '=' + encodeURIComponent(value) +
      ';max-age=' + maxAgeSec +
      ';path=/;SameSite=Lax';
  }

  // Assign or read variant for a given test_id. Always returns a variant
  // from TESTS[test_id].variants. Caller is responsible for applying UI.
  function getVariant(testId) {
    const test = TESTS[testId];
    if (!test) {
      console.warn('[ab] unknown test', testId);
      return '';
    }
    // URL param override (for QA + Sergio testing)
    const urlParam = new URLSearchParams(window.location.search).get('ab_' + testId);
    if (urlParam && test.variants.indexOf(urlParam) !== -1) {
      return urlParam;
    }
    // Cookie cache
    const cookieKey = 'ph_ab_' + testId;
    const cached = _readCookie(cookieKey);
    if (cached && test.variants.indexOf(cached) !== -1) return cached;
    // Fresh assignment
    const picked = _pickVariant(test);
    _writeCookie(cookieKey, picked, test.ttl_days * 86400);
    return picked;
  }

  // Build the suffixed content_category for Meta Pixel events so A/B
  // attribution shows up natively in Events Manager breakdowns.
  function categoryWithVariant(baseCategory, testId) {
    const v = getVariant(testId);
    return v ? baseCategory + '_AB_' + v.toUpperCase() : baseCategory;
  }

  window.phAB = { getVariant, categoryWithVariant, TESTS };
})();
