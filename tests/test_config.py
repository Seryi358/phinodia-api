import os
import pytest


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "test-kie-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("WOMPI_PRIVATE_KEY", "prv_test_abc")
    monkeypatch.setenv("WOMPI_PUBLIC_KEY", "pub_test_abc")
    monkeypatch.setenv("WOMPI_EVENTS_SECRET", "test_events_abc")
    monkeypatch.setenv("WOMPI_INTEGRITY_SECRET", "test_integrity_abc")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test-refresh")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    from app.config import Settings
    s = Settings()
    assert s.kie_api_key == "test-kie-key"
    assert s.openai_api_key == "test-openai-key"
    assert s.wompi_private_key == "prv_test_abc"
    assert s.database_url == "sqlite+aiosqlite:///./phinodia.db"
    assert s.cors_origins == ["https://phinodia.com", "https://www.phinodia.com"]


def test_settings_wompi_base_url_sandbox(monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("WOMPI_PRIVATE_KEY", "k")
    monkeypatch.setenv("WOMPI_PUBLIC_KEY", "k")
    monkeypatch.setenv("WOMPI_EVENTS_SECRET", "k")
    monkeypatch.setenv("WOMPI_INTEGRITY_SECRET", "k")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "k")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "k")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "k")
    monkeypatch.setenv("WOMPI_ENVIRONMENT", "sandbox")

    from app.config import Settings
    s = Settings()
    assert s.wompi_base_url == "https://sandbox.wompi.co/v1"


def test_settings_wompi_base_url_production(monkeypatch):
    monkeypatch.setenv("KIE_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("WOMPI_PRIVATE_KEY", "k")
    monkeypatch.setenv("WOMPI_PUBLIC_KEY", "k")
    monkeypatch.setenv("WOMPI_EVENTS_SECRET", "k")
    monkeypatch.setenv("WOMPI_INTEGRITY_SECRET", "k")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "k")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "k")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "k")
    monkeypatch.setenv("WOMPI_ENVIRONMENT", "production")

    from app.config import Settings
    s = Settings()
    assert s.wompi_base_url == "https://production.wompi.co/v1"
