import hashlib
import logging
import time
import secrets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.config import Settings
from app.database import db
from app.services.credits import CreditService
from app.services.wompi import verify_webhook_signature, resolve_package, PACKAGES_BY_SKU
from app.services.gmail import GmailSender, build_purchase_email

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


@router.post("/webhook")
async def wompi_webhook(event: dict):
    if not verify_webhook_signature(event, settings.wompi_events_secret):
        raise HTTPException(403, "Invalid webhook signature")
    if event.get("event") != "transaction.updated":
        return {"status": "ignored"}

    tx_data = event.get("data", {}).get("transaction", {})
    status = tx_data.get("status")
    if status != "APPROVED":
        logger.info("Transaction %s status: %s — no credits granted", tx_data.get("id"), status)
        return {"status": "ok", "action": "none"}

    amount = tx_data.get("amount_in_cents", 0)
    reference = tx_data.get("reference", "")
    customer_email = tx_data.get("customer_email", "")
    if not customer_email:
        logger.warning("Webhook missing customer_email for tx %s", tx_data.get("id"))
        return {"status": "ok", "action": "none"}

    # Extract SKU from reference (format: PH-{sku}-{timestamp}-{hex})
    ref_parts = reference.split("-")
    sku_from_ref = "-".join(ref_parts[1:-2]) if len(ref_parts) >= 4 else None

    # Deduplicate: check if this transaction was already processed
    wompi_tx_id = str(tx_data.get("id", reference))
    existing = await db.select_one("transactions", {"wompi_transaction_id": f"eq.{wompi_tx_id}"})
    if existing:
        logger.info("Duplicate webhook for tx %s — ignoring", wompi_tx_id)
        return {"status": "ok", "action": "duplicate"}

    package = resolve_package(amount, sku_from_ref)
    if not package:
        logger.warning("Unknown package for amount %d in tx %s", amount, tx_data.get("id"))
        return {"status": "ok", "action": "unknown_package"}

    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(customer_email)

    # Record transaction in Supabase
    await db.insert("transactions", {
        "user_id": user["id"],
        "plan_name": f"{package['service']}_{package['credits']}",
        "credits_added": package["credits"],
        "amount_cop": amount,
        "wompi_transaction_id": wompi_tx_id,
        "status": status,
    })

    await credit_svc.grant_credits(user["id"], package["service"], package["credits"])

    # Send purchase confirmation email
    try:
        subject, html = build_purchase_email(customer_email, package["service"], package["credits"], package["service"])
        sender = GmailSender(
            client_id=settings.gmail_client_id, client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token, sender_email=settings.gmail_sender_email,
        )
        sender.send_email(to=customer_email, subject=subject, html_body=html)
        logger.info("Purchase confirmation email sent to %s", customer_email)
    except Exception as e:
        logger.warning("Failed to send purchase email to %s: %s", customer_email, e)

    logger.info("Granted %d %s credits to %s (tx: %s)", package["credits"], package["service"], customer_email, reference)
    return {"status": "ok", "action": "credits_granted", "credits": package["credits"]}
