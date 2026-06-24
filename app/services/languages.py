"""Langues supportées par l'agent (chat, accueil, widget)."""

SUPPORTED_LANGUAGES: dict[str, str] = {
    "fr": "français",
    "en": "English",
    "it": "italiano",
    "es": "español",
    "pt": "português",
    "de": "Deutsch",
}

SUPPORTED_LANGUAGE_CODES = frozenset(SUPPORTED_LANGUAGES.keys())


def language_label(code: str | None) -> str:
    return SUPPORTED_LANGUAGES.get((code or "fr").lower(), code or "français")


def supported_languages_summary() -> str:
    return ", ".join(SUPPORTED_LANGUAGES.values())


def normalize_language_code(code: str | None) -> str:
    lowered = (code or "fr").lower()
    if lowered in SUPPORTED_LANGUAGE_CODES:
        return lowered
    if lowered.startswith("en"):
        return "en"
    if lowered.startswith("it"):
        return "it"
    if lowered.startswith("es"):
        return "es"
    if lowered.startswith("pt"):
        return "pt"
    if lowered.startswith("de"):
        return "de"
    return "fr"
