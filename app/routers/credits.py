from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db_session
from app.services.credits import CreditService

router = APIRouter()


class BalanceResponse(BaseModel):
    video_8s: int = 0
    video_15s: int = 0
    video_22s: int = 0
    video_30s: int = 0
    image: int = 0
    landing_page: int = 0


@router.get("/check", response_model=BalanceResponse)
async def check_credits(email: str = Query(...), session: AsyncSession = Depends(get_db_session)):
    svc = CreditService(session)
    balance = await svc.get_balance(email)
    return BalanceResponse(**balance)
