import asyncio
import logging
import re

from openai import AsyncOpenAI

from app.config import settings
from app.services.crawl_progress import start_embedding, update_embedding
from app.services.supabase_client import get_supabase
from app.services.text_quality import filter_text_chunks

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset(
    {
        "cest",
        "quand",
        "comment",
        "pourquoi",
        "where",
        "what",
        "when",
        "how",
        "the",
        "and",
        "for",
        "vous",
        "nous",
        "dans",
        "avec",
        "pour",
        "une",
        "des",
        "les",
        "est",
        "son",
        "ses",
        "sur",
        "par",
        "qui",
        "que",
        "quoi",
        "quel",
        "quelle",
        "quels",
        "quelles",
    }
)


async def embed_text(text: str) -> list[float]:
    if not settings.openai_api_key:
        raise RuntimeError("OpenAI API key is required for embeddings")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text,
        dimensions=settings.embedding_dimensions,
    )
    return response.data[0].embedding


async def embed_site_chunks(site_id: str) -> int:
    supabase = get_supabase()
    result = (
        supabase.table("knowledge_chunks")
        .select("id, content")
        .eq("site_id", site_id)
        .is_("embedding", "null")
        .execute()
    )
    rows = result.data or []
    total = len(rows)
    start_embedding(site_id, total)
    if total == 0:
        update_embedding(site_id, 0, 0)
        return 0
    count = 0
    for row in rows:
        try:
            embedding = await embed_text(row["content"])
            supabase.table("knowledge_chunks").update({"embedding": embedding}).eq("id", row["id"]).execute()
            count += 1
            update_embedding(site_id, count, total)
        except Exception as exc:
            logger.warning("Embedding failed for chunk %s: %s", row["id"], exc)
    return count


_GENERIC_KEYWORDS = frozenset(
    {"formation", "session", "date", "calendrier", "inscription", "information", "informations"}
)

_CND_QUERY_TERMS = (
    "cnd",
    "ndt",
    "inspection",
    "destructif",
    "ultrason",
    "9712",
    "ut me",
    "ressuage",
    "radiographie",
)


def _query_keywords(query: str) -> list[str]:
    lowered = query.lower()
    words = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", lowered)
    keywords = [w for w in words if len(w) > 2 and w not in _STOPWORDS]

    if any(term in lowered for term in _CND_QUERY_TERMS):
        for extra in ("cnd", "ndt", "inspection", "iso", "9712", "xof", "euro", "cout", "tarif"):
            if extra not in keywords:
                keywords.append(extra)

    if any(
        w in lowered
        for w in ("formation", "quand", "date", "session", "calendrier", "prix", "tarif", "combien")
    ):
        for extra in ("formation", "session", "date", "calendrier", "inscription"):
            if extra not in keywords:
                keywords.append(extra)

    keywords.sort(key=lambda k: (k in _GENERIC_KEYWORDS, k == "formation"))

    seen: set[str] = set()
    ordered: list[str] = []
    for word in keywords:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return ordered[:8]


def get_site_overview(site_id: str, limit: int = 3) -> list[dict]:
    """Representative chunks (homepage-first) to ground identity questions.

    Always available regardless of the query, so the assistant can answer
    'what does this company do?' from the real crawled site instead of guessing
    from the label the client typed in the dashboard.
    """
    supabase = get_supabase()
    rows = (
        supabase.table("knowledge_chunks")
        .select("id, content, title, source_url, chunk_index")
        .eq("site_id", site_id)
        .order("chunk_index")
        .limit(limit * 6)
        .execute()
    )
    chunks = filter_text_chunks(rows.data or [])
    # Prefer the homepage (shortest URL path), then earliest chunks.
    chunks.sort(key=lambda c: (len(c.get("source_url", "")), c.get("chunk_index", 0)))
    return chunks[:limit]


def _search_by_source_pattern(site_id: str, pattern: str, limit: int = 4) -> list[dict]:
    supabase = get_supabase()
    rows = (
        supabase.table("knowledge_chunks")
        .select("id, content, title, source_url")
        .eq("site_id", site_id)
        .ilike("source_url", f"%{pattern}%")
        .order("chunk_index")
        .limit(limit)
        .execute()
    )
    return filter_text_chunks(rows.data or [])


def _topic_boost_chunks(site_id: str, query: str, limit: int = 4) -> list[dict]:
    lowered = query.lower()
    if not any(term in lowered for term in _CND_QUERY_TERMS):
        return []
    chunks = _search_by_source_pattern(site_id, "formation-inspection", limit=limit)
    if chunks:
        return chunks
    supabase = get_supabase()
    rows = (
        supabase.table("knowledge_chunks")
        .select("id, content, title, source_url")
        .eq("site_id", site_id)
        .ilike("content", "%CND%")
        .ilike("content", "%ISO 9712%")
        .limit(limit)
        .execute()
    )
    return filter_text_chunks(rows.data or [])


def search_knowledge_text(site_id: str, query: str, limit: int = 8) -> list[dict]:
    """Fallback keyword search when vector RAG is empty or unavailable."""
    keywords = _query_keywords(query)
    if not keywords:
        return []

    supabase = get_supabase()
    results: list[dict] = []
    seen: set[str] = set()

    for word in keywords[:3]:
        rows = (
            supabase.table("knowledge_chunks")
            .select("id, content, title, source_url")
            .eq("site_id", site_id)
            .ilike("content", f"%{word}%")
            .limit(limit)
            .execute()
        )
        for row in filter_text_chunks(rows.data or []):
            row_id = row["id"]
            if row_id in seen:
                continue
            seen.add(row_id)
            results.append(row)
            if len(results) >= limit:
                return results

    return results


def _vector_search_chunks(site_id: str, query_embedding: list[float], limit: int) -> list[dict]:
    supabase = get_supabase()
    result = supabase.rpc(
        "match_knowledge",
        {
            "query_embedding": query_embedding,
            "match_site_id": site_id,
            "match_count": limit,
            "match_threshold": 0.45,
        },
    ).execute()
    return result.data or []


async def _vector_search(site_id: str, query: str, limit: int) -> list[dict]:
    try:
        query_embedding = await embed_text(query)
        return await asyncio.to_thread(_vector_search_chunks, site_id, query_embedding, limit)
    except Exception as exc:
        logger.warning("Vector RAG failed: %s", exc)
        return []


def _merge_chunks(target: list[dict], seen: set[str], rows: list[dict], limit: int) -> None:
    for row in rows:
        row_id = row.get("id")
        if row_id and row_id not in seen:
            seen.add(row_id)
            target.append(row)
        if len(target) >= limit:
            return


async def search_knowledge(site_id: str, query: str, limit: int = 8) -> list[dict]:
    chunks: list[dict] = []
    seen: set[str] = set()

    topic_rows, vector_rows = await asyncio.gather(
        asyncio.to_thread(_topic_boost_chunks, site_id, query, 4),
        _vector_search(site_id, query, limit),
    )
    _merge_chunks(chunks, seen, topic_rows, limit)
    _merge_chunks(chunks, seen, vector_rows, limit)

    if len(chunks) < 3:
        text_rows = await asyncio.to_thread(search_knowledge_text, site_id, query, limit)
        _merge_chunks(chunks, seen, text_rows, limit)

    if chunks:
        logger.info("RAG found %s chunk(s) for site %s", len(chunks), site_id)
    else:
        logger.warning("RAG found no chunks for site %s — run crawl + embeddings", site_id)

    return chunks[:limit]
