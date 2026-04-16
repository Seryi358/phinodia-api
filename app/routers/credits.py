from fastapi import APIRouter, Query
from pydantic import BaseModel, EmailStr
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
async def check_credits(email: EmailStr = Query(...)):
    svc = CreditService()
    balance = await svc.get_balance(email)
    return BalanceResponse(**balance)
