import logging

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.languages import language_label, normalize_language_code
from app.services.rag import get_site_overview
from app.services.site_profiles import is_cides_site
from app.services.supabase_client import get_supabase
from app.services.text_quality import clean_text_line, filter_text_chunks, is_readable_text
from app.services.welcome_compose import (
    DEFAULT_WELCOME,
    compose_welcome_message,
    is_usable_intro,
    is_usable_welcome,
)

logger = logging.getLogger(__name__)

_INTRO_LANG_MARKERS: dict[str, tuple[str, ...]] = {
    "fr": ("bienvenue", " voici", " je suis", " nous ", " vous ", " vos ", " votre ", " tu ", " te "),
    "en": ("welcome", " here is", " here's", " i am", " i'm", " we offer", " our ", " your ", " you "),
    "it": ("benvenut", " ecco", " sono ", " nostr", " tuoi", " cosa "),
    "es": ("bienvenid", " aquí", " soy ", " nuestro", " tu ", " ofrecemos"),
    "pt": ("bem-vind", " aqui", " sou ", " nosso", " seu ", " oferecemos"),
    "de": ("willkommen", " hier ist", " ich bin", " unser", " ihr ", " sie "),
}


def intro_matches_language(text: str, language: str) -> bool:
    """Heuristic: reject cached intros stored under the wrong language key."""
    snippet = (text or "")[:420].lower()
    if not snippet.strip():
        return False
    language = normalize_language_code(language)
    scores = {
        lang: sum(1 for marker in markers if marker in snippet)
        for lang, markers in _INTRO_LANG_MARKERS.items()
    }
    best_lang = max(scores, key=lambda code: scores[code])
    if scores[best_lang] == 0:
        return True
    if scores[language] == 0 and scores[best_lang] > 0:
        return False
    return scores[language] >= scores[best_lang]

WELCOME_PRESENTATION_PROMPT = """You write the initial welcome message for a website chat widget.
The visitor must immediately understand what the business does, without asking a question
(like a complete answer to "What do you do?").

Rules:
- Use ONLY the crawled content provided. Do not invent anything.
- "{site_name}" is a dashboard label; describe the REAL activity from crawled content
  (not from the label if content says otherwise).
- Required structure (write ALL headings and bullets in {language}):
  1. A warm welcome sentence with 👋
  2. One sentence summarizing the main activity (real product/service name if visible)
  3. Blank line then a line like "Here is what we offer:" (equivalent in {language})
  4. 4 to 8 bullet points, ONE per line, each starting with "- " (dash space)
     Each bullet = a key service, product or feature from the content
  5. Blank line then a closing sentence inviting questions
- Include the site URL ({site_url}) on its own line, with full https://
- Plain text only: no markdown (**bold**), no # headings, no markdown links
- Do not invent prices, dates, promises or features missing from the content
- Write the ENTIRE message in {language}
"""


def _fetch_site_context(site_id: str, limit: int = 10) -> list[dict]:
    supabase = get_supabase()
    overview = get_site_overview(site_id, limit=4)

    result = (
        supabase.table("knowledge_chunks")
        .select("title, content, source_url, chunk_index")
        .eq("site_id", site_id)
        .order("chunk_index")
        .limit(limit * 5)
        .execute()
    )
    extra = filter_text_chunks(result.data or [])

    seen_urls: set[str] = set()
    merged: list[dict] = []
    for chunk in overview + extra:
        cid = chunk.get("id") or chunk.get("source_url", "") + str(chunk.get("chunk_index", 0))
        url = chunk.get("source_url", "")
        if url and url in seen_urls and len(merged) >= 4:
            continue
        if url:
            seen_urls.add(url)
        if chunk not in merged:
            merged.append(chunk)
        if len(merged) >= limit:
            break
    return merged


async def generate_company_intro(
    site_id: str,
    site_name: str,
    language: str = "fr",
    site_url: str = "",
) -> str:
    """Generate a rich site presentation from the client's crawled pages."""
    chunks = _fetch_site_context(site_id)
    if not chunks:
        return ""

    context = "\n\n---\n\n".join(
        f"[{chunk.get('title', 'Page')}]({chunk.get('source_url', '')})\n"
        f"{clean_text_line(chunk.get('content', ''))[:900]}"
        for chunk in chunks[:8]
    )

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=900,
            system=WELCOME_PRESENTATION_PROMPT.format(
                language=language_label(language),
                site_name=site_name,
                site_url=site_url or "non fournie",
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Dashboard label: {site_name}\n"
                        f"Site URL: {site_url or 'not provided'}\n\n"
                        f"Crawled site content:\n{context}"
                    ),
                }
            ],
        )
        text = response.content[0].text.strip()
        if text and is_usable_intro(text):
            return text
    except Exception as exc:
        logger.warning("Welcome presentation generation failed for site %s: %s", site_id, exc)

    return ""


async def ensure_welcome_intro(
    site_id: str,
    site_name: str,
    site_url: str,
    config: dict,
    language: str = "fr",
    *,
    persist: bool = True,
) -> str:
    """Return stored intro for `language` or generate + optionally persist."""
    language = normalize_language_code(language)

    if is_cides_site(site_url):
        return config.get("welcome_intro") or ""

    intros: dict[str, str] = dict(config.get("welcome_intros") or {})
    stored_lang = normalize_language_code(config.get("welcome_intro_lang") or "")

    cached = (intros.get(language) or "").strip()
    if is_usable_intro(cached) and intro_matches_language(cached, language):
        return cached

    legacy_intro = (config.get("welcome_intro") or "").strip()
    if (
        legacy_intro
        and is_usable_intro(legacy_intro)
        and stored_lang == language
        and intro_matches_language(legacy_intro, language)
    ):
        return legacy_intro

    intro = await generate_company_intro(site_id, site_name, language, site_url=site_url)
    if intro and persist:
        _persist_intro(site_id, intro, language)
    return intro


def save_composed_welcome(site_id: str, site_name: str, site_url: str, agent_config: dict) -> str:
    from app.services.session_dates import filter_upcoming_sessions

    sessions = filter_upcoming_sessions(agent_config.get("training_sessions") or [])
    profiles = agent_config.get("formation_profiles") or []
    welcome = compose_welcome_message(
        agent_config.get("welcome_message") or "",
        site_name,
        sessions,
        profiles,
        welcome_customized=agent_config.get("welcome_customized", False),
        site_url=site_url,
        intro=agent_config.get("welcome_intro") or "",
    )
    save_welcome_message(site_id, welcome)
    return welcome


async def refresh_welcome_after_crawl(
    site_id: str, site_name: str, site_url: str, agent_config: dict, language: str = "fr"
) -> str:
    """Regenerate the welcome after a crawl. Non-Cides sites get a rich presentation."""
    config = dict(agent_config)
    if not is_cides_site(site_url) and not config.get("welcome_customized"):
        intro = await generate_company_intro(site_id, site_name, language, site_url=site_url)
        if intro:
            config["welcome_intro"] = intro
            config["welcome_intro_lang"] = normalize_language_code(language)
            _persist_intro(site_id, intro, normalize_language_code(language))
    return save_composed_welcome(site_id, site_name, site_url, config)


def _persist_intro(site_id: str, intro: str, language: str) -> None:
    from app.services.languages import normalize_language_code

    language = normalize_language_code(language)
    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    intros = dict(config.get("welcome_intros") or {})
    intros[language] = intro
    config["welcome_intros"] = intros
    config["welcome_intro"] = intro
    config["welcome_intro_lang"] = language
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()


def save_welcome_message(site_id: str, welcome: str, auto_generated: bool = True) -> None:
    if not is_readable_text(welcome, min_len=30):
        logger.warning("Refusing to save corrupted welcome for site %s", site_id)
        return

    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})

    if config.get("welcome_customized"):
        return

    config["welcome_message"] = welcome
    config["welcome_auto_generated"] = auto_generated
    config["welcome_message_lang"] = normalize_language_code(
        (site.data.get("agent_config") or {}).get("language") or "fr"
    )
    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()


def resolve_welcome_message(site_id: str, site_name: str, agent_config: dict) -> str:
    welcome = agent_config.get("welcome_message") or DEFAULT_WELCOME

    if agent_config.get("welcome_customized") and is_usable_welcome(welcome):
        return welcome

    if (
        welcome != DEFAULT_WELCOME
        and agent_config.get("welcome_auto_generated")
        and is_readable_text(welcome, min_len=80)
    ):
        return welcome

    return DEFAULT_WELCOME
