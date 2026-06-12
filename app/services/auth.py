"""Vérification JWT Supabase pour l'app mobile agent."""

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


@dataclass
class AgentUser:
    user_id: str
    email: str | None


def _verify_agent_token(token: str) -> AgentUser:
    """Valide le token via Supabase Auth (utilise SUPABASE_SERVICE_KEY déjà configurée)."""
    supabase = get_supabase()
    try:
        response = supabase.auth.get_user(token)
    except Exception as exc:
        logger.warning("Supabase get_user failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user = getattr(response, "user", None)
    if not user or not getattr(user, "id", None):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return AgentUser(user_id=str(user.id), email=getattr(user, "email", None))


async def get_current_agent(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AgentUser:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization required")
    return _verify_agent_token(creds.credentials)


def user_organization_ids(user_id: str) -> list[str]:
    """Organisations accessibles : propriétaire ou membre agent."""
    supabase = get_supabase()
    owned = (
        supabase.table("organizations")
        .select("id")
        .eq("owner_id", user_id)
        .execute()
    )
    member = (
        supabase.table("organization_members")
        .select("organization_id")
        .eq("user_id", user_id)
        .execute()
    )
    ids: set[str] = set()
    for row in owned.data or []:
        ids.add(row["id"])
    for row in member.data or []:
        ids.add(row["organization_id"])
    return list(ids)


def is_organization_owner(user_id: str) -> bool:
    """Propriétaire du compte (client principal) — 1 org, N sites."""
    supabase = get_supabase()
    rows = (
        supabase.table("organizations")
        .select("id")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(rows.data)


def assert_agent_can_access_conversation(user_id: str, conversation_id: str) -> dict:
    """Retourne la conversation si l'agent y a accès, sinon 404."""
    supabase = get_supabase()
    conv = (
        supabase.table("conversations")
        .select("*, sites(id, name, organization_id)")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        raise HTTPException(status_code=404, detail="Conversation not found")

    org_id = (conv.data.get("sites") or {}).get("organization_id")
    if not org_id or org_id not in user_organization_ids(user_id):
        raise HTTPException(status_code=403, detail="Access denied")
    return conv.data
