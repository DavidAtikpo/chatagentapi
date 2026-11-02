"""Extraction de texte depuis PowerPoint (.pptx)."""

from __future__ import annotations

import io


def extract_text_from_pptx(data: bytes) -> str:
    if not data:
        raise ValueError("Fichier PowerPoint vide")
    try:
        from pptx import Presentation
    except ImportError as exc:
        raise RuntimeError("python-pptx non installé") from exc

    prs = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if not hasattr(shape, "text"):
                continue
            text = (shape.text or "").strip()
            if text:
                parts.append(text)

    combined = "\n\n".join(parts)
    cleaned = " ".join(combined.split())
    if len(cleaned) < 30:
        raise ValueError(
            "Peu de texte dans la présentation. Ajoutez du contenu textuel ou exportez en PDF."
        )
    return combined
