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
    // botón flotante de WhatsApp
    if (!document.getElementById('ph-wa')) {
      var wa = document.createElement('a');
      wa.className = 'ph-wa'; wa.id = 'ph-wa'; wa.target = '_blank'; wa.rel = 'noopener';
      wa.href = 'https://wa.me/573222864680?text=' + encodeURIComponent('Hola, quiero crear contenido con PhinodIA 👋');
      wa.setAttribute('aria-label', 'Escríbenos por WhatsApp');
      wa.setAttribute('title', 'Escríbenos por WhatsApp');
      wa.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.149-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>';
      document.body.appendChild(wa);
    }
  }

  /* ─────────────── init ─────────────── */
  applyTheme(currentTheme(), false);
  function start() { build(); applyTheme(root.getAttribute('data-theme') || currentTheme(), false); if (currentLang() === 'en') translate('en'); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
