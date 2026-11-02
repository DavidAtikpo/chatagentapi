"""Reformulation IA des profils emploi et « autre »."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.languages import language_label, normalize_language_code
from app.services.site_summary import _fetch_site_context
from app.services.supabase_client import get_supabase
from app.services.text_quality import clean_text_line, is_readable_text

logger = logging.getLogger(__name__)

PROFILE_TYPES = frozenset({"job_search", "other", "shop"})

_JOB_CANDIDATE_SYSTEM = """Tu rédiges une présentation professionnelle de candidat à destination de recruteurs.
Langue: {language}.
Règles strictes:
- Ne jamais inventer diplômes, employeurs ou dates absents du contexte.
- Ton professionnel, clair et attractif (150 à 320 mots).
- Structure markdown : titre `#`, sections `##`, listes avec `- ` ou `•`, séparateurs `---` si besoin.
- Ne pas ajouter de phrase finale invitant à écrire via le chat (ajoutée automatiquement par l'application)."""

_JOB_RECRUITMENT_SYSTEM = """Tu rédiges une annonce de recrutement / campagne d'embauche à destination de candidats.
Langue: {language}.
Règles strictes:
- Ne jamais inventer salaire, avantages ou exigences absents du contexte.
- Ton professionnel, engageant, clair (150 à 320 mots).
- Mettre en avant le poste, le profil recherché, les missions et le process de candidature.
- Structure markdown : titre `#`, sections `##`, listes avec `- ` ou `•`, séparateurs `---` si besoin.
- Reprendre email / téléphone / WhatsApp du contexte dans une section « Comment postuler » si présents.
- Ne pas ajouter de phrase finale invitant à écrire via le chat (ajoutée automatiquement par l'application)."""

_OTHER_SYSTEM = """Tu rédiges la présentation d'un projet (freelance, ONG, événement, immobilier, artiste, formation…).
Langue: {language}.
Type de profil: {subtype_label}.
Ton adapté au public visiteur, factuel, 120 à 300 mots. Markdown léger autorisé."""

_SUBTYPE_LABELS = {
    "freelance": "Freelance / prestataire",
    "ngo": "Association / ONG",
    "event": "Événement",
    "real_estate": "Immobilier",
    "artist": "Artiste / créateur",
    "training": "Formation / cours",
    "custom": "Projet",
}


def _build_context(site_name: str, business_description: str, site_id: str) -> str:
    parts = [f"Nom affiché: {site_name}"]
    if business_description.strip():
        parts.append(f"\nDescription source:\n{business_description.strip()[:6000]}")
    chunks = _fetch_site_context(site_id)
    if chunks:
        excerpt = "\n\n---\n\n".join(
            clean_text_line(c.get("content", ""))[:800]
            for c in chunks[:6]
        )
        parts.append(f"\nExtraits indexés (site, PDF):\n{excerpt}")
    return "\n".join(parts)


_CHAT_CLOSING_MARKERS = (
    "boutons ci-dessous",
    "via le chat",
    "directement via le chat",
    "posez vos questions",
)


def _presentation_has_chat_closing(text: str) -> bool:
    tail = text[-500:].lower()
    return sum(1 for m in _CHAT_CLOSING_MARKERS if m in tail) >= 2


def _append_job_footer(presentation: str, job_mode: str) -> str:
    if _presentation_has_chat_closing(presentation):
        return presentation.rstrip()
    if job_mode == "recruitment":
        return (
            presentation.rstrip()
            + "\n\n💬 *Intéressé(e) ?* Posez vos questions ou candidatez via les boutons ci-dessous."
        )
    return (
        presentation.rstrip()
        + "\n\n💬 *Recruteur ?* Posez vos questions ou contactez-moi via les boutons ci-dessous."
    )


def _job_mode(config: dict) -> str:
    meta = config.get("profile_meta")
    if isinstance(meta, dict) and meta.get("mode") == "recruitment":
        return "recruitment"
    return "candidate"


def _system_prompt(profile_type: str, language: str, subtype: str, *, job_mode: str = "candidate") -> str:
    lang = language_label(language)
    if profile_type == "job_search":
        if job_mode == "recruitment":
            return _JOB_RECRUITMENT_SYSTEM.format(language=lang)
        return _JOB_CANDIDATE_SYSTEM.format(language=lang)
    if profile_type == "shop":
        return _OTHER_SYSTEM.format(language=lang, subtype_label="Commerce (legacy)")
    label = _SUBTYPE_LABELS.get(subtype, subtype or "Projet")
    return _OTHER_SYSTEM.format(language=lang, subtype_label=label)


async def compose_profile_presentation(site_id: str, *, force: bool = False) -> dict:
    supabase = get_supabase()
    site = (
        supabase.table("sites")
        .select("id, name, agent_config, business_description, is_active")
        .eq("id", site_id)
        .single()
        .execute()
    )
    if not site.data:
        raise ValueError("Site introuvable")

    config = dict(site.data.get("agent_config") or {})
    profile_type = config.get("profile_type") or ""
    if profile_type not in PROFILE_TYPES:
        return {"status": "skipped", "reason": "not_a_profile_site"}

    if config.get("public_presentation") and config.get("profile_composed_at") and not force:
        return {"status": "cached", "presentation": config.get("public_presentation")}

    language = normalize_language_code(config.get("language") or "fr")
    subtype = str(config.get("profile_subtype") or "")
    job_mode = _job_mode(config)
    business_description = (site.data.get("business_description") or "").strip()
    context = _build_context(site.data["name"], business_description, site_id)

    presentation = ""
    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=settings.claude_model,
            max_tokens=900,
            system=_system_prompt(profile_type, language, subtype, job_mode=job_mode),
            messages=[{"role": "user", "content": context}],
        )
        presentation = (response.content[0].text or "").strip()
    except Exception as exc:
        logger.warning("Profile compose LLM failed site=%s: %s", site_id, exc)

    if not is_readable_text(presentation, min_len=80):
        presentation = business_description[:3500] or site.data["name"]

    if profile_type == "job_search":
        presentation = _append_job_footer(presentation, job_mode)

    config["public_presentation"] = presentation
    config["welcome_message"] = presentation
    config["welcome_customized"] = True
    config["welcome_auto_generated"] = True
    config["welcome_message_lang"] = language
    config["profile_composed_at"] = datetime.now(timezone.utc).isoformat()

    supabase.table("sites").update({"agent_config": config}).eq("id", site_id).execute()

    return {"status": "composed", "presentation": presentation, "profile_type": profile_type}
