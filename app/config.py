from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Chatbot SaaS API"
    debug: bool = False
    cors_origins: str = "http://localhost:3000"

    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""

    # Firebase Cloud Messaging — JSON complet du compte de service (une ligne)
    firebase_service_account_json: str = ""

    widget_cdn_url: str = "https://cdn.example.com/widget.js"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000/api/v1"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
