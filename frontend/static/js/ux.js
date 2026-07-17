/* PhinodIA — UX avanzada: dark mode, selector de idioma (ES/EN), botón subir/bajar.
   Compartido por todas las páginas. Sin dependencias. */
(function () {
  'use strict';
  var root = document.documentElement;
  var LS = window.localStorage;

  /* ─────────────── TEMA (claro / oscuro) ─────────────── */
  function currentTheme() {
    var saved = null;
    try { saved = LS.getItem('ph-theme'); } catch (e) {}
    if (saved === 'dark' || saved === 'light') return saved;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  function applyTheme(t, animate) {
    if (animate) { root.classList.add('ph-theme-anim'); setTimeout(function () { root.classList.remove('ph-theme-anim'); }, 350); }
    root.setAttribute('data-theme', t);
    try { LS.setItem('ph-theme', t); } catch (e) {}
    var btn = document.getElementById('ph-theme-btn');
    if (btn) {
      var dark = t === 'dark';
      btn.textContent = dark ? '☀️' : '🌙';
      btn.setAttribute('aria-label', dark ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro');
      btn.setAttribute('title', dark ? 'Modo claro' : 'Modo oscuro');
    }
  }

  /* ─────────────── IDIOMA (ES / EN) ─────────────── */
  /* Traduce por texto exacto: se guarda el original ES en data-i18n para poder volver. */
  var DICT = {
    // Nav
    'Videos': 'Videos', 'Imágenes': 'Images', 'Landing Pages': 'Landing Pages',
    'Mis Generaciones': 'My Generations', 'Mis Créditos': 'My Credits', 'Referidos': 'Referrals',
    'Precios': 'Pricing',
    // Botones / CTAs comunes
    'Crear video': 'Create video', 'Crear imagen': 'Create image', 'Crear landing': 'Create landing',
    'Ver precios': 'View pricing', 'Ver planes': 'View plans', 'Quiero empezar': 'Get started',
    'Comprar': 'Buy', 'Generar': 'Generate', 'Crear mi video': 'Create my video',
    // Home
    'Inteligencia artificial para e-commerce': 'Artificial intelligence for e-commerce',
    'Contenido que vende, generado en minutos.': 'Content that sells, generated in minutes.',
    'Tres herramientas, un objetivo.': 'Three tools, one goal.',
    'Todo lo que necesitas para crear contenido de marketing que convierte': 'Everything you need to create marketing content that converts',
    'Videos UGC': 'UGC Videos', 'Imágenes de Producto': 'Product Images', 'Landing Pages ': 'Landing Pages ',
    'Cómo funciona.': 'How it works.',
    'De la foto de tu producto al contenido final en 3 pasos': 'From your product photo to the final content in 3 steps',
    'Sube tu producto': 'Upload your product', 'Describe tu visión': 'Describe your vision', 'Recibe tu contenido': 'Get your content',
    'Más de 40 tiendas colombianas ya confían en PhinodIA.': 'Over 40 Colombian stores already trust PhinodIA.',
    'Testimonios reales de dueños de tienda que ya crearon su contenido con nuestra IA.': 'Real testimonials from store owners who already created their content with our AI.',
    'Tecnología de vanguardia.': 'Cutting-edge technology.',
    'Hecho en Colombia': 'Made in Colombia', 'Pagos seguros con Wompi': 'Secure payments with Wompi',
    // Precios
    'Contenido profesional. Precios accesibles.': 'Professional content. Accessible prices.',
    'Hasta 80% más barato que la producción tradicional. Resultados en minutos, no semanas.': 'Up to 80% cheaper than traditional production. Results in minutes, not weeks.',
    'PRUEBA': 'TRIAL', 'PRO': 'PRO', 'STUDIO': 'STUDIO', 'Popular': 'Popular',
    'créditos': 'credits', 'por crédito': 'per credit',
    'Pagos procesados de forma segura por Wompi. Aceptamos tarjeta de crédito, PSE, Nequi y Bancolombia.': 'Payments securely processed by Wompi. We accept credit card, PSE, Nequi and Bancolombia.',
    'Sube la foto de tu producto y obtiene videos UGC con acento colombiano, imágenes profesionales y landing pages de alta conversión.': 'Upload your product photo and get UGC videos with a Colombian accent, professional images and high-converting landing pages.',
    // Footer
    'Privacidad': 'Privacy', 'Términos': 'Terms', 'Habeas Data': 'Habeas Data',
    'Todos los derechos reservados.': 'All rights reserved.'
  };
  var SEL = 'a, button, h1, h2, h3, h4, p, span, li, .btn, .card-link, .testi-role, .testi-name, .hero-badge, .section-header p, figcaption, blockquote';
  function translate(lang) {
    var toEN = lang === 'en';
    var nodes = document.querySelectorAll(SEL);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.children.length > 0) continue; // solo hojas de texto
      var es = el.getAttribute('data-i18n-es');
      if (es === null) {
        var txt = (el.textContent || '').trim();
        if (DICT[txt] === undefined || DICT[txt] === txt) continue;
        el.setAttribute('data-i18n-es', txt);
        es = txt;
      }
      // preservar espacios/indentación originales alrededor del texto
      var raw = el.textContent;
      var lead = raw.match(/^\s*/)[0], trail = raw.match(/\s*$/)[0];
      el.textContent = lead + (toEN ? (DICT[es] || es) : es) + trail;
    }
    root.setAttribute('lang', toEN ? 'en' : 'es');
    try { LS.setItem('ph-lang', lang); } catch (e) {}
    var esb = document.getElementById('ph-lang-es'), enb = document.getElementById('ph-lang-en');
    if (esb && enb) { esb.classList.toggle('active', !toEN); enb.classList.toggle('active', toEN); }
  }
  function currentLang() { try { return LS.getItem('ph-lang') === 'en' ? 'en' : 'es'; } catch (e) { return 'es'; } }

  /* ─────────────── construir controles + botón scroll ─────────────── */
  function build() {
    var navInner = document.querySelector('.nav-inner') || document.querySelector('nav');
    if (navInner && !document.getElementById('ph-ux-controls')) {
      var wrap = document.createElement('div');
      wrap.className = 'ph-ux-controls'; wrap.id = 'ph-ux-controls';
      var lang = document.createElement('div'); lang.className = 'ph-lang';
      lang.innerHTML = '<button id="ph-lang-es" type="button" aria-label="Español">ES</button><button id="ph-lang-en" type="button" aria-label="English">EN</button>';
      var theme = document.createElement('button');
      theme.className = 'ph-iconbtn'; theme.id = 'ph-theme-btn'; theme.type = 'button';
      wrap.appendChild(lang); wrap.appendChild(theme);
      // insertarlo dentro de nav-links si existe (para el layout), si no, al final del nav-inner
      var links = navInner.querySelector('.nav-links');
      if (links) links.appendChild(wrap); else navInner.appendChild(wrap);
      theme.addEventListener('click', function () { applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark', true); });
      document.getElementById('ph-lang-es').addEventListener('click', function () { translate('es'); });
      document.getElementById('ph-lang-en').addEventListener('click', function () { translate('en'); });
    }
    // botón flotante subir/bajar
    if (!document.getElementById('ph-scrolltop')) {
      var b = document.createElement('button');
      b.className = 'ph-scrolltop'; b.id = 'ph-scrolltop'; b.type = 'button';
      b.setAttribute('aria-label', 'Ir arriba'); b.textContent = '↑';
      document.body.appendChild(b);
      var atTop = true;
      function refresh() {
        var y = window.scrollY || document.documentElement.scrollTop;
        var docH = document.documentElement.scrollHeight - window.innerHeight;
        b.classList.toggle('show', y > 300 || (docH > 600 && y < docH - 300));
        // si estás cerca de arriba, el botón baja; si no, sube
        atTop = y < 200;
        b.textContent = atTop ? '↓' : '↑';
        b.setAttribute('aria-label', atTop ? 'Ir abajo' : 'Ir arriba');
      }
      b.addEventListener('click', function () {
        window.scrollTo({ top: atTop ? document.documentElement.scrollHeight : 0, behavior: 'smooth' });
      });
      window.addEventListener('scroll', refresh, { passive: true });
      refresh();
    }
    // Sartoria: sello de cera + tagline "a la medida" al inicio del footer
    var footer = document.querySelector('.footer');
    if (footer && !document.getElementById('ph-sartoria')) {
      var sar = document.createElement('div');
      sar.className = 'ph-sartoria'; sar.id = 'ph-sartoria';
      sar.innerHTML = '<div class="ph-sartoria-inner">' +
        '<span class="ph-seal" aria-hidden="true"><span>P</span></span>' +
        '<p class="tl">Hecho a la medida de tu tienda. <b>Una propuesta que no vas a querer rechazar.</b></p>' +
        '</div>';
      footer.insertBefore(sar, footer.firstChild);
    }
  }

  /* ─────────────── init ─────────────── */
  applyTheme(currentTheme(), false);
  function start() { build(); applyTheme(root.getAttribute('data-theme') || currentTheme(), false); if (currentLang() === 'en') translate('en'); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
