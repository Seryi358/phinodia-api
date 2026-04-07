from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, Credit

SERVICE_TYPES = ["video_8s", "video_15s", "video_22s", "video_30s", "image", "landing_page"]


class CreditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_user(self, email: str, name: str | None = None) -> User:
        result = await self.session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            return user
        user = User(email=email, name=name)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def grant_credits(self, user_id: int, service_type: str, amount: int) -> Credit:
        result = await self.session.execute(
            select(Credit).where(Credit.user_id == user_id, Credit.service_type == service_type)
        )
        credit = result.scalar_one_or_none()
        if credit:
            credit.total += amount
        else:
            credit = Credit(user_id=user_id, service_type=service_type, total=amount, used=0)
            self.session.add(credit)
        await self.session.commit()
        await self.session.refresh(credit)
        return credit

    async def deduct_credit(self, user_id: int, service_type: str) -> bool:
        result = await self.session.execute(
            select(Credit).where(Credit.user_id == user_id, Credit.service_type == service_type)
        )
        credit = result.scalar_one_or_none()
        if not credit or credit.remaining <= 0:
            return False
        credit.used += 1
        await self.session.commit()
        return True

    async def get_balance(self, email: str) -> dict[str, int]:
        result = await self.session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if not user:
            return {st: 0 for st in SERVICE_TYPES}
        result = await self.session.execute(select(Credit).where(Credit.user_id == user.id))
        credits = result.scalars().all()
        balance = {st: 0 for st in SERVICE_TYPES}
        for c in credits:
            if c.service_type in balance:
                balance[c.service_type] = c.remaining
        return balance
