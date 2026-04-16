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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
