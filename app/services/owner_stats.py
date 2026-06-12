"""Statistiques dashboard — 1 organisation, tous les sites agrégés."""

from collections import Counter

from app.services.country_utils import normalize_country
from app.services.supabase_client import get_supabase

SOURCE_LABELS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter / X",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "email": "Email",
    "direct_link": "Lien direct",
    "other": "Autre",
}


def _country_from_qualification(data: dict | None) -> str | None:
    if not data or not isinstance(data, dict):
        return None
    raw = data.get("country")
    if not isinstance(raw, str):
        return None
    return normalize_country(raw.strip())


def _aggregate_countries(raw_countries: list[str]) -> list[dict]:
    counter: Counter[str] = Counter()
    for item in raw_countries:
        label = normalize_country(item)
        if label:
            counter[label] += 1
    return [{"country": k, "count": v} for k, v in counter.most_common()]


def fetch_owner_stats(user_id: str) -> dict | None:
    """Stats agrégées sur toute l'organisation et tous ses sites."""
    supabase = get_supabase()
    orgs = (
        supabase.table("organizations")
        .select("id, name")
        .eq("owner_id", user_id)
        .execute()
    )
    if not orgs.data:
        return None

    org_rows = orgs.data
    org_ids = [o["id"] for o in org_rows]
    org_name = org_rows[0]["name"]

    sites = (
        supabase.table("sites")
        .select("id, name")
        .in_("organization_id", org_ids)
        .order("name")
        .execute()
    )
    site_rows = sites.data or []
    site_ids = [s["id"] for s in site_rows]

    site_summaries: list[dict] = []
    conv_count = 0
    for site in site_rows:
        site_conv = (
            supabase.table("conversations")
            .select("id", count="exact")
            .eq("site_id", site["id"])
            .execute()
        )
        n = site_conv.count or 0
        conv_count += n
        site_summaries.append({"id": site["id"], "name": site["name"], "conversations": n})

    leads = (
        supabase.table("leads")
        .select("id", count="exact")
        .in_("organization_id", org_ids)
        .execute()
    )

    tracked_links: list[dict] = []
    if site_ids:
        links = (
            supabase.table("traffic_links")
            .select("id, slug, source, label, click_count, site_id, sites(name)")
            .in_("site_id", site_ids)
            .order("click_count", desc=True)
            .execute()
        )
        for row in links.data or []:
            site = row.get("sites") or {}
            site_name = site.get("name") if isinstance(site, dict) else None
            source = row.get("source") or "direct_link"
            tracked_links.append(
                {
                    "id": row["id"],
                    "slug": row.get("slug"),
                    "label": row.get("label"),
                    "source": source,
                    "source_label": SOURCE_LABELS.get(source, source.replace("_", " ").title()),
                    "click_count": row.get("click_count") or 0,
                    "site_name": site_name,
                }
            )

    countries_raw: list[str] = []
    if site_ids:
        convs = (
            supabase.table("conversations")
            .select("qualification_data")
            .in_("site_id", site_ids)
            .not_.is_("qualification_data", "null")
            .execute()
        )
        for row in convs.data or []:
            c = _country_from_qualification(row.get("qualification_data"))
            if c:
                countries_raw.append(c)

        lead_rows = (
            supabase.table("leads")
            .select("country")
            .in_("site_id", site_ids)
            .not_.is_("country", "null")
            .execute()
        )
        for row in lead_rows.data or []:
            c = normalize_country(row.get("country"))
            if c:
                countries_raw.append(c)

    return {
        "organization_name": org_name,
        "conversations": conv_count,
        "leads": leads.count or 0,
        "tracked_links": tracked_links,
        "countries": _aggregate_countries(countries_raw),
        "sites_count": len(site_ids),
        "sites": site_summaries,
    }
