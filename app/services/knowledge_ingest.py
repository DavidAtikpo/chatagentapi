"""Ingestion manuelle et documents (PDF) dans knowledge_chunks."""

from __future__ import annotations

import logging
import re

import httpx

from app.services.postgres_client import get_connection
from app.services.rag import embed_site_chunks
from app.services.supabase_client import get_supabase
from app.services.text_quality import is_readable_text

logger = logging.getLogger(__name__)

MANUAL_SOURCE = "manual://description"


def document_source_url(document_id: str) -> str:
    return f"document://{document_id}"


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if is_readable_text(chunk, min_len=30):
            chunks.append(chunk)
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def delete_web_knowledge_chunks(site_id: str) -> None:
    """Supprime uniquement les chunks issus du crawl web (garde manual + documents)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM knowledge_chunks
                WHERE site_id = %s
                  AND source_url NOT LIKE 'manual://%%'
                  AND source_url NOT LIKE 'document://%%'
                """,
                (site_id,),
            )


def _insert_chunks(site_id: str, source_url: str, title: str, text: str) -> int:
    supabase = get_supabase()
    supabase.table("knowledge_chunks").delete().eq("site_id", site_id).eq("source_url", source_url).execute()

    chunks = _chunk_text(text)
    if not chunks:
        chunks = [text[:8000]]

    for index, chunk in enumerate(chunks):
        supabase.table("knowledge_chunks").insert(
            {
                "site_id": site_id,
                "source_url": source_url,
                "title": title,
                "content": chunk,
                "chunk_index": index,
            }
        ).execute()
    return len(chunks)


async def ingest_manual_description(site_id: str, text: str, title: str = "Description de l'activité") -> int:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) < 30:
        raise ValueError("Description trop courte (30 caractères minimum)")

    supabase = get_supabase()
    supabase.table("sites").update({"business_description": cleaned}).eq("id", site_id).execute()

    count = _insert_chunks(site_id, MANUAL_SOURCE, title, cleaned)
    embedded = await embed_site_chunks(site_id)
    logger.info("Manual ingest site=%s chunks=%s embedded=%s", site_id, count, embedded)
    return embedded


async def ingest_document_pdf(
    site_id: str,
    document_id: str,
    pdf_bytes: bytes,
    title: str,
    *,
    filename: str = "",
    mime_type: str = "",
) -> int:
    from app.services.document_extract import extract_text_from_document

    supabase = get_supabase()
    source = document_source_url(document_id)

    supabase.table("site_documents").update({"status": "processing", "error_message": None}).eq(
        "id", document_id
    ).execute()

    try:
        text = extract_text_from_document(pdf_bytes, filename=filename, mime_type=mime_type)
        chunk_count = _insert_chunks(site_id, source, title, text)
        embedded = await embed_site_chunks(site_id)
        supabase.table("site_documents").update(
            {"status": "ready", "chunk_count": chunk_count, "error_message": None}
        ).eq("id", document_id).execute()
        logger.info(
            "Document ingest site=%s doc=%s chunks=%s embedded=%s",
            site_id,
            document_id,
            chunk_count,
            embedded,
        )
        return embedded
    except Exception as exc:
        msg = str(exc)[:500]
        supabase.table("site_documents").update(
            {"status": "error", "error_message": msg}
        ).eq("id", document_id).execute()
        raise


async def ingest_document_from_url(
    site_id: str,
    document_id: str,
    file_url: str,
    title: str,
    *,
    filename: str = "",
    mime_type: str = "",
) -> int:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        res = await client.get(file_url)
        res.raise_for_status()
        content_type = (mime_type or res.headers.get("content-type") or "").lower()
        name = filename or title or file_url
        lower_name = name.lower()
        if "presentationml" in content_type or lower_name.endswith((".ppt", ".pptx")):
            raise ValueError(
                "Les présentations PowerPoint ne sont pas acceptées. Exportez en PDF."
            )
        allowed = "pdf" in content_type or lower_name.endswith(".pdf")
        if not allowed:
            raise ValueError("Format accepté : PDF uniquement")
        return await ingest_document_pdf(
            site_id,
            document_id,
            res.content,
            title,
            filename=name,
            mime_type=content_type,
        )


async def delete_document_knowledge(site_id: str, document_id: str) -> None:
    supabase = get_supabase()
    source = document_source_url(document_id)
    supabase.table("knowledge_chunks").delete().eq("site_id", site_id).eq("source_url", source).execute()
