from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # KIE AI
    kie_api_key: str
    kie_base_url: str = "https://api.kie.ai/api/v1"

    # OpenAI
    openai_api_key: str

    # Wompi
    wompi_private_key: str
    wompi_public_key: str
    wompi_events_secret: str
    wompi_integrity_secret: str
    wompi_environment: str = "sandbox"

    # Gmail
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str
    gmail_sender_email: str = "scastellanos@phinodia.com"

    # Supabase
    supabase_url: str = "https://bxeiecdxryelwrtcwupe.supabase.co"
    supabase_service_key: str

    # App
    cors_origins: list[str] = ["https://phinodia.com", "https://www.phinodia.com", "https://app.phinodia.com"]
    api_base_url: str = "https://app.phinodia.com"

    @property
    def wompi_base_url(self) -> str:
        if self.wompi_environment == "production":
            return "https://production.wompi.co/v1"
        return "https://sandbox.wompi.co/v1"

    # Reject empty/short secrets at startup so a misconfigured deploy can't
    # silently accept forged Wompi webhooks (HMAC of empty-string-secret is
    # trivial to compute) or leak the Supabase service key over plain HTTP.
    @field_validator(
        "wompi_events_secret",
        "wompi_integrity_secret",
        "wompi_private_key",
        "supabase_service_key",
        "openai_api_key",
        "kie_api_key",
        "gmail_refresh_token",
        "gmail_client_secret",
    )
    @classmethod
    def _non_empty_secret(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError("secret/key must be set and >= 16 chars")
        return v

    @field_validator("supabase_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("supabase_url must be https://")
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
