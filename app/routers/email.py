from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.config import Settings
from app.services.gmail import GmailSender, build_delivery_email

router = APIRouter()
settings = Settings()


class SendEmailRequest(BaseModel):
    to: EmailStr
    product_name: str
    service_type: str
    download_url: str


@router.post("/send")
async def send_delivery_email(req: SendEmailRequest):
    subject, html = build_delivery_email(req.product_name, req.service_type, req.download_url)
    try:
        sender = GmailSender(
            client_id=settings.gmail_client_id, client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token, sender_email=settings.gmail_sender_email,
        )
        sender.send_email(to=req.to, subject=subject, html_body=html)
        return {"status": "sent"}
    except Exception as e:
        raise HTTPException(500, f"Email sending failed: {str(e)}")
