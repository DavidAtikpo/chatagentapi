from app.services.page_fetcher import html_needs_js_render


def test_html_needs_js_render_detects_spa_shell():
    html = """
    <!DOCTYPE html><html><head><title>App</title></head>
    <body><div id="root"></div>
    <script src="/app.js"></script><script src="/vendor.js"></script>
    </body></html>
    """
    assert html_needs_js_render(html) is True


def test_html_needs_js_render_accepts_static_page():
    html = """
    <!DOCTYPE html><html><head><title>CI.DES</title></head>
    <body><h1>Formations professionnelles</h1>
    <p>Centre de formation certifié ISO 9712 pour les inspections non destructives.</p>
    <p>Nous proposons des sessions CND, cordistes IRATA et bien plus encore.</p>
    </body></html>
    """
    assert html_needs_js_render(html) is False
