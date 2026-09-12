"""Sesiones e inicio de sesion sin contrasena.

POR QUE EXISTE ESTE MODULO
--------------------------
Hasta ahora la identidad del usuario era el correo que venia en el CUERPO de la
peticion. Eso significaba que cualquiera que supiera el correo de un cliente
podia (a) gastarle los creditos comprados, (b) listar y descargar todo lo que
ese cliente habia generado, y (c) consultarle el saldo. Un cliente con 114
creditos comprados estaba a un `curl` de distancia de que se los vaciaran.

COMO FUNCIONA
-------------
Inicio de sesion sin contrasena ("magic link" + codigo de 6 digitos):

  1. El usuario pide entrar con su correo.
  2. Se le envia un correo con un codigo de 6 digitos y un enlace directo.
  3. Al verificar, se le entrega una cookie de sesion firmada.

Todo va FIRMADO con HMAC-SHA256 y sin estado en base de datos: los 4 workers de
uvicorn validan la misma cookie sin compartir memoria ni consultar Postgres, y
no hace falta migracion de esquema.

  cookie de sesion : s1.<correo_b64>.<caducidad>.<firma>
  enlace magico    : m1.<correo_b64>.<caducidad>.<firma>
  codigo de 6 cifras: HMAC(secreto, "codigo|correo|ventana") -> 6 digitos

Cada tipo de token lleva su propia etiqueta de proposito dentro de la firma, asi
que una cookie de sesion no sirve como enlace magico ni al reves.

El codigo se deriva de una ventana temporal de 10 minutos y se aceptan la actual
y la anterior (10-20 min de validez real). No se guarda en ninguna parte, de ahi
que no sea de un solo uso; lo que hace inviable la fuerza bruta es el limitador
de intentos de abajo, no el secreto del codigo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque

from fastapi import Request, Response

from app.config import get_settings

logger = logging.getLogger(__name__)

COOKIE = "ph_sesion"
DIAS_SESION = 30
DURACION_SESION = DIAS_SESION * 24 * 3600
DURACION_ENLACE = 30 * 60          # el enlace del correo vale media hora
VENTANA_CODIGO = 10 * 60           # ventana del codigo de 6 digitos


# ── Secreto ────────────────────────────────────────────────────────────────
def _secreto() -> bytes:
    """Clave de firma. Usa SESSION_SECRET si esta definida; si no, deriva una
    del secreto de integridad de Wompi (ya obligatorio y de 16+ caracteres).

    La derivacion NO usa el secreto de Wompi tal cual: pasa por HKDF-como-SHA256
    con una etiqueta propia, para que filtrar una firma de sesion no revele nada
    reutilizable contra la pasarela de pagos."""
    s = get_settings()
    propio = (getattr(s, "session_secret", "") or "").strip()
    if propio:
        return propio.encode()
    return hashlib.sha256(b"phinodia/sesion/v1|" + s.wompi_integrity_secret.encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _deb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def normalizar(correo: str) -> str:
    return (correo or "").strip().lower()


# ── Tokens firmados ────────────────────────────────────────────────────────
def _firmar(proposito: str, correo: str, caduca: int) -> str:
    cuerpo = f"{proposito}|{correo}|{caduca}"
    firma = hmac.new(_secreto(), cuerpo.encode(), hashlib.sha256).hexdigest()
    return f"{proposito}.{_b64(correo.encode())}.{caduca}.{firma}"


def _abrir(proposito: str, token: str) -> str | None:
    """Devuelve el correo si el token es autentico y no ha caducado."""
    if not token or not isinstance(token, str) or token.count(".") != 3:
        return None
    p, correo_b64, caduca_s, firma = token.split(".", 3)
    if not hmac.compare_digest(p, proposito):
        return None
    try:
        correo = _deb64(correo_b64).decode()
        caduca = int(caduca_s)
    except Exception:
        return None
    esperada = hmac.new(_secreto(), f"{proposito}|{correo}|{caduca}".encode(),
                        hashlib.sha256).hexdigest()
    # Comparacion en tiempo constante: no filtra por cuantos caracteres acerto.
    if not hmac.compare_digest(firma, esperada):
        return None
    if time.time() > caduca:
        return None
    return correo


def emitir_sesion(correo: str) -> str:
    return _firmar("s1", normalizar(correo), int(time.time()) + DURACION_SESION)


def leer_sesion(token: str) -> str | None:
    return _abrir("s1", token)


def emitir_enlace(correo: str) -> str:
    return _firmar("m1", normalizar(correo), int(time.time()) + DURACION_ENLACE)


def leer_enlace(token: str) -> str | None:
    return _abrir("m1", token)


# ── Codigo de 6 digitos ────────────────────────────────────────────────────
def _codigo(correo: str, ventana: int) -> str:
    d = hmac.new(_secreto(), f"codigo|{correo}|{ventana}".encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(d[:8], 'big') % 1_000_000:06d}"


def codigo_actual(correo: str) -> str:
    return _codigo(normalizar(correo), int(time.time()) // VENTANA_CODIGO)


def codigo_valido(correo: str, codigo: str) -> bool:
    """Acepta la ventana actual y la anterior (10-20 min de validez)."""
    codigo = (codigo or "").strip().replace(" ", "").replace("-", "")
    if len(codigo) != 6 or not codigo.isdigit():
        return False
    correo = normalizar(correo)
    ahora = int(time.time()) // VENTANA_CODIGO
    return any(hmac.compare_digest(_codigo(correo, v), codigo) for v in (ahora, ahora - 1))


# ── Cookie ─────────────────────────────────────────────────────────────────
def poner_cookie(respuesta: Response, correo: str) -> None:
    respuesta.set_cookie(
        COOKIE, emitir_sesion(correo),
        max_age=DURACION_SESION,
        httponly=True,      # JS no puede leerla -> un XSS no se lleva la sesion
        secure=True,        # solo por https
        samesite="lax",     # no viaja en peticiones de otro sitio -> corta CSRF
        path="/",
    )


def quitar_cookie(respuesta: Response) -> None:
    respuesta.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="lax")


def correo_de_peticion(peticion: Request) -> str | None:
    return leer_sesion(peticion.cookies.get(COOKIE, ""))


# ── Limitador de intentos (en memoria, por worker) ─────────────────────────
# Sin Redis: cada worker lleva su propia cuenta. Con 4 workers el techo real es
# 4x el nominal, lo que sigue dejando la fuerza bruta de un codigo de 6 cifras
# fuera de alcance (32 intentos por cuarto de hora contra 1.000.000 de combinaciones).
_intentos: dict[str, deque] = defaultdict(deque)


def limitar(clave: str, maximo: int, ventana_s: int) -> bool:
    """True si la accion cabe dentro del limite (y la cuenta). False si se pasa."""
    ahora = time.time()
    q = _intentos[clave]
    while q and q[0] < ahora - ventana_s:
        q.popleft()
    if len(q) >= maximo:
        return False
    q.append(ahora)
    # Poda: evita que el diccionario crezca sin fin con claves ya vencidas.
    if len(_intentos) > 20_000:
        for k in [k for k, v in list(_intentos.items()) if not v or v[-1] < ahora - 3600][:10_000]:
            _intentos.pop(k, None)
    return True


def ip_de(peticion: Request) -> str:
    """IP real del cliente detras de Traefik. Se toma el PRIMER salto de
    X-Forwarded-For, que es el unico que el proxy de confianza garantiza."""
    xff = peticion.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:45]
    return (peticion.client.host if peticion.client else "?")[:45]


# ── Dependencia de FastAPI ─────────────────────────────────────────────────
async def exigir_sesion(peticion: Request) -> str:
    """Correo del usuario autenticado, o 401.

    Es la unica fuente de identidad valida para gastar creditos o leer
    generaciones. El correo que venga en el cuerpo o en la query se ignora:
    ahi estaba precisamente el agujero."""
    from fastapi import HTTPException
    correo = correo_de_peticion(peticion)
    if not correo:
        raise HTTPException(
            status_code=401,
            detail="Inicia sesion con tu correo para continuar.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return correo
