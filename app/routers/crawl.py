import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, HttpUrl

from app.services.crawler import crawl_site, save_site_image
from app.services.crawl_progress import complete_progress, fail_progress, get_progress
from app.services.formation_context import ingest_formation_pages, refresh_formation_profiles
from app.services.rag import embed_site_chunks
from app.services.session_extractor import (
    dedupe_sessions,
    extract_sessions_from_html,
    formation_page_urls,
)
from app.services.session_store import save_training_sessions
from app.services.site_summary import (
    DEFAULT_WELCOME,
    refresh_welcome_after_crawl,
    save_composed_welcome,
)
from app.services.supabase_client import get_supabase
from app.services.text_quality import is_html_content_type

router = APIRouter()
logger = logging.getLogger(__name__)


class CrawlRequest(BaseModel):
    site_id: str


class CrawlFromUrlRequest(BaseModel):
    organization_id: str
    name: str
    url: HttpUrl


async def _crawl_and_embed(site_id: str, url: str):
    supabase = get_supabase()
    try:
        logger.info("Crawl démarré — site_id=%s url=%s", site_id, url)
        result = await crawl_site(site_id, url)
        pages = result.get("pages_crawled", 0)
        sessions = result.get("sessions", [])
        save_site_image(site_id, result.get("site_image_url"))
        logger.info("Crawl terminé — %s page(s), %s session(s) trouvée(s)", pages, len(sessions))
        save_training_sessions(site_id, sessions)
        embedded = await embed_site_chunks(site_id)
        refresh_formation_profiles(site_id)
        logger.info("Embeddings terminés — %s chunk(s) pour site_id=%s", embedded, site_id)

        site_row = supabase.table("sites").select("name, url, agent_config").eq("id", site_id).single().execute()
        site_name = site_row.data.get("name", "l'entreprise")
        config = dict(site_row.data.get("agent_config") or {})
        language = config.get("language", "fr")
        await refresh_welcome_after_crawl(site_id, site_name, url, config, language)
        logger.info("Message d'accueil généré pour site_id=%s", site_id)

        complete_progress(site_id, pages, embedded)
        supabase.table("sites").update(
            {"crawl_status": "completed", "last_crawled_at": "now()"}
        ).eq("id", site_id).execute()
    except Exception:
        logger.exception("Crawl échoué — site_id=%s", site_id)
        fail_progress(site_id)
        supabase.table("sites").update({"crawl_status": "failed"}).eq("id", site_id).execute()


@router.get("/crawl/{site_id}/progress")
async def crawl_progress(site_id: str):
    progress = get_progress(site_id)
    if progress:
        return progress

    supabase = get_supabase()
    site = (
        supabase.table("sites")
        .select("crawl_status")
        .eq("id", site_id)
        .single()
        .execute()
    )
    if not site.data:
        raise HTTPException(status_code=404, detail="Site not found")

    status = site.data.get("crawl_status", "pending")
    return {
        "status": status,
        "phase": "done" if status == "completed" else "crawling",
        "pages_done": 0,
        "pages_total": 0,
        "chunks_done": 0,
        "chunks_total": 0,
        "message": f"Crawl : {status}",
        "updated_at": None,
    }


@router.post("/crawl/{site_id}/formations")
async def refresh_formations(site_id: str):
    """Indexe les pages formation (CND, cordistes…) sans re-crawl complet du site."""
    supabase = get_supabase()
    site = supabase.table("sites").select("id, url").eq("id", site_id).single().execute()
    if not site.data:
        raise HTTPException(status_code=404, detail="Site not found")

    pages = await ingest_formation_pages(site_id, site.data["url"])
    embedded = await embed_site_chunks(site_id)
    profile_count = refresh_formation_profiles(site_id)
    site_row = supabase.table("sites").select("name, url, agent_config").eq("id", site_id).single().execute()
    save_composed_welcome(
        site_id, site_row.data["name"], site_row.data.get("url") or "", dict(site_row.data.get("agent_config") or {})
    )
    logger.info(
        "Formations indexées — site_id=%s pages=%s embeddings=%s profiles=%s",
        site_id,
        pages,
        embedded,
        profile_count,
    )
    return {
        "site_id": site_id,
        "pages_indexed": pages,
        "embeddings": embedded,
        "formation_profiles": profile_count,
    }


@router.post("/crawl/{site_id}/sessions")
async def refresh_sessions(site_id: str):
    """Re-extrait les liens d'inscription depuis les pages formation (sans re-crawl complet)."""
    supabase = get_supabase()
    site = supabase.table("sites").select("id, url").eq("id", site_id).single().execute()
    if not site.data:
        raise HTTPException(status_code=404, detail="Site not found")

    all_sessions: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for formation_url in formation_page_urls(site.data["url"]):
            response = await client.get(
                formation_url, headers={"User-Agent": "ChatbotSaaS-Crawler/1.0"}
            )
            if response.status_code >= 400:
                continue
            if not is_html_content_type(response.headers.get("content-type", "")):
                continue
            all_sessions.extend(extract_sessions_from_html(response.text, formation_url))

    sessions = dedupe_sessions(all_sessions)
    save_training_sessions(site_id, sessions)
    profile_count = refresh_formation_profiles(site_id)
    site_row = supabase.table("sites").select("name, url, agent_config").eq("id", site_id).single().execute()
    save_composed_welcome(
        site_id, site_row.data["name"], site_row.data.get("url") or "", dict(site_row.data.get("agent_config") or {})
    )
    logger.info(
        "Sessions rafraîchies — site_id=%s count=%s profiles=%s",
        site_id,
        len(sessions),
        profile_count,
    )
    return {
        "site_id": site_id,
        "sessions_count": len(sessions),
        "formation_profiles": profile_count,
        "sessions": sessions,
    }


@router.post("/crawl")
async def trigger_crawl(payload: CrawlRequest, background_tasks: BackgroundTasks):
    supabase = get_supabase()
    site = supabase.table("sites").select("id, url").eq("id", payload.site_id).single().execute()
    if not site.data:
        raise HTTPException(status_code=404, detail="Site not found")

    logger.info("Requête crawl reçue — site_id=%s url=%s", payload.site_id, site.data["url"])
    background_tasks.add_task(_crawl_and_embed, site.data["id"], site.data["url"])
    return {"status": "started", "site_id": payload.site_id}


@router.post("/sites")
async def create_site_and_crawl(payload: CrawlFromUrlRequest, background_tasks: BackgroundTasks):
    supabase = get_supabase()
    result = (
        supabase.table("sites")
        .insert(
            {
                "organization_id": payload.organization_id,
                "name": payload.name,
                "url": str(payload.url),
                "crawl_status": "pending",
            }
        )
        .execute()
    )
    site = result.data[0]
    background_tasks.add_task(_crawl_and_embed, site["id"], site["url"])
    return {"site": site, "crawl_status": "started"}
