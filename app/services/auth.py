"""Vérification JWT Supabase pour l'app mobile agent."""

import logging
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


@dataclass
class AgentUser:
    user_id: str
    email: str | None


def _decode_supabase_jwt(token: str) -> dict:
    if not settings.supabase_jwt_secret:
        raise HTTPException(status_code=503, detail="JWT secret not configured")
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        logger.warning("Invalid JWT: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


async def get_current_agent(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AgentUser:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authorization required")
    payload = _decode_supabase_jwt(creds.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return AgentUser(user_id=sub, email=payload.get("email"))


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
