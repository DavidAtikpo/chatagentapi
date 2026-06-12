import asyncio
import json
import re

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.country_utils import detect_country_from_text, enrich_qualification_country, normalize_country
from app.services.formation_context import (
    filter_sessions_for_profiles,
    format_formation_profiles,
    match_formation_profiles,
)
from app.services.plans import has_pro_features
from app.services.rag import get_site_overview, search_knowledge
from app.services.session_dates import filter_upcoming_sessions
from app.services.handoff import (
    detect_handoff_reason,
    extract_handoff_marker,
    get_conversation_handoff,
    insert_user_message_only,
    is_handoff_active,
    request_handoff,
)
from app.services.supabase_client import get_supabase

SYSTEM_PROMPT = """Tu es l'assistant du site web « {site_name} ».

IMPORTANT — identité de l'entreprise :
« {site_name} » n'est qu'un libellé donné dans le tableau de bord. La SEULE source de
vérité sur l'activité, les produits, les services, le secteur et l'identité de
l'entreprise est le CONTEXTE ci-dessous (extraits réels du site web crawlé).
- Décris l'entreprise UNIQUEMENT d'après ce contexte. N'invente JAMAIS une activité,
  un métier, un secteur, des produits, une histoire ou des chiffres.
- Si le contenu du site décrit une activité différente de ce que le nom suggère,
  fie-toi au CONTENU DU SITE, pas au nom.
- Si le contexte ne contient pas l'information demandée, dis simplement et honnêtement
  que tu n'as pas cette information, et propose de contacter l'équipe. Ne devine pas.

Objectifs :
1. Répondre aux questions des visiteurs avec précision, à partir du contexte
2. Présenter les produits/services tels que décrits sur le site
3. Qualifier le prospect (besoin, disponibilité, budget) quand c'est pertinent
4. Proposer le lien d'achat/inscription/contact au bon moment
5. Si le visiteur veut contacter l'équipe, oriente-le vers les boutons WhatsApp / Appel / Email (voir contacts ci-dessous)
6. Handoff humain : si le visiteur demande un conseiller/humain, si tu ne peux pas répondre avec le contexte, ou si le lead est très chaud, ajoute après le bloc qualification : <!--HANDOFF:user_request--> ou <!--HANDOFF:ai_escalation--> ou <!--HANDOFF:hot_lead-->

Règles :
- Langue par défaut du site : {language_label}
- Réponds TOUJOURS dans la langue du visiteur. S'il écrit ou parle en anglais, réponds entièrement en anglais. S'il utilise le français, réponds en français.
- Si le visiteur ne comprend pas le français, demande une traduction, ou dit « in English », « translate », « I don't understand » : traduis ta réponse en anglais clair et simple. Tu peux proposer : « Would you like me to continue in English? »
- Si le visiteur mélange les deux langues, privilégie la langue de sa dernière question.
- Ton : {tone}
- Utilise en priorité le contexte ci-dessous pour décrire l'activité, les prix, dates et FAQ
- Si le contexte contient des dates ou sessions, cite-les clairement
- Ne invente jamais d'informations absentes du contexte
- Si le contexte ne contient pas la réponse, dis-le honnêtement et propose le contact
- Pose une question de qualification à la fois
- Sois concis et commercial sans être agressif
- Pour une question de prix ou tarif : donne le montant UNE seule fois, sans le répéter ni le reformuler plusieurs fois
- Formatage : texte simple, paragraphes séparés par une ligne vide.
  Chaque puce sur sa propre ligne avec « - ». Espace après chaque emoji et chaque phrase.
- Quand tu cites une page du site, inclus le lien complet (https://...)
- Sessions/dates avec inscription : quand tu listes des sessions ayant une URL d'inscription, utilise UNE ligne par session :
  [[SESSION:Libellé court avec dates|https://url-inscription-exacte]]
  Exemple : [[SESSION:Mars 2026 — 09 au 13|https://exemple.com/inscription/20]]
  Utilise UNIQUEMENT les URLs du bloc « Sessions d'inscription » ci-dessous. N'invente jamais d'URL.
  Les URLs ne doivent jamais contenir d'espace.
  Ajoute [[SESSION:...|url]] pour CHAQUE session disponible à venir de la catégorie demandée, jamais en texte brut seul.
  Ne propose jamais une session dont la date est déjà passée — uniquement les sessions futures listées ci-dessous.
- Contact direct : quand le visiteur veut joindre l'équipe, ajoute les boutons cliquables :
  [[CONTACT:whatsapp|numéro]]  [[CONTACT:phone|numéro]]  [[CONTACT:email|adresse@email.com]]
  Utilise UNIQUEMENT les coordonnées du bloc « Contacts directs » ci-dessous. Un bouton par ligne.
  Les boutons sont aussi visibles en permanence en bas du chat.

Qualification interne (invisible pour le visiteur) :
- Demande tôt le pays si inconnu (« Dans quel pays êtes-vous ? » en français, ou « Which country are you in? » en anglais).
- country : nom du pays en français UNIQUEMENT parmi : Togo, France, Gabon, Bénin, Ghana, Sénégal, Côte d'Ivoire, Cameroun, Mali, Burkina Faso, Niger, Guinée, Belgique, Suisse, Canada, Congo. Laisse "" si inconnu. Jamais une phrase, jamais un sujet hors pays (prix, formation, etc.).
À la toute fin de ta réponse uniquement, ajoute exactement ce bloc sur une seule ligne :
<!--QUALIFICATION:{{"country":"","experience":"","availability":"","budget":""}}-->
"""


LANGUAGE_LABELS = {
    "fr": "français",
    "en": "English",
    "es": "español",
    "de": "Deutsch",
}


def _language_label(code: str) -> str:
    return LANGUAGE_LABELS.get((code or "fr").lower(), code or "français")


def _contact_context_block(site_config: dict) -> str:
    if not site_config.get("pro_contacts"):
        return ""

    agent = site_config.get("agent_config") or {}
    whatsapp = site_config.get("whatsapp_number") or agent.get("contact_whatsapp")
    phone = agent.get("contact_phone")
    email = agent.get("contact_email")

    lines: list[str] = []
    if whatsapp:
        lines.append(f"- WhatsApp : {whatsapp}")
    if phone:
        lines.append(f"- Téléphone (appel) : {phone}")
    if email:
        lines.append(f"- Email : {email}")
    if not lines:
        return ""

    return (
        "\n\nContacts directs configurés (utilise [[CONTACT:type|valeur]] pour afficher un bouton cliquable) :\n"
        + "\n".join(lines)
    )


def _calculate_score(data: dict) -> int:
    fields = ["country", "experience", "availability", "budget"]
    filled = 0
    for field in fields:
        value = data.get(field)
        if field == "country":
            if normalize_country(str(value) if value is not None else None):
                filled += 1
        elif value:
            filled += 1
    base = filled * 20
    if data.get("email"):
        base += 10
    if data.get("phone"):
        base += 10
    return min(base, 100)


def _extract_qualification(text: str) -> tuple[str, dict | None]:
    match = re.search(r"<!--QUALIFICATION:(\{.*?\})-->", text, re.DOTALL)
    if not match:
        return text, None
    clean = text[: match.start()].rstrip()
    try:
        data = json.loads(match.group(1))
        data["score"] = _calculate_score(data)
        return clean, data
    except json.JSONDecodeError:
        return text, None


_MAX_CHUNK_CHARS = 1200


def _fetch_message_history(conversation_id: str) -> list[dict]:
    supabase = get_supabase()
    result = (
        supabase.table("messages")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .limit(20)
        .execute()
    )
    return result.data or []


def _format_context_chunk(chunk: dict) -> str:
    content = chunk.get("content", "")
    if len(content) > _MAX_CHUNK_CHARS:
        content = content[:_MAX_CHUNK_CHARS] + "…"
    return f"[{chunk.get('title', 'Source')}]({chunk.get('source_url', '')})\n{content}"


class _QualificationStreamFilter:
    """Hide the qualification block from streamed tokens shown to the visitor."""

    _MARKER = "<!--QUALIFICATION:"

    def __init__(self) -> None:
        self._buffer = ""
        self._done = False

    def feed(self, chunk: str) -> str:
        if self._done or not chunk:
            return ""
        self._buffer += chunk
        if self._MARKER in self._buffer:
            visible = self._buffer.split(self._MARKER, 1)[0]
            self._done = True
            return visible

        hold = 0
        for i in range(1, len(self._MARKER)):
            if self._buffer.endswith(self._MARKER[:i]):
                hold = i
                break

        if hold:
            visible = self._buffer[:-hold]
            self._buffer = self._buffer[-hold:]
        else:
            visible = self._buffer
            self._buffer = ""

        return visible


async def stream_chat(
    site_id: str,
    conversation_id: str,
    user_message: str,
    site_config: dict,
    ip_country: str | None = None,
):
    handoff = get_conversation_handoff(conversation_id)
    if handoff and is_handoff_active(handoff.get("handoff_status")):
        # Enregistre le message visiteur — pas de réponse auto (le conseiller répond via l'app)
        insert_user_message_only(conversation_id, user_message)
        return

    supabase = get_supabase()
    overview_chunks, query_chunks, history_rows = await asyncio.gather(
        asyncio.to_thread(get_site_overview, site_id, 3),
        search_knowledge(site_id, user_message),
        asyncio.to_thread(_fetch_message_history, conversation_id),
    )

    seen_ids: set = set()
    context_chunks: list[dict] = []
    for chunk in overview_chunks + query_chunks:
        cid = chunk.get("id")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        context_chunks.append(chunk)

    context = "\n\n---\n\n".join(_format_context_chunk(chunk) for chunk in context_chunks)

    messages = [{"role": row["role"], "content": row["content"]} for row in history_rows]
    messages.append({"role": "user", "content": user_message})

    system = SYSTEM_PROMPT.format(
        site_name=site_config.get("name", "l'entreprise"),
        language_label=_language_label(site_config.get("agent_config", {}).get("language", "fr")),
        tone=site_config.get("agent_config", {}).get("tone", "professional"),
    )

    agent_config = site_config.get("agent_config") or {}
    formation_profiles = agent_config.get("formation_profiles") or []
    matched_profiles = match_formation_profiles(formation_profiles, user_message)

    if context:
        system += f"\n\nContexte entreprise (source : site web crawlé) :\n{context}"
    elif not matched_profiles:
        system += (
            "\n\nAucun extrait du site n'a été trouvé pour cette question. "
            "Tu ne disposes d'AUCUNE information fiable sur l'activité de l'entreprise. "
            "N'invente rien (ni l'activité, ni le secteur, ni les produits, ni les prix ou dates). "
            "Dis honnêtement que tu n'as pas cette information et propose de contacter l'équipe."
        )

    if matched_profiles:
        system += (
            "\n\nFiches formation officielles (tarifs et programmes — priorité absolue, cite ces chiffres) :\n"
            + format_formation_profiles(matched_profiles)
        )

    all_upcoming = filter_upcoming_sessions(agent_config.get("training_sessions") or [])
    training_sessions = filter_sessions_for_profiles(all_upcoming, matched_profiles)
    if training_sessions:
        system += "\n\nSessions d'inscription à venir (URLs exactes — à utiliser pour les boutons [[SESSION:...|url]]) :\n"
        for session in training_sessions:
            region = session.get("region", "")
            system += (
                f"- [{region}] {session.get('label', 'Session')} : "
                f"{session.get('url')}\n"
            )
        system += (
            "\nCatégories : [cnd] = formation inspection / CND / NDT ISO 9712 (Togo), "
            "[togo] = cordiste IRATA (Togo), [france] = cordiste IRATA (France). "
            "Ne mélange jamais cordiste et CND : si le visiteur parle de CND, inspection ou NDT, "
            "utilise UNIQUEMENT les sessions [cnd].\n"
            "Règle sessions : réponds avec une courte intro (2 phrases max) "
            "puis UNIQUEMENT des lignes [[SESSION:label|url]] pour chaque session ci-dessus "
            "de la catégorie demandée. Pas de liste numérotée en texte seul."
        )

    contact_block = _contact_context_block(site_config)
    if contact_block:
        system += contact_block

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    full_response = ""
    stream_filter = _QualificationStreamFilter()

    async with client.messages.stream(
        model=settings.claude_model,
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            full_response += text
            visible = stream_filter.feed(text)
            if visible:
                yield visible

    clean_response, qualification = _extract_qualification(full_response)
    clean_response, handoff_marker = extract_handoff_marker(clean_response)

    supabase.table("messages").insert(
        {"conversation_id": conversation_id, "role": "user", "content": user_message}
    ).execute()
    supabase.table("messages").insert(
        {"conversation_id": conversation_id, "role": "assistant", "content": clean_response}
    ).execute()

    # Fetch existing qualification data (needed for both branches below)
    existing = (
        supabase.table("conversations")
        .select("qualification_data")
        .eq("id", conversation_id)
        .maybe_single()
        .execute()
    )
    prior: dict = (existing.data or {}).get("qualification_data") or {}
    user_texts = [m["content"] for m in messages if m["role"] == "user"]

    if qualification:
        qualification = enrich_qualification_country(qualification, prior, user_texts, ip_country=ip_country)
        qualification["score"] = _calculate_score(qualification)
    else:
        # No qualification block emitted – still try to update country from IP or messages
        detected = ip_country or next(
            (detect_country_from_text(t) for t in reversed(user_texts) if detect_country_from_text(t)),
            None,
        )
        if detected and not normalize_country(str(prior.get("country") or "")):
            updated_prior = dict(prior)
            updated_prior["country"] = detected
            supabase.table("conversations").update(
                {"qualification_data": updated_prior}
            ).eq("id", conversation_id).execute()

    if qualification:
        supabase.table("conversations").update(
            {
                "lead_score": qualification.get("score", 0),
                "qualification_data": qualification,
                "status": "qualified" if qualification.get("score", 0) >= 60 else "active",
            }
        ).eq("id", conversation_id).execute()

        if qualification.get("score", 0) >= 60:
            site = supabase.table("sites").select("organization_id").eq("id", site_id).single().execute()
            supabase.table("leads").insert(
                {
                    "conversation_id": conversation_id,
                    "site_id": site_id,
                    "organization_id": site.data["organization_id"],
                    "score": qualification["score"],
                    "country": normalize_country(str(qualification.get("country") or ""))
                    or qualification.get("country"),
                    "experience_level": qualification.get("experience"),
                    "budget_range": qualification.get("budget"),
                    "availability": qualification.get("availability"),
                }
            ).execute()
            supabase.table("notifications").insert(
                {
                    "organization_id": site.data["organization_id"],
                    "type": "new_lead",
                    "title": "Nouveau lead qualifié",
                    "body": f"Score : {qualification['score']}/100",
                    "data": {"conversation_id": conversation_id, "qualification": qualification},
                }
            ).execute()

    lead_score = (qualification or {}).get("score", 0) if qualification else 0
    handoff_reason = handoff_marker or detect_handoff_reason(user_message, lead_score)
    if handoff_reason and not is_handoff_active((handoff or {}).get("handoff_status")):
        site_row = (
            supabase.table("sites")
            .select("organization_id, name")
            .eq("id", site_id)
            .single()
            .execute()
        )
        if site_row.data:
            request_handoff(
                conversation_id,
                site_row.data["organization_id"],
                handoff_reason,
                site_row.data.get("name", "Chat"),
            )
            if handoff_reason == "user_request" and "conseiller" not in clean_response.lower():
                yield "\n\nJe vous mets en relation avec un conseiller humain. Un instant…"
