"""Fetch HTML pages — httpx first, Playwright fallback for JS-rendered sites."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

import httpx
from bs4 import BeautifulSoup

from app.services.text_quality import is_html_content_type, is_readable_text

logger = logging.getLogger(__name__)

CRAWL_TIMEOUT = httpx.Timeout(60.0, connect=20.0)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CRAWL_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
}

# Requis sur Linux conteneurisé (Render, Docker) — sans sandbox root
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

FetchSource = Literal["httpx", "playwright"]


@dataclass
class PageFetchResult:
    url: str
    html: str
    status_code: int
    content_type: str
    source: FetchSource


def _quick_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def html_needs_js_render(html: str) -> bool:
    """True when httpx returned HTML but the visible text is too thin (SPA shell)."""
    if not html or len(html.strip()) < 80:
        return True
    text = _quick_visible_text(html)
    if is_readable_text(text, min_len=50):
        return False
    soup = BeautifulSoup(html, "lxml")
    script_count = len(soup.find_all("script"))
    root = soup.find(id="root") or soup.find(id="__next") or soup.find(id="app")
    return script_count >= 2 or root is not None


class PlaywrightSession:
    """Reusable headless browser for crawl batches."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._disabled = False
        self.launch_error: str | None = None

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def available(self) -> bool:
        return self._browser is not None

    async def start(self) -> None:
        if self._browser is not None or self._disabled:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            self.launch_error = f"Package playwright absent : {exc}"
            logger.warning("Playwright non installé — repli JS désactivé")
            self._disabled = True
            return
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=CHROMIUM_ARGS,
            )
            self.launch_error = None
            logger.info(
                "Chromium Playwright prêt (PLAYWRIGHT_BROWSERS_PATH=%s)",
                os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "default"),
            )
        except Exception as exc:
            self.launch_error = str(exc) or type(exc).__name__
            logger.warning("Impossible de lancer Chromium Playwright : %s", self.launch_error)
            self._disabled = True

    async def fetch(self, url: str, timeout_ms: int = 15000) -> PageFetchResult | None:
        if not self._browser:
            return None
        page = await self._browser.new_page(user_agent=BROWSER_USER_AGENT)
        try:
            try:
                response = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            except Exception:
                response = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            html = await page.content()
            final_url = page.url or url
            status = response.status if response else 200
            return PageFetchResult(
                url=final_url,
                html=html,
                status_code=status,
                content_type="text/html",
                source="playwright",
            )
        except Exception as exc:
            logger.warning("Playwright échec %s : %s", url, exc)
            return None
        finally:
            await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


async def check_playwright_ready(*, force: bool = False) -> dict:
    """Health probe — vérifie que Chromium peut démarrer (résultat mis en cache 10 min)."""
    import time

    global _playwright_health_cache, _playwright_health_at
    if (
        not force
        and _playwright_health_cache is not None
        and time.time() - _playwright_health_at < 600
    ):
        return _playwright_health_cache

    session = PlaywrightSession()
    await session.start()
    result = {
        "available": session.available,
        "error": session.launch_error,
    }
    await session.close()
    _playwright_health_cache = result
    _playwright_health_at = time.time()
    return result


_playwright_health_cache: dict | None = None
_playwright_health_at: float = 0.0

    client: httpx.AsyncClient, url: str, trace: dict | None = None
) -> PageFetchResult | None:
    try:
        response = await client.get(url, headers=CRAWL_HEADERS)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        detail = str(exc) or type(exc).__name__
        logger.warning("httpx échec %s : %s", url, detail)
        if trace is not None:
            trace["network_error"] = detail
        return None

    content_type = response.headers.get("content-type", "")
    if not is_html_content_type(content_type):
        logger.info("Ignoré (non HTML) : %s [%s]", url, content_type)
        return None

    return PageFetchResult(
        url=str(response.url),
        html=response.text,
        status_code=response.status_code,
        content_type=content_type,
        source="httpx",
    )


async def fetch_page_html(
    url: str,
    *,
    client: httpx.AsyncClient,
    playwright: PlaywrightSession | None = None,
    trace: dict | None = None,
) -> PageFetchResult | None:
    """Try httpx; fall back to Playwright on network/HTTP errors or empty JS shells."""
    httpx_result = await _fetch_httpx(client, url, trace)

    needs_browser = False
    if httpx_result is None:
        needs_browser = True
    elif httpx_result.status_code >= 400:
        logger.warning("HTTP %s pour %s — repli Playwright", httpx_result.status_code, url)
        needs_browser = True
    elif html_needs_js_render(httpx_result.html):
        logger.info("Contenu JS détecté — repli Playwright : %s", url)
        needs_browser = True

    if not needs_browser and httpx_result is not None:
        return httpx_result

    if playwright is None or not playwright.available:
        if playwright is not None and not playwright.disabled:
            await playwright.start()
        if playwright is None or not playwright.available:
            return httpx_result if httpx_result and httpx_result.status_code < 400 else None

    browser_result = await playwright.fetch(url)
    if trace is not None:
        trace["playwright_used"] = True
    if browser_result and browser_result.status_code < 400:
        if browser_result.source == "playwright":
            logger.info("Page chargée via Playwright : %s", url)
        return browser_result

    return httpx_result if httpx_result and httpx_result.status_code < 400 else browser_result
