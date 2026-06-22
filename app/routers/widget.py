import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.crawler import _extract_site_image, save_site_image
from app.services.plans import has_pro_features
from app.services.formation_context import refresh_formation_profiles
from app.services.session_dates import filter_upcoming_sessions
from app.services.session_store import ensure_training_sessions
from app.services.site_summary import DEFAULT_WELCOME, ensure_welcome_intro
from app.services.welcome_compose import compose_welcome_message
from app.services.supabase_client import get_supabase

router = APIRouter()


async def ensure_site_image(site_id: str, site_url: str, config: dict) -> str | None:
    """Logo uploadé > image crawlée > fetch homepage og:image once."""
    if config.get("logo_url"):
        return config["logo_url"]
    if config.get("site_image_url"):
        return config["site_image_url"]
    if not site_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(
                site_url, headers={"User-Agent": "ChatbotSaaS-Crawler/1.0"}
            )
            if response.status_code >= 400:
                return None
            image_url = _extract_site_image(response.text, str(response.url))
            if image_url:
                save_site_image(site_id, image_url)
                return image_url
    except Exception:
        pass
    return None


class TrainingSession(BaseModel):
    label: str
    url: str
    region: str = "other"


class WidgetConfigResponse(BaseModel):
    site_id: str
    name: str
    site_url: str = ""
    welcome_message: str
    api_url: str
    cta_url: str | None
    cta_label: str
    whatsapp_number: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    primary_color: str = "#C9922A"
    header_title_color: str = "#ffffff"
    header_font: str = "system-ui, -apple-system, sans-serif"
    logo_url: str | None = None
    welcome_image_url: str | None = None
    training_sessions: list[TrainingSession] = []
    language: str = "fr"


@router.get("/widget/{widget_key}")
async def get_widget_config(widget_key: str):
    supabase = get_supabase()
    site = (
        supabase.table("sites")
        .select("id, name, url, agent_config, whatsapp_number, is_active, organization_id")
        .eq("widget_key", widget_key)
        .single()
        .execute()
    )
    if not site.data or not site.data.get("is_active"):
        raise HTTPException(status_code=404, detail="Widget not found")

    config = dict(site.data.get("agent_config") or {})
    site_url = site.data.get("url") or ""

    all_sessions = await ensure_training_sessions(site.data["id"], site_url, config)
    if all_sessions:
        config["training_sessions"] = all_sessions

    profiles = config.get("formation_profiles") or []
    if not profiles and site_url:
        count = refresh_formation_profiles(site.data["id"])
        if count:
            refreshed = (
                supabase.table("sites")
                .select("agent_config")
                .eq("id", site.data["id"])
                .single()
                .execute()
            )
            config = dict(refreshed.data.get("agent_config") or {})
            profiles = config.get("formation_profiles") or []

    language = config.get("language", "fr")
    if not config.get("welcome_customized"):
        intro = await ensure_welcome_intro(
            site.data["id"],
            site.data["name"],
            site_url,
            config,
            language,
        )
        if intro:
            config["welcome_intro"] = intro

    welcome_image = await ensure_site_image(site.data["id"], site_url, config)
    if welcome_image and not config.get("site_image_url") and not config.get("logo_url"):
        config["site_image_url"] = welcome_image

    upcoming = filter_upcoming_sessions(all_sessions)
    welcome = compose_welcome_message(
        config.get("welcome_message") or "",
        site.data["name"],
        all_sessions,
        profiles,
        welcome_customized=config.get("welcome_customized", False),
        site_url=site_url,
        intro=config.get("welcome_intro") or "",
    )

    contact_whatsapp = (
        site.data.get("whatsapp_number")
        or config.get("contact_whatsapp")
        or config.get("whatsapp_number")
    )

    pro_contacts = False
    org_id = site.data.get("organization_id")
    if org_id:
        org = (
            supabase.table("organizations")
            .select("subscription_plan, subscription_status")
            .eq("id", org_id)
            .single()
            .execute()
        )
        if org.data:
            pro_contacts = has_pro_features(
                org.data.get("subscription_plan"),
                org.data.get("subscription_status"),
            )

    if not pro_contacts:
        contact_whatsapp = None
        contact_phone = None
        contact_email = None
        cta_url = None
    else:
        contact_phone = config.get("contact_phone")
        contact_email = config.get("contact_email")
        cta_url = config.get("cta_url")

    return WidgetConfigResponse(
        site_id=site.data["id"],
        name=site.data["name"],
        site_url=site_url,
        welcome_message=welcome or DEFAULT_WELCOME,
        api_url=settings.api_url,
        cta_url=cta_url,
        cta_label=config.get("cta_label", "S'inscrire"),
        whatsapp_number=contact_whatsapp,
        contact_phone=contact_phone,
        contact_email=contact_email,
        primary_color=config.get("primary_color") or "#C9922A",
        header_title_color=config.get("header_title_color") or "#ffffff",
        header_font=config.get("header_font") or "system-ui, -apple-system, sans-serif",
        logo_url=config.get("logo_url"),
        welcome_image_url=config.get("logo_url") or config.get("site_image_url") or welcome_image,
        training_sessions=upcoming,
        language=config.get("language") or "fr",
    )


VALID_EVENT_TYPES = {"whatsapp", "phone", "email", "signup", "link", "session", "open"}
VALID_PLACEMENTS = {"dock", "assistant", "sessions_bar"}


class WidgetEventRequest(BaseModel):
    widget_key: str
    event_type: str
    placement: str = "assistant"
    label: str | None = None
    target_url: str | None = None
    conversation_id: str | None = None
    page_url: str | None = None
    traffic_slug: str | None = None


def _increment_tracked_link_stats(config: dict, traffic_slug: str | None, event_type: str) -> dict:
    if not traffic_slug:
        return config
    by_link = dict(config.get("tracked_link_interactions") or {})
    link_stats = dict(by_link.get(traffic_slug) or {})
    link_stats[event_type] = int(link_stats.get(event_type) or 0) + 1
    link_stats["_total"] = int(link_stats.get("_total") or 0) + 1
    by_link[traffic_slug] = link_stats
    config["tracked_link_interactions"] = by_link
    return config


@router.post("/widget/event")
async def track_widget_event(payload: WidgetEventRequest):
    if payload.event_type not in VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid event_type")
    if payload.placement not in VALID_PLACEMENTS:
        raise HTTPException(status_code=400, detail="Invalid placement")

    supabase = get_supabase()
    site = (
        supabase.table("sites")
        .select("id, organization_id, is_active, agent_config")
        .eq("widget_key", payload.widget_key)
        .single()
        .execute()
    )
    if not site.data or not site.data.get("is_active"):
        raise HTTPException(status_code=404, detail="Widget not found")

    site_id = site.data["id"]
    config = dict(site.data.get("agent_config") or {})

    is_embed = not payload.traffic_slug

    if is_embed:
        embed = dict(config.get("embed_widget_stats") or {})
        if payload.event_type == "open":
            embed["opens"] = int(embed.get("opens") or 0) + 1
        else:
            clicks = dict(embed.get("clicks") or {})
            clicks[payload.event_type] = int(clicks.get(payload.event_type) or 0) + 1
            embed["clicks"] = clicks
        config["embed_widget_stats"] = embed

        stats = dict(config.get("widget_click_stats") or {})
        if payload.event_type != "open":
            stats[payload.event_type] = int(stats.get(payload.event_type) or 0) + 1
            config["widget_click_stats"] = stats

    if payload.traffic_slug:
        link = (
            supabase.table("traffic_links")
            .select("id")
            .eq("site_id", site_id)
            .eq("slug", payload.traffic_slug)
            .limit(1)
            .execute()
        )
        if link.data:
            config = _increment_tracked_link_stats(config, payload.traffic_slug, payload.event_type)

    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()
    return {"ok": True}


@router.get("/embed/{widget_key}")
async def embed_snippet(widget_key: str):
    snippet = f'<script src="{settings.widget_cdn_url}" data-key="{widget_key}" async></script>'
    return {"snippet": snippet}
