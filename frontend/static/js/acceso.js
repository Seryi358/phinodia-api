/* ═══════════════════════════════════════════════
   PhinodIA — Acceso (inicio de sesion sin contrasena)

   El backend ya no acepta el correo del cuerpo como prueba de identidad:
   exige una cookie de sesion. Este modulo es la puerta.

   - Al cargar, pregunta quien es el usuario.
   - Si hay sesion: rellena los campos de correo, los bloquea y pone
     "correo · Salir" en la barra.
   - Si no la hay y la pagina necesita identidad: abre el panel de acceso.
   - Cualquier 401 de la API abre tambien el panel, sin perder lo que
     el usuario estuviera haciendo.
   ═══════════════════════════════════════════════ */
(function () {
  'use strict';

  var RUTAS_PROTEGIDAS = ['/videos', '/imagenes', '/creditos', '/mis-generaciones',
                          '/referidos', '/landing-pages'];
  var estado = { autenticado: false, correo: null, listo: false };

  // ── Estilos ──────────────────────────────────────────────────────────────
  function inyectarEstilos() {
    if (document.getElementById('ph-acceso-css')) return;
    var s = document.createElement('style');
    s.id = 'ph-acceso-css';
    s.textContent = [
      '.ph-acc-fondo{position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,.55);',
      '  backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;padding:16px}',
      '.ph-acc-caja{background:var(--surface,#fff);color:var(--text,#111);border:1px solid var(--border,#ddd);',
      '  border-radius:var(--radius,18px);box-shadow:var(--shadow,0 20px 60px rgba(0,0,0,.25));',
      '  width:100%;max-width:420px;padding:32px 28px;text-align:center}',
      '.ph-acc-caja h2{margin:0 0 6px;font-size:22px;font-weight:600;line-height:1.25}',
      '.ph-acc-caja p{margin:0 0 20px;font-size:14px;color:var(--text-secondary,#666);line-height:1.5}',
      '.ph-acc-caja .form-input{width:100%;margin-bottom:12px}',
      '.ph-acc-etiqueta{display:block;text-align:left;font-size:13px;font-weight:500;',
      '  color:var(--text-secondary,#666);margin:0 0 6px}',
      '.ph-acc-caja .btn{width:100%}',
      '.ph-acc-codigo{text-align:center;letter-spacing:8px;font-size:22px;font-weight:600}',
      '.ph-acc-pie{margin:16px 0 0;font-size:13px}',
      '.ph-acc-pie button{background:none;border:0;color:var(--accent,#06c);cursor:pointer;',
      '  font:inherit;text-decoration:underline;padding:0}',
      '.ph-acc-error{color:var(--error,#c00);font-size:13px;margin:0 0 12px;min-height:1em}',
      '.ph-acc-chip{display:inline-flex;align-items:center;gap:8px;font-size:13px;',
      '  color:var(--text-secondary,#666);white-space:nowrap}',
      '.ph-acc-chip button{background:none;border:0;color:var(--accent,#06c);cursor:pointer;',
      '  font:inherit;text-decoration:underline;padding:0}',
      '@media (max-width:700px){.ph-acc-chip{display:none}}'
    ].join('');
    document.head.appendChild(s);
  }

  // ── Llamadas ─────────────────────────────────────────────────────────────
  function pedir(ruta, cuerpo) {
    var op = { method: cuerpo ? 'POST' : 'GET', credentials: 'same-origin' };
    if (cuerpo) {
      op.headers = { 'Content-Type': 'application/json' };
      op.body = JSON.stringify(cuerpo);
    }
    return fetch('/api/v1/acceso' + ruta, op).then(function (r) {
      return r.json().catch(function () { return {}; })
        .then(function (d) { return { ok: r.ok, estado: r.status, datos: d }; });
    });
  }

  function consultarSesion() {
    return pedir('/sesion').then(function (r) {
      estado.autenticado = !!(r.datos && r.datos.autenticado);
      estado.correo = (r.datos && r.datos.correo) || null;
      estado.listo = true;
      return estado;
    }).catch(function () { estado.listo = true; return estado; });
  }

  // ── Panel ────────────────────────────────────────────────────────────────
  var panel = null;
  var focoPrevio = null;

  function cerrarPanel() {
    if (panel) { panel.remove(); panel = null; }
    document.documentElement.style.overflow = '';
    document.removeEventListener('keydown', alPulsarTecla, true);
    // Devolver el foco a donde estaba: sin esto el lector de pantalla se queda
    // al principio del documento y el usuario pierde el sitio (WCAG 2.4.3).
    if (focoPrevio && focoPrevio.focus) { try { focoPrevio.focus(); } catch (e) {} }
    focoPrevio = null;
  }

  // Escape cierra y Tab no se escapa del panel. Un dialogo del que no se puede
  // salir con el teclado es una trampa de teclado (WCAG 2.1.2); si la pagina
  // necesita sesion, la siguiente accion devolvera 401 y lo reabrira.
  function alPulsarTecla(e) {
    if (!panel) return;
    if (e.key === 'Escape') { e.preventDefault(); cerrarPanel(); return; }
    if (e.key !== 'Tab') return;
    var foco = panel.querySelectorAll('input:not([disabled]), button:not([disabled])');
    var visibles = [];
    for (var i = 0; i < foco.length; i++) {
      if (foco[i].offsetParent !== null) visibles.push(foco[i]);
    }
    if (!visibles.length) return;
    var primero = visibles[0], ultimo = visibles[visibles.length - 1];
    if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus(); }
    else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus(); }
  }

  function abrirPanel(opciones) {
    opciones = opciones || {};
    if (panel) return;
    inyectarEstilos();
    focoPrevio = document.activeElement;
    document.documentElement.style.overflow = 'hidden';
    document.addEventListener('keydown', alPulsarTecla, true);

    panel = document.createElement('div');
    panel.className = 'ph-acc-fondo';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-labelledby', 'ph-acc-titulo');
    panel.innerHTML =
      '<div class="ph-acc-caja">' +
        '<h2 id="ph-acc-titulo">Entra con tu correo</h2>' +
        '<p>Te enviamos un codigo de 6 digitos. Sin contrasenas.</p>' +
        '<p class="ph-acc-error" role="alert" id="ph-acc-error"></p>' +
        '<div id="ph-acc-paso1">' +
          '<label class="ph-acc-etiqueta" for="ph-acc-correo">Tu correo electronico</label>' +
          '<input type="email" class="form-input" id="ph-acc-correo" autocomplete="email" ' +
                'inputmode="email" placeholder="tucorreo@ejemplo.com" ' +
                'aria-label="Tu correo electronico">' +
          '<button class="btn btn-primary" id="ph-acc-enviar">Enviar codigo</button>' +
        '</div>' +
        '<div id="ph-acc-paso2" hidden>' +
          '<label class="ph-acc-etiqueta" for="ph-acc-codigo">Codigo de 6 digitos</label>' +
          '<input type="text" class="form-input ph-acc-codigo" id="ph-acc-codigo" ' +
                'inputmode="numeric" autocomplete="one-time-code" maxlength="6" ' +
                'placeholder="000000" aria-label="Codigo de 6 digitos que te enviamos por correo">' +
          '<button class="btn btn-primary" id="ph-acc-entrar">Entrar</button>' +
          '<p class="ph-acc-pie"><button type="button" id="ph-acc-otro">Usar otro correo</button></p>' +
        '</div>' +
      '</div>';
    document.body.appendChild(panel);

    var $ = function (id) { return panel.querySelector('#' + id); };
    var error = $('ph-acc-error');
    var correoIn = $('ph-acc-correo');
    var codigoIn = $('ph-acc-codigo');

    // Si ya habia un correo escrito en la pagina o guardado, se precarga:
    // menos friccion para quien ya compro.
    var previo = '';
    try { previo = (JSON.parse(localStorage.getItem('phinodia_email') || '{}').v) || ''; } catch (e) {}
    if (!previo) {
      var campo = document.querySelector('input[type="email"]');
      if (campo && campo.value) previo = campo.value;
    }
    if (previo) correoIn.value = previo;
    setTimeout(function () { (previo ? codigoIn : correoIn).focus(); }, 60);

    function fallo(m) { error.textContent = m || ''; }

    function enviar() {
      var c = (correoIn.value || '').trim().toLowerCase();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(c)) { return fallo('Escribe un correo valido.'); }
      fallo('');
      var b = $('ph-acc-enviar'); b.disabled = true; b.textContent = 'Enviando...';
      pedir('/solicitar', { correo: c }).then(function (r) {
        b.disabled = false; b.textContent = 'Enviar codigo';
        if (!r.ok) { return fallo(r.datos.detail || 'No se pudo enviar. Intenta de nuevo.'); }
        $('ph-acc-paso1').hidden = true;
        $('ph-acc-paso2').hidden = false;
        panel.querySelector('#ph-acc-titulo').textContent = 'Revisa tu correo';
        panel.querySelector('.ph-acc-caja p').textContent =
          'Enviamos un codigo a ' + c + '. Caduca en 10 minutos.';
        setTimeout(function () { codigoIn.focus(); }, 60);
      });
    }

    function entrar() {
      var c = (correoIn.value || '').trim().toLowerCase();
      var k = (codigoIn.value || '').replace(/\D/g, '');
      if (k.length !== 6) { return fallo('El codigo tiene 6 digitos.'); }
      fallo('');
      var b = $('ph-acc-entrar'); b.disabled = true; b.textContent = 'Entrando...';
      pedir('/verificar', { correo: c, codigo: k }).then(function (r) {
        b.disabled = false; b.textContent = 'Entrar';
        if (!r.ok) { return fallo(r.datos.detail || 'Codigo incorrecto.'); }
        estado.autenticado = true; estado.correo = r.datos.correo || c;
        try { localStorage.setItem('phinodia_email',
              JSON.stringify({ v: estado.correo, e: Date.now() + 7 * 864e5 })); } catch (e) {}
        cerrarPanel();
        aplicarSesion();
        if (typeof opciones.alEntrar === 'function') { opciones.alEntrar(estado.correo); }
        else if (opciones.recargar !== false) { window.location.reload(); }
        else if (window.showToast) {
          // Sin recarga: el usuario conserva lo que estuviera escribiendo.
          window.showToast('Sesion iniciada. Vuelve a pulsar el boton.', 'success');
        }
      });
    }

    $('ph-acc-enviar').addEventListener('click', enviar);
    $('ph-acc-entrar').addEventListener('click', entrar);
    $('ph-acc-otro').addEventListener('click', function () {
      $('ph-acc-paso2').hidden = true; $('ph-acc-paso1').hidden = false;
      panel.querySelector('#ph-acc-titulo').textContent = 'Entra con tu correo';
      correoIn.focus();
    });
    correoIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') enviar(); });
    codigoIn.addEventListener('keydown', function (e) { if (e.key === 'Enter') entrar(); });
    codigoIn.addEventListener('input', function () {
      codigoIn.value = codigoIn.value.replace(/\D/g, '').slice(0, 6);
      if (codigoIn.value.length === 6) entrar();
    });
  }

  // ── Reflejar la sesion en la pagina ──────────────────────────────────────
  function aplicarSesion() {
    if (!estado.autenticado || !estado.correo) return;
    // Los campos de correo dejan de ser una eleccion: son QUIEN ERES.
    var campos = document.querySelectorAll('input[type="email"]');
    for (var i = 0; i < campos.length; i++) {
      campos[i].value = estado.correo;
      campos[i].readOnly = true;
      campos[i].setAttribute('aria-readonly', 'true');
      campos[i].style.opacity = '.75';
    }
    // Chip en la barra de navegacion.
    var nav = document.querySelector('.nav-links');
    if (nav && !document.getElementById('ph-acc-chip')) {
      inyectarEstilos();
      var chip = document.createElement('span');
      chip.id = 'ph-acc-chip';
      chip.className = 'ph-acc-chip';
      chip.appendChild(document.createTextNode(estado.correo));
      var salir = document.createElement('button');
      salir.type = 'button';
      salir.textContent = 'Salir';
      salir.addEventListener('click', function () {
        pedir('/salir', {}).then(function () {
          try { localStorage.removeItem('phinodia_email'); } catch (e) {}
          window.location.href = '/';
        });
      });
      chip.appendChild(salir);
      nav.appendChild(chip);
    }
  }

  // ── Arranque ─────────────────────────────────────────────────────────────
  function arrancar() {
    var ruta = window.location.pathname.replace(/\/$/, '') || '/';
    var necesita = RUTAS_PROTEGIDAS.indexOf(ruta) !== -1;
    consultarSesion().then(function () {
      if (estado.autenticado) { aplicarSesion(); return; }
      if (necesita) abrirPanel({ recargar: true });
    });
    // Aviso al volver del enlace magico.
    if (/[?&]acceso=caducado/.test(window.location.search) && window.showToast) {
      window.showToast('Ese enlace ya caduco. Pide un codigo nuevo.', 'warning');
    }
  }

  window.PhAcceso = {
    estado: estado,
    consultar: consultarSesion,
    abrir: abrirPanel,
    aplicar: aplicarSesion,
    correo: function () { return estado.correo; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', arrancar);
  } else {
    arrancar();
  }
})();
