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

    package = resolve_package(amount)
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
        "wompi_transaction_id": reference,
        "status": status,
    })

    await credit_svc.grant_credits(user["id"], package["service"], package["credits"])

    logger.info("Granted %d %s credits to %s (tx: %s)", package["credits"], package["service"], customer_email, reference)
    return {"status": "ok", "action": "credits_granted", "credits": package["credits"]}
