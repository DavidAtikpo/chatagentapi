import httpx

from app.services.session_extractor import (
    dedupe_sessions,
    extract_sessions_from_html,
    formation_page_urls,
)
from app.services.supabase_client import get_supabase
from app.services.text_quality import is_html_content_type


async def fetch_training_sessions_from_site(site_url: str) -> list[dict]:
    """Extract inscription links from formation pages (same logic as refresh_sessions)."""
    all_sessions: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for formation_url in formation_page_urls(site_url):
            try:
                response = await client.get(
                    formation_url, headers={"User-Agent": "ChatbotSaaS-Crawler/1.0"}
                )
            except Exception:
                continue
            if response.status_code >= 400:
                continue
            if not is_html_content_type(response.headers.get("content-type", "")):
                continue
            all_sessions.extend(extract_sessions_from_html(response.text, formation_url))
    return dedupe_sessions(all_sessions)


async def ensure_training_sessions(site_id: str, site_url: str, config: dict) -> list[dict]:
    """Return stored sessions or fetch + persist from formation pages if missing."""
    sessions = config.get("training_sessions") or []
    if len(sessions) >= 3:
        return sessions

    fetched = await fetch_training_sessions_from_site(site_url)
    if fetched:
        save_training_sessions(site_id, fetched)
        return fetched
    return sessions


def save_training_sessions(site_id: str, sessions: list[dict]) -> None:
    if not sessions:
        return

    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    config["training_sessions"] = sessions
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()
