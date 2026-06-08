import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.text_quality import clean_text_line, is_readable_text
from app.services.url_utils import sanitize_url

_SKIP_LABELS = frozenset({"read more", "lire la suite", "inscription", "en savoir plus"})

FORMATION_PATHS = (
    "/formations-accueil/formation-togo/formation-cordistes",
    "/formations-accueil/formation-france/formation-cordistes",
    "/formations-accueil/formation-togo/formation-inspection-tg",
)


def _is_session_href(path: str) -> bool:
    p = path.lower().rstrip("/")
    if re.search(r"sessions-(?:togo|france|afrique)/\d+", p):
        return True
    if re.fullmatch(r"/sessions/\d+", p):
        return True
    return False


def _session_region(url: str, page_url: str = "") -> str:
    lowered = url.lower()
    page = page_url.lower()
    if "formation-inspection" in page or "inspection-tg" in page:
        return "cnd"
    if "sessions-togo" in lowered or "formation-togo" in page:
        return "togo"
    if "sessions-france" in lowered or "formation-france" in page:
        return "france"
    if re.search(r"/sessions/\d+", lowered):
        return "france"
    return "other"


def extract_sessions_from_html(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    sessions: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        href = sanitize_url(urljoin(page_url, anchor["href"]))
        parsed = urlparse(href)
        if parsed.fragment and not parsed.path:
            continue
        if not _is_session_href(parsed.path):
            continue

        label = clean_text_line(anchor.get_text(" ", strip=True))
        if not label or label.lower() in _SKIP_LABELS:
            label = anchor.get("title") or anchor.get("aria-label") or ""
            label = clean_text_line(str(label))

        if not label or len(label) < 3 or label.lower() in _SKIP_LABELS:
            parent = anchor.find_parent(["li", "td", "div", "p", "span"])
            if parent:
                label = clean_text_line(parent.get_text(" ", strip=True))[:160]

        if not label or label.lower() in _SKIP_LABELS:
            label = f"Session {parsed.path.rstrip('/').split('/')[-1]}"

        region = _session_region(href, page_url)

        sessions.append(
            {
                "label": label[:160],
                "url": href.split("#")[0],
                "region": region,
            }
        )

    return sessions


def dedupe_sessions(sessions: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for session in sessions:
        url = session.get("url", "")
        label = session.get("label", "")
        if not url:
            continue
        key = f"{url}|{label}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(session)
    return unique


def formation_page_urls(site_url: str) -> list[str]:
    # CI.DES-specific formation pages. Other clients rely on the normal crawl
    # of their own site, so we return nothing for non-Cides domains.
    from app.services.site_profiles import is_cides_site

    parsed = urlparse(site_url)
    if not parsed.netloc:
        return []
    if not is_cides_site(site_url):
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    seen: set[str] = set()
    urls: list[str] = []
    for path in FORMATION_PATHS:
        url = base + path.rstrip("/")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls
