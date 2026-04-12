/* ═══════════════════════════════════════════════
   PhinodIA — API Client & UI Helpers
   ═══════════════════════════════════════════════ */

const API = window.location.origin + '/api/v1';

// ── Toast Notifications ────────────────────────
function initToasts() {
  if (!document.querySelector('.toast-container')) {
    const c = document.createElement('div');
    c.className = 'toast-container';
    document.body.appendChild(c);
  }
}

function showToast(message, type = 'info') {
  initToasts();
  const container = document.querySelector('.toast-container');
  const icons = { success: '\u2713', error: '\u2717', warning: '\u26A0', info: '\u2139' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${icons[type] || ''}</span><span>${message}</span>`;
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
  if (!file.type.startsWith('image/')) {
    showToast('Solo se permiten imagenes', 'error');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showToast('La imagen no puede superar 10MB', 'error');
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
    if (!res.ok) throw new Error('Error al subir la imagen');
    const data = await res.json();
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
async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Error en la solicitud');
  return data;
}

async function apiGet(path) {
  const res = await fetch(`${API}${path}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Error en la solicitud');
  return data;
}

// ── Generate Video ─────────────────────────────
async function generateVideo(formData) {
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
  return apiPost('/generate/image', {
    email: formData.email,
    image_url: formData.image_url,
    description: formData.description,
    aspect_ratio: formData.aspect_ratio,
    product_name: formData.product_name,
    product_category: formData.product_category || '',
    creative_direction: formData.creative_direction || '',
    data_consent: formData.data_consent,
  });
}

// ── Generate Landing Page ──────────────────────
async function generateLanding(formData) {
  return apiPost('/generate/landing', {
    email: formData.email,
    image_url: formData.image_url,
    description: formData.description,
    product_name: formData.product_name,
    product_category: formData.product_category || '',
    target_audience: formData.target_audience || '',
    style_preference: formData.style_preference || '',
    data_consent: formData.data_consent,
  });
}

// ── Job Status Polling ─────────────────────────
async function pollJobStatus(jobId, onUpdate, intervalMs = 5000) {
  let errorCount = 0;
  const maxErrors = 10;
  const poll = async () => {
    try {
      const data = await apiGet(`/jobs/status/${jobId}`);
      errorCount = 0;
      onUpdate(data);
      if (data.status === 'completed' || data.status === 'failed') return;
      setTimeout(poll, intervalMs);
    } catch (err) {
      errorCount++;
      if (errorCount >= maxErrors) {
        onUpdate({ status: 'error', error_message: 'Se perdio la conexion. Recarga la pagina e intenta de nuevo.' });
        return;
      }
      setTimeout(poll, intervalMs * 2);
    }
  };
  poll();
}

// ── Credits Balance ────────────────────────────
async function getCredits(email) {
  return apiGet(`/credits/check?email=${encodeURIComponent(email)}`);
}

// ── Wompi Checkout ─────────────────────────────
async function openWompiCheckout(sku, email, redirectUrl) {
  try {
    const checkout = await apiPost('/payments/checkout', { sku, email });
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
    toggle.addEventListener('click', () => links.classList.toggle('open'));
    links.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', () => links.classList.remove('open'));
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
    if (el.type === 'checkbox') data[el.name] = el.checked;
    else if (el.type === 'file') return;
    else data[el.name] = el.value;
  });
  return data;
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn._originalText = btn.textContent;
    btn.innerHTML = '<div class="spinner"></div> Procesando...';
  } else {
    btn.disabled = false;
    btn.textContent = btn._originalText || 'Generar';
  }
}

// ── Format Price ───────────────────────────────
function formatCOP(cents) {
  return '$' + (cents / 100).toLocaleString('es-CO', { minimumFractionDigits: 0 });
}
