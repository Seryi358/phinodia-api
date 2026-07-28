"""Endpoints de suscripción recurrente (renta mensual auto-cobrada vía Wompi).

Router delgado: toda la lógica vive en services/subscription_service.py. Aquí solo
se validan los cuerpos (Pydantic) y se traducen las excepciones del servicio a
códigos HTTP con mensajes en español para el usuario final.

AISLADO del checkout de pago único (/api/v1/payments/*). No comparte estado con él;
la única intersección es el webhook de Wompi, que desvía a este servicio los
eventos cuya referencia empieza por 'PH-SUB-' (ver app/routers/payments.py).

Identidad = email en el body (no hay login), igual que el resto de la API. Para
cancelar se exige el manage_token (HMAC) devuelto al crear la suscripción.
"""
import hmac
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from app.config import get_settings
from app.services import subscription_service as subs
from app.services.subscription_plans import (
    DIAGNOSTICO_AMOUNT_IN_CENTS,
    DIAGNOSTICO_PIECES,
    PLANS_BY_CODE,
    price_per_video_in_cents,
)

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


class CreateSubscriptionRequest(BaseModel):
    email: EmailStr
    plan_code: str = Field(..., max_length=32)
    billing_interval: str = Field("monthly", max_length=16)
    # Token de tarjeta generado por el NAVEGADOR con la public key (Wompi
    # tokens/cards). El PAN nunca toca nuestro servidor.
    card_token: str = Field(..., min_length=8, max_length=128)


class CancelRequest(BaseModel):
    manage_token: str = Field(..., min_length=8, max_length=64)


class ActivateRequest(BaseModel):
    manage_token: str = Field(..., min_length=8, max_length=64)


def _check_admin(token: str) -> None:
    """Shared-secret por query para el cron (mismo patrón que /api/v1/admin/*)."""
    if not settings.admin_token:
        raise HTTPException(404, "Not found")
    if not token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(401, "Token inválido")


def _raise_http(e: Exception):
    """Traduce las excepciones del servicio a HTTP + mensaje en español."""
    msg = str(e)
    if isinstance(e, LookupError):
        raise HTTPException(404, "Suscripción no encontrada")
    if isinstance(e, PermissionError):
        if "3ds" in msg:
            raise HTTPException(409, "El reto 3DS de la tarjeta aún no se completa; espera un momento.")
        raise HTTPException(403, "No autorizado para gestionar esta suscripción")
    if isinstance(e, ConnectionError):
        raise HTTPException(402, "El banco rechazó el cobro. Prueba con otra tarjeta.")
    if isinstance(e, RuntimeError):
        # Fallo hablando con Wompi (tokens de aceptación / fuente de pago).
        logger.error("Fallo Wompi en suscripción: %s", msg)
        raise HTTPException(502, "No pudimos comunicarnos con la pasarela de pago. Intenta de nuevo.")
    if isinstance(e, ValueError):
        pretty = {
            "plan_invalido": "Ese plan no admite suscripción recurrente.",
            "intervalo_invalido": "El intervalo de cobro no es válido (usa 'monthly' o 'annual').",
            "card_token_requerido": "Falta el token de la tarjeta.",
            "subscription_canceled": "Esta suscripción ya fue cancelada.",
        }.get(msg, "Solicitud inválida")
        raise HTTPException(400, pretty)
    logger.exception("Error inesperado en suscripción: %s", msg)
    raise HTTPException(500, "Error interno")


@router.get("/config")
async def config():
    """Datos públicos para el frontend: public key, ambiente y catálogo de planes.

    La public key es pública por diseño (se usa en el navegador para tokenizar).
    No expone ningún secreto (private/events/integrity se quedan en el servidor).
    """
    return {
        "public_key": settings.wompi_public_key,
        "environment": settings.wompi_environment,
        "plans": {
            code: {
                "name": p["name"],
                "amount_in_cents": p["amount_in_cents"],
                "popular": p["popular"],
                # Lo que se le PROMETE al cliente del servicio gestionado.
                # `credits_per_period` es presupuesto interno de producción y se
                # omite a propósito: el cliente no entra a la app ni gasta créditos.
                "videos": p["videos"],
                "variants_per_video": p["variants_per_video"],
                "pieces": p["pieces"],
                "revisions": p["revisions"],
                "unlimited_revisions": p.get("unlimited_revisions", False),
                "delivery_days": p["delivery_days"],
                "price_per_video_in_cents": price_per_video_in_cents(code),
            }
            for code, p in PLANS_BY_CODE.items()
        },
        "diagnostico": {
            "amount_in_cents": DIAGNOSTICO_AMOUNT_IN_CENTS,
            "pieces": DIAGNOSTICO_PIECES,
            "credited_to_first_month": True,
        },
    }


@router.post("/create")
async def create(req: CreateSubscriptionRequest):
    """Crea la fuente de pago 3DS + la suscripción (status=incomplete).

    Devuelve el subscription_id, el manage_token y el estado 3DS. El frontend debe
    hacer polling de /{id}/status hasta AVAILABLE y luego llamar /{id}/activate.
    """
    try:
        return await subs.create_subscription(
            email=req.email,
            plan_code=req.plan_code,
            billing_interval=req.billing_interval,
            card_token=req.card_token,
        )
    except HTTPException:
        raise
    except Exception as e:
        _raise_http(e)


@router.get("/{subscription_id}/status")
async def status(
    subscription_id: str,
    manage_token: str = Query(..., min_length=8, max_length=64),
):
    """Estado de la suscripción + del reto 3DS de su fuente (polling del frontend).

    Exige el manage_token de create(): el id solo filtraría plan, marca de tarjeta
    y fecha del próximo cobro.
    """
    try:
        return await subs.get_status(subscription_id, manage_token)
    except HTTPException:
        raise
    except Exception as e:
        _raise_http(e)


@router.post("/{subscription_id}/activate")
async def activate(subscription_id: str, req: ActivateRequest):
    """Con la fuente 3DS AVAILABLE, dispara el primer cobro y activa el ciclo.

    Exige el manage_token (igual que /cancel): este endpoint mueve dinero.
    """
    try:
        return await subs.activate_subscription(subscription_id, req.manage_token)
    except HTTPException:
        raise
    except Exception as e:
        _raise_http(e)


@router.post("/{subscription_id}/cancel")
async def cancel(subscription_id: str, req: CancelRequest):
    """Detiene cobros futuros. Exige el manage_token entregado al crear."""
    try:
        return await subs.cancel_subscription(subscription_id, req.manage_token)
    except HTTPException:
        raise
    except Exception as e:
        _raise_http(e)


@router.get("")
async def mine(email: EmailStr = Query(...), token: str = Query(...)):
    """Lista las suscripciones de un email. USO INTERNO: exige el admin token.

    Antes era público y bastaba con conocer (o adivinar) un email para obtener el
    id de la suscripción, su plan, la marca de la tarjeta y la fecha del próximo
    cobro — y con ese id se podía forzar /activate. Dos problemas en uno: fuga de
    datos personales (Ley 1581) y cobro no autorizado.

    Si algún día hace falta autogestión del cliente sin sesión, el patrón correcto
    es un magic-link enviado al propio correo, no confiar en el email como prueba.
    """
    _check_admin(token)
    try:
        return {"subscriptions": await subs.list_by_email(email)}
    except HTTPException:
        raise
    except Exception as e:
        _raise_http(e)


@router.post("/cron/run")
async def run_cron(token: str = Query(...), limit: int = Query(50, ge=1, le=200)):
    """Motor de cobro mensual + dunning. Lo dispara el cron (n8n) con el admin token.

    Escanea las suscripciones vencidas (idx_subscriptions_due) y cobra cada una.
    Idempotente por período: correrlo dos veces el mismo día NO cobra doble.
    """
    _check_admin(token)
    due = await subs.find_due(limit)
    results = []
    for s in due:
        try:
            r = await subs.charge_due_subscription(s["id"])
            results.append({"id": s["id"], **r})
        except Exception as e:
            logger.exception("Cron: cobro falló para suscripción %s", s.get("id"))
            results.append({"id": s.get("id"), "action": "error", "error": type(e).__name__})
    return {"scanned": len(due), "processed": len(results), "results": results}
