"""Extraction de texte depuis PDF (texte natif puis OCR si scan)."""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

_MIN_CHARS = 30


def _clean_len(text: str) -> int:
    return len(" ".join(text.split()))


def _extract_native_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf non installé") from exc

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("PDF page extract failed: %s", exc)
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def extract_text_from_pdf(data: bytes) -> str:
    if not data:
        raise ValueError("Fichier PDF vide")

    native = _extract_native_text(data)
    if _clean_len(native) >= _MIN_CHARS:
        return native

    from app.services.pdf_ocr import extract_text_with_ocr, ocr_available

    if ocr_available():
        logger.info("PDF peu de texte natif (%s chars) — tentative OCR", _clean_len(native))
        try:
            return extract_text_with_ocr(data)
        except Exception as exc:
            logger.warning("OCR PDF failed: %s", exc)
            raise ValueError(
                "PDF scanné : l'OCR n'a pas pu lire le document. "
                "Améliorez la qualité du scan ou ajoutez une description manuelle."
            ) from exc

    raise ValueError(
        "Peu ou pas de texte lisible (PDF scanné ?). "
        "Activez Tesseract sur le serveur (OCR) ou ajoutez une description manuelle."
    )
