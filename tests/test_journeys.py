"""Parcours HTTP de haut niveau — enchaînements multi-étapes côté utilisateur.

Complète les tests par endpoint : chaque test suit un scénario réaliste
de bout en bout (recette → épicerie → mode cuisine, connexion OIDC).
"""

import html
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from recipes.shared.db import init_db, upsert_recipe

JOURNEY_RECIPE = {
    "title": "Gratin parcours",
    "description": "Plat.",
    "ingredients": [
        {"food": "pommes de terre", "quantity_min": 800, "quantity_max": None, "unit": "g"},
        {"food": "crème", "quantity_min": 200, "quantity_max": None, "unit": "ml"},
    ],
    "instructions": "Éplucher. Cuire.",
    "servings": 4,
    "steps": [
        {"text": "Éplucher", "timer_seconds": None, "ingredients": []},
        {"text": "Cuire 20 minutes", "timer_seconds": 1200, "ingredients": []},
    ],
    "source_file": "/recipes/gratin_parcours.docx",
    "file_hash": "hhh999",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()


def _page(resp) -> str:
    return html.unescape(resp.text)


def _owned_list_id(client: TestClient, name: str) -> int:
    resp = client.post("/shopping/lists", data={"name": name}, follow_redirects=False)
    assert resp.status_code == 303
    return int(resp.headers["location"].rstrip("/").split("/")[-1])


class TestRecipeToShoppingToCookJourney:
    def test_full_journey(self, client: TestClient):
        recipe_id = upsert_recipe(JOURNEY_RECIPE)

        # 1. Page recette : minuteur lisible affiché.
        page = _page(client.get(f"/recipe/{recipe_id}"))
        assert "20:00" in page

        # 2. Création d'une liste puis ajout des ingrédients de la recette.
        list_id = _owned_list_id(client, "Courses gratin")
        resp = client.post(
            f"/api/shopping/from-recipe?recipe_id={recipe_id}",
            json={"ingredient_indices": [0, 1], "list_id": list_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["list_id"] == list_id

        # 3. La liste contient les deux ingrédients.
        page = _page(client.get(f"/shopping/{list_id}"))
        assert "pommes de terre" in page
        assert "crème" in page

        # 4. Mode cuisine épicerie : cochage d'un article.
        resp = client.get(f"/shopping/{list_id}/cook")
        assert resp.status_code == 200
        items_resp = client.get(f"/shopping/{list_id}")
        assert items_resp.status_code == 200

        # 5. Suppression de la liste : elle disparaît.
        resp = client.post(f"/shopping/lists/{list_id}/delete", follow_redirects=False)
        assert resp.status_code == 303
        assert client.get(f"/shopping/{list_id}").status_code == 404


class TestAuthJourney:
    def test_login_redirects_home_when_oidc_disabled(self, client: TestClient):
        resp = client.get("/auth/login", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_callback_with_mocked_provider_logs_user_in(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Callback OIDC avec fournisseur simulé : session posée, favoris OK."""
        import recipes.features.auth.controllers as auth_controllers
        import recipes.shared.auth as shared_auth

        monkeypatch.setattr(auth_controllers, "OIDC_ENABLED", True)
        monkeypatch.setattr(shared_auth, "OIDC_ENABLED", True)
        monkeypatch.setattr(
            auth_controllers,
            "fetch_token",
            AsyncMock(
                return_value={
                    "userinfo": {
                        "sub": "journey-subject",
                        "email": "parcours@example.com",
                        "name": "Parcours",
                        "groups": [],
                    }
                }
            ),
        )
        resp = client.get("/auth/callback?code=fake&state=fake", follow_redirects=False)
        assert resp.status_code == 302, resp.text
        assert resp.headers["location"] == "/"

        # La session est posée : page favoris accessible (un anonyme est redirigé).
        resp = client.get("/favorites", follow_redirects=False)
        assert resp.status_code == 200, resp.text
        assert "Mes favoris" in _page(resp)

    def test_logout_clears_session(self, client: TestClient):
        resp = client.get("/auth/logout", follow_redirects=False)
        assert resp.status_code == 302
