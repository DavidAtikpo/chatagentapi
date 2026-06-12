import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.services.chat import stream_chat
from app.services.country_utils import COUNTRY_ALIASES, _country_key
from app.services.handoff import get_conversation_handoff
from app.services.plans import has_pro_features
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

# Headers added automatically by hosting providers (Vercel, Cloudflare, AWS, etc.)
_IP_COUNTRY_HEADERS = [
    "x-vercel-ip-country",   # Vercel
    "cf-ipcountry",           # Cloudflare
    "x-country-code",         # Generic / Railway custom header
    "cloudfront-viewer-country",  # AWS CloudFront
    "x-appengine-country",    # Google App Engine
]


def _country_from_request(request: Request) -> str | None:
    """Return a canonical country name from hosting-provider IP headers, or None."""
    for header in _IP_COUNTRY_HEADERS:
        code = request.headers.get(header, "").strip().upper()
        if code and code not in ("", "XX", "T1", "ZZ"):  # XX/T1/ZZ = unknown/Tor/reserved
            label = COUNTRY_ALIASES.get(_country_key(code))
            if label:
                return label
    return None


class ChatRequest(BaseModel):
    widget_key: str
    message: str
    conversation_id: str | None = None
    visitor_fingerprint: str | None = None
    page_url: str | None = None
    traffic_slug: str | None = None


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request):
    supabase = get_supabase()
    ip_country = _country_from_request(request)
    site = (
        supabase.table("sites")
        .select("id, name, url, agent_config, organization_id, is_active, whatsapp_number")
        .eq("widget_key", payload.widget_key)
        .single()
        .execute()
    )
    if not site.data or not site.data.get("is_active"):
        raise HTTPException(status_code=404, detail="Widget not found")

    site_id = site.data["id"]
    org_id = site.data.get("organization_id")
    pro_contacts = False
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
    site.data["pro_contacts"] = pro_contacts

    # Sessions are loaded at crawl/widget init — never re-fetch the live site per message.
    visitor_id = None

    if payload.visitor_fingerprint:
        visitor = (
            supabase.table("visitors")
            .upsert(
                {
                    "site_id": site_id,
                    "fingerprint": payload.visitor_fingerprint,
                    "last_seen_at": "now()",
                },
                on_conflict="site_id,fingerprint",
            )
            .execute()
        )
        visitor_id = visitor.data[0]["id"] if visitor.data else None

    traffic_link_id = None
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
            traffic_link_id = link.data[0]["id"]
            current = (
                supabase.table("traffic_links")
                .select("click_count")
                .eq("id", traffic_link_id)
                .single()
                .execute()
            )
            if current.data:
                supabase.table("traffic_links").update(
                    {"click_count": (current.data.get("click_count") or 0) + 1}
                ).eq("id", traffic_link_id).execute()

    conversation_id = payload.conversation_id
    if not conversation_id:
        new_conv: dict = {
            "site_id": site_id,
            "visitor_id": visitor_id,
            "traffic_link_id": traffic_link_id,
            "page_url": payload.page_url,
        }
        if ip_country:
            new_conv["qualification_data"] = {"country": ip_country}
        conv = supabase.table("conversations").insert(new_conv).execute()
        conversation_id = conv.data[0]["id"]
    elif ip_country:
        # Existing conversation: seed country from IP if not yet known
        existing = (
            supabase.table("conversations")
            .select("qualification_data")
            .eq("id", conversation_id)
            .maybe_single()
            .execute()
        )
        prior_qd: dict = (existing.data or {}).get("qualification_data") or {}
        if not prior_qd.get("country"):
            supabase.table("conversations").update(
                {"qualification_data": {**prior_qd, "country": ip_country}}
            ).eq("id", conversation_id).execute()

    async def event_generator():
        try:
            async for token in stream_chat(
                site_id, conversation_id, payload.message, site.data, ip_country=ip_country
            ):
                yield {"event": "token", "data": token}
        except Exception:
            logger.exception("Chat stream failed for conversation %s", conversation_id)
            yield {
                "event": "token",
                "data": "Désolé, une erreur est survenue. Réessayez dans un instant.",
            }
        hs = (get_conversation_handoff(conversation_id) or {}).get("handoff_status", "none")
        if hs in ("requested", "active"):
            yield {"event": "handoff", "data": hs}
        yield {"event": "done", "data": conversation_id}

    return EventSourceResponse(event_generator())
