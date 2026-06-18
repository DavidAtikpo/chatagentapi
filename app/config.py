from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_API_DIR = Path(__file__).resolve().parent.parent


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

    # Firebase FCM — JSON sur une ligne OU chemin vers le fichier .json
    firebase_service_account_json: str = ""
    firebase_service_account_file: str = ""

    widget_cdn_url: str = "https://cdn.example.com/widget.js"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000/api/v1"

    @model_validator(mode="after")
    def _load_firebase_from_file(self) -> "Settings":
        if self.firebase_service_account_json.strip():
            return self
        raw_path = self.firebase_service_account_file.strip()
        if not raw_path:
            default = _API_DIR / "firebase-service-account.json"
            if default.is_file():
                raw_path = str(default)
        if raw_path:
            path = Path(raw_path)
            if not path.is_absolute():
                path = _API_DIR / path
            if path.is_file():
                object.__setattr__(self, "firebase_service_account_json", path.read_text(encoding="utf-8"))
        return self

    @property
    def firebase_enabled(self) -> bool:
        return bool(self.firebase_service_account_json.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
