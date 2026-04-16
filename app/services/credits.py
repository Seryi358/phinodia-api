from app.database import db

SERVICE_TYPES = ["video_8s", "video_15s", "video_22s", "video_30s", "image", "landing_page"]


def _normalize_email(email: str) -> str:
    """Lowercase and strip — emails are treated case-insensitively in practice."""
    return (email or "").strip().lower()


class CreditService:

    async def get_or_create_user(self, email: str) -> dict:
        email = _normalize_email(email)
        user = await db.select_one("users", {"email": f"eq.{email}"})
        if user:
            return user
        user = await db.insert("users", {"email": email, "credits": 0})
        return user

    async def grant_credits(self, user_id: str, service_type: str, amount: int):
        """Grant credits for a specific service. Race-safe via optimistic locking."""
        for _ in range(3):  # retry on lost race
            row = await db.select_one("credits", {
                "user_id": f"eq.{user_id}",
                "service_type": f"eq.{service_type}",
            })
            if row:
                updated = await db.update(
                    "credits",
                    {"id": f"eq.{row['id']}", "total": f"eq.{row['total']}"},
                    {"total": row["total"] + amount},
                )
                if updated:
                    return
                # lost the race, re-read and retry
                continue
            try:
                await db.insert("credits", {
                    "user_id": user_id,
                    "service_type": service_type,
                    "total": amount,
                    "used": 0,
                })
                return
            except Exception:
                # Another request inserted the row between our select and insert.
                # Re-read and apply as an update on next loop iteration.
                continue

    async def refund_credit(self, user_id: str, service_type: str | None = None):
        """Refund one credit for a specific service. Race-safe via optimistic locking."""
        if service_type:
            for _ in range(3):
                row = await db.select_one("credits", {
                    "user_id": f"eq.{user_id}",
                    "service_type": f"eq.{service_type}",
                })
                if not row or row["used"] <= 0:
                    break
                updated = await db.update(
                    "credits",
                    {"id": f"eq.{row['id']}", "used": f"eq.{row['used']}"},
                    {"used": row["used"] - 1},
                )
                if updated:
                    return
        # Last resort fallback: any service with used > 0
        rows = await db.select("credits", {"user_id": f"eq.{user_id}"})
        for row in rows:
            if row["used"] > 0:
                updated = await db.update(
                    "credits",
                    {"id": f"eq.{row['id']}", "used": f"eq.{row['used']}"},
                    {"used": row["used"] - 1},
                )
                if updated:
                    return

    async def deduct_credit(self, user_id: str, service_type: str) -> bool:
        """Deduct one credit for a specific service.

        Uses optimistic locking to prevent double-spend under concurrent requests:
        the UPDATE only matches rows where `used` is still what we read, so two
        racing requests can't both succeed.
        """
        for _ in range(3):  # retry on lost race
            row = await db.select_one("credits", {
                "user_id": f"eq.{user_id}",
                "service_type": f"eq.{service_type}",
            })
            if not row or (row["total"] - row["used"]) <= 0:
                return False
            updated = await db.update(
                "credits",
                {"id": f"eq.{row['id']}", "used": f"eq.{row['used']}"},
                {"used": row["used"] + 1},
            )
            if updated:
                return True
            # else lost the race, re-read and retry
        return False

    async def get_balance(self, email: str) -> dict[str, int]:
        """Get per-service credit balance."""
        email = _normalize_email(email)
        user = await db.select_one("users", {"email": f"eq.{email}"})
        if not user:
            return {st: 0 for st in SERVICE_TYPES}
        rows = await db.select("credits", {"user_id": f"eq.{user['id']}"})
        balance = {st: 0 for st in SERVICE_TYPES}
        for row in rows:
            st = row.get("service_type")
            if st in balance:
                balance[st] = row["total"] - row["used"]
        return balance
