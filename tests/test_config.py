def test_settings_loads_required_env():
    from app.config import Settings
    s = Settings()
    assert s.kie_api_key.startswith("test-kie-key")
    assert s.openai_api_key.startswith("test-openai-key")
    assert s.wompi_private_key.startswith("prv_test_abc")
    assert s.api_base_url == "https://app.phinodia.com"
    assert "https://app.phinodia.com" in s.cors_origins
    assert s.gmail_sender_email == "scastellanos@phinodia.com"


def test_wompi_base_url_sandbox(monkeypatch):
    monkeypatch.setenv("WOMPI_ENVIRONMENT", "sandbox")
    from app.config import Settings
    s = Settings()
    assert s.wompi_base_url == "https://sandbox.wompi.co/v1"


def test_wompi_base_url_production(monkeypatch):
    monkeypatch.setenv("WOMPI_ENVIRONMENT", "production")
    from app.config import Settings
    s = Settings()
    assert s.wompi_base_url == "https://production.wompi.co/v1"


def test_supabase_url_default():
    from app.config import Settings
    s = Settings()
    assert s.supabase_url == "https://bxeiecdxryelwrtcwupe.supabase.co"
