import re

_BINARY_MARKERS = ("IHDR", "IDAT", "JFIF", "GIF89a", "PNG", "PDF-", "ftyp", "webp")


def is_readable_text(text: str, min_len: int = 50) -> bool:
    cleaned = text.strip()
    if len(cleaned) < min_len:
        return False

    sample = cleaned[:2000]
    upper = sample.upper()
    if any(marker in upper for marker in _BINARY_MARKERS):
        if "IHDR" in upper and "IDAT" in upper:
            return False
        if sample.startswith("PNG") or "PNG IHDR" in upper:
            return False

    replacement = sample.count("\ufffd") + sample.count("�")
    if replacement > max(3, len(sample) * 0.01):
        return False

    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\t\r")
    if printable / len(sample) < 0.88:
        return False

    letters = sum(1 for ch in sample if ch.isalpha())
    if letters < 20:
        return False

    return True


def is_html_content_type(content_type: str) -> bool:
    lowered = content_type.lower()
    return "text/html" in lowered or "application/xhtml" in lowered


def should_skip_crawl_url(url: str) -> bool:
    path = url.lower()
    if "/wp-content/uploads/" in path:
        return True
    if path.endswith(
        (
            ".mp4",
            ".webm",
            ".mov",
            ".avi",
            ".pdf",
            ".zip",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
            ".css",
            ".js",
            ".woff",
            ".woff2",
            ".ico",
        )
    ):
        return True
    return False


def filter_text_chunks(chunks: list[dict]) -> list[dict]:
    valid: list[dict] = []
    for chunk in chunks:
        content = chunk.get("content", "")
        if is_readable_text(content):
            valid.append(chunk)
    return valid


def clean_text_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
