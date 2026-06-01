"""Unified credit wallet.

Migrated from the old per-service model (a `credits` table with one
total/used row per service_type: video_8s, video_15s, …) to a SINGLE balance
held in `users.credits`. One wallet pays for everything; an action's cost is a
number of credits:

    video : 1 credit per 10s segment  (10s=1, 20s=2, 30s=3)
    image : 1 credit
    landing_page : 5 credits

Costs live in CREDIT_COST / video_credit_cost() so the routers deduct the right
amount and refund the SAME amount on failure.

Concurrency: there is no atomic increment in PostgREST, so we use a
read-modify-write compare-and-swap on `users.credits` (UPDATE only matches the
row while credits still equals what we read). Racing requests retry on a lost
CAS instead of double-spending or losing a grant.
"""
import logging
from app.database import db

logger = logging.getLogger(__name__)

# Per-action credit cost (the single source of truth for what things cost).
CREDIT_COST = {
    "image": 1,
    "landing_page": 6,
}
VIDEO_CREDITS_PER_10S = 3
# Durations offered in the UI (seconds). Each maps to ceil(d/10) credits.
ALLOWED_VIDEO_DURATIONS = (10, 20, 30)

_CAS_RETRIES = 8


class CreditExhausted(Exception):
    """No credit available to deduct — surfaces as 402 to the user."""


class CreditContention(Exception):
    """Persistent CAS contention prevented a grant/refund — caller should retry
    later (e.g. leave the transaction PENDING_GRANT for Wompi to redeliver)."""


def video_credit_cost(duration_seconds: int) -> int:
    """Credits a video costs: 1 per 10s segment (ceil)."""
    return max(1, (int(duration_seconds) + 9) // 10) * VIDEO_CREDITS_PER_10S


def action_cost(service_type: str, duration_seconds: int | None = None) -> int:
    """Credit cost for any action. Video depends on duration; others are flat."""
    if service_type == "video":
        return video_credit_cost(duration_seconds or 10)
    return CREDIT_COST.get(service_type, 1)


def credits_for_service_label(label: str) -> int:
    """Credits a job costs, derived from its stored service_type label
    (image, landing_page, or video_<secs>s like 'video_20s'). Used to refund
    the EXACT amount charged when a job fails, since the job row stores the
    descriptive label rather than the raw credit count."""
    if label and label.startswith("video_"):
        try:
            secs = int(label[len("video_"):].rstrip("s"))
        except ValueError:
            secs = 10
        return video_credit_cost(secs)
    return CREDIT_COST.get(label, 1)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


class CreditService:

    async def get_or_create_user(self, email: str) -> dict:
        email = _normalize_email(email)
        user = await db.select_one("users", {"email": f"eq.{email}"})
        if user:
            return user
        try:
            return await db.insert("users", {"email": email, "credits": 0})
        except Exception:
            # Concurrent webhook+generate inserted between our SELECT and INSERT.
            existing = await db.select_one("users", {"email": f"eq.{email}"})
            if existing:
                return existing
            raise

    async def get_balance(self, email: str) -> int:
        """Single unified balance for the user (0 if unknown)."""
        email = _normalize_email(email)
        user = await db.select_one("users", {"email": f"eq.{email}"})
        return int(user.get("credits") or 0) if user else 0

    async def _adjust(self, user_id: str, delta: int, *, require_available: bool) -> bool:
        """CAS read-modify-write on users.credits.

        delta < 0 deducts, delta > 0 grants/refunds. With require_available, a
        deduct that would overdraw returns False (legitimate "no credits"); a
        persistent CAS loss raises CreditContention so grants aren't silently
        dropped.
        """
        for _ in range(_CAS_RETRIES):
            user = await db.select_one("users", {"id": f"eq.{user_id}"})
            if not user:
                return False
            current = int(user.get("credits") or 0)
            if require_available and current + delta < 0:
                return False  # not enough credits
            updated = await db.update(
                "users",
                {"id": f"eq.{user_id}", "credits": f"eq.{current}"},
                {"credits": current + delta},
            )
            if updated:
                return True
            # lost the CAS — someone else changed the balance; re-read and retry
        logger.warning("credit _adjust CAS exhausted user=%s delta=%d", user_id, delta)
        if delta > 0:
            raise CreditContention("credit grant/refund failed after retries")
        return False

    async def deduct(self, user_id: str, amount: int = 1) -> bool:
        """Deduct `amount` credits atomically. False on insufficient balance."""
        if amount <= 0:
            return True
        return await self._adjust(user_id, -amount, require_available=True)

    async def refund(self, user_id: str, amount: int = 1) -> bool:
        """Refund `amount` credits (e.g. on a failed generation)."""
        if amount <= 0:
            return True
        try:
            return await self._adjust(user_id, amount, require_available=False)
        except CreditContention:
            logger.error("refund CAS exhausted user=%s amount=%d — needs manual replay", user_id, amount)
            return False

    async def grant_credits(self, user_id: str, service_or_amount, amount: int | None = None) -> None:
        """Add purchased/bonus credits to the unified wallet.

        Dual-signature for backward compatibility with the old per-service API:
          grant_credits(user_id, amount)                # new unified
          grant_credits(user_id, service_type, amount)  # old (service ignored)
        Race-safe; raises CreditContention if the CAS budget is exhausted so the
        Wompi webhook can leave the tx retryable instead of marking APPROVED with
        no credits delivered.
        """
        amt = int(service_or_amount if amount is None else amount)
        if amt <= 0:
            return
        await self._adjust(user_id, amt, require_available=False)

    # ── Backward-compat wrappers (old per-service call sites) ─────────
    # Existing routers pass a service_type label; the unified wallet only needs
    # the credit COUNT, which credits_for_service_label derives from the label
    # (image=1, landing_page=6, video_<secs>s = 3 per 10s). This keeps every
    # image/landing/jobs/video-fail call site working without edits.

    async def deduct_credit(self, user_id: str, service_type: str) -> bool:
        return await self.deduct(user_id, credits_for_service_label(service_type))

    async def refund_credit(self, user_id: str, service_type: str) -> bool:
        return await self.refund(user_id, credits_for_service_label(service_type))
