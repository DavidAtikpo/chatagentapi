"""API agent mobile — inbox, réponses humaines, handoff."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.auth import (
    AgentUser,
    assert_agent_can_access_conversation,
    get_current_agent,
    is_organization_owner,
    user_accessible_site_ids,
    user_organization_ids,
    _member_rows_for_user,
)
from app.services.handoff import claim_handoff, insert_human_message, release_handoff
from app.services.owner_stats import fetch_owner_stats
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class DeviceTokenRequest(BaseModel):
    token: str = Field(min_length=10)
    platform: str = Field(pattern="^(android|ios|web)$")


@router.get("/owner-stats")
async def owner_stats(agent: AgentUser = Depends(get_current_agent)):
    """Tableau de bord mobile — réservé au propriétaire du compte."""
    data = fetch_owner_stats(agent.user_id)
    if not data:
        raise HTTPException(
            status_code=403,
            detail="Réservé au propriétaire du compte (client principal)",
        )
    return data


@router.get("/profile")
async def agent_profile(agent: AgentUser = Depends(get_current_agent)):
    """Profil agent : rôle, site assigné, accès stats."""
    supabase = get_supabase()
    is_owner = is_organization_owner(agent.user_id)

    rows = _member_rows_for_user(agent.user_id)
    row = rows[0] if rows else None
    site_name = None
    site_id = None
    role = "owner" if is_owner else "agent"
    if row:
        role = row.get("role") or role
        site_id = row.get("site_id")
        if site_id:
            site_row = (
                supabase.table("sites")
                .select("name")
                .eq("id", site_id)
                .maybe_single()
                .execute()
            )
            if site_row.data:
                site_name = site_row.data.get("name")

    site_ids = user_accessible_site_ids(agent.user_id)
    return {
        "email": agent.email,
        "is_owner": is_owner,
        "role": role,
        "assigned_site_id": site_id,
        "assigned_site_name": site_name,
        "accessible_sites_count": len(site_ids),
    }


@router.get("/inbox")
async def agent_inbox(agent: AgentUser = Depends(get_current_agent)):
    site_ids = user_accessible_site_ids(agent.user_id)
    if not site_ids:
        return {"pending": [], "active": [], "pool_active": []}

    supabase = get_supabase()
    sites = supabase.table("sites").select("id, name").in_("id", site_ids).execute()
    site_names = {s["id"]: s["name"] for s in (sites.data or [])}

    pending = (
        supabase.table("conversations")
        .select("id, site_id, handoff_status, handoff_reason, handoff_requested_at, lead_score, page_url, updated_at")
        .in_("site_id", site_ids)
        .eq("handoff_status", "requested")
        .order("handoff_requested_at", desc=True)
        .limit(50)
        .execute()
    )

    active = (
        supabase.table("conversations")
        .select("id, site_id, handoff_status, handoff_reason, assigned_agent_id, lead_score, page_url, updated_at")
        .in_("site_id", site_ids)
        .eq("handoff_status", "active")
        .order("updated_at", desc=True)
        .limit(50)
        .execute()
    )

    def enrich(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            out.append({**row, "site_name": site_names.get(row["site_id"], "Site")})
        return out

    my_active = [r for r in (active.data or []) if r.get("assigned_agent_id") == agent.user_id]
    pool_active = [r for r in (active.data or []) if r.get("assigned_agent_id") != agent.user_id]

    return {
        "pending": enrich(pending.data or []),
        "active": enrich(my_active),
        "pool_active": enrich(pool_active),
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    agent: AgentUser = Depends(get_current_agent),
):
    conv = assert_agent_can_access_conversation(agent.user_id, conversation_id)
    supabase = get_supabase()
    messages = (
        supabase.table("messages")
        .select("id, role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return {
        "conversation": conv,
        "messages": messages.data or [],
    }


@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation_id: str,
    agent: AgentUser = Depends(get_current_agent),
):
    assert_agent_can_access_conversation(agent.user_id, conversation_id)
    try:
        result = claim_handoff(conversation_id, agent.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.post("/conversations/{conversation_id}/messages")
async def send_agent_message(
    conversation_id: str,
    payload: AgentMessageRequest,
    agent: AgentUser = Depends(get_current_agent),
):
    conv = assert_agent_can_access_conversation(agent.user_id, conversation_id)
    if conv.get("handoff_status") != "active":
        raise HTTPException(status_code=409, detail="Claim the conversation first")
    if conv.get("assigned_agent_id") != agent.user_id:
        raise HTTPException(status_code=403, detail="Assigned to another agent")

    try:
        msg = insert_human_message(conversation_id, payload.content.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("send_agent_message failed conv=%s", conversation_id)
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le message") from exc
    return {"message": msg}


@router.post("/conversations/{conversation_id}/release")
async def release_conversation(
    conversation_id: str,
    agent: AgentUser = Depends(get_current_agent),
):
    conv = assert_agent_can_access_conversation(agent.user_id, conversation_id)
    if conv.get("assigned_agent_id") and conv.get("assigned_agent_id") != agent.user_id:
        raise HTTPException(status_code=403, detail="Assigned to another agent")
    release_handoff(conversation_id)
    return {"handoff_status": "returned"}


@router.post("/device-token")
async def register_device_token(
    payload: DeviceTokenRequest,
    agent: AgentUser = Depends(get_current_agent),
):
    supabase = get_supabase()
    supabase.table("agent_device_tokens").upsert(
        {
            "user_id": agent.user_id,
            "token": payload.token,
            "platform": payload.platform,
            "updated_at": datetime.utcnow().isoformat(),
        },
        on_conflict="user_id,token",
    ).execute()
    return {"ok": True}


@router.get("/notifications")
async def list_notifications(
    agent: AgentUser = Depends(get_current_agent),
    limit: int = 30,
):
    org_ids = user_organization_ids(agent.user_id)
    if not org_ids:
        return {"notifications": []}

    supabase = get_supabase()
    rows = (
        supabase.table("notifications")
        .select("id, type, title, body, data, created_at")
        .in_("organization_id", org_ids)
        .order("created_at", desc=True)
        .limit(min(limit, 100))
        .execute()
    )
    return {"notifications": rows.data or []}
