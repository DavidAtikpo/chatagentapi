"""Origines CORS autorisées pour le widget embed (domaines clients enregistrés)."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from app.config import settings
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300
_cached_origins: set[str] | None = None
_cache_allow_all = False
_cache_loaded_at = 0.0

_DEV_ORIGINS = frozenset(
    {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8787",
    }
)


def normalize_origin(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    try:
        parsed = urlparse(raw)
        if not parsed.netloc:
            return None
        scheme = (parsed.scheme or "https").lower()
        host = parsed.netloc.lower()
        if host.endswith(":80") and scheme == "http":
            host = host[:-3]
        if host.endswith(":443") and scheme == "https":
            host = host[:-4]
        return f"{scheme}://{host}"
    except Exception:
        return None


def origin_variants(origin: str) -> set[str]:
    """https://example.com ↔ https://www.example.com"""
    normalized = normalize_origin(origin)
    if not normalized:
        return set()
    out = {normalized}
    parsed = urlparse(normalized)
    host = parsed.netloc
    if host.startswith("www."):
        out.add(f"{parsed.scheme}://{host[4:]}")
    else:
        out.add(f"{parsed.scheme}://www.{host}")
    return out


def _static_origins() -> set[str]:
    allowed: set[str] = set(_DEV_ORIGINS)
    for entry in settings.cors_origins_list:
        if entry == "*":
            continue
        allowed.update(origin_variants(entry))
    app_origin = normalize_origin(settings.app_url)
    if app_origin:
        allowed.update(origin_variants(app_origin))
    return allowed


def _load_site_origins() -> set[str]:
    allowed = _static_origins()
    try:
        supabase = get_supabase()
        rows = (
            supabase.table("sites")
            .select("url, agent_config, is_active")
            .eq("is_active", True)
            .execute()
        )
        for row in rows.data or []:
            allowed.update(origin_variants(row.get("url") or ""))
            config = row.get("agent_config") or {}
            extras = config.get("embed_origins") or []
            if isinstance(extras, str):
                extras = [e.strip() for e in extras.split(",") if e.strip()]
            for extra in extras:
                if isinstance(extra, str):
                    allowed.update(origin_variants(extra))
    except Exception as exc:
        logger.warning("CORS: impossible de charger les sites (%s)", exc)
    return allowed


def get_allowed_origins() -> tuple[bool, set[str]]:
    """Retourne (allow_all, origins)."""
    global _cached_origins, _cache_allow_all, _cache_loaded_at

    if "*" in settings.cors_origins_list:
        return True, set()

    now = time.time()
    if _cached_origins is not None and now - _cache_loaded_at < _CACHE_TTL_SECONDS:
        return _cache_allow_all, _cached_origins

    _cache_allow_all = False
    _cached_origins = _load_site_origins()
    _cache_loaded_at = now
    logger.info("CORS: %s origines client autorisées", len(_cached_origins))
    return _cache_allow_all, _cached_origins


def is_origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    allow_all, allowed = get_allowed_origins()
    if allow_all:
        return True
    normalized = normalize_origin(origin)
    return normalized in allowed if normalized else False


def invalidate_cors_cache() -> None:
    global _cached_origins, _cache_loaded_at
    _cached_origins = None
    _cache_loaded_at = 0.0
