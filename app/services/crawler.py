import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.crawl_progress import fail_progress, init_progress, update_crawl_page
from app.services.page_fetcher import CRAWL_TIMEOUT, PlaywrightSession, fetch_page_html
import httpx
from app.services.session_extractor import (
    dedupe_sessions,
    extract_sessions_from_html,
    formation_page_urls,
)
from app.services.supabase_client import get_supabase
from app.services.text_quality import is_readable_text, should_skip_crawl_url

logger = logging.getLogger(__name__)


def _normalize_url(base: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    absolute = urljoin(base, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None
    if should_skip_crawl_url(absolute):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/') or '/'}"


def _extract_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    body = "\n".join(line for line in lines if line)
    return title, body


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if is_readable_text(chunk, min_len=50):
            chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def _extract_site_image(html: str, page_url: str) -> str | None:
    """Best image for chat branding: og:image, twitter:image, icon, favicon."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []

    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}, {"property": "twitter:image"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            candidates.append(urljoin(page_url, tag["content"].strip()))

    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if any(token in rel for token in ("apple-touch-icon", "icon", "shortcut icon")):
            candidates.append(urljoin(page_url, link["href"].strip()))

    parsed = urlparse(page_url)
    if parsed.scheme and parsed.netloc:
        candidates.append(f"{parsed.scheme}://{parsed.netloc}/favicon.ico")

    seen: set[str] = set()
    for raw in candidates:
        absolute = raw.strip()
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        return absolute
    return None


def save_site_image(site_id: str, image_url: str | None) -> None:
    if not image_url:
        return
    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    config["site_image_url"] = image_url
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()


async def discover_links(base_url: str, html: str, max_links: int = 50) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base_domain = urlparse(base_url).netloc
    found: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        normalized = _normalize_url(base_url, anchor["href"])
        if not normalized:
            continue
        if urlparse(normalized).netloc != base_domain:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append(normalized)
        if len(found) >= max_links:
            break
    return found


async def crawl_site(site_id: str, start_url: str, max_pages: int = 30) -> dict:
    supabase = get_supabase()
    supabase.table("sites").update({"crawl_status": "running"}).eq("id", site_id).execute()
    supabase.table("knowledge_chunks").delete().eq("site_id", site_id).execute()
    supabase.table("crawled_pages").delete().eq("site_id", site_id).execute()
    init_progress(site_id, max_pages)

    visited: set[str] = set()
    queue = [start_url.rstrip("/") or start_url]
    for seed_url in formation_page_urls(start_url):
        if seed_url not in queue:
            queue.append(seed_url)
    pages_crawled = 0
    all_sessions: list[dict] = []
    site_image_url: str | None = None

    try:
        playwright = PlaywrightSession()
        await playwright.start()
        try:
            async with httpx.AsyncClient(timeout=CRAWL_TIMEOUT, follow_redirects=True) as client:
                while queue and pages_crawled < max_pages:
                    url = queue.pop(0)
                    if url in visited or should_skip_crawl_url(url):
                        continue
                    visited.add(url)

                    fetched = await fetch_page_html(url, client=client, playwright=playwright)
                    if not fetched:
                        logger.warning("  Page ignorée (fetch) : %s", url)
                        continue

                    if fetched.status_code >= 400:
                        logger.warning("  Page ignorée (HTTP %s) : %s", fetched.status_code, url)
                        continue

                    html = fetched.html
                    page_url = fetched.url
                    if site_image_url is None:
                        site_image_url = _extract_site_image(html, page_url)
                    title, text = _extract_text(html)
                    all_sessions.extend(extract_sessions_from_html(html, page_url))
                    if not is_readable_text(text, min_len=50):
                        logger.info("  Ignoré (contenu illisible) : %s", page_url)
                        continue

                    if not title:
                        title = page_url

                    supabase.table("crawled_pages").upsert(
                        {
                            "site_id": site_id,
                            "url": page_url,
                            "title": title,
                            "status_code": fetched.status_code,
                        },
                        on_conflict="site_id,url",
                    ).execute()

                    chunks = _chunk_text(text)
                    for index, chunk in enumerate(chunks):
                        supabase.table("knowledge_chunks").insert(
                            {
                                "site_id": site_id,
                                "source_url": page_url,
                                "title": title,
                                "content": chunk,
                                "chunk_index": index,
                            }
                        ).execute()

                    pages_crawled += 1
                    logger.info("  Page %s/%s : %s", pages_crawled, max_pages, page_url)
                    update_crawl_page(site_id, pages_crawled, max_pages, page_url)
                    for link in await discover_links(page_url, html):
                        if link not in visited:
                            queue.append(link)

                for formation_url in formation_page_urls(start_url):
                    if formation_url in visited:
                        continue
                    try:
                        fetched = await fetch_page_html(
                            formation_url, client=client, playwright=playwright
                        )
                        if fetched and fetched.status_code < 400:
                            found = extract_sessions_from_html(fetched.html, formation_url)
                            logger.info(
                                "  Sessions formation : %s → %s lien(s)",
                                formation_url,
                                len(found),
                            )
                            all_sessions.extend(found)
                    except Exception as exc:
                        logger.warning("  Sessions formation échouées %s : %s", formation_url, exc)
        finally:
            await playwright.close()

        sessions = dedupe_sessions(all_sessions)
        logger.info("  Total sessions extraites : %s", len(sessions))

        if pages_crawled == 0:
            msg = (
                f"Aucune page lue pour {start_url} — vérifiez l'URL du site "
                "(ex. https://cides.tf, pas un site tiers comme odoo.com) ou réessayez plus tard"
            )
            fail_progress(site_id, msg)
            supabase.table("sites").update({"crawl_status": "failed"}).eq("id", site_id).execute()
            raise RuntimeError(msg)

        return {
            "site_id": site_id,
            "pages_crawled": pages_crawled,
            "status": "crawled",
            "sessions": sessions,
            "site_image_url": site_image_url,
        }
    except Exception as exc:
        fail_progress(site_id, "Échec lors de la lecture du site")
        supabase.table("sites").update({"crawl_status": "failed"}).eq("id", site_id).execute()
        raise exc
