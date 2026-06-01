from fastapi import APIRouter, Query
from pydantic import BaseModel, EmailStr
from app.services.credits import CreditService

router = APIRouter()


class BalanceResponse(BaseModel):
    # Single unified wallet balance. (Was per-service: video_8s/15s/22s/30s/…)
    credits: int = 0


@router.api_route("/check", methods=["GET", "HEAD"], response_model=BalanceResponse)
async def check_credits(email: EmailStr = Query(...)):
    svc = CreditService()
    credits = await svc.get_balance(email)
    return BalanceResponse(credits=credits)
