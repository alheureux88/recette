"""test_not_found.py — Pages 404 contextuelles (recette / épicerie / générique)."""

from recipes.shared.errors import infer_not_found_variant

HTML_HEADERS = {"Accept": "text/html"}


def test_infer_variant():
    assert infer_not_found_variant("/recipe/12") == "recipe"
    assert infer_not_found_variant("/recipe/12/cook") == "recipe"
    assert infer_not_found_variant("/favorites") == "recipe"
    assert infer_not_found_variant("/shopping/3") == "shopping"
    assert infer_not_found_variant("/shopping/shared/abc") == "shopping"
    assert infer_not_found_variant("/nimporte-quoi") == "generic"
    assert infer_not_found_variant("/admin/bogus") == "generic"


def test_recipe_404_html_fr(client):
    resp = client.get("/recipe/9999", headers=HTML_HEADERS)
    assert resp.status_code == 404
    assert "🍳" in resp.text
    assert "Recette introuvable" in resp.text
    assert "Parcourir les recettes" in resp.text
    assert "/recipe/9999" in resp.text


def test_recipe_404_html_en(client):
    resp = client.get("/recipe/9999", headers=HTML_HEADERS, cookies={"lang": "en"})
    assert resp.status_code == 404
    assert "Recipe not found" in resp.text
    assert "Browse recipes" in resp.text


def test_recipe_404_json_preserved_without_html_accept(client):
    resp = client.get("/recipe/9999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Recette introuvable"}


def test_shopping_404_html(client):
    resp = client.get("/shopping/9999", headers=HTML_HEADERS)
    assert resp.status_code == 404
    assert "🛒" in resp.text
    assert "Liste introuvable" in resp.text
    assert "/shopping" in resp.text


def test_shopping_404_json_preserved(client):
    resp = client.get("/shopping/9999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Liste de courses introuvable"}


def test_generic_404_html(client):
    resp = client.get("/cette-page-nexiste-pas", headers=HTML_HEADERS)
    assert resp.status_code == 404
    assert "🧭" in resp.text
    assert "Page introuvable" in resp.text


def test_generic_404_html_en(client):
    resp = client.get("/cette-page-nexiste-pas", headers=HTML_HEADERS, cookies={"lang": "en"})
    assert resp.status_code == 404
    assert "Page not found" in resp.text


def test_notfound_keys_translated_fr_en():
    from recipes.shared.i18n import TRANSLATIONS, gettext

    keys = [k for k in TRANSLATIONS if k.startswith("notfound.")]
    assert len(keys) >= 9
    for key in keys:
        assert gettext(key, "fr") not in ("", key)
        assert gettext(key, "en") not in ("", key)
