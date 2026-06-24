import re
from typing import Any

from app.services.session_dates import filter_upcoming_sessions, is_upcoming_session, sort_sessions_by_date
from app.services.session_store import ensure_training_sessions
from app.services.text_quality import clean_text_line, is_readable_text

DEFAULT_WELCOME = "Bonjour ! Comment puis-je vous aider ?"

GENERIC_WELCOME_TEXTS = frozenset(
    {
        DEFAULT_WELCOME,
        "Bonjour ! Comment puis-je vous aider ?",
        "Bonjour! Comment puis-je vous aider ?",
        "Hello! How can I help you?",
        "Hello! How can I help you today?",
        "Hi! How can I help you?",
    }
)

_SHORT_WELCOME: dict[str, tuple[str, str, str]] = {
    "fr": ("Bienvenue ! 👋", "Comment puis-je vous aider ? 😊", "Prochaines sessions disponibles :"),
    "en": ("Welcome! 👋", "How can I help you? 😊", "Upcoming sessions:"),
    "it": ("Benvenuto! 👋", "Come posso aiutarti? 😊", "Prossime sessioni:"),
    "es": ("¡Bienvenido! 👋", "¿Cómo puedo ayudarte? 😊", "Próximas sesiones:"),
    "pt": ("Bem-vindo! 👋", "Como posso ajudar? 😊", "Próximas sessões:"),
    "de": ("Willkommen! 👋", "Wie kann ich Ihnen helfen? 😊", "Nächste Termine:"),
}


def is_generic_welcome(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    return cleaned in GENERIC_WELCOME_TEXTS

_BAD_WELCOME_MARKERS = (
    "Passer au contenu principal",
    "Passer au contenu",
    "Comment puis-je vous aider aujourd'hui ?",
    "Histoire France",
    "Histoire CI",
    "CIDES Training Center",
)

FORMATION_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "key": "cordiste_france",
        "title": "🇫🇷 Formation Cordiste IRATA — France",
        "profile_key": "cordiste_france",
        "session_regions": ("france",),
        "default_price": (
            "1 350 € net hébergement inclus (Nouvelle-Aquitaine) · "
            "5 jours de formation + 1 jour d'examen IRATA"
        ),
        "default_url": "https://cides.tf/formations-accueil/formation-france/formation-cordistes/",
    },
    {
        "key": "cordiste_togo",
        "title": "🇹🇬 Formation Cordiste IRATA — Togo",
        "profile_key": "cordiste_togo",
        "session_regions": ("togo",),
        "default_price": (
            "325 000 FCFA net hors hébergement (Ghana-Togo-Bénin) · "
            "450 000 FCFA net hébergement inclus · 5 à 6 jours + 1 jour d'examen"
        ),
        "default_url": "https://cides.tf/formations-accueil/formation-togo/formation-cordistes/",
    },
    {
        "key": "cnd_togo",
        "title": "🔬 Formation CND / NDT (ISO 9712) — Togo",
        "profile_key": "cnd_togo",
        "session_regions": ("cnd",),
        "default_price": (
            "1 500 000 XOF (2 287 € net) — formation UT Me ISO 9712 niveau 2 · "
            "5 jours + 1 jour d'examen · logement inclus"
        ),
        "default_url": "https://cides.tf/formations-accueil/formation-togo/formation-inspection-tg/",
    },
)


def is_usable_welcome(text: str) -> bool:
    """Short custom welcome typed by the client in the dashboard."""
    cleaned = (text or "").strip()
    if not cleaned or cleaned == DEFAULT_WELCOME:
        return False
    if any(marker in cleaned for marker in _BAD_WELCOME_MARKERS):
        return False
    if not is_readable_text(cleaned, min_len=30):
        return False
    if len(cleaned) > 480:
        return False
    if cleaned.count("…") >= 2:
        return False
    return True


def is_usable_intro(text: str) -> bool:
    """Rich auto-generated site presentation shown on first chat open."""
    cleaned = (text or "").strip()
    if not cleaned or cleaned == DEFAULT_WELCOME:
        return False
    if any(marker in cleaned for marker in _BAD_WELCOME_MARKERS):
        return False
    if not is_readable_text(cleaned, min_len=80):
        return False
    if len(cleaned) > 3200:
        return False
    if cleaned.count("…") >= 3:
        return False
    return True


def _short_session_label(label: str) -> str:
    cleaned = label.strip().lstrip("0123456789.-) ")
    if len(cleaned) > 90:
        return cleaned[:87] + "…"
    return cleaned


def _profiles_map(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p.get("key", ""): p for p in profiles if p.get("key")}


def _extract_price(summary: str, default: str) -> str:
    flat = clean_text_line(summary)
    patterns = (
        r"1[\s\u00a0]?\d{3}[\s\u00a0]?\d{3}\s*XOF[^.]{0,80}",
        r"\d{2,3}[\s\u00a0]?\d{3}\s*FCFA[^.]{0,80}",
        r"1[\s\u00a0]?350\s*€[^.]{0,60}",
        r"1[\s\u00a0]?500\s*€[^.]{0,60}",
        r"2[\s\u00a0]?287\s*Euros?[^.]{0,60}",
        r"325[\s\u00a0]?000\s*FCFA[^.]{0,60}",
        r"450[\s\u00a0]?000\s*FCFA[^.]{0,60}",
    )
    for pattern in patterns:
        match = re.search(pattern, flat, re.IGNORECASE)
        if match:
            snippet = clean_text_line(match.group())
            if len(snippet) > 20:
                return snippet[:160]
    return default


def _extract_cnd_session(profile: dict[str, Any]) -> dict[str, str] | None:
    summary = profile.get("summary", "")
    patterns = (
        r"(20\d{2}[\s/]*\s*\w+[^:\n]{0,25}:\s*du\s+\d{1,2}\s+au\s+\d{1,2}(?:\s*\(\s*Examen\s+\d+\s*\))?)",
        r"(septembre[^.\n]{0,40}du\s+\d{1,2}\s+au\s+\d{1,2}[^.\n]{0,30})",
    )
    for pattern in patterns:
        match = re.search(pattern, summary, re.IGNORECASE)
        if match:
            return {
                "label": clean_text_line(match.group(1))[:90],
                "url": profile.get("url", ""),
            }
    return None


def _sessions_for_catalog_entry(
    entry: dict[str, Any],
    upcoming: list[dict[str, Any]],
    all_sessions: list[dict[str, Any]],
    profile: dict[str, Any] | None,
) -> list[dict[str, str]]:
    regions = tuple(r.lower() for r in (entry.get("session_regions") or ()))

    def pick(pool: list[dict[str, Any]]) -> list[dict[str, str]]:
        matched = [
            s
            for s in pool
            if (s.get("region") or "").lower() in regions and s.get("url")
        ]
        return [
            {"label": _short_session_label(s.get("label", "Session")), "url": s["url"]}
            for s in matched
        ]

    found = pick(upcoming)
    if found:
        return found

    fallback_pool = sort_sessions_by_date(all_sessions)
    fallback_pool = [
        s for s in fallback_pool if is_upcoming_session(s.get("label", ""))
    ]
    found = pick(fallback_pool)
    if found:
        return found

    if entry["key"] == "cnd_togo" and profile:
        cnd = _extract_cnd_session(profile)
        if cnd and cnd.get("url"):
            return [cnd]

    return []


def _formation_section(
    entry: dict[str, Any],
    profile: dict[str, Any] | None,
    section_sessions: list[dict[str, str]],
) -> str:
    lines = [entry["title"], ""]

    summary = profile.get("summary", "") if profile else ""
    price = _extract_price(summary, entry["default_price"])
    lines.append(f"Tarif : {price}")

    page_url = (profile or {}).get("url") or entry.get("default_url", "")
    if page_url:
        lines.append(f"Infos : {page_url}")

    lines.append("")
    if section_sessions:
        lines.append("Prochaines sessions :")
        for session in section_sessions:
            lines.append(f"[[SESSION:{session['label']}|{session['url']}]]")
    else:
        lines.append("Prochaines sessions : contactez-nous ou consultez la page ci-dessus pour les dates.")

    return "\n".join(lines)


def build_full_welcome(
    site_name: str,
    sessions: list[dict[str, Any]],
    formation_profiles: list[dict[str, Any]] | None = None,
) -> str:
    profiles = _profiles_map(formation_profiles or [])
    upcoming = sort_sessions_by_date(filter_upcoming_sessions(sessions))
    all_sorted = sort_sessions_by_date(sessions)

    parts = [
        f"Bienvenue chez {site_name} ! 👋",
        "",
        "CI.DES — centre de formations certifiantes en travail sur cordes (IRATA) "
        "et contrôle non destructif / CND (ISO 9712), en France et au Togo.",
        "",
        "Voici nos formations, tarifs et prochaines sessions disponibles :",
    ]

    for entry in FORMATION_CATALOG:
        profile = profiles.get(entry["profile_key"])
        section_sessions = _sessions_for_catalog_entry(entry, upcoming, all_sorted, profile)
        parts.append("")
        parts.append(_formation_section(entry, profile, section_sessions))

    parts.append("")
    parts.append(
        "Cliquez sur une session pour vous inscrire, ou posez-moi vos questions "
        "(programme, prérequis, hébergement…) 😊"
    )
    return "\n".join(parts).strip()


def _strip_sessions(text: str) -> str:
    return re.sub(r"\[\[SESSION:[^\]]*\]\]", "", text or "").strip()


def build_generic_welcome(
    site_name: str,
    intro: str,
    sessions: list[dict[str, Any]],
    site_url: str = "",
    language: str = "fr",
) -> str:
    """Welcome for any (non-branded) client, built from their own site only."""
    from app.services.languages import normalize_language_code

    lang = normalize_language_code(language)
    clean_intro = _strip_sessions(intro).strip()

    if is_usable_intro(clean_intro):
        parts = [clean_intro]
    else:
        hello, question, _sessions_hdr = _SHORT_WELCOME.get(lang, _SHORT_WELCOME["fr"])
        parts = [hello]
        if site_url:
            parts.append(f"{site_url}")
        parts.append("")
        parts.append(question)

    upcoming = sort_sessions_by_date(filter_upcoming_sessions(sessions))
    if upcoming:
        _, _, sessions_hdr = _SHORT_WELCOME.get(lang, _SHORT_WELCOME["fr"])
        parts.append("")
        parts.append(sessions_hdr)
        for session in upcoming[:8]:
            label = _short_session_label(session.get("label", "Session"))
            url = session.get("url", "")
            if url:
                parts.append(f"[[SESSION:{label}|{url}]]")

    return "\n".join(parts).strip()


def compose_welcome_message(
    base_welcome: str,
    site_name: str,
    sessions: list[dict[str, Any]],
    formation_profiles: list[dict[str, Any]] | None = None,
    *,
    welcome_customized: bool = False,
    site_url: str = "",
    intro: str = "",
    language: str = "fr",
) -> str:
    from app.services.site_profiles import is_cides_site

    # A welcome manually written by the client always wins.
    if welcome_customized and is_usable_welcome(base_welcome) and not is_generic_welcome(base_welcome):
        return base_welcome.strip()

    # Branded CI.DES catalog only for CI.DES domains.
    if is_cides_site(site_url):
        return build_full_welcome(site_name, sessions, formation_profiles)

    # Auto mode: use generated intro only (never a stale welcome_message).
    content_intro = (intro or "").strip()
    return build_generic_welcome(
        site_name,
        content_intro,
        sessions,
        site_url=site_url,
        language=language,
    )
