"""Lógica de negocio de suscripciones recurrentes (renta mensual auto-cobrada).

AISLADO del checkout de pago único. Orquesta el motor Wompi 3DS/3RI
(services/wompi_recurring.py) + la persistencia (subscriptions,
subscription_invoices) + la acreditación de créditos (services/credits.py).
Lo consumen: el router (app/routers/subscriptions.py), el webhook de Wompi
(rama PH-SUB- en app/routers/payments.py) y el cron de cobro mensual.

Idempotencia en DOS capas (calcada del webhook de pago único de payments.py):
  1. Cobro:        subscription_invoices.reference UNIQUE  -> un cobro por período.
  2. Acreditación: subscription_invoices.credited (CAS)    -> un grant por factura.

Fuente única de verdad de los estados de dinero: Wompi. Nunca acreditamos sin un
status=APPROVED confirmado (por fetch_transaction o por el webhook). grant_credits
es ADITIVO (no idempotente), así que TODA acreditación pasa por el flip
credited=false->true; solo el ganador otorga créditos.

DECISIÓN DE PRODUCTO (MVP, refinable): un plan ANUAL cobra 10 meses de una vez y
acredita el valor de 12 períodos (credits_per_period × 12) en ese único cobro;
next_charge_at salta +12 meses. Un plan MENSUAL cobra y acredita
credits_per_period cada mes. Si más adelante se quiere "drip" mensual de créditos
en el anual, hace falta un segundo reloj (next_credit_at) + su cron; hoy no existe.
"""
import calendar
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database import db
from app.services.credits import CreditService
from app.services.wompi import fetch_transaction
from app.services import wompi_recurring as wr
from app.services.subscription_plans import (
    resolve_plan,
    period_amount_in_cents,
    MONTHLY,
    ANNUAL,
)

settings = get_settings()
logger = logging.getLogger(__name__)

# Dunning: reintentos antes de pausar la suscripción por falta de pago.
MAX_FAILED_ATTEMPTS = 4
# Backoff en días entre reintentos de cobro fallido, por número de intento.
DUNNING_BACKOFF_DAYS = {1: 1, 2: 3, 3: 5}


# ── helpers de tiempo (sin dependencias externas) ───────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(value: str) -> datetime:
    """Parsea un timestamptz de Supabase (acepta sufijo Z y naive → UTC)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _add_months(dt: datetime, n: int) -> datetime:
    """Suma n meses preservando el día (clamp al último día del mes destino)."""
    total = dt.month - 1 + n
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _period_months(billing_interval: str) -> int:
    return 12 if billing_interval == ANNUAL else 1


def _credits_per_invoice(plan: dict, billing_interval: str) -> int:
    """Créditos a acreditar por factura pagada. Anual = 12 períodos de una vez."""
    per = int(plan["credits_per_period"])
    return per * 12 if billing_interval == ANNUAL else per


def _period_ref(subscription_id: str, period: datetime) -> str:
    """Referencia idempotente por período: PH-SUB-{sub_id8}-{YYYYMM}.

    El prefijo PH-SUB- es lo que el webhook de payments.py usa para desviar el
    evento a este servicio en vez de tratarlo como un pack de pago único.
    """
    return f"PH-SUB-{subscription_id[:8]}-{period.strftime('%Y%m')}"


# ── tokens de gestión (cancelar/ver sin sesión de usuario) ──────────────────
def manage_token(subscription_id: str) -> str:
    """HMAC determinístico sobre el id de la suscripción.

    No hay login en la API (la identidad es el email en el body). El front recibe
    este token al crear la suscripción y lo presenta para cancelarla, de modo que
    un tercero no pueda cancelar solo adivinando el email. Reutiliza el mismo
    secreto de integridad que el resto de firmas Wompi.
    """
    return hmac.new(
        settings.wompi_integrity_secret.encode(),
        subscription_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def _verify_manage_token(subscription_id: str, token: str) -> bool:
    return hmac.compare_digest(manage_token(subscription_id), token or "")


def _public_view(sub: dict) -> dict:
    """Proyección segura para el frontend (sin ids internos ni datos de pago)."""
    plan = resolve_plan(sub.get("plan_code")) or {}
    return {
        "id": sub.get("id"),
        "plan_code": sub.get("plan_code"),
        "plan_name": plan.get("name"),
        "billing_interval": sub.get("billing_interval"),
        "status": sub.get("status"),
        "card_brand": sub.get("card_brand"),
        "current_period_end": sub.get("current_period_end"),
        "next_charge_at": sub.get("next_charge_at"),
    }


# ── acreditación idempotente (el corazón: webhook y cobro directo la comparten) ─
async def _credit_invoice(
    reference: str,
    user_id: str,
    plan: dict,
    billing_interval: str,
    wompi_tx_id: str | None,
) -> bool:
    """Acredita los créditos de UNA factura, exactamente una vez (CAS).

    Gana el flip credited=false->true quien llegue primero (este cobro directo o
    el webhook). Solo el ganador llama grant_credits (que es ADITIVO). Devuelve
    True si ESTA llamada fue la que acreditó; False si otro proceso ya lo hizo.
    Si grant_credits falla tras ganar el flip, revierte credited para que el
    webhook (o el próximo retry) reintente, y re-lanza.
    """
    won = await db.update(
        "subscription_invoices",
        {"reference": f"eq.{reference}", "credited": "eq.false"},
        {
            "credited": True,
            "status": "approved",
            "paid_at": _iso(_now()),
            **({"wompi_transaction_id": wompi_tx_id} if wompi_tx_id else {}),
        },
    )
    if not won:
        return False  # otro proceso ya acreditó este período

    credits = _credits_per_invoice(plan, billing_interval)
    try:
        await CreditService().grant_credits(user_id, credits)
    except Exception as e:
        await db.update(
            "subscription_invoices",
            {"reference": f"eq.{reference}", "credited": "eq.true"},
            {"credited": False},
        )
        logger.exception("grant_credits falló para %s — rollback credited: %s", reference, e)
        raise
    logger.info("Suscripción: acreditados %d créditos por factura %s", credits, reference)
    return True


async def _charge_period(sub: dict, period: datetime, attempt: int) -> dict:
    """Cobra UN período contra la fuente de la suscripción y acredita si aprueba.

    Idempotente por período: la factura (reference UNIQUE) se asegura ANTES de
    cobrar, para que el webhook siempre encuentre una fila que marcar y no haya
    carrera cobro↔webhook. Reintentar el mismo período reutiliza la factura y NO
    genera un segundo cargo. Devuelve {reference, status, credited, wompi_transaction_id}.
    """
    subscription_id = sub["id"]
    plan = resolve_plan(sub["plan_code"])
    if not plan:
        raise ValueError("plan_desconocido")
    amount = period_amount_in_cents(sub["plan_code"], sub["billing_interval"])
    if not amount:
        raise ValueError("monto_invalido")
    reference = _period_ref(subscription_id, period)

    # (a) Asegura la factura del período ANTES de cobrar.
    invoice = await db.select_one("subscription_invoices", {"reference": f"eq.{reference}"})
    if invoice and invoice.get("credited"):
        return {
            "reference": reference,
            "status": (invoice.get("status") or "approved").upper(),
            "credited": True,
            "wompi_transaction_id": invoice.get("wompi_transaction_id"),
        }
    if not invoice:
        try:
            invoice = await db.insert(
                "subscription_invoices",
                {
                    "subscription_id": subscription_id,
                    "user_id": sub["user_id"],
                    "reference": reference,
                    "amount_in_cents": amount,
                    "currency": "COP",
                    "status": "pending",
                    "attempt": attempt,
                    "credited": False,
                },
            )
        except Exception:
            # Carrera con otro cobro del mismo período: el UNIQUE lo atrapó, relee.
            invoice = await db.select_one("subscription_invoices", {"reference": f"eq.{reference}"})
            if not invoice:
                raise

    # (b) Cobra sin cliente presente (MIT/3RI).
    tx = await wr.charge_payment_source(
        settings.wompi_base_url,
        settings.wompi_private_key,
        payment_source_id=int(sub["payment_source_id"]),
        amount_in_cents=amount,
        customer_email=sub["customer_email"],
        reference=reference,
        integrity_secret=settings.wompi_integrity_secret,
    )
    if not tx:
        # Ni se creó la transacción (red/validación). Factura queda pending para
        # que el cron reintente; no se toca ningún crédito.
        return {"reference": reference, "status": "ERROR", "credited": False, "wompi_transaction_id": None}

    wompi_tx_id = str(tx.get("id"))
    await db.update(
        "subscription_invoices",
        {"reference": f"eq.{reference}"},
        {"wompi_transaction_id": wompi_tx_id},
    )

    # (c) Estado autoritativo (el cobro MIT suele volver PENDING; el webhook lo
    # confirmará). Si ya viene APPROVED, acreditamos de inmediato — idempotente,
    # el webhook posterior verá credited=true y no duplicará.
    authoritative = await fetch_transaction(
        wompi_tx_id, settings.wompi_base_url, settings.wompi_private_key
    )
    status = (authoritative or tx).get("status") or "PENDING"

    credited = False
    if status == "APPROVED":
        try:
            credited = await _credit_invoice(
                reference, sub["user_id"], plan, sub["billing_interval"], wompi_tx_id
            )
        except Exception:
            credited = False  # el webhook reintentará la acreditación
    else:
        await db.update(
            "subscription_invoices",
            {"reference": f"eq.{reference}"},
            {"status": status.lower()},
        )
    return {"reference": reference, "status": status, "credited": credited, "wompi_transaction_id": wompi_tx_id}


# ── API pública del servicio ────────────────────────────────────────────────
async def create_subscription(
    email: str, plan_code: str, billing_interval: str, card_token: str
) -> dict:
    """Pasos 1-3: obtiene tokens de aceptación, crea la fuente de pago 3DS y la
    fila de suscripción (status=incomplete).

    La fuente arranca en PENDING; el frontend debe hacer polling de get_status()
    hasta que el reto 3DS deje la fuente AVAILABLE y entonces llamar a activate().
    Devuelve {subscription_id, payment_source_id, status, manage_token, three_ds}.
    """
    plan = resolve_plan(plan_code)
    if not plan or plan["amount_in_cents"] <= 0:
        raise ValueError("plan_invalido")  # gratis/desconocido no se auto-cobra
    if billing_interval not in (MONTHLY, ANNUAL):
        raise ValueError("intervalo_invalido")
    if not card_token:
        raise ValueError("card_token_requerido")

    base = settings.wompi_base_url
    toks = await wr.get_acceptance_tokens(base, settings.wompi_public_key)
    if not toks:
        raise RuntimeError("wompi_acceptance_tokens")

    ps = await wr.create_card_payment_source(
        base, settings.wompi_private_key, token=card_token, customer_email=email, **toks
    )
    if not ps:
        raise RuntimeError("wompi_payment_source")

    user = await CreditService().get_or_create_user(email)
    sub = await db.insert(
        "subscriptions",
        {
            "user_id": user["id"],
            "plan_code": plan_code,
            "billing_interval": billing_interval,
            "status": "incomplete",
            "payment_source_id": ps.get("id"),
            "payment_source_type": ps.get("type") or "CARD",
            "card_brand": ((ps.get("public_data") or {}).get("brand") or "").lower() or None,
            "customer_email": email,
        },
    )
    three_ds = (ps.get("extra") or {}).get("three_ds_auth") or {}
    return {
        "subscription_id": sub["id"],
        "payment_source_id": ps.get("id"),
        "status": ps.get("status"),  # normalmente PENDING
        "manage_token": manage_token(sub["id"]),
        "three_ds": three_ds,  # el front lo usa para el iframe del reto
    }


async def get_status(subscription_id: str) -> dict:
    """Estado de la suscripción + del reto 3DS de su fuente (para el polling).

    El frontend llama esto cada ~2s tras create() hasta que payment_source_status
    sea AVAILABLE (entonces activa) o DECLINED/ERROR (la tarjeta no autorizó).
    """
    sub = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    if not sub:
        raise LookupError("subscription_not_found")

    ps_status = None
    three_ds: dict = {}
    psid = sub.get("payment_source_id")
    if sub.get("status") == "incomplete" and psid:
        ps = await wr.get_payment_source(
            settings.wompi_base_url, settings.wompi_private_key, int(psid)
        )
        if ps:
            ps_status = ps.get("status")
            three_ds = (ps.get("extra") or {}).get("three_ds_auth") or {}
    return {
        "subscription": _public_view(sub),
        "payment_source_status": ps_status,
        "three_ds": three_ds,
    }


async def activate_subscription(subscription_id: str) -> dict:
    """Paso 5: con la fuente AVAILABLE, hace el primer cobro y activa el ciclo.

    Idempotente: si ya está activa no recobra. El cobro y la acreditación están
    protegidos por subscription_invoices, así que un doble-click no duplica nada.
    """
    sub = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    if not sub:
        raise LookupError("subscription_not_found")
    if sub.get("status") in ("active", "past_due"):
        return {"status": sub["status"], "subscription": _public_view(sub), "action": "already_active"}
    if sub.get("status") == "canceled":
        raise ValueError("subscription_canceled")

    psid = sub.get("payment_source_id")
    if not psid:
        raise RuntimeError("sin_fuente_de_pago")
    ps = await wr.get_payment_source(settings.wompi_base_url, settings.wompi_private_key, int(psid))
    if not ps or ps.get("status") != "AVAILABLE":
        # El reto 3DS aún no termina: el front debe seguir en polling.
        raise PermissionError("3ds_no_completado")

    now = _now()
    result = await _charge_period(sub, period=now, attempt=1)

    # Un DECLINED/ERROR inmediato (tarjeta sin fondos, etc.) NO activa: el cliente
    # debe reintentar con otra tarjeta.
    if result["status"] in ("DECLINED", "ERROR", "VOIDED"):
        raise ConnectionError(f"cobro_rechazado:{result['status']}")

    months = _period_months(sub["billing_interval"])
    period_end = _add_months(now, months)
    await db.update(
        "subscriptions",
        {"id": f"eq.{subscription_id}"},
        {
            "status": "active",
            "current_period_start": _iso(now),
            "current_period_end": _iso(period_end),
            "next_charge_at": _iso(period_end),
            "failed_attempts": 0,
            "updated_at": _iso(now),
        },
    )
    fresh = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    return {"status": "active", "subscription": _public_view(fresh or sub), "first_charge": result}


async def cancel_subscription(subscription_id: str, token: str) -> dict:
    """Detiene cobros futuros. Los créditos ya acreditados NO se revocan."""
    if not _verify_manage_token(subscription_id, token):
        raise PermissionError("token_invalido")
    sub = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    if not sub:
        raise LookupError("subscription_not_found")
    if sub.get("status") == "canceled":
        return {"status": "canceled", "subscription": _public_view(sub), "action": "already_canceled"}
    now = _now()
    await db.update(
        "subscriptions",
        {"id": f"eq.{subscription_id}"},
        {"status": "canceled", "canceled_at": _iso(now), "next_charge_at": None, "updated_at": _iso(now)},
    )
    fresh = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    return {"status": "canceled", "subscription": _public_view(fresh or sub)}


async def list_by_email(email: str) -> list[dict]:
    rows = await db.select(
        "subscriptions", {"customer_email": f"eq.{email}", "order": "created_at.desc"}
    )
    return [_public_view(r) for r in rows]


# ── entradas para el webhook y el cron ──────────────────────────────────────
async def credit_invoice_from_webhook(
    reference: str, wompi_transaction_id: str, amount_in_cents: int, customer_email: str
) -> dict:
    """Punto de entrada desde el webhook de Wompi para un cobro recurrente.

    El webhook YA validó firma, replay, entorno y refetch autoritativo, y garantiza
    status=APPROVED + COP. Aquí solo acreditamos (idempotente) la factura del
    período. La factura ya existe (el cobro la insertó antes de cobrar); si no
    existe, no acreditamos a ciegas. Puede re-lanzar si grant_credits falla, para
    que el webhook devuelva 503 y Wompi reintente.
    """
    invoice = await db.select_one("subscription_invoices", {"reference": f"eq.{reference}"})
    if not invoice:
        logger.warning("Webhook suscripción sin factura para %s — no se acredita", reference)
        return {"action": "invoice_not_found"}
    if invoice.get("credited"):
        return {"action": "duplicate"}

    # Defensa (defense-in-depth): el monto autoritativo del webhook debe cuadrar
    # con lo facturado en este período. El webhook ya refetchea el monto real de
    # Wompi; esto detecta un desvío entre lo cobrado y lo que el período debía
    # costar antes de tocar créditos.
    try:
        if int(amount_in_cents) != int(invoice.get("amount_in_cents") or 0):
            logger.warning(
                "Webhook %s: monto %s != factura %s — no se acredita",
                reference, amount_in_cents, invoice.get("amount_in_cents"),
            )
            return {"action": "amount_mismatch"}
    except (TypeError, ValueError):
        return {"action": "amount_mismatch"}

    sub = await db.select_one("subscriptions", {"id": f"eq.{invoice['subscription_id']}"})
    if not sub:
        logger.error("Factura %s sin suscripción %s", reference, invoice.get("subscription_id"))
        return {"action": "subscription_missing"}
    # Defensa: el email autoritativo debe ser el de la suscripción (no acreditar a
    # un tercero si el refetch de Wompi trajera un email inesperado).
    if (customer_email or "").strip().lower() != (sub.get("customer_email") or "").strip().lower():
        logger.error("Webhook %s: email no coincide con la suscripción — no se acredita", reference)
        return {"action": "email_mismatch"}
    plan = resolve_plan(sub["plan_code"])
    if not plan:
        return {"action": "unknown_plan"}

    credited = await _credit_invoice(
        reference, invoice["user_id"], plan, sub["billing_interval"], wompi_transaction_id
    )
    # Un APPROVED confirmado sana el ciclo: si venía de past_due (dunning), vuelve
    # a active y limpia los intentos fallidos.
    if credited and sub.get("status") == "past_due":
        await db.update(
            "subscriptions",
            {"id": f"eq.{sub['id']}"},
            {"status": "active", "failed_attempts": 0, "updated_at": _iso(_now())},
        )
    return {"action": "credits_granted" if credited else "already_processed"}


async def find_due(limit: int = 50) -> list[dict]:
    """Suscripciones cuyo período venció (activas o morosas). Para el cron."""
    return await db.select(
        "subscriptions",
        {
            "status": "in.(active,past_due)",
            "next_charge_at": f"lte.{_iso(_now())}",
            "order": "next_charge_at.asc",
            "limit": str(limit),
        },
    )


async def charge_due_subscription(subscription_id: str) -> dict:
    """Cobra el período vencido de UNA suscripción (lo invoca el cron n8n).

    Reprograma el siguiente cobro si aprueba; aplica dunning (past_due + backoff,
    luego paused al agotar intentos) si falla. Idempotente por período: la
    reference se deriva de next_charge_at, no de "ahora", para que un reintento
    tardío conserve la MISMA reference.
    """
    sub = await db.select_one("subscriptions", {"id": f"eq.{subscription_id}"})
    if not sub:
        raise LookupError("subscription_not_found")
    if sub.get("status") not in ("active", "past_due"):
        return {"action": "skip", "status": sub.get("status")}

    next_at = sub.get("next_charge_at")
    period = _parse_dt(next_at) if next_at else _now()
    attempt = int(sub.get("failed_attempts") or 0) + 1
    result = await _charge_period(sub, period=period, attempt=attempt)
    now = _now()

    if result["status"] == "APPROVED":
        new_end = _add_months(period, _period_months(sub["billing_interval"]))
        await db.update(
            "subscriptions",
            {"id": f"eq.{subscription_id}"},
            {
                "status": "active",
                "current_period_start": _iso(period),
                "current_period_end": _iso(new_end),
                "next_charge_at": _iso(new_end),
                "failed_attempts": 0,
                "updated_at": _iso(now),
            },
        )
        return {"action": "charged", "status": "APPROVED", "reference": result["reference"]}

    # Cobro PENDING también reprograma como aprobado-optimista NO: PENDING aún no
    # es dinero. Lo tratamos como fallo de este intento y el webhook, si termina
    # APPROVED, sanará el ciclo vía credit_invoice_from_webhook.
    if attempt >= MAX_FAILED_ATTEMPTS:
        await db.update(
            "subscriptions",
            {"id": f"eq.{subscription_id}"},
            {"status": "paused", "failed_attempts": attempt, "next_charge_at": None, "updated_at": _iso(now)},
        )
        return {"action": "paused", "status": result["status"], "attempt": attempt}

    retry_at = now + timedelta(days=DUNNING_BACKOFF_DAYS.get(attempt, 5))
    await db.update(
        "subscriptions",
        {"id": f"eq.{subscription_id}"},
        {"status": "past_due", "failed_attempts": attempt, "next_charge_at": _iso(retry_at), "updated_at": _iso(now)},
    )
    return {"action": "retry_scheduled", "status": result["status"], "attempt": attempt, "next_charge_at": _iso(retry_at)}
