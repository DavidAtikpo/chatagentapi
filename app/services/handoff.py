"""Human Handoff — détection, notification agents, gestion d'état."""

import asyncio
import logging
import re
from datetime import datetime, timezone

from postgrest.exceptions import APIError

from app.services.push_notifications import notify_org_handoff
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

HUMAN_REQUEST_RE = re.compile(
    r"(parler\s+(à|a)\s+(un\s+)?(humain|conseiller|personne|réel|vraie\s+personne)"
    r"|humain|conseiller|agent\s+(humain|réel)"
    r"|talk\s+to\s+(a\s+)?human|real\s+person|speak\s+to\s+someone"
    r"|je\s+veux\s+(un\s+)?conseiller|besoin\s+d['']un\s+humain)",
    re.IGNORECASE,
)

HANDOFF_MARKER_RE = re.compile(r"<!--HANDOFF:([a-z_]+)-->")


def detect_handoff_reason(user_message: str, lead_score: int = 0) -> str | None:
    if HUMAN_REQUEST_RE.search(user_message):
        return "user_request"
    if lead_score >= 80:
        return "hot_lead"
    return None


def extract_handoff_marker(text: str) -> tuple[str, str | None]:
    match = HANDOFF_MARKER_RE.search(text)
    if not match:
        return text, None
    clean = text[: match.start()].rstrip()
    return clean, match.group(1)


def get_conversation_handoff(conversation_id: str) -> dict | None:
    supabase = get_supabase()
    result = (
        supabase.table("conversations")
        .select("id, handoff_status, assigned_agent_id, handoff_reason, site_id, lead_score")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    return result.data


def is_handoff_active(status: str | None) -> bool:
    return status in ("requested", "active")


def request_handoff(
    conversation_id: str,
    organization_id: str,
    reason: str,
    site_name: str = "Chat",
) -> None:
    supabase = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    supabase.table("conversations").update(
        {
            "handoff_status": "requested",
            "handoff_reason": reason,
            "handoff_requested_at": now,
            "status": "handed_off",
        }
    ).eq("id", conversation_id).execute()

    reason_labels = {
        "user_request": "Le visiteur demande un conseiller",
        "hot_lead": "Lead très chaud",
        "ai_escalation": "L'IA ne peut pas répondre",
    }
    label = reason_labels.get(reason, "Passage conseiller")

    supabase.table("notifications").insert(
        {
            "organization_id": organization_id,
            "type": "handoff_request",
            "title": f"🙋 {label}",
            "body": f"Conversation sur {site_name} — répondez depuis l'app mobile",
            "data": {
                "conversation_id": conversation_id,
                "reason": reason,
            },
        }
    ).execute()

    logger.info("Handoff requested conv=%s reason=%s", conversation_id, reason)

    # Push Firebase (fire-and-forget depuis le flux async chat)
    try:
        asyncio.create_task(
            notify_org_handoff(
                organization_id,
                title=f"🙋 {label}",
                body=f"Conversation sur {site_name} — ouvrez l'app conseiller",
                data={
                    "type": "handoff_request",
                    "conversation_id": conversation_id,
                    "reason": reason,
                },
            )
        )
    except RuntimeError as exc:
        logger.warning("FCM handoff notify skipped: %s", exc)


def claim_handoff(conversation_id: str, agent_user_id: str) -> dict:
    supabase = get_supabase()
    conv = (
        supabase.table("conversations")
        .select("handoff_status, assigned_agent_id")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        raise ValueError("Conversation not found")

    status = conv.data.get("handoff_status")
    if status not in ("requested", "active"):
        raise ValueError("Handoff not available")

    assigned = conv.data.get("assigned_agent_id")
    if assigned and assigned != agent_user_id:
        raise ValueError("Already assigned to another agent")

    supabase.table("conversations").update(
        {
            "handoff_status": "active",
            "assigned_agent_id": agent_user_id,
            "status": "handed_off",
        }
    ).eq("id", conversation_id).execute()

    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "Un conseiller a rejoint la conversation. Comment puis-je vous aider ?",
        }
    ).execute()

    return {"handoff_status": "active", "assigned_agent_id": agent_user_id}


def release_handoff(conversation_id: str) -> None:
    supabase = get_supabase()
    supabase.table("conversations").update(
        {
            "handoff_status": "returned",
            "assigned_agent_id": None,
            "status": "active",
        }
    ).eq("id", conversation_id).execute()

    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "Le conseiller a quitté la conversation. Je reprends — comment puis-je vous aider ?",
        }
    ).execute()


def insert_human_message(conversation_id: str, content: str, agent_name: str | None = None) -> dict:
    supabase = get_supabase()
    prefix = f"**{agent_name}** : " if agent_name else ""
    try:
        row = (
            supabase.table("messages")
            .insert(
                {
                    "conversation_id": conversation_id,
                    "role": "human",
                    "content": prefix + content if prefix else content,
                }
            )
            .execute()
        )
    except APIError as exc:
        msg = str(exc)
        if "human" in msg.lower() or "check constraint" in msg.lower():
            raise ValueError(
                "Rôle 'human' non autorisé — exécutez 010_messages_human_role.sql dans Supabase."
            ) from exc
        raise ValueError(msg) from exc

    supabase.table("conversations").update({"updated_at": "now()"}).eq(
        "id", conversation_id
    ).execute()
    return row.data[0] if row.data else {}


def insert_user_message_only(conversation_id: str, content: str) -> None:
    supabase = get_supabase()
    supabase.table("messages").insert(
        {"conversation_id": conversation_id, "role": "user", "content": content}
    ).execute()
    supabase.table("conversations").update({"updated_at": "now()"}).eq(
        "id", conversation_id
    ).execute()
