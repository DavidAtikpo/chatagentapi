"""API agent mobile — inbox, réponses humaines, handoff."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.auth import (
    AgentUser,
    assert_agent_can_access_conversation,
    get_current_agent,
    user_organization_ids,
)
from app.services.handoff import claim_handoff, insert_human_message, release_handoff
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class DeviceTokenRequest(BaseModel):
    token: str = Field(min_length=10)
    platform: str = Field(pattern="^(android|ios|web)$")


@router.get("/inbox")
async def agent_inbox(agent: AgentUser = Depends(get_current_agent)):
    org_ids = user_organization_ids(agent.user_id)
    if not org_ids:
        return {"pending": [], "active": []}

    supabase = get_supabase()
    sites = supabase.table("sites").select("id, name").in_("organization_id", org_ids).execute()
    site_ids = [s["id"] for s in (sites.data or [])]
    site_names = {s["id"]: s["name"] for s in (sites.data or [])}

    if not site_ids:
        return {"pending": [], "active": []}

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

    supabase = get_supabase()
    member = (
        supabase.table("organization_members")
        .select("display_name")
        .eq("user_id", agent.user_id)
        .limit(1)
        .execute()
    )
    name = (member.data[0]["display_name"] if member.data else None) or "Conseiller"

    try:
        msg = insert_human_message(conversation_id, payload.content.strip(), name)
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
