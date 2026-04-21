/* ═══════════════════════════════════════════════
   PhinodIA — API Client & UI Helpers
   ═══════════════════════════════════════════════ */

const API = window.location.origin + '/api/v1';

// ── URL Safety ────────────────────────────────
// Reject anything that isn't an https URL or a same-origin /path. Used as
// defense-in-depth before assigning attacker-influenceable strings to
// <a>.href / <img>.src / <video>.src — protects against javascript:, data:,
// file:, etc. URI schemes that browsers would otherwise execute.
function isSafeMediaUrl(u) {
  if (typeof u !== 'string') return false;
  const t = u.trim();
  return /^https:\/\//i.test(t) || t.startsWith('/');
}

// ── Email Validation ──────────────────────────
// Reject leading/trailing dots in the local part, consecutive dots anywhere,
// no-dot domains, and TLDs shorter than 2 chars. Matches what Pydantic's
// EmailStr will accept on the server side.
function isValidEmail(email) {
  if (typeof email !== 'string') return false;
  const e = email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(e)) return false;
  if (/\.\./.test(e)) return false;
  const local = e.split('@')[0];
  if (local.startsWith('.') || local.endsWith('.')) return false;
  return true;
}

// ── Toast Notifications ────────────────────────
function initToasts() {
  if (!document.querySelector('.toast-container')) {
    const c = document.createElement('div');
    c.className = 'toast-container';
    // role=status with aria-live=polite so screen readers announce form
    // validation messages that are otherwise visual-only.
    c.setAttribute('role', 'status');
    c.setAttribute('aria-live', 'polite');
    c.setAttribute('aria-atomic', 'true');
    document.body.appendChild(c);
  }
}

function showToast(message, type = 'info') {
  initToasts();
  const container = document.querySelector('.toast-container');
  const icons = { success: '\u2713', error: '\u2717', warning: '\u26A0', info: '\u2139' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  // Use textContent (not innerHTML) so error messages from the API can't
  // inject HTML/event handlers (XSS protection).
  const iconSpan = document.createElement('span');
  iconSpan.textContent = icons[type] || '';
  const msgSpan = document.createElement('span');
  msgSpan.textContent = message;
  toast.appendChild(iconSpan);
  toast.appendChild(msgSpan);
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 4000);
}

// ── File Upload ────────────────────────────────
function initFileUpload(dropzoneId, previewId, urlFieldId) {
  const dropzone = document.getElementById(dropzoneId);
  if (!dropzone) return;
  const input = dropzone.querySelector('input[type="file"]');
  const preview = document.getElementById(previewId);

  ['dragenter', 'dragover'].forEach(e => {
    dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(e => {
    dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.remove('dragover'); });
  });

  dropzone.addEventListener('drop', ev => {
    if (ev.dataTransfer.files.length) {
      input.files = ev.dataTransfer.files;
      handleFileSelect(input.files[0], dropzone, preview, urlFieldId);
    }
  });

  input.addEventListener('change', () => {
    if (input.files.length) handleFileSelect(input.files[0], dropzone, preview, urlFieldId);
  });
}

async function handleFileSelect(file, dropzone, preview, urlFieldId) {
  // Server enforces JPEG/PNG/WebP via magic bytes; mirror it client-side so
  // SVG (with embedded JS) and HEIC fail before they even hit the network.
  const ALLOWED = new Set(['image/jpeg', 'image/png', 'image/webp']);
  if (!ALLOWED.has(file.type)) {
    showToast('Solo se permiten JPEG, PNG o WebP', 'error');
    return;
  }
  // Leave headroom for multipart overhead so the server's 10MB cap doesn't
  // reject files the user thinks are within bounds.
  if (file.size > 9.5 * 1024 * 1024) {
    showToast('La imagen no puede superar 9.5MB', 'error');
    return;
  }

  // Show local preview
  const reader = new FileReader();
  reader.onload = e => {
    if (preview) {
      preview.src = e.target.result;
      preview.classList.remove('hidden');
    }
    dropzone.classList.add('has-file');
  };
  reader.readAsDataURL(file);

  // Upload to server
  const formData = new FormData();
  formData.append('file', file);

  try {
    dropzone.style.opacity = '0.6';
    const res = await fetch(`${API}/upload/image`, { method: 'POST', body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || 'Error al subir la imagen');
    }
    const urlField = document.getElementById(urlFieldId);
    if (urlField) urlField.value = data.url;
    dropzone.style.opacity = '1';
    showToast('Imagen subida correctamente', 'success');
  } catch (err) {
    dropzone.style.opacity = '1';
    showToast(err.message, 'error');
  }
}

// ── API Calls ──────────────────────────────────
// Helper: parse JSON body, but never throw if the server returned HTML / empty
// (5xx behind a CDN, network blip, etc) — the caller deserves a clean message.
async function _safeJson(res) {
  try { return await res.json(); } catch (_) { return {}; }
}

// FastAPI 422 returns `detail` as an array of validation errors. Coercing it
// to a string yields "[object Object]" in the toast — useless to the user.
// Flatten it to a human-readable message.
function _formatDetail(detail, status) {
  if (!detail) return `Error ${status}`;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(d => (d && d.msg) ? d.msg : JSON.stringify(d)).join('; ');
  }
  return JSON.stringify(detail);
}

async function apiPost(path, body) {
  let res;
  try {
    res = await fetch(`${API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (_) {
    throw new Error('Sin conexion. Verifica tu red e intenta de nuevo.');
  }
  const data = await _safeJson(res);
  if (!res.ok) {
    const err = new Error(_formatDetail(data.detail, res.status));
    err.status = res.status;
    throw err;
  }
  return data;
}

async function apiGet(path) {
  let res;
  try {
    res = await fetch(`${API}${path}`);
  } catch (_) {
    throw new Error('Sin conexion. Verifica tu red e intenta de nuevo.');
  }
  const data = await _safeJson(res);
  if (!res.ok) {
    const err = new Error(_formatDetail(data.detail, res.status));
    err.status = res.status;
    throw err;
  }
  return data;
}

// Convenience: redirect to /precios on 402 from any generate flow.
function handleGenerateError(err) {
  if (err && err.status === 402) {
    showToast('Sin creditos. Te llevamos a la tienda...', 'warning');
    setTimeout(() => { window.location.href = '/precios'; }, 1500);
    return true;
  }
  return false;
}

// ── Persist email for /mis-generaciones auto-load ──
// 7-day TTL so a shared/library computer doesn't leak previous user's job
// history to the next browser session indefinitely.
function persistEmail(email) {
  if (email && isValidEmail(email)) {
    lsSet('phinodia_email', email, 7 * 24 * 60 * 60 * 1000);
  }
}

function getPersistedEmail() {
  return lsGet('phinodia_email');
}

// ── Generate Video ─────────────────────────────
async function generateVideo(formData) {
  persistEmail(formData.email);
  return apiPost('/generate/video', {
    email: formData.email,
    image_url: formData.image_url,
    description: formData.description,
    format: formData.format,
    duration: parseInt(formData.duration),
    product_name: formData.product_name,
    product_category: formData.product_category || '',
    pain_point: formData.pain_point || '',
    creative_direction: formData.creative_direction || '',
    data_consent: formData.data_consent,
  });
}

// ── Generate Image ─────────────────────────────
async function generateImage(formData) {
  persistEmail(formData.email);
  return apiPost('/generate/image', {
    email: formData.email,
    image_url: formData.image_url,
    description: formData.description,
    aspect_ratio: formData.aspect_ratio,
    product_name: formData.product_name,
    product_category: formData.product_category || '',
    creative_direction: formData.creative_direction || '',
    image_style: formData.image_style || 'product',
    data_consent: formData.data_consent,
  });
}

// ── Generate Landing Page ──────────────────────
async function generateLanding(formData) {
  persistEmail(formData.email);
  // Coerce numeric pricing fields — getFormData returns them as strings, but
  // the Pydantic LandingRequest expects ints. Without parseInt the empty
  // string becomes "", which Pydantic rejects with 422.
  const _toInt = (v) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n >= 0 ? n : 0;
  };
  return apiPost('/generate/landing', {
    email: formData.email,
    image_url: formData.image_url,
    description: formData.description,
    product_name: formData.product_name,
    product_category: formData.product_category || '',
    target_audience: formData.target_audience || '',
    style_preference: formData.style_preference || '',
    price: _toInt(formData.price),
    original_price: _toInt(formData.original_price),
    discount_percent: _toInt(formData.discount_percent),
    stock_urgency: formData.stock_urgency || '',
    guarantee: formData.guarantee || '',
    bonus: formData.bonus || '',
    whatsapp_number: formData.whatsapp_number || '',
    key_benefits: formData.key_benefits || '',
    shipping_info: formData.shipping_info || '',
    data_consent: formData.data_consent,
  });
}

// ── Job Status Polling ─────────────────────────
// Tracks per-jobId active polls and ALSO exposes a global cancel for any prior
// poll on the same page (so re-clicking "Consultar" doesn't stack loops).
const _activePolls = new Map();   // jobId -> { cancelled: bool }
let _lastPollHandle = null;

function cancelAllPolls() {
  for (const handle of _activePolls.values()) handle.cancelled = true;
  _activePolls.clear();
  _lastPollHandle = null;
}

async function pollJobStatus(jobId, onUpdate, intervalMs = 5000) {
  // Cancel any prior poll for this jobId AND any prior poll on the page —
  // re-entering checkStatus() with a new id should kill the old loop.
  cancelAllPolls();
  const handle = { cancelled: false };
  _activePolls.set(jobId, handle);
  _lastPollHandle = handle;

  let networkErrors = 0;
  const maxNetworkErrors = 10;
  const safeUpdate = (data) => {
    if (handle.cancelled) return;
    // Always supply a numeric progress so consumers don't render `width: undefined%`
    if (data.progress === undefined || data.progress === null) data.progress = 0;
    onUpdate(data);
  };
  // Validate UUID before any fetch — `jobId` from URL params is attacker-controlled.
  // Without this, a payload like `../by-email?email=victim@x.com` would interpolate
  // verbatim and the browser would normalize the path back into a different endpoint,
  // exposing other users' job histories.
  const _UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!_UUID.test(String(jobId))) {
    onUpdate({ status: 'error', error_message: 'ID no valido.', progress: 0 });
    return handle;
  }
  const poll = async () => {
    if (handle.cancelled) return;
    try {
      const res = await fetch(`${API}/jobs/status/${encodeURIComponent(jobId)}`);
      if (handle.cancelled) return;
      if (res.status === 404) {
        safeUpdate({ status: 'error', error_message: 'Trabajo no encontrado. Verifica el ID.', progress: 0 });
        return;
      }
      const data = await _safeJson(res);
      // 5xx is transient (Supabase blip, KIE provider hiccup) — retry instead
      // of marking the whole job lost. The status row exists; the worker is
      // still cooking the result.
      if (res.status >= 500) {
        networkErrors++;
        if (networkErrors >= maxNetworkErrors) {
          safeUpdate({ status: 'error', error_message: 'Servidor con problemas. Recarga la pagina.', progress: 0 });
          return;
        }
        setTimeout(poll, intervalMs * 2);
        return;
      }
      if (!res.ok) {
        safeUpdate({ status: 'error', error_message: data.detail || 'Error al consultar estado.', progress: 0 });
        return;
      }
      networkErrors = 0;
      // Wrap onUpdate so a buggy consumer callback (e.g. accessing a null DOM
      // node) doesn't kill the loop and freeze the user's progress bar.
      try { safeUpdate(data); } catch (e) { /* swallow consumer errors */ }
      if (data.status === 'completed' || data.status === 'failed') return;
      setTimeout(poll, intervalMs);
    } catch (err) {
      if (handle.cancelled) return;
      networkErrors++;
      if (networkErrors >= maxNetworkErrors) {
        safeUpdate({ status: 'error', error_message: 'Se perdio la conexion. Recarga la pagina e intenta de nuevo.', progress: 0 });
        return;
      }
      setTimeout(poll, intervalMs * 2);
    }
  };
  poll();
  return handle;  // caller can do handle.cancelled = true to stop early
}

// Stop in-flight polls when the user navigates away — otherwise mobile Safari
// keeps firing requests in the background tab until the tab is killed.
window.addEventListener('pagehide', cancelAllPolls);

// On bfcache restore (Safari/Firefox back-button), polls were cancelled by
// pagehide but the result UI still shows "Procesando". Skip the reload if
// there's an active job persisted in sessionStorage so we don't blow away
// recoverable in-flight state — the page can resume polling instead.
window.addEventListener('pageshow', (e) => {
  if (!e.persisted) return;
  let active = null;
  try { active = sessionStorage.getItem('phinodia_active_job'); } catch (_) {}
  if (!active) location.reload();
});

// ── localStorage with TTL ─────────────────────
// Wrapper that expires entries after `ttlMs` so a shared device doesn't leak
// one user's email/referral attribution to the next user indefinitely.
function lsSet(key, value, ttlMs) {
  try {
    localStorage.setItem(key, JSON.stringify({ v: value, e: Date.now() + ttlMs }));
  } catch (_) { /* quota / private mode */ }
}

function lsGet(key) {
  let raw;
  try { raw = localStorage.getItem(key); } catch (_) { return null; }
  if (!raw) return null;
  // Backward-compat: accept legacy plain-string values too.
  if (raw.charAt(0) !== '{') return raw;
  try {
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object') {
      try { localStorage.removeItem(key); } catch (_) {}
      return null;
    }
    if (obj.e && obj.e < Date.now()) {
      try { localStorage.removeItem(key); } catch (_) {}
      return null;
    }
    return obj.v == null ? null : obj.v;
  } catch (_) {
    // Self-heal: corrupted entry would otherwise return null forever.
    try { localStorage.removeItem(key); } catch (_) {}
    return null;
  }
}

// ── Credits Balance ────────────────────────────
async function getCredits(email) {
  return apiGet(`/credits/check?email=${encodeURIComponent(email)}`);
}

// ── Wompi Checkout ─────────────────────────────
async function openWompiCheckout(sku, email, redirectUrl) {
  try {
    // Forward Meta tracking signals so the backend CAPI call has the same
    // identity context as the browser Pixel — boosts Event Match Quality
    // from ~5 to ~8+ which Meta's algorithm explicitly rewards in 2026
    // (better attribution → cheaper CPA on Advantage+ Sales).
    const fbp = (typeof getFbp === 'function') ? getFbp() : '';
    const fbc = (typeof getFbc === 'function') ? getFbc() : '';
    const checkout = await apiPost('/payments/checkout', {
      sku, email, fbp, fbc, page_url: window.location.href.slice(0, 500),
    });

    // Fire Pixel InitiateCheckout with the matched event_id Meta will use
    // to dedupe against our server-side InitiateCheckout (already fired
    // by the backend in /payments/checkout). Prefix matches the backend's
    // `ic_${reference}` convention.
    if (typeof phTrack === 'function') {
      phTrack('InitiateCheckout', {
        value: checkout.amount_cents / 100,
        currency: checkout.currency,
        content_ids: [sku],
        content_type: 'product',
      }, 'ic_' + checkout.reference);
    }

    const widget = new WidgetCheckout({
      currency: checkout.currency,
      amountInCents: checkout.amount_cents,
      reference: checkout.reference,
      publicKey: checkout.public_key,
      signature: { integrity: checkout.integrity_hash },
      redirectUrl: redirectUrl || window.location.origin + '/precios',
      customerData: { email: email },
    });
    widget.open(function(result) {
      if (result.transaction && result.transaction.status === 'APPROVED') {
        // Fire Pixel Purchase with event_id = reference. The backend
        // CAPI Purchase event in /payments/webhook uses the same event_id
        // so Meta dedupes within its 48h window. If the user uses PSE
        // (which redirects to /gracias-* on phinodia.com instead of
        // returning here), this Pixel call doesn't fire — but the CAPI
        // webhook still does, so the conversion is captured.
        if (typeof phTrack === 'function') {
          phTrack('Purchase', {
            value: checkout.amount_cents / 100,
            currency: checkout.currency,
            content_ids: [sku],
            content_type: 'product',
          }, checkout.reference);
        }
        showToast('Pago aprobado. Tus creditos se acreditaran en segundos.', 'success');
        setTimeout(() => window.location.reload(), 3000);
      }
    });
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ── Option Picker ──────────────────────────────
function initOptionPicker(containerId, hiddenInputId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const buttons = container.querySelectorAll('.option-btn');
  const hiddenInput = document.getElementById(hiddenInputId);

  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      if (hiddenInput) hiddenInput.value = btn.dataset.value;
    });
  });

  // Select first by default
  if (buttons.length && !container.querySelector('.selected')) {
    buttons[0].click();
  }
}

// ── Mobile Nav Toggle ──────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('.nav-toggle');
  const links = document.querySelector('.nav-links');
  if (toggle && links) {
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', 'nav-links');
    links.id = links.id || 'nav-links';
    const setOpen = (open) => {
      links.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      setOpen(!links.classList.contains('open'));
    });
    links.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', () => setOpen(false));
    });
    // Tap anywhere outside the menu closes it (otherwise the user is trapped
    // having to reach back up to the corner hamburger).
    document.addEventListener('click', (e) => {
      if (!links.classList.contains('open')) return;
      if (toggle.contains(e.target) || links.contains(e.target)) return;
      setOpen(false);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') setOpen(false);
    });
  }

  // Mark active nav link
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  document.querySelectorAll('.nav-links a').forEach(a => {
    const href = a.getAttribute('href').replace(/\/$/, '') || '/';
    if (path === href || (href !== '/' && path.startsWith(href))) {
      a.classList.add('active');
    }
  });
});

// ── Form Helper ────────────────────────────────
function getFormData(formId) {
  const form = document.getElementById(formId);
  if (!form) return {};
  const data = {};
  form.querySelectorAll('input, textarea, select').forEach(el => {
    if (!el.name) return;
    if (el.type === 'file') return;
    if (el.type === 'checkbox') {
      data[el.name] = el.checked;
      return;
    }
    if (el.type === 'radio') {
      // Only take the value of the *checked* radio in the group; otherwise
      // the last-iterated radio overwrites the checked one.
      if (el.checked) data[el.name] = el.value;
      else if (!(el.name in data)) data[el.name] = '';
      return;
    }
    data[el.name] = el.value;
  });
  return data;
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (loading) {
    // Re-entrant guard: if we're already loading, don't capture the spinner
    // text as the "original" — that would freeze the button label as
    // "Procesando..." forever after a retry.
    if (!btn._isLoading) {
      btn._originalText = btn.textContent;
      btn._isLoading = true;
    }
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner"></div> Procesando...';
  } else {
    btn.disabled = false;
    btn.textContent = btn._originalText || 'Generar';
    btn._isLoading = false;
  }
}

// Reset a generation form back to its initial state so the user can retry
// without a hard reload after a failure or completed generation.
function resetGenerateForm(formId, resultId, submitBtnId) {
  const form = document.getElementById(formId);
  const result = document.getElementById(resultId);
  if (form) form.classList.remove('hidden');
  if (result) result.classList.add('hidden');
  if (submitBtnId) setLoading(submitBtnId, false);
}

// ── Format Price ───────────────────────────────
function formatCOP(cents) {
  return '$' + (cents / 100).toLocaleString('es-CO', { minimumFractionDigits: 0 });
}
