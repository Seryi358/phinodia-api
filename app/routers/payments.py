import asyncio
import hashlib
import logging
import time
import secrets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.config import Settings
from app.database import db
from app.services.credits import CreditService, CreditContention
from app.services.wompi import verify_webhook_signature, resolve_package, PACKAGES_BY_SKU
from app.services.gmail import GmailSender, build_purchase_email
from app.routers.referrals import process_referral_bonus

router = APIRouter()
settings = Settings()
logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    sku: str
    email: EmailStr


class CheckoutResponse(BaseModel):
    reference: str
    amount_cents: int
    currency: str
    integrity_hash: str
    public_key: str


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest):
    package = PACKAGES_BY_SKU.get(req.sku)
    if not package:
        raise HTTPException(400, f"Paquete desconocido: {req.sku}")

    reference = f"PH-{req.sku}-{int(time.time())}-{secrets.token_hex(4)}"
    currency = "COP"
    amount = package["amount"]

    concat = f"{reference}{amount}{currency}{settings.wompi_integrity_secret}"
    integrity_hash = hashlib.sha256(concat.encode()).hexdigest()

    return CheckoutResponse(
        reference=reference,
        amount_cents=amount,
        currency=currency,
        integrity_hash=integrity_hash,
        public_key=settings.wompi_public_key,
    )


# Reject webhooks with timestamp drift > REPLAY_WINDOW_SECONDS to mitigate
# replay attacks and old-secret leakage. Wompi delivers within seconds; allow
# 5 min for clock skew + retries.
REPLAY_WINDOW_SECONDS = 300


@router.post("/webhook")
async def wompi_webhook(event: dict):
    if not verify_webhook_signature(event, settings.wompi_events_secret):
        raise HTTPException(403, "Invalid webhook signature")
    if event.get("event") != "transaction.updated":
        return {"status": "ignored"}

    # Replay protection: reject events older than the window. Wompi documents
    # `timestamp` as seconds-epoch but some panel/redelivery tools emit ms;
    # detect and normalize so we don't silently 200-OK every event.
    ev_ts = event.get("timestamp")
    try:
        ev_ts_int = int(ev_ts)
    except (TypeError, ValueError):
        logger.warning("Webhook missing/invalid timestamp")
        return {"status": "ok", "action": "bad_timestamp"}
    if ev_ts_int > 10_000_000_000:  # > year 2286 in seconds → must be ms epoch
        ev_ts_int //= 1000
    if abs(int(time.time()) - ev_ts_int) > REPLAY_WINDOW_SECONDS:
        logger.warning("Webhook timestamp %s outside replay window", ev_ts_int)
        return {"status": "ok", "action": "stale_timestamp"}

    # Soft cross-check Wompi environment if the field is present. Don't
    # reject if missing — Wompi's webhook spec doesn't guarantee this field.
    # Both "prod"/"production" and "test"/"sandbox" forms are tolerated.
    ev_env_raw = (event.get("environment") or "").lower()
    if ev_env_raw:
        prod_words = {"prod", "production"}
        test_words = {"test", "sandbox", "dev"}
        ev_is_prod = ev_env_raw in prod_words
        ev_is_test = ev_env_raw in test_words
        we_are_prod = settings.wompi_environment == "production"
        if (we_are_prod and ev_is_test) or (not we_are_prod and ev_is_prod):
            # 503 (not 200) so Wompi keeps retrying — a 200 here would mark
            # the event delivered and silently lose the production payment
            # if WOMPI_ENVIRONMENT was misconfigured during a deploy.
            logger.error("Webhook environment mismatch: expected wompi_env=%s, got event.environment=%s — forcing Wompi retry", settings.wompi_environment, ev_env_raw)
            raise HTTPException(503, "wompi env mismatch — retry")

    tx_data = event.get("data", {}).get("transaction", {})
    status = tx_data.get("status")
    if status != "APPROVED":
        logger.info("Transaction %s status: %s — no credits granted", tx_data.get("id"), status)
        return {"status": "ok", "action": "none"}

    # Currency must be COP — our packages are priced in COP cents and a rogue/
    # mistaken USD payment would happen to match an unrelated COP price level.
    if tx_data.get("currency") != "COP":
        logger.warning("Webhook currency %r != COP for tx %s", tx_data.get("currency"), tx_data.get("id"))
        return {"status": "ok", "action": "bad_currency"}

    # `or 0` instead of dict default — Wompi can send explicit null. Coerce to
    # int so a string-encoded amount doesn't slip past resolve_package's
    # strict equality (some webhook serializers stringify numbers).
    try:
        amount = int(tx_data.get("amount_in_cents") or 0)
    except (TypeError, ValueError):
        amount = 0
    reference = tx_data.get("reference") or ""
    customer_email = tx_data.get("customer_email") or ""
    if not customer_email:
        # Don't 200-OK — Wompi would mark delivered and never retry, leaving
        # the customer paid but uncredited with no PENDING_GRANT row to recover
        # from. 503 forces Wompi to redeliver while ops investigates.
        logger.error("Webhook missing customer_email for tx %s — forcing Wompi retry", tx_data.get("id"))
        raise HTTPException(503, "customer_email missing — retry")

    # Extract SKU from reference (format: PH-{sku}-{timestamp}-{hex}) — we always
    # generate this format in /payments/checkout. Reject if Wompi sends a reference
    # we didn't issue.
    ref_parts = reference.split("-")
    if len(ref_parts) < 4 or ref_parts[0] != "PH":
        logger.warning("Webhook reference %r doesn't match PH-{sku}-{ts}-{hex}", reference)
        return {"status": "ok", "action": "bad_reference"}
    sku_from_ref = "-".join(ref_parts[1:-2])

    # Wompi's tx id is mandatory — without it we have no idempotency key.
    wompi_tx_id_raw = tx_data.get("id")
    if not wompi_tx_id_raw:
        logger.warning("Webhook missing transaction.id for ref %r", reference)
        return {"status": "ok", "action": "missing_tx_id"}
    wompi_tx_id = str(wompi_tx_id_raw)
    existing = await db.select_one("transactions", {"wompi_transaction_id": f"eq.{wompi_tx_id}"})
    # Only treat fully-completed APPROVED rows as duplicates. A PENDING_GRANT
    # row means a previous attempt inserted but grant_credits failed — Wompi
    # retries are the natural recovery path and MUST be allowed to complete the
    # grant or the customer permanently loses their credits.
    if existing and existing.get("status") == "APPROVED":
        logger.info("Duplicate webhook for tx %s — ignoring", wompi_tx_id)
        return {"status": "ok", "action": "duplicate"}
    # Explicit branch for the lock state so a stuck row gets ERROR-level alerts
    # (not buried in info logs across many Wompi retries) and so concurrent
    # retries don't all do redundant SELECT/UPDATE work.
    if existing and existing.get("status") == "GRANTING":
        logger.error("Tx %s stuck in GRANTING — needs manual review", wompi_tx_id)
        return {"status": "ok", "action": "stuck_granting"}

    package = resolve_package(amount, sku_from_ref)
    if not package:
        logger.warning("Unknown package for amount %d sku %s in tx %s", amount, sku_from_ref, tx_data.get("id"))
        return {"status": "ok", "action": "unknown_package"}

    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(customer_email)

    # grant_credits is ADDITIVE, not idempotent — calling it twice doubles the
    # user's balance. So we use the transactions row as a CAS lock: insert
    # PENDING_GRANT first, then atomically transition PENDING_GRANT→GRANTING
    # and only the winner of that flip is allowed to call grant_credits.
    # If our status update at the end fails, the row stays at GRANTING and
    # the next retry's CAS will see status=GRANTING (not PENDING_GRANT) and
    # bail without re-granting.
    if not existing:
        try:
            await db.insert("transactions", {
                "user_id": user["id"],
                "plan_name": f"{package['service']}_{package['credits']}",
                "credits_added": package["credits"],
                "amount_cop": amount,
                "wompi_transaction_id": wompi_tx_id,
                "status": "PENDING_GRANT",
            })
        except Exception:
            # UNIQUE-collision race with a concurrent webhook — re-read.
            existing = await db.select_one("transactions", {"wompi_transaction_id": f"eq.{wompi_tx_id}"})
            if not existing:
                # Genuine insert failure (Supabase 5xx, network blip), not
                # a UNIQUE-collision race. Force Wompi retry — returning 200
                # would silently lose the payment with no row to recover from.
                logger.error("Webhook insert failed for tx %s with no recovery row — forcing retry", wompi_tx_id)
                raise HTTPException(503, "transaction insert failed — retry")

    # CAS: only the writer that wins this PENDING_GRANT→GRANTING flip may
    # grant credits. Everyone else (concurrent retry, post-grant retry,
    # already-finalized retry) bails without touching the balance.
    flipped = await db.update(
        "transactions",
        {"wompi_transaction_id": f"eq.{wompi_tx_id}", "status": "eq.PENDING_GRANT"},
        {"status": "GRANTING"},
    )
    if not flipped:
        logger.info("Webhook for tx %s already past PENDING_GRANT — skipping", wompi_tx_id)
        return {"status": "ok", "action": "already_processed"}

    try:
        await credit_svc.grant_credits(user["id"], package["service"], package["credits"])
    except Exception as e:
        # Catch ALL exceptions (not just CreditContention) so a Supabase
        # 5xx between SELECT and UPDATE doesn't bubble up — that would
        # leave the row stuck at GRANTING and Wompi's retries would all
        # short-circuit on the explicit GRANTING branch above. Rolling
        # back to PENDING_GRANT lets the next retry re-attempt.
        await db.update(
            "transactions",
            {"wompi_transaction_id": f"eq.{wompi_tx_id}", "status": "eq.GRANTING"},
            {"status": "PENDING_GRANT"},
        )
        logger.exception("grant_credits failed for tx %s — rolled back to PENDING_GRANT: %s", wompi_tx_id, e)
        return {"status": "ok", "action": "retry_grant"}
    # Process referral bonus BEFORE marking APPROVED. If we mark APPROVED
    # first and the worker crashes (SIGTERM during deploy, OOM, slow Gmail
    # call timing out the lifespan), the next webhook retry hits the
    # "duplicate APPROVED" short-circuit and the referrer's bonus is lost
    # forever. Running it first means retries naturally re-trigger it; the
    # PENDING_BONUS lock inside process_referral_bonus prevents double-grant.
    try:
        await process_referral_bonus(customer_email, package["service"])
    except Exception as e:
        # Don't log the customer email — Habeas Data. tx id suffices for ops.
        # Log at error level — this WOULD silently lose a referrer credit if
        # we just shrug. Roll the GRANTING flip back to PENDING_GRANT so the
        # next Wompi retry re-runs the referral bonus path. The PENDING_BONUS
        # lock inside process_referral_bonus prevents double-credit.
        logger.error("Failed to process referral bonus for tx %s: %s — rolling back for retry", wompi_tx_id, type(e).__name__)
        await db.update(
            "transactions",
            {"wompi_transaction_id": f"eq.{wompi_tx_id}", "status": "eq.GRANTING"},
            {"status": "PENDING_GRANT"},
        )
        return {"status": "ok", "action": "retry_referral"}

    # CAS-guarded final flip: only overwrite the GRANTING we set ourselves.
    # Without the status filter, a manual-recovery operator who flipped the
    # row back to PENDING_GRANT would see their state silently clobbered.
    await db.update(
        "transactions",
        {"wompi_transaction_id": f"eq.{wompi_tx_id}", "status": "eq.GRANTING"},
        {"status": status},
    )

    # Send purchase confirmation email — Gmail SDK is sync; run in thread to avoid
    # blocking the event loop while Wompi waits for our 200 OK.
    def _send_purchase_email():
        try:
            subject, html = build_purchase_email(customer_email, package["service"], package["credits"], package["service"])
            sender = GmailSender(
                client_id=settings.gmail_client_id, client_secret=settings.gmail_client_secret,
                refresh_token=settings.gmail_refresh_token, sender_email=settings.gmail_sender_email,
            )
            sender.send_email(to=customer_email, subject=subject, html_body=html)
            logger.info("Purchase confirmation email sent")
        except Exception as e:
            logger.warning("Failed to send purchase email: %s", e)
    # Fire-and-forget email so a slow Gmail OAuth refresh doesn't push the
    # webhook response past Wompi's ~30s timeout (which would trigger a
    # retry and double-send the email even though the credit grant is
    # idempotent). Hold a strong ref in the same _background_tasks set the
    # generate pipeline uses, otherwise asyncio's GC could collect the task
    # before the email actually sends.
    from app.routers.generate import _background_tasks
    _email_task = asyncio.create_task(asyncio.to_thread(_send_purchase_email))
    _background_tasks.add(_email_task)
    _email_task.add_done_callback(_background_tasks.discard)

    logger.info("Granted %d %s credits (tx: %s)", package["credits"], package["service"], reference)
    return {"status": "ok", "action": "credits_granted", "credits": package["credits"]}
