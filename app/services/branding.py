"""Assets ChatAgent servis par l'app web (app_url)."""

AI_ASSISTANT_AVATAR_PATH = "/ia-assistant.png"


def default_assistant_avatar_url(app_url: str) -> str:
    base = (app_url or "http://localhost:3000").rstrip("/")
    return f"{base}{AI_ASSISTANT_AVATAR_PATH}"
