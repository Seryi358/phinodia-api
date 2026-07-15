"""Cobro recurrente vía Wompi payment_sources (3DS / 3RI).

AISLADO a propósito del checkout de pago único (services/wompi.py + /comprar).
Un bug aquí NO puede tumbar el flujo de pago que hoy genera ingresos.

Flujo (verificado contra docs.wompi.co, jul-2026):
  1. get_acceptance_tokens()      GET  /merchants/{public_key}
  2. (frontend) tokeniza tarjeta  POST /tokens/cards   -> tok_...
  3. create_card_payment_source() POST /payment_sources -> id, status=PENDING
  4. (frontend) reto 3DS + poll   GET  /payment_sources/{id} -> status=AVAILABLE
  5. charge_payment_source()      POST /transactions   (sin cliente presente)

NO USAR EN PRODUCCIÓN hasta que se cumplan LAS DOS cosas:
  (a) El equipo de fraude de Wompi active "3DS para fuentes de pago"
      (trámite del gestor de cuenta, por el chat del panel).
  (b) Se prueben los 5 pasos contra SANDBOX (llaves pub_test/prv_test en el env)
      con evidencia — ver scripts/test_recurring_sandbox.py.

Notas de red confirmadas en la doc:
  - 3RI (auto-cobro protegido, sin cliente): solo Mastercard.
  - 3DS para CREAR la fuente: Mastercard y Visa.
  - COF `recurrent:true` (VISA/MC con procesador RBM) sube la tasa de aprobación.

Sandbox-first: todas las funciones reciben `base_url` (settings.wompi_base_url
ya conmuta por wompi_environment) — nunca hardcodean el ambiente.
"""
import hashlib

import httpx

# Timeouts explícitos (mismo criterio que services/wompi.py.fetch_transaction):
# connect corto para fallar rápido ante red caída; read holgado porque el POST
# de una transacción puede tardar en el lado del procesador.
_TIMEOUT = httpx.Timeout(15.0, connect=4.0)


def compute_integrity_signature(
    reference: str, amount_in_cents: int, currency: str, integrity_secret: str
) -> str:
    """Firma de integridad de una transacción API.

    IDÉNTICA a la del checkout (main.py /comprar) para no tener dos fuentes de
    verdad: SHA256(reference + amount_in_cents + currency + integrity_secret).
    Wompi rechaza la transacción si esta firma no cuadra, así que un atacante no
    puede alterar monto/referencia aunque intercepte el POST.
    """
    concat = f"{reference}{amount_in_cents}{currency}{integrity_secret}"
    return hashlib.sha256(concat.encode()).hexdigest()


async def get_acceptance_tokens(base_url: str, public_key: str) -> dict | None:
    """Obtiene los dos tokens legales que Wompi exige para crear fuentes/tx.

    GET /merchants/{public_key} (el public_key va en la URL, no en el header).
    Devuelve {"acceptance_token": ..., "accept_personal_auth": ...} o None ante
    cualquier fallo (el llamador debe abortar la creación de la fuente, no
    continuar con tokens vacíos — Wompi rechazaría la fuente igual).
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{base_url}/merchants/{public_key}")
        r.raise_for_status()
        data = (r.json() or {}).get("data") or {}
        acceptance = (data.get("presigned_acceptance") or {}).get("acceptance_token")
        personal = (data.get("presigned_personal_data_auth") or {}).get("acceptance_token")
        if not acceptance or not personal:
            return None
        return {"acceptance_token": acceptance, "accept_personal_auth": personal}
    except (httpx.HTTPError, ValueError):
        return None


async def create_card_payment_source(
    base_url: str,
    private_key: str,
    *,
    token: str,
    customer_email: str,
    acceptance_token: str,
    accept_personal_auth: str,
) -> dict | None:
    """Crea la fuente de pago (tarjeta tokenizada) — arranca en status PENDING.

    POST /payment_sources. Autenticación server-side con la PRIVATE key (Bearer).
    Devuelve el objeto `data` (con id + status) o None ante fallo.

    Tras esto el frontend debe HACER POLLING de get_payment_source() hasta que
    status pase de PENDING -> AVAILABLE (el reto 3DS del banco), porque solo una
    fuente AVAILABLE se puede cobrar.
    """
    payload = {
        "type": "CARD",
        "token": token,
        "customer_email": customer_email,
        "acceptance_token": acceptance_token,
        "accept_personal_auth": accept_personal_auth,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{base_url}/payment_sources",
                headers={"Authorization": f"Bearer {private_key}"},
                json=payload,
            )
        r.raise_for_status()
        return (r.json() or {}).get("data") or None
    except (httpx.HTTPError, ValueError):
        return None


async def get_payment_source(
    base_url: str, private_key: str, payment_source_id: int
) -> dict | None:
    """Lee el estado de una fuente (polling del reto 3DS, cada ~2s).

    GET /payment_sources/{id}. Devuelve `data` con:
      - status: PENDING | AVAILABLE | DECLINED | ERROR
      - extra.three_ds_auth.current_step / current_step_status (progreso 3DS)
    None ante fallo de red (el llamador reintenta el poll, no asume fallo final).
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{base_url}/payment_sources/{payment_source_id}",
                headers={"Authorization": f"Bearer {private_key}"},
            )
        r.raise_for_status()
        return (r.json() or {}).get("data") or None
    except (httpx.HTTPError, ValueError):
        return None


async def charge_payment_source(
    base_url: str,
    private_key: str,
    *,
    payment_source_id: int,
    amount_in_cents: int,
    customer_email: str,
    reference: str,
    integrity_secret: str,
    currency: str = "COP",
    recurrent: bool = True,
    installments: int = 1,
) -> dict | None:
    """Cobra contra una fuente AVAILABLE — SIN el cliente presente (MIT/3RI).

    POST /transactions. Este es el corazón de la renta mensual: lo dispara el
    cron, no un humano. `recurrent=True` activa COF (Credential On File), que
    Wompi recomienda para subir la aprobación en cobros periódicos.

    `reference` DEBE ser único e idempotente por período (p.ej.
    PH-SUB-{subscription_id}-{YYYYMM}) para que un reintento del cron no genere
    dos cobros. Devuelve `data` (con id + status: PENDING/APPROVED/DECLINED) o
    None ante fallo de red — el llamador NO debe acreditar créditos hasta
    confirmar status=APPROVED vía webhook o refetch (services/wompi.fetch_transaction).
    """
    signature = compute_integrity_signature(
        reference, amount_in_cents, currency, integrity_secret
    )
    payload = {
        "amount_in_cents": amount_in_cents,
        "currency": currency,
        "customer_email": customer_email,
        "reference": reference,
        "payment_source_id": payment_source_id,
        "signature": signature,
        "recurrent": recurrent,
        # Wompi EXIGE installments para tarjetas — verificado en sandbox: sin
        # este campo devuelve HTTP 422 INPUT_VALIDATION_ERROR ("No se especificó
        # el número de cuotas"). 1 = un solo pago (lo normal en suscripción).
        "payment_method": {"installments": installments},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{base_url}/transactions",
                headers={"Authorization": f"Bearer {private_key}"},
                json=payload,
            )
        r.raise_for_status()
        return (r.json() or {}).get("data") or None
    except (httpx.HTTPError, ValueError):
        return None
