"""Branded site detection.

Some clients (e.g. CI.DES) have bespoke formation catalogs, fixed pricing and a
custom welcome. That logic must ONLY apply to their own domains — never to other
clients who sign up with their own website. Add new branded domains here.
"""

from urllib.parse import urlparse

CIDES_DOMAINS = ("cides.tf",)


def _domain(site_url: str) -> str:
    netloc = urlparse(site_url or "").netloc.lower()
    if not netloc and site_url:
        # Allow passing a bare domain without scheme.
        netloc = urlparse("https://" + site_url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _matches(site_url: str, domains: tuple[str, ...]) -> bool:
    d = _domain(site_url)
    if not d:
        return False
    return any(d == base or d.endswith("." + base) for base in domains)


def is_cides_site(site_url: str) -> bool:
    """True only for CI.DES owned domains."""
    return _matches(site_url, CIDES_DOMAINS)
