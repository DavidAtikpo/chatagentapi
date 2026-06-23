from app.services.crawl_errors import (
    CrawlErrorCode,
    detect_block_from_response,
    diagnose_empty_crawl,
)


def test_detect_cloudflare():
    html = "<html><body>Just a moment... Checking your browser before accessing. Ray ID abc</body></html>"
    failure = detect_block_from_response(html, 200)
    assert failure is not None
    assert failure.code == CrawlErrorCode.CLOUDFLARE


def test_detect_403_forbidden():
    failure = detect_block_from_response("<html>Forbidden</html>", 403)
    assert failure is not None
    assert failure.code == CrawlErrorCode.HTTP_FORBIDDEN


def test_detect_timeout():
    failure = detect_block_from_response(None, None, network_error="Read timed out")
    assert failure is not None
    assert failure.code == CrawlErrorCode.TIMEOUT


def test_diagnose_robots_blocked():
    failure = diagnose_empty_crawl("https://example.com", robots_blocked=True)
    assert failure.code == CrawlErrorCode.ROBOTS_TXT


def test_diagnose_js_render_after_playwright():
    html = '<html><body><div id="root"></div><script></script><script></script></body></html>'
    failure = diagnose_empty_crawl(
        "https://example.com",
        homepage_html=html,
        homepage_status=200,
        playwright_tried=True,
        playwright_available=True,
    )
    assert failure.code == CrawlErrorCode.JS_RENDER
