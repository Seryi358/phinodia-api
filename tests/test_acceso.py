"""Pruebas del control de acceso.

Estas pruebas van por HTTP a proposito. Las que ya existian eran unitarias y por
eso NO se rompieron al meter la autenticacion: un guardian que no se prueba a
nivel de peticion es un guardian del que no sabes si salta.

Cada prueba de aqui empieza por comprobar que la puerta esta CERRADA, no solo
que se abre con la llave correcta.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import sesion

VICTIMA = "melissabravo1987@gmail.com"
ATACANTE = "atacante@example.com"

PROTEGIDOS = [
    ("GET", "/api/v1/credits/check", None),
    ("GET", "/api/v1/jobs/by-email", None),
    ("GET", "/api/v1/referrals/code", None),
    ("GET", "/api/v1/referrals/stats", None),
    ("POST", "/api/v1/generate/video", {
        "email": VICTIMA, "product_name": "Crema", "description": "Una crema",
        "image_url": "https://app.phinodia.com/uploads/a.jpg", "duration": 10,
        "data_consent": True}),
    ("POST", "/api/v1/generate/image", {
        "email": VICTIMA, "product_name": "Crema", "description": "Una crema",
        "image_url": "https://app.phinodia.com/uploads/a.jpg", "data_consent": True}),
    # Subidas: sin sesion, cualquiera escribia 10 MB por peticion en el disco
    # del servidor. El VPS llego al 98% y la app se cayo.
    ("POST", "/api/v1/upload/from-url", {"url": "https://example.com/a.jpg"}),
]


@pytest.fixture
def cliente():
    # base_url https a proposito: las cookies de sesion son Secure y un cliente
    # sobre http las descarta en silencio, con lo que la prueba de cierre de
    # sesion pasaria por un fallo del producto cuando es del arnes.
    with TestClient(app, base_url="https://app.phinodia.com") as c:
        yield c


@pytest.fixture(autouse=True)
def _sin_limite():
    """Cada prueba parte con el limitador a cero: si no, el orden de ejecucion
    haria fallar pruebas que no tienen nada que ver."""
    sesion._intentos.clear()
    yield
    sesion._intentos.clear()


# ── La puerta esta cerrada ─────────────────────────────────────────────────
@pytest.mark.parametrize("metodo,ruta,cuerpo", PROTEGIDOS)
def test_sin_sesion_devuelve_401(cliente, metodo, ruta, cuerpo):
    r = cliente.request(metodo, ruta, json=cuerpo)
    assert r.status_code == 401, f"{metodo} {ruta} quedo abierto: {r.status_code}"


@pytest.mark.parametrize("metodo,ruta,cuerpo", PROTEGIDOS)
def test_el_correo_del_cuerpo_o_la_query_no_abre(cliente, metodo, ruta, cuerpo):
    """El agujero original: bastaba con nombrar a la victima."""
    r = cliente.request(metodo, f"{ruta}?email={VICTIMA}", json=cuerpo)
    assert r.status_code == 401


def test_cookie_manipulada_no_vale(cliente):
    correo_b64 = sesion._b64(VICTIMA.encode())
    cliente.cookies.set(sesion.COOKIE, f"s1.{correo_b64}.{int(time.time())+9999}.00deadbeef")
    assert cliente.get("/api/v1/credits/check").status_code == 401


def test_cookie_caducada_no_vale(cliente):
    caducada = sesion._firmar("s1", VICTIMA, int(time.time()) - 10)
    cliente.cookies.set(sesion.COOKIE, caducada)
    assert cliente.get("/api/v1/credits/check").status_code == 401


def test_un_enlace_magico_no_sirve_de_cookie(cliente):
    """Separacion de propositos: los dos van firmados con la misma clave, asi
    que sin la etiqueta el enlace del correo seria una sesion de 30 dias."""
    cliente.cookies.set(sesion.COOKIE, sesion.emitir_enlace(VICTIMA))
    assert cliente.get("/api/v1/credits/check").status_code == 401


def test_una_cookie_no_sirve_de_enlace_magico(cliente):
    r = cliente.get(f"/api/v1/acceso/entrar?t={sesion.emitir_sesion(VICTIMA)}",
                    follow_redirects=False)
    assert r.status_code == 303 and "caducado" in r.headers["location"]


# ── La llave correcta abre, y abre SOLO lo tuyo ────────────────────────────
def test_con_sesion_devuelve_el_saldo_del_dueno_de_la_sesion(cliente):
    cliente.cookies.set(sesion.COOKIE, sesion.emitir_sesion(ATACANTE))
    with patch("app.services.credits.CreditService.get_balance",
               new=AsyncMock(return_value=7)) as saldo:
        # Se pide con el correo de la VICTIMA en la query, a proposito.
        r = cliente.get(f"/api/v1/credits/check?email={VICTIMA}")
    assert r.status_code == 200 and r.json()["credits"] == 7
    # Lo que importa: se consulto el saldo del ATACANTE, no el de la victima.
    assert saldo.await_args.args[0] == ATACANTE


def test_by_email_lista_solo_lo_del_dueno_de_la_sesion(cliente):
    cliente.cookies.set(sesion.COOKIE, sesion.emitir_sesion(ATACANTE))
    with patch("app.routers.jobs.db.select_one", new=AsyncMock(return_value=None)) as uno:
        r = cliente.get(f"/api/v1/jobs/by-email?email={VICTIMA}")
    assert r.status_code == 200 and r.json() == []
    assert uno.await_args.args[1]["email"] == f"eq.{ATACANTE}"


# ── Codigo de 6 digitos ────────────────────────────────────────────────────
def test_el_codigo_correcto_entrega_cookie(cliente):
    codigo = sesion.codigo_actual(VICTIMA)
    r = cliente.post("/api/v1/acceso/verificar", json={"correo": VICTIMA, "codigo": codigo})
    assert r.status_code == 200
    assert sesion.leer_sesion(r.cookies[sesion.COOKIE]) == VICTIMA


def test_un_codigo_de_otro_correo_no_entra(cliente):
    r = cliente.post("/api/v1/acceso/verificar",
                     json={"correo": VICTIMA, "codigo": sesion.codigo_actual(ATACANTE)})
    assert r.status_code == 401


def test_codigo_incorrecto_no_entra(cliente):
    r = cliente.post("/api/v1/acceso/verificar", json={"correo": VICTIMA, "codigo": "000000"})
    assert r.status_code in (401, 429)


def test_la_cookie_es_httponly_y_secure(cliente):
    r = cliente.post("/api/v1/acceso/verificar",
                     json={"correo": VICTIMA, "codigo": sesion.codigo_actual(VICTIMA)})
    cab = r.headers["set-cookie"].lower()
    assert "httponly" in cab      # un XSS no puede leerla
    assert "secure" in cab        # no viaja por http
    assert "samesite=lax" in cab  # no viaja desde otro sitio -> corta CSRF


# ── Limitador ──────────────────────────────────────────────────────────────
def test_la_fuerza_bruta_del_codigo_se_corta(cliente):
    vistos = [cliente.post("/api/v1/acceso/verificar",
                           json={"correo": VICTIMA, "codigo": "123456"}).status_code
              for _ in range(12)]
    assert 429 in vistos, "se pudieron probar 12 codigos seguidos sin freno"
    assert vistos.index(429) <= 8


def test_no_se_puede_bombardear_a_un_cliente_con_correos(cliente):
    with patch("app.routers.acceso._enviar_codigo"):
        vistos = [cliente.post("/api/v1/acceso/solicitar",
                               json={"correo": VICTIMA}).status_code for _ in range(9)]
    assert 429 in vistos


def test_solicitar_no_revela_si_la_cuenta_existe(cliente):
    """Misma respuesta para cliente y para desconocido: si no, es un oraculo
    para saber quien compra en PhinodIA."""
    with patch("app.routers.acceso._enviar_codigo"):
        a = cliente.post("/api/v1/acceso/solicitar", json={"correo": VICTIMA})
        sesion._intentos.clear()
        b = cliente.post("/api/v1/acceso/solicitar", json={"correo": "no-existe-jamas@example.com"})
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


# ── Sesion y cierre ────────────────────────────────────────────────────────
def test_sesion_dice_quien_soy_y_no_se_cachea(cliente):
    assert cliente.get("/api/v1/acceso/sesion").json() == {"autenticado": False, "correo": None}
    cliente.cookies.set(sesion.COOKIE, sesion.emitir_sesion(VICTIMA))
    r = cliente.get("/api/v1/acceso/sesion")
    assert r.json() == {"autenticado": True, "correo": VICTIMA}
    assert "no-store" in r.headers.get("cache-control", "")


def test_salir_borra_la_cookie(cliente):
    # Se ENTRA por la puerta de verdad (no inyectando la cookie en el tarro del
    # cliente): asi la cookie lleva el mismo dominio que la de borrado y la
    # prueba mide lo que hara el navegador, no como indexa httpx su tarro.
    cliente.post("/api/v1/acceso/verificar",
                 json={"correo": VICTIMA, "codigo": sesion.codigo_actual(VICTIMA)})
    assert cliente.get("/api/v1/acceso/sesion").json()["autenticado"] is True
    r = cliente.post("/api/v1/acceso/salir")
    assert r.status_code == 200
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert cliente.get("/api/v1/acceso/sesion").json()["autenticado"] is False


def test_el_enlace_del_correo_entra(cliente):
    r = cliente.get(f"/api/v1/acceso/entrar?t={sesion.emitir_enlace(VICTIMA)}",
                    follow_redirects=False)
    assert r.status_code == 303
    assert sesion.leer_sesion(r.cookies[sesion.COOKIE]) == VICTIMA


def test_subir_fichero_exige_sesion(cliente):
    r = cliente.post("/api/v1/upload/image",
                     files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")})
    assert r.status_code == 401


@pytest.mark.parametrize("cuerpo", [
    {},                                   # sin nada
    {"email": "no-es-un-correo"},         # correo invalido
    {"email": None},                      # correo nulo
])
def test_sin_sesion_es_401_y_no_422(cliente, cuerpo):
    """Un cliente sin sesion debe leer \"inicia sesion\", no una queja sobre un
    campo que ya no decide nada."""
    r = cliente.post("/api/v1/generate/video", json=cuerpo)
    assert r.status_code == 401, f"devolvio {r.status_code}: {r.text[:120]}"
