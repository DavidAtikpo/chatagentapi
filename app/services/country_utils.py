import re
import unicodedata

INVALID_COUNTRY_KEYS = frozenset(
    {
        "",
        "?",
        "...",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "inconnu",
        "inconnue",
        "not specified",
        "non specifie",
        "non renseigne",
        "pas encore",
    }
)

COUNTRY_ALIASES: dict[str, str] = {
    "tg": "Togo",
    "togo": "Togo",
    "fr": "France",
    "france": "France",
    "ga": "Gabon",
    "gabon": "Gabon",
    "bj": "Bénin",
    "benin": "Bénin",
    "gh": "Ghana",
    "ghana": "Ghana",
    "sn": "Sénégal",
    "senegal": "Sénégal",
    "ci": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "cm": "Cameroun",
    "cameroun": "Cameroun",
    "ml": "Mali",
    "mali": "Mali",
    "bf": "Burkina Faso",
    "burkina faso": "Burkina Faso",
    "ne": "Niger",
    "niger": "Niger",
    "gn": "Guinée",
    "guinee": "Guinée",
    "cd": "RD Congo",
    "rdc": "RD Congo",
    "congo": "Congo",
    "be": "Belgique",
    "belgique": "Belgique",
    "ch": "Suisse",
    "suisse": "Suisse",
    "ca": "Canada",
    "canada": "Canada",
}

CANONICAL_COUNTRIES = frozenset(COUNTRY_ALIASES.values())

_NON_COUNTRY_RE = re.compile(
    r"\b("
    r"frais|inscription|formation|prix|session|cordiste|euro|fcfa|budget|"
    r"disponib|expérience|experience|bonjour|merci|salut|comment|quel|quelle|"
    r"combien|tarif|programme|examen|cnd|irata|visiteur|lead|score"
    r")\b",
    re.IGNORECASE,
)

_COUNTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = []
for _label, _patterns in (
    ("Togo", (r"\btogo\b", r"🇹🇬", r"\b(?:au|en|du|de la|des)\s+togo\b")),
    ("France", (r"\bfrance\b", r"🇫🇷", r"\bfrançais\b", r"\bfrancais\b", r"\b(?:au|en|du|de la|des)\s+france\b")),
    ("Gabon", (r"\bgabon\b", r"\b(?:au|en|du|de la|des)\s+gabon\b")),
    ("Bénin", (r"\bb[ée]nin\b",)),
    ("Ghana", (r"\bghana\b",)),
    ("Sénégal", (r"\bs[ée]n[ée]gal\b",)),
    ("Côte d'Ivoire", (r"\bc[ôo]te d.?ivoire\b", r"\bivoirien\b", r"\bivoirienne\b")),
    ("Cameroun", (r"\bcameroun\b",)),
    ("Mali", (r"\bmali\b",)),
    ("Burkina Faso", (r"\bburkina\b",)),
    ("Niger", (r"\bniger\b",)),
    ("Guinée", (r"\bguin[ée]e\b",)),
    ("Belgique", (r"\bbelgique\b",)),
    ("Suisse", (r"\bsuisse\b",)),
    ("Canada", (r"\bcanada\b",)),
):
    for pat in _patterns:
        _COUNTRY_PATTERNS.append((re.compile(pat, re.IGNORECASE), _label))


_NATIONALITY_PATTERNS: list[tuple[re.Pattern[str], str]] = []
for _nat_label, _nat_patterns in (
    ("Togo", (r"\btogolai[se]?\b",)),
    ("France", (r"\bfrançai[se]?\b", r"\bfrancai[se]?\b")),
    ("Gabon", (r"\bgabonai[se]?\b",)),
    ("Bénin", (r"\bbeninoi[se]?\b", r"\bb[ée]ninoi[se]?\b")),
    ("Ghana", (r"\bghan[ée]en\b", r"\bghanaian\b")),
    ("Sénégal", (r"\bs[ée]n[ée]galai[se]?\b",)),
    ("Côte d'Ivoire", (r"\bivoirien\b", r"\bivoirienne\b")),
    ("Cameroun", (r"\bcamerounai[se]?\b",)),
    ("Mali", (r"\bmalien\b", r"\bmalienne\b")),
    ("Burkina Faso", (r"\bburkinab[eè]\b",)),
    ("Niger", (r"\bnig[eé]rien\b",)),
    ("Guinée", (r"\bguin[ée]en\b",)),
    ("Belgique", (r"\bbelge\b",)),
    ("Suisse", (r"\bsuisse\b",)),
    ("Canada", (r"\bcanadie[nn]+\b",)),
):
    for pat in _nat_patterns:
        _COUNTRY_PATTERNS.append((re.compile(pat, re.IGNORECASE), _nat_label))


def _country_key(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def is_valid_country(value: str | None) -> bool:
    return normalize_country(value) is not None


def normalize_country(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped or len(stripped) > 35:
        return None
    if len(stripped.split()) > 4:
        return None
    if _NON_COUNTRY_RE.search(stripped):
        return None

    key = _country_key(stripped)
    if key in INVALID_COUNTRY_KEYS:
        return None
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]

    for label in CANONICAL_COUNTRIES:
        if _country_key(label) == key:
            return label

    return None


def detect_country_from_text(text: str) -> str | None:
    if not text or not text.strip():
        return None

    for pattern, label in _COUNTRY_PATTERNS:
        if pattern.search(text):
            return label

    stripped = text.strip()
    if len(stripped) <= 25 and len(stripped.split()) <= 3:
        return normalize_country(stripped)

    return None


def enrich_qualification_country(
    qualification: dict,
    existing: dict | None,
    user_messages: list[str],
    ip_country: str | None = None,
) -> dict:
    result = dict(qualification)
    prior = dict(existing or {})

    # Priority order: IP geolocation (most reliable) → existing saved country
    # → AI qualification block → text detection from messages
    candidates: list[str | None] = [
        ip_country,
        normalize_country(str(prior.get("country") or "")),
        normalize_country(str(result.get("country") or "")),
    ]
    for message in reversed(user_messages):
        candidates.append(detect_country_from_text(message))

    country = next((c for c in candidates if c), None)
    if country:
        result["country"] = country
    elif "country" in result:
        result.pop("country", None)

    return result
