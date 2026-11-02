"""Désactivation automatique des profils événement après la date de fin."""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


def _parse_event_end_date(config: dict) -> date | None:
    raw = (config.get("profile_event_end_date") or "").strip()
    if not raw:
        meta = config.get("profile_meta")
        if isinstance(meta, dict):
            raw = (meta.get("eventEndDate") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def is_event_profile_expired(config: dict, *, today: date | None = None) -> bool:
    if config.get("profile_type") != "other":
        return False
    if config.get("profile_subtype") != "event":
        return False
    end = _parse_event_end_date(config)
    if not end:
        return False
    ref = today or date.today()
    return ref > end


def apply_event_expiry(supabase, site_id: str, config: dict, is_active: bool) -> bool:
    """
    Désactive le site si l'événement est terminé.
    Retourne True si le widget peut rester actif.
    """
    if not is_active:
        return False
    if not is_event_profile_expired(config):
        return True
    try:
        supabase.table("sites").update({"is_active": False}).eq("id", site_id).execute()
        logger.info("Site %s désactivé — événement terminé", site_id)
    except Exception as exc:
        logger.warning("Impossible de désactiver le site %s : %s", site_id, exc)
    return False
