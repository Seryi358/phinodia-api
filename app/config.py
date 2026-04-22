from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # KIE AI
    kie_api_key: str
    kie_base_url: str = "https://api.kie.ai/api/v1"

    # OpenAI
    openai_api_key: str

    # Anthropic — direct API for landing-page generation (Opus 4.6).
    # Optional: if absent, the landing path falls back to OpenAI.
    anthropic_api_key: str = ""

    # Admin token — single-secret for /api/v1/admin/* endpoints
    # (sales dashboard + CSV export). Set in EasyPanel env. Empty = disabled.
    admin_token: str = ""

    # Meta (Facebook/Instagram) — Conversions API server-side tracking.
    # All optional: if any is empty the CAPI calls become no-ops so the
    # app boots fine before the BM is wired. When set, every conversion
    # the backend witnesses (Purchase, Lead, InitiateCheckout, Contact)
    # is mirrored to Meta with a shared event_id so frontend Pixel + our
    # server fire dedupe within Meta's 48h window. CAPI in 2026 is
    # mandatory: pixel-only setups lose 30-50% of events to Safari/iOS
    # privacy mitigations, ad-blockers, and consent banners — without
    # CAPI, Advantage+ AI optimizes blind and CPA inflates 2-3x.
    meta_pixel_id: str = ""
    meta_capi_access_token: str = ""
    meta_test_event_code: str = ""
    # Marketing-bot ENV (read by the separate phinodia-ads-bot service —
    # kept here too so a single source-of-truth holds the whole stack).
    meta_ad_account_id: str = ""
    meta_business_id: str = ""
    meta_catalog_id: str = ""

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
    # wompi_public_key is included so a missing env doesn't silently break
    # checkout (frontend would render a malformed widget with no diagnostic).
    @field_validator(
        "wompi_events_secret",
        "wompi_integrity_secret",
        "wompi_private_key",
        "wompi_public_key",
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

    @field_validator("anthropic_api_key")
    @classmethod
    def _optional_anthropic(cls, v: str) -> str:
        # Optional secret — empty string is allowed (we fall back to OpenAI),
        # but if set it must look like a real key.
        if v and len(v) < 16:
            raise ValueError("anthropic_api_key must be empty or >= 16 chars")
        return v

    @field_validator("supabase_url", "api_base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        # Strip whitespace defensively — a stray trailing newline in .env
        # would otherwise corrupt the api_base_url prefix and break every
        # upload validation at request time (silent prod breakage).
        v = (v or "").strip()
        if not v.startswith("https://"):
            raise ValueError("URL must be https://")
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Process-wide cached factory. Avoids re-parsing .env + re-validating secrets
# on each module import (was happening 6× across main + database + 4 routers).
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
