"""Routes widget pour polling handoff (messages humains en temps quasi-réel)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.supabase_client import get_supabase

router = APIRouter(prefix="/widget", tags=["widget-handoff"])


class HandoffStatusResponse(BaseModel):
    conversation_id: str
    handoff_status: str
    assigned: bool


def _verify_widget_conversation(widget_key: str, conversation_id: str) -> dict:
    supabase = get_supabase()
    site = (
        supabase.table("sites")
        .select("id, is_active")
        .eq("widget_key", widget_key)
        .maybe_single()
        .execute()
    )
    if not site.data or not site.data.get("is_active"):
        raise HTTPException(status_code=404, detail="Widget not found")

    conv = (
        supabase.table("conversations")
        .select("id, site_id, handoff_status, assigned_agent_id")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not conv.data or conv.data["site_id"] != site.data["id"]:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.data


@router.get("/conversation/{conversation_id}/status")
async def conversation_handoff_status(conversation_id: str, widget_key: str = Query(...)):
    conv = _verify_widget_conversation(widget_key, conversation_id)
    status = conv.get("handoff_status") or "none"
    return HandoffStatusResponse(
        conversation_id=conversation_id,
        handoff_status=status,
        assigned=status == "active",
    )


@router.get("/conversation/{conversation_id}/messages")
async def poll_conversation_messages(
    conversation_id: str,
    widget_key: str = Query(...),
    after: str | None = Query(None, description="ISO timestamp — messages après cette date"),
):
    _verify_widget_conversation(widget_key, conversation_id)
    supabase = get_supabase()

    query = (
        supabase.table("messages")
        .select("id, role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
    )
    if after:
        try:
            datetime.fromisoformat(after.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid after timestamp") from exc
        query = query.gt("created_at", after)

    rows = query.execute()
    return {"messages": rows.data or []}
