import hashlib
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr
from app.database import db
from app.services.credits import CreditService

router = APIRouter()
logger = logging.getLogger(__name__)


CODE_LEN = 8  # 16^8 = 4.3B codes; 6-char (16M) had ~3% collision at 1k users


def generate_referral_code(email: str) -> str:
    """Generate a deterministic 8-char referral code from an email."""
    return hashlib.md5(email.lower().strip().encode()).hexdigest()[:CODE_LEN].upper()


def _legacy_code_6(email: str) -> str:
    """Old 6-char code — kept only to recognize pre-migration registrations."""
    return hashlib.md5(email.lower().strip().encode()).hexdigest()[:6].upper()


async def find_referrer_email(referral_code: str) -> str | None:
    """Find the referrer email for a given referral code by scanning users.

    Accepts both new 8-char codes and legacy 6-char codes for backward
    compatibility with referrals registered before the length increase.

    Paginated to handle DB growth past PostgREST's default 1000-row limit.
    """
    code_upper = referral_code.upper()
    page_size = 1000
    offset = 0
    while True:
        users = await db.select("users", {
            "select": "email",
            "limit": str(page_size),
            "offset": str(offset),
        })
        if not users:
            return None
        for user in users:
            if generate_referral_code(user["email"]) == code_upper:
                return user["email"]
            if len(code_upper) == 6 and _legacy_code_6(user["email"]) == code_upper:
                return user["email"]
        if len(users) < page_size:
            return None
        offset += page_size


class ReferralCodeResponse(BaseModel):
    referral_code: str
    referral_link: str


class ReferralStatsResponse(BaseModel):
    referral_code: str
    referral_link: str
    total_referred: int
    completed: int
    pending: int
    credits_earned: list[dict]


class RegisterReferralRequest(BaseModel):
    referred_email: EmailStr
    referral_code: str


@router.get("/code", response_model=ReferralCodeResponse)
async def get_referral_code(email: EmailStr = Query(..., description="User email")):
    """Get or generate referral code for a user."""
    code = generate_referral_code(email)
    link = f"https://app.phinodia.com/precios/?ref={code}"
    return ReferralCodeResponse(referral_code=code, referral_link=link)


@router.get("/stats", response_model=ReferralStatsResponse)
async def get_referral_stats(email: EmailStr = Query(..., description="User email")):
    """Get referral stats for a user."""
    code = generate_referral_code(email)
    link = f"https://app.phinodia.com/precios/?ref={code}"

    # Find all referral registrations where this user is the referrer
    registrations = await db.select("transactions", {
        "plan_name": "eq.referral_registration",
        "status": "eq.REFERRAL",
        "order": "created_at.desc",
    })

    # Filter to only those where the wompi_transaction_id contains this user's code
    my_referrals = [
        r for r in registrations
        if r.get("wompi_transaction_id", "").startswith(f"ref_{code}_")
    ]

    # Fetch all bonus transactions for this referrer once
    bonus_transactions = await db.select("transactions", {
        "plan_name": "like.referral_bonus_%",
        "status": "eq.REFERRAL_BONUS",
    })
    my_bonuses = [
        b for b in bonus_transactions
        if b.get("wompi_transaction_id", "").startswith(f"refbonus_{code}_")
    ]
    bonus_emails = {
        b.get("wompi_transaction_id", "").split(f"refbonus_{code}_", 1)[-1]
        for b in my_bonuses
    }

    # Count pending vs completed
    pending = 0
    completed = 0
    for ref in my_referrals:
        referred_email = ref.get("wompi_transaction_id", "").split(f"ref_{code}_", 1)[-1]
        if referred_email in bonus_emails:
            completed += 1
        else:
            pending += 1
    credits_earned = []
    for b in my_bonuses:
        plan = b.get("plan_name", "")
        service = plan.replace("referral_bonus_", "") if plan.startswith("referral_bonus_") else "unknown"
        credits_earned.append({
            "service": service,
            "credits": b.get("credits_added", 0),
            "date": b.get("created_at", ""),
        })

    return ReferralStatsResponse(
        referral_code=code,
        referral_link=link,
        total_referred=len(my_referrals),
        completed=completed,
        pending=pending,
        credits_earned=credits_earned,
    )


@router.post("/register")
async def register_referral(req: RegisterReferralRequest, request: Request):
    """Register a referral when someone signs up via a referral link.

    Anti-fraud: only accepts a registration when the request was made from a
    browser that actually visited /precios?ref=<code> first (referer header
    contains '?ref=<code>' or contains '/precios'). Otherwise an attacker who
    knows their own code could pre-register arbitrary victim emails to harvest
    bonus credits when the victim eventually buys.
    """
    code = req.referral_code.upper().strip()
    referer = request.headers.get("referer", "")

    # Reject: must come from a browser that loaded a phinodia /precios page.
    # We don't pin the exact ref code in the referer because the user may bounce
    # through other pages — the precios origin is enough proof.
    if "/precios" not in referer or "phinodia.com" not in referer:
        raise HTTPException(400, "Solicitud invalida")

    # Validate referral code: find who it belongs to
    referrer_email = await find_referrer_email(code)
    if not referrer_email:
        raise HTTPException(400, "Codigo de referido invalido")

    # Can't refer yourself
    if req.referred_email.lower().strip() == referrer_email.lower().strip():
        raise HTTPException(400, "No puedes referirte a ti mismo")

    # Check if this user was already referred
    existing = await db.select("transactions", {
        "plan_name": "eq.referral_registration",
        "status": "eq.REFERRAL",
    })
    already_referred = any(
        r.get("wompi_transaction_id", "").endswith(f"_{req.referred_email.lower().strip()}")
        for r in existing
    )
    if already_referred:
        return {"status": "ok", "message": "Ya estas registrado como referido"}

    # Ensure the referred user exists in the users table
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.referred_email.lower().strip())

    # Record the referral as a transaction with special plan_name
    await db.insert("transactions", {
        "user_id": user["id"],
        "plan_name": "referral_registration",
        "credits_added": 0,
        "amount_cop": 0,
        "wompi_transaction_id": f"ref_{code}_{req.referred_email.lower().strip()}",
        "status": "REFERRAL",
    })

    logger.info("Referral registered: %s referred by code %s", req.referred_email, code)
    return {"status": "ok", "message": "Referido registrado exitosamente"}


async def process_referral_bonus(customer_email: str, service_type: str):
    """Check if a purchasing user was referred and grant bonus to referrer.

    Called from the payments webhook after a successful purchase.
    Only grants bonus on the user's FIRST purchase.
    """
    email = customer_email.lower().strip()

    # Check if this user was referred (has a referral_registration transaction)
    registrations = await db.select("transactions", {
        "plan_name": "eq.referral_registration",
        "status": "eq.REFERRAL",
    })
    referral = None
    for r in registrations:
        tx_id = r.get("wompi_transaction_id", "")
        if tx_id.endswith(f"_{email}"):
            referral = r
            break

    if not referral:
        return  # User was not referred

    # Extract referrer code from the transaction ID (format: ref_{code}_{email})
    tx_id = referral.get("wompi_transaction_id", "")
    parts = tx_id.split("_")
    if len(parts) < 3:
        return
    referrer_code = parts[1]

    # Check if bonus was already granted for this referral
    existing_bonuses = await db.select("transactions", {
        "plan_name": "like.referral_bonus_%",
        "status": "eq.REFERRAL_BONUS",
    })
    already_granted = any(
        b.get("wompi_transaction_id", "").startswith(f"refbonus_{referrer_code}_{email}")
        for b in existing_bonuses
    )
    if already_granted:
        return  # Bonus already granted

    # Find the referrer's email
    referrer_email = await find_referrer_email(referrer_code)
    if not referrer_email:
        logger.warning("Could not find referrer for code %s", referrer_code)
        return

    # Grant 1 credit of the same service type to the referrer
    credit_svc = CreditService()
    referrer_user = await credit_svc.get_or_create_user(referrer_email)
    await credit_svc.grant_credits(referrer_user["id"], service_type, 1)

    # Record the bonus transaction
    await db.insert("transactions", {
        "user_id": referrer_user["id"],
        "plan_name": f"referral_bonus_{service_type}",
        "credits_added": 1,
        "amount_cop": 0,
        "wompi_transaction_id": f"refbonus_{referrer_code}_{email}",
        "status": "REFERRAL_BONUS",
    })

    logger.info(
        "Referral bonus: granted 1 %s credit to %s (referrer of %s)",
        service_type, referrer_email, email,
    )
