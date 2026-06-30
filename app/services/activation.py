"""Activacion: recordatorios por correo a compradores con creditos sin usar.

El cuello de botella del negocio: la gente paga creditos y NO crea el video
(95% de creditos vendidos sin usar; a nivel usuario, ~71 compraron y nunca
completaron una generacion). Este job corre en el lifespan (cada pocas horas)
y reengancha por correo a quienes tienen credits>0 y NUNCA completaron un job,
escalonado segun la antiguedad de la cuenta:

    [1, 3) dias  -> recordatorio dia 1   (steps + CTA)
    [3, 7) dias  -> recordatorio dia 3
    [7, 14) dias -> recordatorio dia 7
    >= 14 dias   -> win-back             (compradores historicos)
    < 1 dia      -> nada (el correo de compra ya salio)

Garantias:
  * UNA sola vez por (user, kind): se reclama insertando en activation_emails
    (UNIQUE(user_id, kind)); cualquier error de insert (incluido el 409 de
    conflicto) => ya reclamado => no se reenvia. Si el ENVIO falla, se borra el
    reclamo para reintentar en el proximo ciclo.
  * Como mucho UN correo por usuario por ciclo: la ventana por edad mapea cada
    usuario a exactamente un `kind`, asi los compradores historicos reciben solo
    el win-back (no los tres recordatorios de golpe).
  * Seguro entre los 4 workers de uvicorn: el UNIQUE serializa el reclamo (cero
    duplicados aunque los 4 corran el ciclo a la vez).
  * Topes: MAX_PER_CYCLE acota POR WORKER (no es global — el loop corre en los 4);
    GLOBAL_HOURLY_CAP impone el techo GLOBAL real contando envios recientes en DB.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.database import db
from app.services.gmail import GmailSender, build_reminder_email, build_winback_email

logger = logging.getLogger(__name__)
settings = get_settings()

# Tope de correos POR WORKER por ciclo. OJO: el loop corre en el lifespan de los
# 4 workers de uvicorn, asi que este techo NO es global (el real es ~4x). La
# idempotencia (cero duplicados) la garantiza el UNIQUE(user_id,kind); este numero
# solo regula el RITMO por proceso.
MAX_PER_CYCLE = 50
# Tope GLOBAL (todos los workers) de correos en la ultima hora — red de seguridad
# REAL ante un bug de elegibilidad masiva. Se evalua contando filas en la DB al
# inicio de cada ciclo, asi que los 4 workers comparten el mismo techo.
GLOBAL_HOURLY_CAP = 200


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _kind_for_age(age_days: float) -> str | None:
    """Mapea la antiguedad (dias) al unico recordatorio aplicable, o None."""
    if age_days < 1:
        return None
    if age_days < 3:
        return "reminder_d1"
    if age_days < 7:
        return "reminder_d3"
    if age_days < 14:
        return "reminder_d7"
    return "winback"


def _build_for_kind(email: str, credits: int, kind: str) -> tuple[str, str]:
    if kind == "reminder_d1":
        return build_reminder_email(email, credits, 1)
    if kind == "reminder_d3":
        return build_reminder_email(email, credits, 3)
    if kind == "reminder_d7":
        return build_reminder_email(email, credits, 7)
    return build_winback_email(email, credits)


async def _eligible_users() -> list[dict]:
    """Usuarios con credits>0 que NUNCA completaron una generacion.

    Trae a los candidatos (credits>0) y, SOLO para ellos, consulta si tienen
    algun job 'completed' (filtrando por user_id en lotes de 100). Asi se evita
    traer la tabla `jobs` entera con un limit fijo: ese patron podia TRUNCARSE al
    crecer el volumen de jobs y dejar a un usuario YA activado fuera del set ->
    se marcaria como elegible -> recibiria un recordatorio erroneo.
    """
    users = await db.select("users", {
        "select": "id,email,credits,created_at",
        "credits": "gt.0",
        "limit": "5000",
    })
    if not users:
        return []
    ids = [u["id"] for u in users if u.get("id")]
    activated_ids: set[str] = set()
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        in_clause = "in.(" + ",".join(f'"{x}"' for x in chunk) + ")"
        rows = await db.select("jobs", {
            "select": "user_id",
            "status": "eq.completed",
            "user_id": in_clause,
            "limit": "10000",
        })
        activated_ids |= {j["user_id"] for j in (rows or []) if j.get("user_id")}
    return [u for u in users if u.get("id") not in activated_ids]


def _send_sync(to: str, subject: str, html_body: str) -> None:
    """Gmail SDK es sincrono — se llama dentro de asyncio.to_thread."""
    from app.services.gmail import unsubscribe_url
    sender = GmailSender(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
        sender_email=settings.gmail_sender_email,
    )
    # Cabeceras estandar para el boton nativo "Cancelar suscripcion" de Gmail / Apple
    # Mail (one-click). Junto con el link en el cuerpo, evita que sea spam y mejora la
    # entregabilidad.
    _url = unsubscribe_url(to)
    headers = {"List-Unsubscribe": f"<{_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    sender.send_email(to=to, subject=subject, html_body=html_body, extra_headers=headers)


async def run_activation_reminders() -> int:
    """Un ciclo de recordatorios. Devuelve el numero de correos enviados."""
    if not getattr(settings, "activation_reminders_enabled", True):
        return 0
    # Cap GLOBAL entre los 4 workers: si ya salieron muchos correos en la ultima
    # hora (p.ej. por un bug de elegibilidad masiva), abortar el ciclo. El
    # MAX_PER_CYCLE de abajo es solo un throttle POR WORKER; este conteo en DB es
    # el techo global verdadero. Best-effort: si la consulta falla, el cap
    # por-worker + el UNIQUE siguen protegiendo.
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        recent = await db.select("activation_emails", {
            "select": "id", "sent_at": f"gte.{cutoff}", "limit": str(GLOBAL_HOURLY_CAP + 1)})
        if len(recent or []) >= GLOBAL_HOURLY_CAP:
            logger.warning("activation: cap GLOBAL/hora (%d) alcanzado — abortando ciclo", GLOBAL_HOURLY_CAP)
            return 0
    except Exception:
        pass
    try:
        users = await _eligible_users()
    except Exception as e:
        logger.warning("activation: no se pudo listar elegibles: %s", type(e).__name__)
        return 0

    # Respetar las BAJAS (anti-spam): nunca escribir a quien se dio de baja.
    try:
        outs = await db.select("email_optout", {"select": "email", "limit": "10000"})
        optout = {(o.get("email") or "").strip().lower() for o in (outs or [])}
        if optout:
            users = [u for u in users if (u.get("email") or "").strip().lower() not in optout]
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    sent = 0
    for u in users:
        if sent >= MAX_PER_CYCLE:
            logger.info("activation: tope de %d correos/ciclo alcanzado", MAX_PER_CYCLE)
            break
        email = (u.get("email") or "").strip()
        user_id = u.get("id")
        if not email or not user_id:
            continue
        created = _parse_ts(u.get("created_at"))
        # created_at nulo/ilegible => tratar como historico (winback), no saltar.
        age_days = (now - created).total_seconds() / 86400.0 if created else 999.0
        kind = _kind_for_age(age_days)
        if not kind:
            continue
        # Reclamar el envio. El UNIQUE(user_id, kind) hace esto idempotente y
        # seguro entre workers: si ya se envio (o lo reclama otro worker), el
        # insert falla y saltamos sin reenviar.
        try:
            await db.insert("activation_emails", {"user_id": user_id, "kind": kind})
        except Exception:
            continue
        # Enviar. Si falla, revertir el reclamo para reintentar el proximo ciclo.
        try:
            credits = int(float(u.get("credits") or 0))
            subject, html_body = _build_for_kind(email, credits, kind)
            await asyncio.to_thread(_send_sync, email, subject, html_body)
            sent += 1
            logger.info("activation: %s enviado (user %s)", kind, user_id)
        except Exception as e:
            logger.warning("activation: fallo enviando %s a user %s: %s — revierto reclamo",
                           kind, user_id, type(e).__name__)
            try:
                await db.delete("activation_emails",
                                {"user_id": f"eq.{user_id}", "kind": f"eq.{kind}"})
            except Exception:
                logger.error("activation: no se pudo revertir reclamo %s/%s — no se reintentara",
                             user_id, kind)
    if sent:
        logger.info("activation: ciclo completado, %d correos enviados", sent)
    return sent
