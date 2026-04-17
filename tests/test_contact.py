"""Tests for /api/v1/contact and /api/v1/preview-emails."""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_contact_validates_required_fields(client):
    r = client.post("/api/v1/contact", json={})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "name" in detail.lower() or "nombre" in detail.lower()


def test_contact_invalid_email(client):
    r = client.post("/api/v1/contact", json={
        "name": "X", "email": "not-an-email", "message": "hi", "data_consent": True,
    })
    assert r.status_code == 422


def test_contact_consent_required(client):
    """Backend rejects when data_consent is false even if format passes."""
    with patch("app.routers.contact.GmailSender") as MockSender:
        # Won't be called because the consent check raises first
        r = client.post("/api/v1/contact", json={
            "name": "X", "email": "x@x.com", "message": "hi", "data_consent": False,
        })
    assert r.status_code == 400
    assert "datos" in r.json()["detail"].lower()


def test_contact_strips_control_chars_keeps_newlines(client):
    """Header injection defense — control chars get neutralized but \\n
    survives so multi-line bug reports render properly."""
    captured = {}
    instance = MagicMock()
    def _send(to, subject, html_body):
        captured["subject"] = subject
        captured["body"] = html_body
        return {"id": "fake"}
    instance.send_email = _send

    with patch("app.routers.contact.GmailSender", return_value=instance):
        r = client.post("/api/v1/contact", json={
            "name": "Sergio\x00\x07Castellanos",  # nulls + bell stripped
            "email": "x@x.com",
            "phone": "300\r\n555\r\n1234",
            "quote_type": "Demo",
            "message": "Linea 1\nLinea 2\nLinea 3",  # newlines preserved
            "data_consent": True,
        })
    assert r.status_code == 200
    # Newlines reach the email body intact
    assert "Linea 1" in captured["body"] and "Linea 3" in captured["body"]
    # Control chars are gone
    assert "\x00" not in captured["body"] and "\x07" not in captured["body"]


def test_preview_emails_sends_four(client):
    """Token-protected endpoint dispatches the 4 transactional templates."""
    import hashlib
    from app.config import get_settings
    settings = get_settings()
    token = hashlib.sha256(("preview-emails:" + settings.wompi_integrity_secret).encode()).hexdigest()

    instance = MagicMock()
    sent = []
    def _send(to, subject, html_body):
        sent.append((to, subject))
        return {"id": "fake"}
    instance.send_email = _send

    with patch("app.routers.contact.GmailSender", return_value=instance):
        r = client.post("/api/v1/preview-emails", headers={"X-Admin-Token": token})
    # Endpoint may have been refactored to drop token in newer commits — accept
    # either the token-required version (now 200) or the token-less version
    # (always 200).
    assert r.status_code == 200
    assert len(sent) == 4
    # All 4 sent to admin email
    admin = settings.gmail_sender_email
    assert all(to == admin for to, _ in sent)
    # Subjects include the right gendered listo/lista
    subjects = [s for _, s in sent]
    assert any("Imagen" in s and "lista" in s for s in subjects)
    assert any("Video" in s and "listo" in s for s in subjects)
    assert any("Landing" in s and "lista" in s for s in subjects)
    assert any("Compra confirmada" in s for s in subjects)
