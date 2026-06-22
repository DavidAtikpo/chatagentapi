"""Human Handoff — détection, notification agents, gestion d'état."""

import asyncio
import logging
import re
from datetime import datetime, timezone

from postgrest.exceptions import APIError

from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

HANDOFF_REASSURANCE_DELAY_SECONDS = 45

HUMAN_REQUEST_RE = re.compile(
    r"(parler\s+(à|a|avec)\s+(un\s+)?(humain|conseiller|personne|réel|vraie\s+personne|quelqu['']un)"
    r"|(?:mis|mettre)\s+en\s+relation"
    r"|humain|conseiller|agent\s+(humain|réel)"
    r"|talk\s+to\s+(a\s+)?human|real\s+person|speak\s+to\s+someone"
    r"|je\s+veux\s+(un\s+)?conseiller|besoin\s+d['']un\s+humain"
    r"|(?:contacter|joindre)\s+(un\s+)?(conseiller|humain|(?:votre\s+)?équipe))",
    re.IGNORECASE,
)

SIMPLE_GREETING_RE = re.compile(
    r"^(bonjour|bonsoir|salut|hello|hi|hey|coucou|bonne journée|good morning|good evening)[\s!.?,]*$",
    re.IGNORECASE,
)

HANDOFF_MARKER_RE = re.compile(r"<!--HANDOFF:([a-z_]+)-->")


def detect_handoff_reason(user_message: str, lead_score: int = 0) -> str | None:
    if HUMAN_REQUEST_RE.search(user_message):
        return "user_request"
    if lead_score >= 80:
        return "hot_lead"
    return None


def is_simple_greeting(user_message: str) -> bool:
    return bool(SIMPLE_GREETING_RE.match(user_message.strip()))


def resolve_handoff_reason(
    user_message: str,
    handoff_marker: str | None,
    lead_score: int = 0,
) -> str | None:
    """Déclenche le handoff depuis le message visiteur ou le marqueur IA (sauf simple salutation)."""
    detected = detect_handoff_reason(user_message, lead_score)
    if detected:
        return detected
    if handoff_marker == "user_request" and not is_simple_greeting(user_message):
        return "user_request"
    if handoff_marker in ("ai_escalation", "hot_lead"):
        return handoff_marker
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
        .select(
            "id, handoff_status, assigned_agent_id, handoff_reason, "
            "site_id, lead_score, handoff_reassured_at"
        )
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    return result.data


def is_handoff_active(status: str | None) -> bool:
    return status in ("requested", "active")


def handoff_blocks_ai(handoff: dict | None) -> bool:
    """Bloque l'IA seulement si conseiller actif, ou attente sans message de patience."""
    if not handoff:
        return False
    status = handoff.get("handoff_status")
    if status == "active":
        return True
    if status == "requested" and not handoff.get("handoff_reassured_at"):
        return True
    return False


def _agent_intro_message(display_name: str | None, site_name: str) -> str:
    name = (display_name or "").strip() or "votre conseiller"
    site = (site_name or "").strip() or "notre équipe"
    return (
        f"Bonjour ! 👋\n\n"
        f"Je suis **{name}**, conseiller·ère pour **{site}**. "
        f"Je prends le relais avec plaisir — dites-moi comment je peux vous aider, "
        f"je suis là pour vous accompagner."
    )


async def schedule_handoff_reassurance(conversation_id: str) -> None:
    """Après un délai sans claim : active le bandeau header widget (handoff_reassured_at)."""
    await asyncio.sleep(HANDOFF_REASSURANCE_DELAY_SECONDS)
    supabase = get_supabase()
    conv = (
        supabase.table("conversations")
        .select("handoff_status, handoff_reassured_at")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return
    if conv.data.get("handoff_status") != "requested":
        return
    if conv.data.get("handoff_reassured_at"):
        return

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("conversations").update(
        {"handoff_reassured_at": now, "updated_at": now}
    ).eq("id", conversation_id).execute()
    logger.info("Handoff reassurance (header) conv=%s", conversation_id)


def request_handoff(
    conversation_id: str,
    organization_id: str,
    reason: str,
    site_name: str = "Chat",
    site_id: str | None = None,
) -> dict | None:
    supabase = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    supabase.table("conversations").update(
        {
            "handoff_status": "requested",
            "handoff_reason": reason,
            "handoff_requested_at": now,
            "handoff_reassured_at": None,
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

    return {
        "organization_id": organization_id,
        "site_id": site_id,
        "conversation_id": conversation_id,
        "reason": reason,
        "title": f"🙋 {label}",
        "body": f"Conversation sur {site_name} — ouvrez l'app conseiller",
    }


def claim_handoff(conversation_id: str, agent_user_id: str) -> dict:
    supabase = get_supabase()
    conv = (
        supabase.table("conversations")
        .select("handoff_status, assigned_agent_id, site_id")
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

    site_name = "notre équipe"
    display_name: str | None = None
    site_id = conv.data.get("site_id")
    if site_id:
        site_row = (
            supabase.table("sites")
            .select("name, organization_id")
            .eq("id", site_id)
            .maybe_single()
            .execute()
        )
        if site_row.data:
            site_name = site_row.data.get("name") or site_name
            org_id = site_row.data.get("organization_id")
            if org_id:
                member = (
                    supabase.table("organization_members")
                    .select("display_name")
                    .eq("organization_id", org_id)
                    .eq("user_id", agent_user_id)
                    .maybe_single()
                    .execute()
                )
                if member.data:
                    display_name = member.data.get("display_name")

    supabase.table("conversations").update(
        {
            "handoff_status": "active",
            "assigned_agent_id": agent_user_id,
            "handoff_reassured_at": None,
            "status": "handed_off",
        }
    ).eq("id", conversation_id).execute()

    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "role": "human",
            "content": _agent_intro_message(display_name, site_name),
        }
    ).execute()

    return {"handoff_status": "active", "assigned_agent_id": agent_user_id}


def release_handoff(conversation_id: str, *, visitor_return: bool = False) -> None:
    supabase = get_supabase()
    supabase.table("conversations").update(
        {
            "handoff_status": "returned",
            "assigned_agent_id": None,
            "status": "active",
        }
    ).eq("id", conversation_id).execute()

    if visitor_return:
        content = (
            "Très bien, je reprends la conversation avec vous. "
            "Comment puis-je vous aider ?"
        )
    else:
        content = (
            "Le conseiller a quitté la conversation. "
            "Je reprends — comment puis-je vous aider ?"
        )

    supabase.table("messages").insert(
        {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": content,
        }
    ).execute()


def insert_human_message(conversation_id: str, content: str) -> dict:
    supabase = get_supabase()
    try:
        row = (
            supabase.table("messages")
            .insert(
                {
                    "conversation_id": conversation_id,
                    "role": "human",
                    "content": content,
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
