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

    # Match either the new "ref|code|email" or the legacy "ref_code_email" format.
    new_prefix = f"ref|{code}|"
    legacy_prefix = f"ref_{code}_"
    my_referrals = [
        r for r in registrations
        if r.get("wompi_transaction_id", "").startswith(new_prefix)
        or r.get("wompi_transaction_id", "").startswith(legacy_prefix)
    ]

    # Fetch all bonus transactions for this referrer once
    bonus_transactions = await db.select("transactions", {
        "plan_name": "like.referral_bonus_%",
        "status": "eq.REFERRAL_BONUS",
    })
    new_bonus_prefix = f"refbonus|{code}|"
    legacy_bonus_prefix = f"refbonus_{code}_"
    my_bonuses = [
        b for b in bonus_transactions
        if b.get("wompi_transaction_id", "").startswith(new_bonus_prefix)
        or b.get("wompi_transaction_id", "").startswith(legacy_bonus_prefix)
    ]

    def _bonus_email(tx_id: str) -> str:
        if tx_id.startswith(new_bonus_prefix):
            return tx_id[len(new_bonus_prefix):]
        if tx_id.startswith(legacy_bonus_prefix):
            return tx_id[len(legacy_bonus_prefix):]
        return ""

    def _referral_email(tx_id: str) -> str:
        if tx_id.startswith(new_prefix):
            return tx_id[len(new_prefix):]
        if tx_id.startswith(legacy_prefix):
            return tx_id[len(legacy_prefix):]
        return ""

    bonus_emails = {_bonus_email(b.get("wompi_transaction_id", "")) for b in my_bonuses}

    # Count pending vs completed
    pending = 0
    completed = 0
    for ref in my_referrals:
        referred_email = _referral_email(ref.get("wompi_transaction_id", ""))
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

    The Referer header is attacker-controlled, so we don't gate on it. Real
    anti-fraud is enforced downstream: the referrer only earns a credit when
    the referred email's webhook-verified purchase fires (process_referral_bonus).
    Pre-binding random emails here without a real purchase grants nothing.
    """
    code = req.referral_code.upper().strip()

    # Validate referral code: find who it belongs to
    referrer_email = await find_referrer_email(code)
    if not referrer_email:
        raise HTTPException(400, "Codigo de referido invalido")

    # Can't refer yourself
    if req.referred_email.lower().strip() == referrer_email.lower().strip():
        raise HTTPException(400, "No puedes referirte a ti mismo")

    # Check if this user was already referred. Use a non-overlapping separator
    # ("|") so emails containing "_" can't suffix-match unrelated registrations.
    referred = req.referred_email.lower().strip()
    existing = await db.select("transactions", {
        "plan_name": "eq.referral_registration",
        "status": "eq.REFERRAL",
    })
    already_referred = any(
        r.get("wompi_transaction_id", "").endswith(f"|{referred}")
        or r.get("wompi_transaction_id", "").endswith(f"_{referred}")  # legacy rows
        for r in existing
    )
    if already_referred:
        return {"status": "ok", "message": "Ya estas registrado como referido"}

    # Ensure the referred user exists in the users table
    credit_svc = CreditService()
    user = await credit_svc.get_or_create_user(req.referred_email.lower().strip())

    # Record the referral as a transaction with special plan_name. The "|" is
    # used as a separator that cannot appear in either a code (hex) or an
    # email (RFC 5322 forbids it in the addr-spec without quoting), so parsing
    # back the (code, email) pair is unambiguous.
    await db.insert("transactions", {
        "user_id": user["id"],
        "plan_name": "referral_registration",
        "credits_added": 0,
        "amount_cop": 0,
        "wompi_transaction_id": f"ref|{code}|{referred}",
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

    # Check if this user was referred (has a referral_registration transaction).
    # Match new "ref|code|email" or legacy "ref_code_email"; the new format
    # avoids suffix collisions for emails that share trailing characters.
    registrations = await db.select("transactions", {
        "plan_name": "eq.referral_registration",
        "status": "eq.REFERRAL",
    })
    referral = None
    for r in registrations:
        tx_id = r.get("wompi_transaction_id", "")
        if tx_id.endswith(f"|{email}") or tx_id.endswith(f"_{email}"):
            referral = r
            break

    if not referral:
        return  # User was not referred

    # Extract referrer code from the transaction ID (handle both formats)
    tx_id = referral.get("wompi_transaction_id", "")
    if tx_id.startswith("ref|"):
        parts = tx_id.split("|")
        if len(parts) < 3:
            return
        referrer_code = parts[1]
    else:
        parts = tx_id.split("_")
        if len(parts) < 3:
            return
        referrer_code = parts[1]

    # Check if bonus was already FINALIZED (not PENDING_BONUS) for this referral.
    # PENDING_BONUS rows mean a previous attempt inserted but grant_credits never
    # finished — we want this run to retry, not bail.
    existing_bonuses = await db.select("transactions", {
        "plan_name": "like.referral_bonus_%",
        "status": "eq.REFERRAL_BONUS",
    })
    already_granted = any(
        b.get("wompi_transaction_id", "") in (
            f"refbonus|{referrer_code}|{email}",
            f"refbonus_{referrer_code}_{email}",
        )
        for b in existing_bonuses
    )
    if already_granted:
        return  # Bonus already granted

    # Find the referrer's email
    referrer_email = await find_referrer_email(referrer_code)
    if not referrer_email:
        logger.warning("Could not find referrer for code %s", referrer_code)
        return

    # Insert PENDING_BONUS row first so concurrent webhooks collide on the
    # UNIQUE(wompi_transaction_id) constraint and only one grant runs. If the
    # grant raises CreditContention we leave the row PENDING_BONUS for the
    # next webhook redelivery to retry — a manual sweep can also pick it up.
    credit_svc = CreditService()
    referrer_user = await credit_svc.get_or_create_user(referrer_email)
    bonus_tx_id = f"refbonus|{referrer_code}|{email}"
    try:
        await db.insert("transactions", {
            "user_id": referrer_user["id"],
            "plan_name": f"referral_bonus_{service_type}",
            "credits_added": 1,
            "amount_cop": 0,
            "wompi_transaction_id": bonus_tx_id,
            "status": "PENDING_BONUS",
        })
    except Exception as e:
        # UNIQUE-constraint collision from a concurrent webhook OR a previous
        # PENDING_BONUS we should retry. Re-read; only bail if it's already
        # finalized as REFERRAL_BONUS.
        existing = await db.select_one("transactions", {"wompi_transaction_id": f"eq.{bonus_tx_id}"})
        if existing and existing.get("status") == "REFERRAL_BONUS":
            logger.info("Referral bonus already finalized for %s: %s", bonus_tx_id, e)
            return
    from app.services.credits import CreditContention
    try:
        await credit_svc.grant_credits(referrer_user["id"], service_type, 1)
    except CreditContention:
        logger.error("Referral bonus grant contention for %s — leaving PENDING_BONUS", bonus_tx_id)
        return
    await db.update(
        "transactions",
        {"wompi_transaction_id": f"eq.{bonus_tx_id}"},
        {"status": "REFERRAL_BONUS"},
    )

    logger.info(
        "Referral bonus: granted 1 %s credit to %s (referrer of %s)",
        service_type, referrer_email, email,
    )
