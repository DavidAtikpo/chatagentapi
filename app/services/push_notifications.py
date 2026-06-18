"""Notifications push Firebase Cloud Messaging (HTTP v1)."""

import json
import logging
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from postgrest.exceptions import APIError

from app.config import settings
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _firebase_enabled() -> bool:
    return settings.firebase_enabled


def _credentials():
    info = json.loads(settings.firebase_service_account_json)
    return service_account.Credentials.from_service_account_info(
        info, scopes=[FCM_SCOPE]
    ), info.get("project_id", "")


def _access_token() -> tuple[str, str]:
    creds, project_id = _credentials()
    creds.refresh(Request())
    return creds.token, project_id


def get_org_agent_tokens(organization_id: str, site_id: str | None = None) -> list[str]:
    """Tokens FCM : propriétaire (tous sites) + conseillers du site concerné."""
    supabase = get_supabase()

    org = (
        supabase.table("organizations")
        .select("owner_id")
        .eq("id", organization_id)
        .maybe_single()
        .execute()
    )
    if not org.data:
        return []

    user_ids: set[str] = {org.data["owner_id"]}

    try:
        members = (
            supabase.table("organization_members")
            .select("user_id, site_id, role")
            .eq("organization_id", organization_id)
            .eq("is_available", True)
            .execute()
        )
        member_rows = members.data or []
    except APIError as exc:
        msg = str(exc).lower()
        if "site_id" not in msg and "42703" not in msg:
            raise
        members = (
            supabase.table("organization_members")
            .select("user_id, role")
            .eq("organization_id", organization_id)
            .eq("is_available", True)
            .execute()
        )
        member_rows = [{**r, "site_id": None} for r in (members.data or [])]

    for row in member_rows:
        uid = row.get("user_id")
        if not uid or uid == org.data["owner_id"]:
            continue
        member_site = row.get("site_id")
        role = row.get("role") or "agent"
        if role == "admin" or not member_site:
            user_ids.add(uid)
        elif site_id and member_site == site_id:
            user_ids.add(uid)
        elif not site_id:
            user_ids.add(uid)

    if not user_ids:
        return []

    tokens = (
        supabase.table("agent_device_tokens")
        .select("token")
        .in_("user_id", list(user_ids))
        .execute()
    )
    return list({row["token"] for row in (tokens.data or []) if row.get("token")})


async def send_push_to_token(
    token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> bool:
    if not _firebase_enabled():
        logger.debug("FCM disabled — no FIREBASE_SERVICE_ACCOUNT_JSON")
        return False

    try:
        access_token, project_id = _access_token()
    except Exception as exc:
        logger.warning("FCM auth failed: %s", exc)
        return False

    if not project_id:
        logger.warning("FCM: project_id missing in service account JSON")
        return False

    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in (data or {}).items()},
            "android": {
                "priority": "HIGH",
                "notification": {
                    "channel_id": "handoff_urgent",
                    "sound": "default",
                    "default_vibrate_timings": True,
                    "notification_priority": "PRIORITY_MAX",
                    "visibility": "PUBLIC",
                },
            },
            "apns": {
                "payload": {
                    "aps": {
                        "sound": "default",
                        "badge": 1,
                        "interruption-level": "time-sensitive",
                    }
                }
            },
        }
    }

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if res.status_code >= 400:
            logger.warning("FCM send failed (%s): %s", res.status_code, res.text[:300])
            return False
        return True


async def notify_org_handoff(
    organization_id: str,
    title: str,
    body: str,
    data: dict[str, Any],
    site_id: str | None = None,
) -> int:
    """Envoie une push aux conseillers concernés par le site. Retourne le nb de succès."""
    tokens = get_org_agent_tokens(organization_id, site_id=site_id)
    if not tokens:
        logger.info("No FCM tokens for org %s", organization_id)
        return 0

    sent = 0
    for token in tokens:
        ok = await send_push_to_token(token, title, body, data)
        if ok:
            sent += 1
    logger.info("FCM handoff: %s/%s sent for org %s", sent, len(tokens), organization_id)
    return sent
