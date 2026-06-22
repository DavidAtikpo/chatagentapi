"""Compteur de visites pour les liens trackés (/c/{slug})."""

from app.services.supabase_client import get_supabase


def increment_traffic_link_click(site_id: str, slug: str) -> bool:
    """Incrémente traffic_links.click_count pour une visite (ouverture lien tracké)."""
    if not slug:
        return False

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
        return False

    row = link.data[0]
    supabase.table("traffic_links").update(
        {"click_count": (row.get("click_count") or 0) + 1}
    ).eq("id", row["id"]).execute()
    return True
