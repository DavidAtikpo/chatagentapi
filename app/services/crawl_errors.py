"""Diagnose crawl failures for dashboard display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.services.page_fetcher import CRAWL_HEADERS, _quick_visible_text
from app.services.text_quality import is_readable_text


class CrawlErrorCode(StrEnum):
    CLOUDFLARE = "cloudflare"
    ANTI_BOT = "anti_bot"
    ROBOTS_TXT = "robots_txt"
    HTTP_FORBIDDEN = "http_forbidden"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    JS_RENDER = "js_render"
    EMPTY_CONTENT = "empty_content"
    PLAYWRIGHT_UNAVAILABLE = "playwright_unavailable"
    WRONG_URL = "wrong_url"
    UNKNOWN = "unknown"


@dataclass
class CrawlFailure:
    code: CrawlErrorCode
    message: str
    detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "detail": self.detail,
            "at": datetime.now(UTC).isoformat(),
        }


_CLOUDFLARE_MARKERS = (
    "cloudflare",
    "cf-browser-verification",
    "challenge-platform",
    "just a moment",
    "attention required",
    "checking your browser",
    "ray id",
)
_ANTI_BOT_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "access denied",
    "bot detected",
    "verify you are human",
    "blocked",
    "forbidden",
    "security check",
    "ddos protection",
    "please enable javascript",
)


def _html_lower(html: str) -> str:
    return html[:8000].lower()


def detect_block_from_response(
    html: str | None,
    status_code: int | None,
    *,
    network_error: str | None = None,
) -> CrawlFailure | None:
    if network_error:
        lowered = network_error.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return CrawlFailure(
                CrawlErrorCode.TIMEOUT,
                "Délai dépassé — le site met trop de temps à répondre.",
                network_error,
            )
        if any(token in lowered for token in ("getaddrinfo", "name or service not known", "nodename")):
            return CrawlFailure(
                CrawlErrorCode.NETWORK,
                "Domaine introuvable — vérifiez l'URL du site.",
                network_error,
            )
        return CrawlFailure(
            CrawlErrorCode.NETWORK,
            "Erreur réseau — impossible de joindre le site.",
            network_error,
        )

    if status_code == 403:
        if html and any(m in _html_lower(html) for m in _CLOUDFLARE_MARKERS):
            return CrawlFailure(
                CrawlErrorCode.CLOUDFLARE,
                "Blocage anti-bot (Cloudflare) — le site refuse les crawlers automatiques.",
            )
        return CrawlFailure(
            CrawlErrorCode.HTTP_FORBIDDEN,
            "Accès refusé (HTTP 403) — le serveur bloque la lecture automatique.",
        )

    if status_code == 401:
        return CrawlFailure(
            CrawlErrorCode.HTTP_FORBIDDEN,
            "Accès refusé (HTTP 401) — authentification requise.",
        )

    if status_code and status_code >= 400:
        return CrawlFailure(
            CrawlErrorCode.HTTP_ERROR,
            f"Erreur HTTP {status_code} — le site a renvoyé une erreur.",
        )

    if not html:
        return None

    sample = _html_lower(html)
    if any(marker in sample for marker in _CLOUDFLARE_MARKERS):
        return CrawlFailure(
            CrawlErrorCode.CLOUDFLARE,
            "Blocage anti-bot (Cloudflare) — page de vérification détectée.",
        )
    if any(marker in sample for marker in _ANTI_BOT_MARKERS):
        return CrawlFailure(
            CrawlErrorCode.ANTI_BOT,
            "Blocage anti-bot — captcha ou filtrage User-Agent détecté.",
        )

    return None


def diagnose_empty_crawl(
    start_url: str,
    *,
    homepage_html: str | None = None,
    homepage_status: int | None = None,
    network_error: str | None = None,
    playwright_tried: bool = False,
    playwright_available: bool = True,
    playwright_error: str | None = None,
    robots_blocked: bool = False,
) -> CrawlFailure:
    if robots_blocked:
        return CrawlFailure(
            CrawlErrorCode.ROBOTS_TXT,
            "Interdit par robots.txt — le site n'autorise pas le crawl automatique.",
        )

    block = detect_block_from_response(homepage_html, homepage_status, network_error=network_error)
    if block:
        return block

    if homepage_html and not is_readable_text(_quick_visible_text(homepage_html), min_len=50):
        if not playwright_available:
            detail = playwright_error or (
                "Déployez l'API avec le Dockerfile Playwright (voir api/DEPLOY_RENDER.md)."
            )
            return CrawlFailure(
                CrawlErrorCode.PLAYWRIGHT_UNAVAILABLE,
                "Site JavaScript — Playwright (navigateur headless) n'est pas disponible sur le serveur.",
                detail,
            )
        if playwright_tried:
            return CrawlFailure(
                CrawlErrorCode.JS_RENDER,
                "Contenu JavaScript — la page reste vide même avec le navigateur headless.",
            )
        return CrawlFailure(
            CrawlErrorCode.JS_RENDER,
            "Contenu JavaScript — la page nécessite l'exécution JS (repli Playwright en cours ou échoué).",
        )

    host = urlparse(start_url).netloc.lower()
    if re.search(r"(^|\.)odoo\.com$", host):
        return CrawlFailure(
            CrawlErrorCode.WRONG_URL,
            "URL incorrecte — utilisez l'URL de votre site (ex. https://votre-entreprise.com), pas un domaine tiers.",
        )

    return CrawlFailure(
        CrawlErrorCode.EMPTY_CONTENT,
        "Aucune page lisible — vérifiez l'URL ou réessayez plus tard.",
        start_url,
    )


async def check_robots_txt(start_url: str, client: httpx.AsyncClient) -> bool:
    """True if robots.txt disallows crawling the start URL path."""
    parsed = urlparse(start_url)
    if not parsed.scheme or not parsed.netloc:
        return False

    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = await client.get(robots_url, headers=CRAWL_HEADERS, timeout=10)
    except Exception:
        return False

    if response.status_code >= 400:
        return False

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    path = parsed.path or "/"
    ua = CRAWL_HEADERS.get("User-Agent", "*")
    if parser.can_fetch(ua, start_url) or parser.can_fetch(ua, path):
        return False
    if parser.can_fetch("*", start_url) or parser.can_fetch("*", path):
        return False
    return True


def save_crawl_error(site_id: str, failure: CrawlFailure) -> None:
    from app.services.supabase_client import get_supabase

    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    config["crawl_error"] = failure.to_dict()
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()


def clear_crawl_error(site_id: str) -> None:
    from app.services.supabase_client import get_supabase

    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    if "crawl_error" in config:
        del config["crawl_error"]
        supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()
