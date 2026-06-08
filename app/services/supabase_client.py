from supabase import Client, create_client

from app.config import settings

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError("Supabase credentials are not configured")
        if settings.supabase_url.startswith("postgresql://"):
            raise RuntimeError(
                "SUPABASE_URL doit être l'URL API https://xxx.supabase.co, pas postgresql://..."
            )
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client
