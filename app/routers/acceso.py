"""Inicio de sesion sin contrasena.

    POST /api/v1/acceso/solicitar   {correo}            -> manda codigo + enlace
    POST /api/v1/acceso/verificar   {correo, codigo}    -> entrega la cookie
    GET  /api/v1/acceso/entrar?t=                       -> enlace del correo
    GET  /api/v1/acceso/sesion                          -> quien soy
    POST /api/v1/acceso/salir                           -> cierra la sesion

Decisiones que importan:

* `/solicitar` responde SIEMPRE 200 y con el mismo texto exista o no la cuenta.
  Si distinguiera, seria un oraculo para saber quien es cliente de PhinodIA
  (enumeracion de usuarios, OWASP A07).
* El envio del correo va en un hilo aparte y su resultado no cambia la
  respuesta: el tiempo de respuesta tampoco debe delatar si la cuenta existe.
* `/verificar` limita a 8 intentos por correo cada 15 minutos. Es lo unico que
  hace inviable adivinar un codigo de 6 cifras.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from app.config import get_settings
from app.services import sesion as ses

logger = logging.getLogger(__name__)
router = APIRouter()

_MISMO_TEXTO = ("Si ese correo tiene cuenta, te hemos enviado un codigo. "
                "Revisa tu bandeja (y la carpeta de spam).")


class SolicitudAcceso(BaseModel):
    correo: EmailStr


class VerificacionAcceso(BaseModel):
    correo: EmailStr
    codigo: str = Field(min_length=4, max_length=12)


def _enviar_codigo(correo: str, codigo: str, enlace: str) -> None:
    """Envio sincrono, pensado para correr en un hilo. Nunca propaga."""
    try:
        from app.services.gmail import GmailSender, build_acceso_email
        s = get_settings()
        asunto, cuerpo = build_acceso_email(codigo, enlace)
        GmailSender(
            client_id=s.gmail_client_id, client_secret=s.gmail_client_secret,
            refresh_token=s.gmail_refresh_token, sender_email=s.gmail_sender_email,
        ).send_email(to=correo, subject=asunto, html_body=cuerpo)
    except Exception as e:
        # No se registra el correo: Habeas Data (Ley 1581 de 2012).
        logger.warning("No se pudo enviar el codigo de acceso: %s", type(e).__name__)


@router.post("/solicitar")
async def solicitar(req: SolicitudAcceso, peticion: Request):
    correo = ses.normalizar(req.correo)
    ip = ses.ip_de(peticion)

    # Dos limites: uno por correo (que no se pueda bombardear a un cliente) y
    # otro por IP (que una sola maquina no barra la lista de correos).
    if not ses.limitar(f"sol:{correo}", 5, 3600) or not ses.limitar(f"solip:{ip}", 20, 3600):
        return JSONResponse(
            {"detail": "Demasiadas solicitudes. Espera unos minutos e intentalo de nuevo."},
            status_code=429, headers={"Retry-After": "600"},
        )

    codigo = ses.codigo_actual(correo)
    base = (get_settings().api_base_url or "https://app.phinodia.com").rstrip("/")
    enlace = f"{base}/api/v1/acceso/entrar?t={ses.emitir_enlace(correo)}"
    # A un hilo: el envio tarda ~1s y no debe bloquear ni delatar por tiempo.
    asyncio.create_task(asyncio.to_thread(_enviar_codigo, correo, codigo, enlace))
    return {"ok": True, "mensaje": _MISMO_TEXTO}


@router.post("/verificar")
async def verificar(req: VerificacionAcceso, peticion: Request, respuesta: Response):
    correo = ses.normalizar(req.correo)
    ip = ses.ip_de(peticion)

    if not ses.limitar(f"ver:{correo}", 8, 900) or not ses.limitar(f"verip:{ip}", 40, 900):
        return JSONResponse(
            {"detail": "Demasiados intentos. Espera 15 minutos y pide un codigo nuevo."},
            status_code=429, headers={"Retry-After": "900"},
        )

    if not ses.codigo_valido(correo, req.codigo):
        return JSONResponse({"detail": "Codigo incorrecto o caducado."}, status_code=401)

    r = JSONResponse({"ok": True, "correo": correo})
    ses.poner_cookie(r, correo)
    return r


@router.api_route("/entrar", methods=["GET", "HEAD"])
async def entrar(t: str = ""):
    """Enlace magico del correo. Deja la cookie puesta y lleva a la app."""
    correo = ses.leer_enlace(t)
    if not correo:
        return RedirectResponse("/?acceso=caducado", status_code=303)
    r = RedirectResponse("/?acceso=ok", status_code=303)
    ses.poner_cookie(r, correo)
    return r


@router.api_route("/sesion", methods=["GET", "HEAD"])
async def sesion(peticion: Request):
    correo = ses.correo_de_peticion(peticion)
    return JSONResponse(
        {"autenticado": bool(correo), "correo": correo},
        # Una respuesta que dice quien eres jamas debe quedar en una cache
        # intermedia: el siguiente usuario veria la identidad del anterior.
        headers={"Cache-Control": "no-store, private"},
    )


@router.post("/salir")
async def salir():
    r = JSONResponse({"ok": True})
    ses.quitar_cookie(r)
    return r
