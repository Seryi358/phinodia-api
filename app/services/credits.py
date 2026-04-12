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
        """Grant per-service credits to the credits table.

        Does NOT update users.credits to avoid double-counting since
        get_balance() reads from both sources. The n8n Wompi workflow
        writes to users.credits directly for legacy purchases.
        """
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
        """Refund one credit. Tries per-service credits first, then users.credits."""
        if service_type:
            row = await db.select_one("credits", {
                "user_id": f"eq.{user_id}",
                "service_type": f"eq.{service_type}",
            })
            if row and row["used"] > 0:
                await db.update("credits", {"id": f"eq.{row['id']}"}, {"used": row["used"] - 1})
                return
        # Fall back to general credits
        user = await db.select_one("users", {"id": f"eq.{user_id}"})
        if user:
            new_credits = (user.get("credits", 0) or 0) + 1
            await db.update("users", {"id": f"eq.{user_id}"}, {"credits": new_credits})

    async def deduct_credit(self, user_id: str, service_type: str) -> bool:
        """Deduct credit: first from per-service credits table, then from users.credits."""
        # Try per-service credits first
        row = await db.select_one("credits", {
            "user_id": f"eq.{user_id}",
            "service_type": f"eq.{service_type}",
        })
        if row and (row["total"] - row["used"]) > 0:
            await db.update("credits", {"id": f"eq.{row['id']}"}, {"used": row["used"] + 1})
            return True
        # Fall back to general credits from users.credits column
        user = await db.select_one("users", {"id": f"eq.{user_id}"})
        if user and (user.get("credits", 0) or 0) > 0:
            await db.update("users", {"id": f"eq.{user_id}"}, {"credits": user["credits"] - 1})
            return True
        return False

    async def get_balance(self, email: str) -> dict:
        """Get combined balance: per-service credits + general credits from users.credits."""
        user = await db.select_one("users", {"email": f"eq.{email}"})
        if not user:
            balance = {st: 0 for st in SERVICE_TYPES}
            balance["general"] = 0
            return balance
        # Per-service credits from credits table
        rows = await db.select("credits", {"user_id": f"eq.{user['id']}"})
        balance = {st: 0 for st in SERVICE_TYPES}
        for row in rows:
            st = row.get("service_type")
            if st in balance:
                balance[st] = row["total"] - row["used"]
        # General credits from users.credits column (from n8n purchases)
        balance["general"] = user.get("credits", 0) or 0
        return balance
