from fastapi import APIRouter

from app.services.supabase_client import get_supabase

router = APIRouter()


@router.get("/leads/{organization_id}")
async def list_leads(organization_id: str, limit: int = 50):
    supabase = get_supabase()
    result = (
        supabase.table("leads")
        .select("*, conversations(id, page_url, traffic_link_id)")
        .eq("organization_id", organization_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"leads": result.data or []}


@router.get("/conversations/{site_id}")
async def list_conversations(site_id: str, limit: int = 50):
    supabase = get_supabase()
    result = (
        supabase.table("conversations")
        .select("*, messages(count)")
        .eq("site_id", site_id)
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"conversations": result.data or []}


@router.get("/stats/{organization_id}")
async def organization_stats(organization_id: str):
    supabase = get_supabase()
    sites = supabase.table("sites").select("id").eq("organization_id", organization_id).execute()
    site_ids = [s["id"] for s in (sites.data or [])]

    if not site_ids:
        return {"conversations": 0, "leads": 0, "avg_score": 0}

    conversations = (
        supabase.table("conversations").select("id", count="exact").in_("site_id", site_ids).execute()
    )
    leads = supabase.table("leads").select("score").eq("organization_id", organization_id).execute()
    scores = [lead["score"] for lead in (leads.data or []) if lead.get("score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    return {
        "conversations": conversations.count or 0,
        "leads": len(leads.data or []),
        "avg_score": avg_score,
    }
