"""test_error_pages.py — Pages d'erreur 403 / 422 / 500 (HTML navigateur, JSON API)."""

import unittest.mock as mock

from recipes.features.shopping.services import create_shopping_list

HTML_HEADERS = {"Accept": "text/html"}


def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise RuntimeError("boom")


def test_403_html_forbidden_delete(client):
    victim = create_shopping_list("Victim 403")
    resp = client.post(f"/shopping/lists/{victim['id']}/delete", headers=HTML_HEADERS)
    assert resp.status_code == 403
    assert "🔒" in resp.text
    assert "Accès refusé" in resp.text


def test_403_json_preserved(client):
    victim = create_shopping_list("Victim 403 json")
    resp = client.post(f"/shopping/lists/{victim['id']}/delete")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Action non autorisée"}


def test_400_keeps_default_json_behavior(client):
    """Les codes sans handler dédié (400) gardent le JSON natif FastAPI."""
    created = client.post("/shopping/lists", data={"name": "Owned"}, follow_redirects=False)
    list_id = created.headers["location"].rsplit("/", 1)[-1]
    resp = client.post(f"/shopping/lists/{list_id}/rename", data={"name": "   "})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Le nom est requis"}


def test_422_html_invalid_category(client):
    resp = client.get("/search?category=abc", headers=HTML_HEADERS)
    assert resp.status_code == 422
    assert "⚠️" in resp.text
    assert "Requête invalide" in resp.text


def test_422_json_preserved(client):
    resp = client.get("/search?category=abc")
    assert resp.status_code == 422
    assert resp.json() == {"detail": "La catégorie doit être un entier valide"}


def test_422_html_request_validation(client):
    resp = client.get("/shopping/abc", headers=HTML_HEADERS)
    assert resp.status_code == 422
    assert "Requête invalide" in resp.text


def test_422_json_request_validation_shape(client):
    resp = client.get("/shopping/abc")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)


def test_500_html_and_json(temp_db, monkeypatch):
    from fastapi.testclient import TestClient

    from recipes.shared.db import get_recipe, init_db, upsert_recipe

    init_db()
    recipe_id = upsert_recipe(
        {
            "title": "Soupe boom",
            "description": "",
            "steps": [],
            "ingredients": [],
            "source_file": "/recipes/boom.docx",
            "file_hash": "boom1",
        }
    )
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    slug = str(recipe["slug"])

    monkeypatch.setattr("recipes.features.recipes.controllers.get_recipe", _boom)
    with mock.patch("recipes.main.poll_dropbox"):
        from recipes.main import app

        with TestClient(app, raise_server_exceptions=False) as raw:
            html = raw.get(f"/recipe/{slug}", headers=HTML_HEADERS)
            assert html.status_code == 500
            assert "Erreur serveur" in html.text
            assert "boom" not in html.text  # pas de fuite de détail interne

            api = raw.get(f"/recipe/{slug}")
            assert api.status_code == 500
            assert api.json() == {
                "detail": "Une erreur inattendue s'est produite. Réessayez dans un instant."
            }


def test_errorpage_keys_translated_fr_en():
    from recipes.shared.i18n import TRANSLATIONS, gettext

    keys = [k for k in TRANSLATIONS if k.startswith("errorpage.")]
    assert len(keys) >= 7
    for key in keys:
        assert gettext(key, "fr") not in ("", key)
        assert gettext(key, "en") not in ("", key)
