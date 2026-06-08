import re


def sanitize_url(url: str) -> str:
    return re.sub(r"\s+", "", (url or "").strip())


def fix_urls_in_text(text: str) -> str:
    text = re.sub(
        r"https?://[^\s\n]+",
        lambda m: sanitize_url(m.group(0)),
        text,
        flags=re.I,
    )
    text = re.sub(r"(\b[a-z0-9-]+\.)\s+([a-z]{2,6})(?=\/|\b)", r"\1\2", text, flags=re.I)
    return text
