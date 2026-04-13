from app.database import db

SERVICE_TYPES = ["video_8s", "video_15s", "video_22s", "video_30s", "image", "landing_page"]


class CreditService:

    async def get_or_create_user(self, email: str) -> dict:
        user = await db.select_one("users", {"email": f"eq.{email}"})
        if user:
            return user
        user = await db.insert("users", {"email": email, "credits": 0})
        return user

    async def grant_credits(self, user_id: str, service_type: str, amount: int):
        """Grant credits for a specific service."""
        row = await db.select_one("credits", {
            "user_id": f"eq.{user_id}",
            "service_type": f"eq.{service_type}",
        })
        if row:
            new_total = row["total"] + amount
            await db.update("credits", {"id": f"eq.{row['id']}"}, {"total": new_total})
        else:
            await db.insert("credits", {
                "user_id": user_id,
                "service_type": service_type,
                "total": amount,
                "used": 0,
            })

    async def refund_credit(self, user_id: str, service_type: str | None = None):
        """Refund one credit for a specific service."""
        if service_type:
            row = await db.select_one("credits", {
                "user_id": f"eq.{user_id}",
                "service_type": f"eq.{service_type}",
            })
            if row and row["used"] > 0:
                await db.update("credits", {"id": f"eq.{row['id']}"}, {"used": row["used"] - 1})
                return
        # Last resort fallback
        rows = await db.select("credits", {"user_id": f"eq.{user_id}"})
        for row in rows:
            if row["used"] > 0:
                await db.update("credits", {"id": f"eq.{row['id']}"}, {"used": row["used"] - 1})
                return

    async def deduct_credit(self, user_id: str, service_type: str) -> bool:
        """Deduct one credit for a specific service. Only from credits table."""
        row = await db.select_one("credits", {
            "user_id": f"eq.{user_id}",
            "service_type": f"eq.{service_type}",
        })
        if row and (row["total"] - row["used"]) > 0:
            await db.update("credits", {"id": f"eq.{row['id']}"}, {"used": row["used"] + 1})
            return True
        return False

    async def get_balance(self, email: str) -> dict[str, int]:
        """Get per-service credit balance."""
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
