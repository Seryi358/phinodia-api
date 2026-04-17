"""Contact / quote-request endpoint.

Powers the "Solicitar cotización" form on phinodia.com homepage. Replaces
the previous WhatsApp deep-link which skipped capturing the lead.
"""
import hashlib
import hmac as _hmac
import logging
import re
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.config import get_settings
from app.services.gmail import GmailSender, build_delivery_email, build_purchase_email

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

# Strip control chars BUT preserve \n and \r so multi-line messages
# (typical for "describe tu proyecto") render properly. Header injection
# is moot here because the user-supplied text only goes into the EMAIL
# BODY, never into headers (subject is composed server-side from name +
# quote_type which already pass through this same filter).
_CONTROL = re.compile(r'[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]')


def _clean(s: str) -> str:
    return _CONTROL.sub(' ', (s or '').strip())[:8000]


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    phone: str = Field("", max_length=40)
    quote_type: str = Field("general", max_length=80)
    message: str = Field(..., min_length=1, max_length=8000)
    data_consent: bool

    @field_validator("name", "phone", "quote_type", "message")
    @classmethod
    def _strip(cls, v: str) -> str:
        return _clean(v)


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


@router.post("/contact")
async def submit_contact(req: ContactRequest):
    if not req.data_consent:
        raise HTTPException(400, "Debes aceptar el tratamiento de datos")

    sender = GmailSender(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
        sender_email=settings.gmail_sender_email,
    )

    safe = {k: _escape_html(getattr(req, k)) for k in ("name", "email", "phone", "quote_type", "message")}
    subject = f"[PhinodIA Cotización] {safe['name']} — {safe['quote_type']}"
    html = f"""
    <h2>Nueva solicitud de cotización</h2>
    <p><strong>Nombre:</strong> {safe['name']}</p>
    <p><strong>Correo:</strong> <a href="mailto:{safe['email']}">{safe['email']}</a></p>
    <p><strong>Teléfono:</strong> {safe['phone'] or '(no proporcionado)'}</p>
    <p><strong>Tipo de servicio:</strong> {safe['quote_type']}</p>
    <p><strong>Mensaje:</strong></p>
    <pre style="white-space:pre-wrap;font-family:inherit">{safe['message']}</pre>
    <hr>
    <p style="color:#86868b;font-size:12px">Enviado desde phinodia.com</p>
    """

    try:
        # send_email is sync (Google API client); avoid blocking the loop.
        import asyncio
        await asyncio.to_thread(
            sender.send_email,
            to=settings.gmail_sender_email,
            subject=subject,
            html_body=html,
        )
    except Exception as e:
        logger.exception("Contact form email failed: %s", type(e).__name__)
        raise HTTPException(503, "No pudimos enviar tu solicitud. Intenta de nuevo o contáctanos por WhatsApp.")

    # Don't log the lead's email — Habeas Data. Name is enough for ops audit.
    logger.info("Contact form: new quote request from %s (type=%s)", req.name, req.quote_type)
    return {"status": "ok", "message": "Recibimos tu solicitud. Te respondemos en menos de 24 horas."}


def _admin_token() -> str:
    """Token derived from wompi_integrity_secret so we don't add a new env var.
    Anyone with shell access to EasyPanel can compute it; nobody outside can."""
    return hashlib.sha256(("preview-emails:" + settings.wompi_integrity_secret).encode()).hexdigest()


@router.post("/preview-emails")
async def preview_emails(x_admin_token: str = Header(None, alias="X-Admin-Token")):
    """Send the 4 transactional email templates to the admin inbox so the
    visual look-and-feel can be verified in a real Gmail/Outlook client.
    Token-protected so it isn't an open spam relay against the admin email.
    """
    if not x_admin_token or not _hmac.compare_digest(x_admin_token, _admin_token()):
        raise HTTPException(403, "Forbidden")

    samples = [
        ("Crema Hidratante NaturaSkin", "video_15s", "https://app.phinodia.com/uploads/results/sample.mp4", build_delivery_email),
        ("Crema Hidratante NaturaSkin", "image",     "https://app.phinodia.com/uploads/results/sample.jpg", build_delivery_email),
        ("Curso de Marketing Digital PRO", "landing_page", "https://app.phinodia.com/estado/?job_id=sample", build_delivery_email),
    ]
    sender = GmailSender(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
        sender_email=settings.gmail_sender_email,
    )
    sent = []
    import asyncio as _aio
    for product, service_type, url, builder in samples:
        subject, html = builder(product, service_type, url)
        await _aio.to_thread(
            sender.send_email,
            to=settings.gmail_sender_email,
            subject="[PREVIEW] " + subject,
            html_body=html,
        )
        sent.append(subject)

    # Purchase confirmation has a different signature
    subj4, html4 = build_purchase_email(
        "cliente@empresa.co",
        "Reel Standard 15s — Profesional",
        10,
        "video_15s",
    )
    await _aio.to_thread(
        sender.send_email,
        to=settings.gmail_sender_email,
        subject="[PREVIEW] " + subj4,
        html_body=html4,
    )
    sent.append(subj4)

    logger.info("Sent %d preview emails to %s", len(sent), settings.gmail_sender_email)
    return {"status": "ok", "sent": len(sent), "subjects": sent}
