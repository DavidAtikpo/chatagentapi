import logging

from anthropic import AsyncAnthropic

from app.config import settings
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

WELCOME_PRESENTATION_PROMPT = """Tu rédiges le message d'accueil initial d'un chat sur un site web.
Le visiteur doit voir immédiatement ce que fait l'entreprise ou le site, sans poser de question
(comme une réponse complète à « Qu'est-ce que vous faites ? »).

Règles :
- Base-toi UNIQUEMENT sur le contenu crawlé fourni. N'invente rien.
- « {site_name} » est un libellé saisi dans un tableau de bord ; décris l'activité RÉELLE du site
  d'après le contenu crawlé (pas d'après le libellé si le contenu dit autre chose).
- Structure obligatoire :
  1. Une phrase d'accueil chaleureuse avec 👋
  2. Une phrase qui résume l'activité principale (nom réel du produit/service si visible dans le contenu)
  3. Ligne vide puis « Voici ce que nous proposons : » (ou formulation équivalente)
  4. 4 à 8 puces, UNE par ligne, chaque ligne commence par « - » (tiret espace)
     Chaque puce = un service, produit ou fonctionnalité clé tiré du contenu
  5. Ligne vide puis une phrase de clôture invitant à poser une question
- Inclus l'URL du site ({site_url}) sur une ligne séparée, avec https:// complet
- Texte simple : pas de markdown (**gras**), pas de titres #, pas de liens markdown
- N'invente aucun prix, date, promesse ou fonctionnalité absente du contenu
- Réponds en {language}
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
                language=language,
                site_name=site_name,
                site_url=site_url or "non fournie",
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Libellé dashboard : {site_name}\n"
                        f"URL du site : {site_url or 'non fournie'}\n\n"
                        f"Contenu crawlé du site :\n{context}"
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
) -> str:
    """Return stored intro or generate + persist a rich site presentation."""
    if is_cides_site(site_url):
        return config.get("welcome_intro") or ""

    intro = config.get("welcome_intro") or ""
    if is_usable_intro(intro):
        return intro

    intro = await generate_company_intro(site_id, site_name, language, site_url=site_url)
    if intro:
        _persist_intro(site_id, intro)
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
            _persist_intro(site_id, intro)
    return save_composed_welcome(site_id, site_name, site_url, config)


def _persist_intro(site_id: str, intro: str) -> None:
    supabase = get_supabase()
    site = supabase.table("sites").select("agent_config").eq("id", site_id).single().execute()
    config = dict(site.data.get("agent_config") or {})
    config["welcome_intro"] = intro
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
