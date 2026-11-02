"""Extraction texte documents sources (PDF)."""

from __future__ import annotations


def extract_text_from_document(data: bytes, *, filename: str = "", mime_type: str = "") -> str:
    name = filename.lower()
    mime = mime_type.lower()

    if (
        mime in ("application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        or name.endswith((".ppt", ".pptx"))
    ):
        raise ValueError(
            "Les présentations PowerPoint ne sont pas acceptées. Exportez votre fichier en PDF."
        )

    from app.services.pdf_extract import extract_text_from_pdf

    return extract_text_from_pdf(data)
