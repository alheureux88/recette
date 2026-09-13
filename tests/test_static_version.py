"""Tests cache-busting des assets statiques (hash ?v= calculé au startup)."""

import hashlib
from pathlib import Path

from recipes.shared.assets import CSS_VERSION, asset_url, asset_version
from recipes.shared.web import templates


def _md5_8(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def test_css_version_matches_file_hash():
    css = Path("static/css/style.css")
    assert css.is_file()
    assert _md5_8(css) == CSS_VERSION
    assert len(CSS_VERSION) == 8


def test_js_versions_match_file_hash():
    for rel in ("js/timers.js", "js/wake_lock.js"):
        assert asset_version(rel) == _md5_8(Path("static") / rel)


def test_missing_asset_falls_back():
    assert asset_version("css/nonexistent-xyz.css") == "dev"


def test_jinja_globals_exposed():
    assert templates.env.globals["css_v"] == CSS_VERSION
    assert templates.env.globals["asset_url"] is asset_url


def test_asset_url_format():
    assert asset_url("css/style.css") == f"/static/css/style.css?v={CSS_VERSION}"


def test_home_page_uses_versioned_css(client):
    page = client.get("/").text
    assert f"/static/css/style.css?v={CSS_VERSION}" in page
    assert 'href="/static/css/style.css"' not in page


def test_versioned_static_has_immutable_header(client):
    resp = client.get(f"/static/css/style.css?v={CSS_VERSION}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_unversioned_static_has_no_immutable_header(client):
    resp = client.get("/static/css/style.css")
    assert resp.status_code == 200
    assert "immutable" not in resp.headers.get("Cache-Control", "")
