"""OCR pour pages PDF scannées (images) via PyMuPDF + Tesseract."""

from __future__ import annotations

import io
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def ocr_available() -> bool:
    if not settings.ocr_enabled:
        return False
    try:
        import fitz  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def extract_text_with_ocr(pdf_bytes: bytes, *, languages: str | None = None) -> str:
    if not settings.ocr_enabled:
        raise RuntimeError("OCR désactivé (OCR_ENABLED=false)")

    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Dépendances OCR manquantes (pymupdf, pytesseract, pillow)") from exc

    lang = languages or settings.ocr_languages
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts: list[str] = []
    zoom = settings.ocr_render_scale

    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                text = pytesseract.image_to_string(img, lang=lang) or ""
            except Exception as exc:
                logger.warning("OCR page %s failed: %s", page_index + 1, exc)
                text = ""
            text = text.strip()
            if text:
                parts.append(text)
    finally:
        doc.close()

    combined = "\n\n".join(parts)
    cleaned = " ".join(combined.split())
    if len(cleaned) < 30:
        raise ValueError(
            "OCR n'a pas extrait assez de texte. Vérifiez la qualité du scan "
            "ou ajoutez une description manuelle dans Sources de connaissance."
        )
    return combined
