from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.services.credits import CreditService
from app.services.sesion import exigir_sesion

router = APIRouter()


class BalanceResponse(BaseModel):
    # Single unified wallet balance. (Was per-service: video_8s/15s/22s/30s/…)
    credits: int = 0


@router.api_route("/check", methods=["GET", "HEAD"], response_model=BalanceResponse)
async def check_credits(correo: str = Depends(exigir_sesion)):
    """Saldo del usuario de la SESION.

    Antes aceptaba ?email= y devolvia el saldo de cualquiera: con una lista de
    correos se podia averiguar quien es cliente y cuanto ha comprado."""
    svc = CreditService()
    credits = await svc.get_balance(correo)
    return BalanceResponse(credits=credits)
