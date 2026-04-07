import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailSender:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, sender_email: str):
        self.sender_email = sender_email
        self.credentials = Credentials(
            token=None, refresh_token=refresh_token, token_uri=TOKEN_URI,
            client_id=client_id, client_secret=client_secret, scopes=SCOPES,
        )

    def send_email(self, to: str, subject: str, html_body: str) -> dict:
        service = build("gmail", "v1", credentials=self.credentials)
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["From"] = self.sender_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def build_delivery_email(product_name: str, service_type: str, download_url: str) -> tuple[str, str]:
    service_labels = {
        "video_8s": "Video de 8 segundos", "video_15s": "Video de 15 segundos",
        "video_22s": "Video de 22 segundos", "video_30s": "Video de 30 segundos",
        "image": "Imagen de producto", "landing_page": "Landing Page",
    }
    label = service_labels.get(service_type, "Contenido")
    subject = f"Tu {label} de {product_name} esta listo - PhinodIA"
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #050505; color: #ffffff; padding: 40px;">
<div style="max-width: 600px; margin: 0 auto; background: rgba(255,255,255,0.05); border-radius: 24px; padding: 40px; border: 1px solid rgba(255,255,255,0.1);">
    <img src="https://ik.imagekit.io/nllhy6wch/White%20Logo%20Transparent%20Background.png?updatedAt=1771340685783" alt="PhinodIA" style="height: 40px; margin-bottom: 24px;">
    <h1 style="font-size: 24px; margin: 0 0 8px;">Tu {label} esta listo</h1>
    <p style="color: #aaa; margin: 0 0 24px;">Producto: {product_name}</p>
    <a href="{download_url}" style="display: inline-block; background: #4ade80; color: #000; padding: 14px 32px; border-radius: 12px; text-decoration: none; font-weight: 600;">Descargar ahora</a>
    <p style="color: #666; font-size: 12px; margin-top: 32px;">Este enlace expira en 24 horas.</p>
    <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 24px 0;">
    <p style="color: #444; font-size: 11px;">PhinodIA — Automatiza Sin Limites<br>Conforme a la Ley 1581 de 2012, tus datos personales son tratados segun nuestra <a href="https://phinodia.com/privacidad" style="color: #4ade80;">Politica de Privacidad</a>.</p>
</div>
</body>
</html>"""
    return subject, html
