from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

Phase = Literal["crawling", "embedding", "done"]
Status = Literal["pending", "running", "completed", "failed"]

_store: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_progress(site_id: str, pages_total: int = 30) -> None:
    _store[site_id] = {
        "status": "running",
        "phase": "crawling",
        "pages_done": 0,
        "pages_total": pages_total,
        "chunks_done": 0,
        "chunks_total": 0,
        "current_url": None,
        "message": "Démarrage du crawl…",
        "updated_at": _now(),
    }


def update_crawl_page(site_id: str, pages_done: int, pages_total: int, current_url: str) -> None:
    entry = _store.setdefault(site_id, {})
    entry.update(
        {
            "status": "running",
            "phase": "crawling",
            "pages_done": pages_done,
            "pages_total": pages_total,
            "current_url": current_url,
            "message": f"Page {pages_done}/{pages_total} indexée",
            "updated_at": _now(),
        }
    )


def start_embedding(site_id: str, chunks_total: int) -> None:
    entry = _store.setdefault(site_id, {})
    entry.update(
        {
            "status": "running",
            "phase": "embedding",
            "chunks_done": 0,
            "chunks_total": chunks_total,
            "current_url": None,
            "message": f"Analyse sémantique : 0/{chunks_total} sections",
            "updated_at": _now(),
        }
    )


def update_embedding(site_id: str, chunks_done: int, chunks_total: int) -> None:
    entry = _store.setdefault(site_id, {})
    pct = round((chunks_done / chunks_total) * 100) if chunks_total else 0
    entry.update(
        {
            "status": "running",
            "phase": "embedding",
            "chunks_done": chunks_done,
            "chunks_total": chunks_total,
            "message": f"Analyse sémantique : {chunks_done}/{chunks_total} ({pct} %)",
            "updated_at": _now(),
        }
    )


def complete_progress(site_id: str, pages_crawled: int, chunks_embedded: int) -> None:
    _store[site_id] = {
        "status": "completed",
        "phase": "done",
        "pages_done": pages_crawled,
        "pages_total": pages_crawled,
        "chunks_done": chunks_embedded,
        "chunks_total": chunks_embedded,
        "current_url": None,
        "message": f"Terminé — {pages_crawled} page(s), {chunks_embedded} section(s)",
        "updated_at": _now(),
    }


def fail_progress(site_id: str, message: str = "Échec du crawl", error_code: str | None = None) -> None:
    entry = _store.setdefault(site_id, {})
    entry.update(
        {
            "status": "failed",
            "message": message,
            "error_code": error_code,
            "updated_at": _now(),
        }
    )


def get_progress(site_id: str) -> dict | None:
    return _store.get(site_id)
