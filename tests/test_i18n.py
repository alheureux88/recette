"""Tests for the i18n front-end layer (FR/EN UI strings only)."""

import pytest

from recipes.db import init_db, sync_recipe_tags, upsert_recipe
from recipes.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    gettext,
    ngettext,
    resolve_language,
)

SAMPLE = {
    "title": "Poulet Rôti",
    "description": "Simple French roast chicken.",
    "ingredients": [
        {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
    ],
    "instructions": "Roast.",
    "servings": 4,
    "category": "plat-principal",
    "tags": {"origin": ["francais"]},
    "source_file": "/recipes/poulet.docx",
    "file_hash": "aaa111",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()
    rid = upsert_recipe(SAMPLE)
    sync_recipe_tags(rid, SAMPLE["tags"])


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_supported_languages_exposes_fr_and_en():
    assert "fr" in SUPPORTED_LANGUAGES
    assert "en" in SUPPORTED_LANGUAGES


def test_default_language_is_fr():
    assert DEFAULT_LANGUAGE == "fr"


def test_resolve_language_cookie_wins():
    assert resolve_language("en", "fr-FR,fr;q=0.9") == "en"


def test_resolve_language_accept_language_fallback():
    assert resolve_language(None, "en-GB,en;q=0.9,fr;q=0.5") == "en"
    assert resolve_language(None, "fr-CA,fr;q=0.9") == "fr"


def test_resolve_language_defaults_when_no_signal():
    assert resolve_language(None, None) == DEFAULT_LANGUAGE


def test_resolve_language_invalid_cookie_falls_back_to_header():
    assert resolve_language("zz", "en") == "en"


def test_resolve_language_invalid_header_falls_back_to_default():
    assert resolve_language(None, "xx-YY,zz;q=0.9") == DEFAULT_LANGUAGE


def test_gettext_returns_fr_by_default():
    assert gettext("nav.favorites", "fr") == "Mes favoris"


def test_gettext_returns_en_when_requested():
    assert gettext("nav.favorites", "en") == "My favorites"


def test_gettext_missing_key_returns_key_unchanged():
    assert gettext("totally.missing.key", "en") == "totally.missing.key"
    assert gettext("totally.missing.key", "fr") == "totally.missing.key"


def test_gettext_format_substitutes_values():
    msg = gettext("flash.dropbox_name_taken", "en", name="Famille")
    assert "Famille" in msg
    assert "already exists" in msg


def test_gettext_keeps_unknown_placeholders_intact():
    # The message has only {n}; passing an extra kwarg should be a no-op.
    msg = gettext("admin.confirm_apply_category", "en", n=3, unknown="ignored")
    assert "{n}" not in msg
    assert "3" in msg


def test_gettext_keeps_missing_named_placeholder():
    # Use a message containing {name} but pass nothing for it.
    msg = gettext("flash.dropbox_name_taken", "en")
    # Missing {name} placeholder should be kept literally.
    assert "{name}" in msg


def test_ngettext_singular_fr():
    msg = ngettext("home.subtitle_one", "home.subtitle_other", 1, "fr")
    assert "1" in msg
    assert "recette" in msg


def test_ngettext_plural_fr():
    msg = ngettext("home.subtitle_one", "home.subtitle_other", 5, "fr")
    assert "5" in msg
    assert "recettes" in msg


def test_ngettext_uses_en_singular_rule_n_equals_1():
    assert ngettext("home.subtitle_one", "home.subtitle_other", 1, "en") == "1 recipe to discover"


def test_ngettext_uses_en_plural_rule_n_diff_from_1():
    msg = ngettext("home.subtitle_one", "home.subtitle_other", 0, "en")
    assert "0 recipes" in msg


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
def test_index_default_lang_is_fr(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "1 recette" in resp.text
    assert "Rechercher des recettes" in resp.text


def test_index_in_en_via_cookie(client):
    resp = client.get("/", cookies={"lang": "en"})
    assert resp.status_code == 200
    assert "1 recipe to discover" in resp.text
    assert "Search recipes" in resp.text
    assert "Rechercher des recettes" not in resp.text


def test_index_in_en_via_accept_language(client):
    resp = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert resp.status_code == 200
    assert "Search recipes" in resp.text


def test_html_lang_attribute_reflects_active_language(client):
    fr = client.get("/")
    assert '<html lang="fr">' in fr.text
    en = client.get("/", cookies={"lang": "en"})
    assert '<html lang="en">' in en.text


def test_lang_switch_endpoint_sets_cookie_and_redirects(client):
    resp = client.get("/lang/en", headers={"Referer": "https://app/"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://app/"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "lang=en" in set_cookie


def test_lang_switch_unknown_code_returns_404(client):
    resp = client.get("/lang/zz")
    assert resp.status_code == 404


def test_recipe_detail_translated_404(client):
    resp = client.get("/recipe/9999", cookies={"lang": "en"})
    assert resp.status_code == 404
    assert "Recipe not found" in resp.text


def test_recipe_detail_translated_404_fr_default(client):
    resp = client.get("/recipe/9999")
    assert resp.status_code == 404
    assert "Recette introuvable" in resp.text


def test_recipe_detail_en_uses_english_ingredients_labels(client):
    resp = client.get("/recipe/1", cookies={"lang": "en"})
    page = resp.text
    assert "Ingredients" in page
    assert "Servings" in page
    assert "Units" in page
    assert "Original recipe" in page
    assert "Ingredients</h2>" in page or "Ingredients</h2>" in page


def test_recipe_cook_en_titles(client):
    resp = client.get("/recipe/1/cook", cookies={"lang": "en"})
    assert resp.status_code == 200
    assert "Ingredients" in resp.text
    assert "Steps" in resp.text
    assert "Reset" in resp.text


def test_index_pluralization_in_en(client):
    # One recipe → singular form
    one = client.get("/", cookies={"lang": "en"})
    assert "1 recipe to discover" in one.text
    assert "recipes to discover" not in one.text


def test_index_pluralization_in_fr(client):
    one = client.get("/", cookies={"lang": "fr"})
    assert "1 recette à découvrir" in one.text
    assert "recettes à découvrir" not in one.text


def test_favorites_page_in_en(client):
    resp = client.get("/favorites", cookies={"lang": "en"}, follow_redirects=False)
    # OIDC not configured in tests → redirect to "/"
    assert resp.status_code == 302


def test_lang_switcher_shows_only_other_language_in_fr(client):
    resp = client.get("/")
    assert 'href="/lang/en"' in resp.text
    assert 'href="/lang/fr"' not in resp.text
    # aria-label localisé
    assert "Passer en en" in resp.text or "Switch to en" in resp.text


def test_lang_switcher_shows_only_other_language_in_en(client):
    resp = client.get("/", cookies={"lang": "en"})
    assert 'href="/lang/fr"' in resp.text
    assert 'href="/lang/en"' not in resp.text
