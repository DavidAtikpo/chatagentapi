import logging
import re

import httpx

from app.services.crawler import _chunk_text, _extract_text
from app.services.page_fetcher import PlaywrightSession, fetch_page_html
from app.services.session_extractor import formation_page_urls
from app.services.supabase_client import get_supabase
from app.services.text_quality import filter_text_chunks, is_readable_text

logger = logging.getLogger(__name__)

FORMATION_SPECS = (
    ("cnd_togo", "formation-inspection-tg", "Formation CND / NDT Togo (ISO 9712)"),
    ("cordiste_togo", "formation-togo/formation-cordistes", "Formation Cordiste IRATA Togo"),
    ("cordiste_france", "formation-france/formation-cordistes", "Formation Cordiste IRATA France"),
)

TOPIC_TRIGGERS: dict[str, tuple[str, ...]] = {
    "cnd_togo": (
        "cnd",
        "ndt",
        "inspection",
        "destructif",
        "ultrason",
        "9712",
        "ut me",
        "ut-me",
        "ressuage",
        "radiographie",
        "magnétoscopie",
        "magnetoscopie",
        "courant",
        "foucault",
    ),
    "cordiste_togo": ("cordiste", "irata", "cordes", "hauteur", "corde"),
    "cordiste_france": ("cordiste", "irata", "france", "cordes", "hauteur", "bordeaux", "corde"),
}

PROFILE_SESSION_REGION: dict[str, str] = {
    "cnd_togo": "cnd",
    "cordiste_togo": "togo",
    "cordiste_france": "france",
}


def refresh_formation_profiles(site_id: str) -> int:
    """Extract key excerpts from CI.DES formation pages into agent_config.

    Only applies to CI.DES sites — the FORMATION_SPECS paths are Cides-specific.
    """
    from app.services.site_profiles import is_cides_site

    supabase = get_supabase()
    site_row = supabase.table("sites").select("url, agent_config").eq("id", site_id).single().execute()
    if not site_row.data or not is_cides_site(site_row.data.get("url") or ""):
        return 0

    profiles: list[dict] = []

    for key, path_part, label in FORMATION_SPECS:
        rows = (
            supabase.table("knowledge_chunks")
            .select("title, content, source_url, chunk_index")
            .eq("site_id", site_id)
            .ilike("source_url", f"%{path_part}%")
            .order("chunk_index")
            .limit(3)
            .execute()
        )
        chunks = filter_text_chunks(rows.data or [])
        if not chunks:
            continue

        summary_parts: list[str] = []
        for chunk in chunks[:2]:
            text = re.sub(r"\s+", " ", chunk.get("content", "")).strip()
            if text:
                summary_parts.append(text[:900])

        if not summary_parts:
            continue

        profiles.append(
            {
                "key": key,
                "label": label,
                "url": chunks[0].get("source_url", ""),
                "summary": " ".join(summary_parts)[:2200],
            }
        )

    if not profiles:
        return 0

    config = dict(site_row.data.get("agent_config") or {})
    config["formation_profiles"] = profiles
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()
    logger.info("Formation profiles saved — site_id=%s count=%s", site_id, len(profiles))
    return len(profiles)


def _has_cnd_intent(query: str) -> bool:
    lowered = query.lower()
    return any(trigger in lowered for trigger in TOPIC_TRIGGERS["cnd_togo"])


def _has_cordiste_intent(query: str) -> bool:
    lowered = query.lower()
    return any(t in lowered for t in ("cordiste", "irata", "cordes", "hauteur", "corde"))


def match_formation_profiles(profiles: list[dict], query: str) -> list[dict]:
    if not profiles:
        return []

    lowered = query.lower()

    if _has_cnd_intent(lowered):
        cnd = next((p for p in profiles if p.get("key") == "cnd_togo"), None)
        if cnd:
            return [cnd]

    matched: list[dict] = []
    has_togo_geo = any(t in lowered for t in ("togo", "🇹🇬", "lomé", "lome", "aneho"))
    has_france_geo = any(t in lowered for t in ("france", "🇫🇷", "bordeaux"))

    for profile in profiles:
        key = profile.get("key", "")
        triggers = TOPIC_TRIGGERS.get(key, ())
        if key == "cordiste_france" and has_togo_geo and not has_france_geo:
            continue
        if key == "cordiste_togo" and has_france_geo and not has_togo_geo:
            continue
        if any(trigger in lowered for trigger in triggers):
            matched.append(profile)
            continue
        if key == "cordiste_togo" and has_togo_geo and not _has_cnd_intent(lowered):
            matched.append(profile)
        elif key == "cordiste_france" and has_france_geo and not _has_cnd_intent(lowered):
            matched.append(profile)

    if matched:
        return matched

    if any(t in lowered for t in ("prix", "tarif", "coût", "cout", "combien")):
        if _has_cnd_intent(lowered):
            cnd = next((p for p in profiles if p.get("key") == "cnd_togo"), None)
            if cnd:
                return [cnd]

    return []


def filter_sessions_for_profiles(sessions: list[dict], matched_profiles: list[dict]) -> list[dict]:
    if not matched_profiles or not sessions:
        return sessions

    regions = {
        PROFILE_SESSION_REGION[p["key"]]
        for p in matched_profiles
        if p.get("key") in PROFILE_SESSION_REGION
    }
    if not regions:
        return sessions

    filtered = [s for s in sessions if s.get("region") in regions]
    return filtered or sessions


def format_formation_profiles(profiles: list[dict]) -> str:
    blocks: list[str] = []
    for profile in profiles:
        blocks.append(
            f"### {profile.get('label', 'Formation')}\n"
            f"URL : {profile.get('url', '')}\n"
            f"{profile.get('summary', '')}"
        )
    return "\n\n".join(blocks)


async def ingest_formation_pages(site_id: str, site_url: str) -> int:
    """Fetch formation pages (CND, cordistes…) and store them as knowledge chunks."""
    supabase = get_supabase()
    pages_saved = 0

    playwright = PlaywrightSession()
    await playwright.start()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for page_url in formation_page_urls(site_url):
                try:
                    fetched = await fetch_page_html(
                        page_url, client=client, playwright=playwright
                    )
                except Exception as exc:
                    logger.warning("Formation page fetch failed %s: %s", page_url, exc)
                    continue

                if not fetched or fetched.status_code >= 400:
                    continue

                title, text = _extract_text(fetched.html)
                if not is_readable_text(text, min_len=50):
                    continue

                supabase.table("knowledge_chunks").delete().eq("site_id", site_id).eq("source_url", page_url).execute()
                supabase.table("crawled_pages").upsert(
                    {
                        "site_id": site_id,
                        "url": page_url,
                        "title": title or page_url,
                        "status_code": fetched.status_code,
                    },
                    on_conflict="site_id,url",
                ).execute()

                for index, chunk in enumerate(_chunk_text(text)):
                    supabase.table("knowledge_chunks").insert(
                        {
                            "site_id": site_id,
                            "source_url": page_url,
                            "title": title or page_url,
                            "content": chunk,
                            "chunk_index": index,
                        }
                    ).execute()

                pages_saved += 1
                logger.info("Formation page indexed — %s", page_url)
    finally:
        await playwright.close()

    return pages_saved
