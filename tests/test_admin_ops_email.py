from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_build_ops_alert_email_renders_sections():
    from app.services.gmail import build_ops_alert_email

    subject, html = build_ops_alert_email(
        subject="PhinodIA Ads Bot · WARNING",
        title="Alerta de gasto",
        summary="La campaña superó el umbral diario.",
        severity="warning",
        facts={"Severidad": "WARNING", "Bot": "phinodia-ads-bot"},
        items=["Campaña: PHN-SALES-Conversions-CO", "Spend hoy: $28.185 COP"],
        raw_text="[alert] campaign PHN-SALES-Conversions-CO",
    )

    assert "Alerta de gasto" in html
    assert "Campaña: PHN-SALES-Conversions-CO" in html
    assert "Detalle bruto" in html
    assert subject == "PhinodIA Ads Bot · WARNING"


@pytest.mark.asyncio
async def test_ops_email_requires_token():
    from fastapi import HTTPException
    from app.routers.admin import OpsEmailRequest, ops_email

    with pytest.raises(HTTPException) as exc:
        await ops_email(
            OpsEmailRequest(subject="ABC", title="XYZ"),
            token="bad",
        )
    assert exc.value.status_code in (401, 404)


@pytest.mark.asyncio
async def test_ops_email_sends_via_gmail_sender():
    from app.routers.admin import OpsEmailRequest, ops_email

    fake_sender = MagicMock()
    fake_sender.send_email.return_value = {"id": "gmail-msg-1"}
    fake_settings = SimpleNamespace(
        admin_token="shared-token",
        gmail_client_id="client-id",
        gmail_client_secret="client-secret-1234567890",
        gmail_refresh_token="refresh-token-1234567890",
        gmail_sender_email="scastellanos@phinodia.com",
    )

    with patch("app.routers.admin.get_settings", return_value=fake_settings), patch(
        "app.routers.admin.GmailSender",
        return_value=fake_sender,
    ):
        r = await ops_email(
            OpsEmailRequest(
                subject="PhinodIA Ads Bot · INFO",
                title="Reporte semanal",
                summary="Todo sano.",
                severity="info",
                facts={"Severidad": "INFO"},
                items=["Sin errores"],
            ),
            token="shared-token",
        )

    assert r["status"] == "ok"
    fake_sender.send_email.assert_called_once()
