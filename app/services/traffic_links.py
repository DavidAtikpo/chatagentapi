"""Compteur de visites et pays pour les liens trackés (/c/{slug})."""

from app.services.country_utils import normalize_country
from app.services.supabase_client import get_supabase


def parse_link_country_stats(raw: dict | None) -> list[dict]:
    if not raw or not isinstance(raw, dict):
        return []
    items = [
        {"country": country, "count": int(count)}
        for country, count in raw.items()
        if isinstance(count, int) and count > 0 and country
    ]
    items.sort(key=lambda row: (-row["count"], row["country"]))
    return items


def countries_from_agent_config(agent_config: dict | None) -> list[str]:
    """Expand tracked_link_countries into a flat list for agrégation globale."""
    if not agent_config or not isinstance(agent_config, dict):
        return []
    out: list[str] = []
    by_link = agent_config.get("tracked_link_countries") or {}
    if not isinstance(by_link, dict):
        return out
    for slug_stats in by_link.values():
        if not isinstance(slug_stats, dict):
            continue
        for country, count in slug_stats.items():
            if not isinstance(count, int) or count <= 0:
                continue
            label = normalize_country(str(country))
            if label:
                out.extend([label] * count)
    return out


def collect_tracked_visit_countries(site_ids: list[str]) -> list[str]:
    if not site_ids:
        return []
    supabase = get_supabase()
    sites = (
        supabase.table("sites")
        .select("agent_config")
        .in_("id", site_ids)
        .execute()
    )
    out: list[str] = []
    for row in sites.data or []:
        out.extend(countries_from_agent_config(row.get("agent_config") or {}))
    return out


def record_traffic_link_visit(
    site_id: str,
    slug: str,
    config: dict,
    country: str | None = None,
) -> dict:
    """Incrémente click_count et enregistre le pays visiteur si connu."""
    if not slug:
        return config

    supabase = get_supabase()
    link = (
        supabase.table("traffic_links")
        .select("id, click_count")
        .eq("site_id", site_id)
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    if not link.data:
        return config

    row = link.data[0]
    supabase.table("traffic_links").update(
        {"click_count": (row.get("click_count") or 0) + 1}
    ).eq("id", row["id"]).execute()

    normalized = normalize_country(country)
    if normalized:
        by_link = dict(config.get("tracked_link_countries") or {})
        link_countries = dict(by_link.get(slug) or {})
        link_countries[normalized] = int(link_countries.get(normalized) or 0) + 1
        by_link[slug] = link_countries
        config["tracked_link_countries"] = by_link

    return config
