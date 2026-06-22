"""Statistiques widget embed et liens trackés (totaux + séries jour/mois)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.services.supabase_client import get_supabase

CLICK_TYPES = ("whatsapp", "phone", "email", "signup", "session", "link")


def _to_day_key(iso: str) -> str:
    return iso[:10]


def _to_month_key(iso: str) -> str:
    return iso[:7]


def _build_day_range(days: int) -> list[tuple[str, str]]:
    now = datetime.now(timezone.utc).date()
    out: list[tuple[str, str]] = []
    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        key = d.isoformat()
        label = d.strftime("%d %b")
        out.append((key, label))
    return out


def _build_month_range(months: int) -> list[tuple[str, str]]:
    now = datetime.now(timezone.utc).date()
    y, m = now.year, now.month
    out: list[tuple[str, str]] = []
    for i in range(months - 1, -1, -1):
        total = y * 12 + (m - 1) - i
        yy, mm = divmod(total, 12)
        key = f"{yy:04d}-{mm + 1:02d}"
        label = datetime(yy, mm + 1, 1, tzinfo=timezone.utc).strftime("%b %y")
        out.append((key, label))
    return out


def _empty_series(day_range, month_range) -> tuple[list[dict], list[dict]]:
    daily = [
        {
            "period": k,
            "label": lbl,
            "opens": 0,
            "conversations": 0,
            "visitor_messages": 0,
            "clicks": 0,
        }
        for k, lbl in day_range
    ]
    monthly = [
        {
            "period": k,
            "label": lbl,
            "opens": 0,
            "conversations": 0,
            "visitor_messages": 0,
            "clicks": 0,
        }
        for k, lbl in month_range
    ]
    return daily, monthly


def _parse_clicks(raw: dict | None) -> list[dict]:
    if not raw or not isinstance(raw, dict):
        return []
    out = []
    for event_type in CLICK_TYPES:
        val = raw.get(event_type)
        if isinstance(val, int) and val > 0:
            out.append({"event_type": event_type, "count": val})
    return out


def _parse_link_interactions(raw: dict | None) -> tuple[int, list[dict]]:
    if not raw or not isinstance(raw, dict):
        return 0, []
    events = _parse_clicks(raw)
    total = raw.get("_total")
    if isinstance(total, int):
        return total, events
    return sum(e["count"] for e in events), events


def _increment(
    store: dict[str, dict],
    key: str,
    label: str,
    field: str,
) -> None:
    row = store.get(key) or {
        "period": key,
        "label": label,
        "opens": 0,
        "conversations": 0,
        "visitor_messages": 0,
        "clicks": 0,
    }
    row[field] = row.get(field, 0) + 1
    store[key] = row


def fetch_embed_analytics(site_ids: list[str]) -> dict:
    day_range = _build_day_range(30)
    month_range = _build_month_range(12)
    day_keys = {k for k, _ in day_range}
    month_keys = {k for k, _ in month_range}
    day_labels = dict(day_range)
    month_labels = dict(month_range)

    if not site_ids:
        daily, monthly = _empty_series(day_range, month_range)
        return {
            "totals": {
                "opens": 0,
                "conversations": 0,
                "visitor_messages": 0,
                "clicks": [],
            },
            "daily": daily,
            "monthly": monthly,
        }

    supabase = get_supabase()
    year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    sites = (
        supabase.table("sites")
        .select("id, name, url, agent_config")
        .in_("id", site_ids)
        .execute()
    )

    convs = (
        supabase.table("conversations")
        .select("id, created_at")
        .in_("site_id", site_ids)
        .is_("traffic_link_id", "null")
        .gte("created_at", year_ago)
        .execute()
    )

    conv_ids = [c["id"] for c in (convs.data or []) if c.get("created_at")]
    daily_map: dict[str, dict] = {}
    monthly_map: dict[str, dict] = {}
    total_visitor_messages = 0

    for row in convs.data or []:
        created = row.get("created_at")
        if not created:
            continue
        day, month = _to_day_key(created), _to_month_key(created)
        if day in day_keys:
            _increment(daily_map, day, day_labels[day], "conversations")
        if month in month_keys:
            _increment(monthly_map, month, month_labels[month], "conversations")

    for i in range(0, len(conv_ids), 500):
        batch = conv_ids[i : i + 500]
        msgs = (
            supabase.table("messages")
            .select("created_at")
            .in_("conversation_id", batch)
            .eq("role", "user")
            .gte("created_at", year_ago)
            .execute()
        )
        for row in msgs.data or []:
            total_visitor_messages += 1
            created = row.get("created_at")
            if not created:
                continue
            day, month = _to_day_key(created), _to_month_key(created)
            if day in day_keys:
                _increment(daily_map, day, day_labels[day], "visitor_messages")
            if month in month_keys:
                _increment(monthly_map, month, month_labels[month], "visitor_messages")

    try:
        events = (
            supabase.table("widget_click_events")
            .select("event_type, created_at")
            .in_("site_id", site_ids)
            .gte("created_at", year_ago)
            .execute()
        )
        for row in events.data or []:
            created = row.get("created_at")
            if not created:
                continue
            field = "opens" if row.get("event_type") == "open" else "clicks"
            day, month = _to_day_key(created), _to_month_key(created)
            if day in day_keys:
                _increment(daily_map, day, day_labels[day], field)
            if month in month_keys:
                _increment(monthly_map, month, month_labels[month], field)
    except Exception:
        pass

    total_opens = 0
    click_totals: dict[str, int] = defaultdict(int)
    for site in sites.data or []:
        config = site.get("agent_config") or {}
        embed = config.get("embed_widget_stats") or {}
        total_opens += int(embed.get("opens") or 0)
        clicks_raw = embed.get("clicks") or config.get("widget_click_stats") or {}
        if isinstance(clicks_raw, dict):
            for k, v in clicks_raw.items():
                if isinstance(v, int) and v > 0:
                    click_totals[k] += v

    daily = [
        daily_map.get(k)
        or {
            "period": k,
            "label": lbl,
            "opens": 0,
            "conversations": 0,
            "visitor_messages": 0,
            "clicks": 0,
        }
        for k, lbl in day_range
    ]
    monthly = [
        monthly_map.get(k)
        or {
            "period": k,
            "label": lbl,
            "opens": 0,
            "conversations": 0,
            "visitor_messages": 0,
            "clicks": 0,
        }
        for k, lbl in month_range
    ]

    return {
        "totals": {
            "opens": total_opens,
            "conversations": len(conv_ids),
            "visitor_messages": total_visitor_messages,
            "clicks": [{"event_type": k, "count": v} for k, v in click_totals.items() if v > 0],
        },
        "daily": daily,
        "monthly": monthly,
    }


def fetch_tracked_links_analytics(site_ids: list[str]) -> list[dict]:
    if not site_ids:
        return []

    supabase = get_supabase()
    day_range = _build_day_range(30)
    month_range = _build_month_range(12)
    day_keys = {k for k, _ in day_range}
    month_keys = {k for k, _ in month_range}
    day_labels = dict(day_range)
    month_labels = dict(month_range)
    year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    links = (
        supabase.table("traffic_links")
        .select("id, slug, source, label, click_count, site_id, sites(name, agent_config)")
        .in_("site_id", site_ids)
        .order("click_count", desc=True)
        .execute()
    )

    convs = (
        supabase.table("conversations")
        .select("id, traffic_link_id, created_at")
        .in_("site_id", site_ids)
        .not_.is_("traffic_link_id", "null")
        .gte("created_at", year_ago)
        .execute()
    )

    convs_by_link: dict[str, list[dict]] = defaultdict(list)
    for c in convs.data or []:
        lid = c.get("traffic_link_id")
        if lid:
            convs_by_link[lid].append(c)

    all_conv_ids = [c["id"] for c in (convs.data or [])]
    msg_times_by_conv: dict[str, list[str]] = defaultdict(list)
    for i in range(0, len(all_conv_ids), 500):
        batch = all_conv_ids[i : i + 500]
        msgs = (
            supabase.table("messages")
            .select("conversation_id, created_at")
            .in_("conversation_id", batch)
            .eq("role", "user")
            .gte("created_at", year_ago)
            .execute()
        )
        for m in msgs.data or []:
            cid = m.get("conversation_id")
            if cid and m.get("created_at"):
                msg_times_by_conv[cid].append(m["created_at"])

    from app.services.owner_stats import SOURCE_LABELS

    result: list[dict] = []
    for link in links.data or []:
        site = link.get("sites") or {}
        if isinstance(site, list):
            site = site[0] if site else {}
        agent_config = site.get("agent_config") or {}
        by_link = agent_config.get("tracked_link_interactions") or {}
        interaction_total, interactions = _parse_link_interactions(by_link.get(link.get("slug")))
        country_stats = parse_link_country_stats(
            (agent_config.get("tracked_link_countries") or {}).get(link.get("slug"))
        )

        link_convs = convs_by_link.get(link["id"], [])
        link_conv_ids = {c["id"] for c in link_convs}
        visitor_messages = sum(
            len(msg_times_by_conv.get(cid, [])) for cid in link_conv_ids
        )

        daily_map: dict[str, dict] = {}
        monthly_map: dict[str, dict] = {}

        for c in link_convs:
            created = c.get("created_at")
            if not created:
                continue
            day, month = _to_day_key(created), _to_month_key(created)
            if day in day_keys:
                _increment(daily_map, day, day_labels[day], "conversations")
            if month in month_keys:
                _increment(monthly_map, month, month_labels[month], "conversations")

        for cid in link_conv_ids:
            for created in msg_times_by_conv.get(cid, []):
                day, month = _to_day_key(created), _to_month_key(created)
                if day in day_keys:
                    _increment(daily_map, day, day_labels[day], "visitor_messages")
                if month in month_keys:
                    _increment(monthly_map, month, month_labels[month], "visitor_messages")

        source = link.get("source") or "direct_link"
        result.append(
            {
                "id": link["id"],
                "slug": link.get("slug"),
                "label": link.get("label"),
                "source": source,
                "source_label": SOURCE_LABELS.get(
                    source, source.replace("_", " ").title()
                ),
                "click_count": link.get("click_count") or 0,
                "site_name": site.get("name"),
                "conversations": len(link_convs),
                "visitor_messages": visitor_messages,
                "interaction_total": interaction_total,
                "interactions": interactions,
                "countries": country_stats,
                "daily": [
                    daily_map.get(k)
                    or {
                        "period": k,
                        "label": lbl,
                        "opens": 0,
                        "conversations": 0,
                        "visitor_messages": 0,
                        "clicks": 0,
                    }
                    for k, lbl in day_range
                ],
                "monthly": [
                    monthly_map.get(k)
                    or {
                        "period": k,
                        "label": lbl,
                        "opens": 0,
                        "conversations": 0,
                        "visitor_messages": 0,
                        "clicks": 0,
                    }
                    for k, lbl in month_range
                ],
            }
        )

    return result
