import base64
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from typing import Literal


def _safe_url(u: str) -> str:
    """Reject anything that isn't an https URL or our own /-relative path so a
    poisoned KIE result_url can't render a phishing link in the email body.
    Case-insensitive scheme check matches what urlparse-based callers accept."""
    if not isinstance(u, str):
        return ""
    u = u.strip()
    if u.lower().startswith("https://"):
        return html.escape(u, quote=True)
    if u.startswith("/"):
        return html.escape(u, quote=True)
    return ""

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


def _apple_email_base(content: str) -> str:
    """Wrap content in Apple-style email template."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #fbfbfd; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', 'Segoe UI', Roboto, sans-serif; color: #1d1d1f; -webkit-font-smoothing: antialiased;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fbfbfd;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="max-width: 600px; margin: 0 auto;">
    <!-- Logo -->
    <tr><td style="padding: 0 0 32px; text-align: center;">
        <img src="https://ik.imagekit.io/nllhy6wch/Logo%20Google%20Workspace.png?updatedAt=1767905998676" alt="PhinodIA" style="height: 28px;">
    </td></tr>
    <!-- Content -->
    <tr><td style="background: #ffffff; border-radius: 16px; padding: 48px 40px; border: 1px solid #d2d2d7;">
        {content}
    </td></tr>
    <!-- Footer -->
    <tr><td style="padding: 24px 0 0; text-align: center;">
        <p style="margin: 0; font-size: 12px; color: #86868b; line-height: 1.5;">PhinodIA — Automatiza Sin Límites</p>
        <p style="margin: 8px 0 0; font-size: 11px; color: #86868b; line-height: 1.5;">Conforme a la Ley 1581 de 2012, tus datos personales son tratados según nuestra <a href="https://phinodia.com/politica-de-privacidad/" style="color: #0066cc; text-decoration: none;">Política de Privacidad</a>.</p>
        <p style="margin: 8px 0 0; font-size: 11px; color: #86868b;"><a href="https://phinodia.com/condiciones-del-servicio/" style="color: #86868b; text-decoration: none;">Términos</a> &nbsp;|&nbsp; <a href="https://phinodia.com/habeas-data/" style="color: #86868b; text-decoration: none;">Habeas Data</a></p>
    </td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def build_ops_alert_email(
    *,
    subject: str,
    title: str,
    summary: str,
    severity: Literal["info", "warning", "critical"] = "info",
    facts: dict[str, str] | None = None,
    items: list[str] | None = None,
    raw_text: str = "",
) -> tuple[str, str]:
    """Render a polished operations email for bot alerts/reports."""
    badge = {
        "info": ("#1D1D1F", "#F5F5F7", "INFO"),
        "warning": ("#9A6700", "#FFF7D6", "WARNING"),
        "critical": ("#B42318", "#FEE4E2", "CRITICAL"),
    }.get(severity, ("#1D1D1F", "#F5F5F7", severity.upper()))
    badge_fg, badge_bg, badge_label = badge

    safe_title = html.escape(title or "Alerta operativa")
    safe_summary = html.escape(summary or "")

    facts_html = ""
    if facts:
        rows = []
        for key, value in facts.items():
            rows.append(
                f"""
                <tr>
                    <td style="padding:10px 0;color:#86868b;font-size:12px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">{html.escape(str(key))}</td>
                    <td style="padding:10px 0;color:#1d1d1f;font-size:14px;text-align:right;font-weight:600;">{html.escape(str(value))}</td>
                </tr>"""
            )
        facts_html = f"""
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:24px 0 0;background:#f5f5f7;border-radius:12px;padding:0 18px;">
            {''.join(rows)}
        </table>"""

    items_html = ""
    clean_items = [str(item).strip() for item in (items or []) if str(item).strip()]
    if clean_items:
        items_html = "<ul style=\"margin:20px 0 0;padding-left:20px;color:#1d1d1f;font-size:14px;line-height:1.7;\">" + "".join(
            f"<li style=\"margin:0 0 10px;\">{html.escape(item)}</li>"
            for item in clean_items
        ) + "</ul>"

    raw_html = ""
    if raw_text.strip():
        raw_html = f"""
        <div style="margin-top:24px;background:#0b1437;border-radius:12px;padding:18px 20px;">
            <div style="font-size:11px;font-weight:600;color:#d0d5dd;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;">Detalle bruto</div>
            <pre style="margin:0;white-space:pre-wrap;font-family:'SF Mono',Menlo,monospace;font-size:12px;line-height:1.65;color:#ffffff;">{html.escape(raw_text)}</pre>
        </div>"""

    content = f"""
        <div style="display:inline-block;padding:6px 12px;border-radius:999px;background:{badge_bg};color:{badge_fg};font-size:12px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:20px;">{badge_label}</div>
        <h1 style="margin:0 0 8px;font-size:28px;font-weight:700;letter-spacing:-0.02em;color:#1d1d1f;">{safe_title}</h1>
        <p style="margin:0;font-size:17px;color:#6e6e73;line-height:1.5;">{safe_summary}</p>
        {facts_html}
        {items_html}
        {raw_html}
    """
    return subject, _apple_email_base(content)


def build_delivery_email(product_name: str, service_type: str, download_url: str) -> tuple[str, str]:
    service_labels = {
        "video_8s": "Video de 8 segundos", "video_15s": "Video de 15 segundos",
        "video_22s": "Video de 22 segundos", "video_30s": "Video de 30 segundos",
        "image": "Imagen de producto", "landing_page": "Landing Page",
    }
    label = service_labels.get(service_type, "Contenido")
    # Spanish gender — "imagen" and "landing page" are feminine, the rest are masculine.
    is_feminine = service_type in ("image", "landing_page")
    listo = "lista" if is_feminine else "listo"
    subject = f"Tu {label} está {listo} — PhinodIA"
    cta_label = "Abrir landing page" if service_type == "landing_page" else "Descargar ahora"
    # Result URLs are now mirrored to our own /uploads/results/ so neither
    # videos/images nor landing pages have a hard expiry — keep the
    # "tambien en Mis Generaciones" pointer but drop the alarmist tone.
    expiry_note = (
        '<p style="margin: 32px 0 0; font-size: 12px; color: #86868b;">'
        'También puedes verlo cuando quieras en <a href="https://app.phinodia.com/mis-generaciones" style="color: #0066cc; text-decoration: none;">Mis Generaciones</a>.'
        '</p>'
    )
    safe_product = html.escape(product_name or "")
    safe_link = _safe_url(download_url)
    if not safe_link:
        # Fall back to /mis-generaciones if KIE handed us something we don't
        # trust — better to drop the CTA than to email a javascript: link.
        safe_link = "https://app.phinodia.com/mis-generaciones"
    content = f"""
        <h1 style="margin: 0 0 8px; font-size: 28px; font-weight: 700; letter-spacing: -0.005em; color: #1d1d1f;">Tu {label} está {listo}.</h1>
        <p style="margin: 0 0 32px; font-size: 17px; color: #86868b; line-height: 1.47;">Producto: {safe_product}</p>
        <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td style="border-radius: 980px; background: #1d1d1f;">
            <a href="{safe_link}" style="display: inline-block; padding: 14px 32px; color: #ffffff; font-size: 17px; font-weight: 400; text-decoration: none; letter-spacing: -0.022em;">{cta_label}</a>
        </td></tr></table>
        {expiry_note}"""
    return subject, _apple_email_base(content)


def build_purchase_email(email: str, plan_name: str, credits: int, service_type: str) -> tuple[str, str]:
    """Build purchase confirmation email in Apple style."""
    service_labels = {
        "video_8s": "Videos de 8 segundos", "video_15s": "Videos de 15 segundos",
        "video_22s": "Videos de 22 segundos", "video_30s": "Videos de 30 segundos",
        "image": "Imagenes de producto", "landing_page": "Landing Pages",
    }
    label = service_labels.get(service_type, "Créditos")
    subject = f"Compra confirmada — {credits} créditos de {label}"
    app_url = "https://app.phinodia.com"
    content = f"""
        <h1 style="margin: 0 0 8px; font-size: 28px; font-weight: 700; letter-spacing: -0.005em; color: #1d1d1f;">Compra confirmada.</h1>
        <p style="margin: 0 0 32px; font-size: 17px; color: #86868b; line-height: 1.47;">Tus créditos ya están disponibles para usar.</p>

        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom: 32px;">
            <tr><td style="padding: 24px; background: #f5f5f7; border-radius: 12px;">
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                    <tr>
                        <td style="font-size: 14px; color: #86868b; padding-bottom: 12px;">Servicio</td>
                        <td style="font-size: 14px; color: #1d1d1f; text-align: right; padding-bottom: 12px; font-weight: 600;">{label}</td>
                    </tr>
                    <tr>
                        <td style="font-size: 14px; color: #86868b; padding-bottom: 12px;">Créditos agregados</td>
                        <td style="font-size: 14px; color: #1d1d1f; text-align: right; padding-bottom: 12px; font-weight: 600;">{credits}</td>
                    </tr>
                    <tr>
                        <td style="font-size: 14px; color: #86868b;">Cuenta</td>
                        <td style="font-size: 14px; color: #1d1d1f; text-align: right;">{html.escape(email or "")}</td>
                    </tr>
                </table>
            </td></tr>
        </table>

        <p style="margin: 0 0 24px; font-size: 17px; color: #1d1d1f; line-height: 1.47;">Ya puedes generar contenido de marketing para tus productos.</p>

        <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr><td style="border-radius: 980px; background: #1d1d1f;">
            <a href="{app_url}" style="display: inline-block; padding: 14px 32px; color: #ffffff; font-size: 17px; font-weight: 400; text-decoration: none; letter-spacing: -0.022em;">Ir a PhinodIA</a>
        </td></tr></table>"""
    return subject, _apple_email_base(content)
