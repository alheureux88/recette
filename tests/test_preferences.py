"""Tests for the preferences feature — JSON-blob user settings."""

import json

import pytest

from recipes.features.preferences.services import (
    DEFAULT_PREFERENCES,
    get_department_order,
    get_preferences,
    save_preferences,
)
from recipes.features.shopping.services import (
    apply_department_order,
    create_shopping_list,
    get_shopping_departments,
)
from recipes.shared.db import get_conn, get_recipe, init_db, upsert_recipe


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    # `user_preferences.user_id` référence `users(id)` : chaque test a ses usagers.
    with get_conn() as conn:
        for uid in (1, 2, 3):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, subject) VALUES (?, ?)",
                (uid, f"test-{uid}"),
            )


@pytest.fixture()
def as_user(client, monkeypatch):
    fake_user = {"id": 1, "sub": "test-1", "name": "Test", "groups": []}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    # `shopping/controllers.py` importe `get_user` directement : le patcher aussi.
    for namespace in (
        "recipes.shared.auth.get_user",
        "recipes.features.shopping.controllers.get_user",
        "recipes.features.recipes.controllers.get_user",
    ):
        monkeypatch.setattr(namespace, lambda request: fake_user)
    return client


class TestPreferencesServices:
    def test_defaults_for_unknown_user(self):
        prefs = get_preferences(999)
        assert prefs["language"] == "fr"
        assert prefs["units"] == "original"
        assert prefs["theme"] == "system"
        assert prefs["print_images"] is False
        assert prefs["print_tags"] is False
        assert prefs["print_description"] is False
        assert prefs["print_links"] is False
        assert prefs["print_step_ingredients"] is False
        assert prefs["show_step_ingredients"] is True
        assert prefs["department_order"] == []

    def test_save_and_reload_roundtrip(self):
        saved = save_preferences(
            1,
            {
                "language": "en",
                "units": "metric",
                "theme": "dark",
                "print_images": True,
                "print_links": True,
                "department_order": ["boulangerie", "fruits-legumes"],
            },
        )
        assert saved["language"] == "en"
        assert saved["units"] == "metric"
        assert saved["theme"] == "dark"
        assert saved["print_images"] is True
        assert saved["print_tags"] is False
        assert saved["department_order"] == ["boulangerie", "fruits-legumes"]
        assert get_preferences(1) == saved

    def test_invalid_values_keep_previous(self):
        save_preferences(1, {"language": "en", "units": "imperial", "theme": "dark"})
        saved = save_preferences(1, {"language": "de", "units": "lightyears", "theme": "neon"})
        assert saved["language"] == "en"
        assert saved["units"] == "imperial"
        assert saved["theme"] == "dark"

    def test_unknown_keys_are_ignored(self):
        saved = save_preferences(1, {"future_setting": "value", "language": "en"})
        assert "future_setting" not in saved
        assert saved["language"] == "en"

    def test_stored_unknown_keys_do_not_break_read(self):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO user_preferences (user_id, prefs) VALUES (?, ?)",
                (2, json.dumps({"future_setting": 1, "language": "xx"})),
            )
        prefs = get_preferences(2)
        assert prefs["language"] == DEFAULT_PREFERENCES["language"]
        assert prefs["units"] == DEFAULT_PREFERENCES["units"]

    def test_corrupt_json_falls_back_to_defaults(self):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO user_preferences (user_id, prefs) VALUES (?, ?)",
                (3, "not-json{{{"),
            )
        assert get_preferences(3)["language"] == "fr"

    def test_department_order_is_cleaned(self):
        saved = save_preferences(
            1,
            {"department_order": ["b", "a", "b", "  ", 42, "a", " c "]},
        )
        assert saved["department_order"] == ["b", "a", "c"]

    def test_checkbox_coercion(self):
        saved = save_preferences(
            1,
            {"print_images": "on", "print_tags": "false", "print_links": 1},
        )
        assert saved["print_images"] is True
        assert saved["print_tags"] is False
        assert saved["print_links"] is True

    def test_get_department_order_anonymous(self):
        assert get_department_order(None) == []

    def test_get_department_order_user(self):
        assert get_department_order(1) == []
        save_preferences(1, {"department_order": ["boulangerie"]})
        assert get_department_order(1) == ["boulangerie"]


class TestApplyDepartmentOrder:
    def test_empty_order_is_noop(self):
        departments = get_shopping_departments()
        assert apply_department_order(departments, []) == departments
        assert apply_department_order(departments, None) == departments

    def test_reorders_by_name(self):
        departments = get_shopping_departments()
        names = [str(d["name"]) for d in departments]
        reversed_names = list(reversed(names))
        ordered = apply_department_order(departments, reversed_names)
        assert [str(d["name"]) for d in ordered] == reversed_names

    def test_unknown_names_ignored_and_missing_appended(self):
        departments = get_shopping_departments()
        ordered = apply_department_order(departments, ["nope", "boulangerie"])
        assert str(ordered[0]["name"]) == "boulangerie"
        assert len(ordered) == len(departments)


class TestPreferencesPages:
    def test_page_requires_login(self, client):
        resp = client.get("/preferences", follow_redirects=False)
        assert resp.status_code == 302

    def test_page_renders_for_user(self, as_user):
        resp = as_user.get("/preferences")
        assert resp.status_code == 200
        assert "preferences" in resp.text.lower() or "préférences" in resp.text.lower()

    def test_save_form_persists_and_sets_lang_cookie(self, as_user):
        resp = as_user.post(
            "/preferences",
            data={
                "language": "en",
                "units": "metric",
                "print_images": "on",
                "department_order": "boulangerie,fruits-legumes",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "lang=en" in (resp.headers.get("set-cookie") or "")
        prefs = get_preferences(1)
        assert prefs["language"] == "en"
        assert prefs["units"] == "metric"
        assert prefs["print_images"] is True
        assert prefs["print_tags"] is False
        assert prefs["department_order"] == ["boulangerie", "fruits-legumes"]

    def test_save_form_requires_login(self, client):
        resp = client.post("/preferences", data={"language": "en"}, follow_redirects=False)
        assert resp.status_code == 302

    def test_save_order_api_requires_login(self, client):
        # 401 → le handler redirige vers "/" (OIDC désactivé en test).
        resp = client.post(
            "/api/preferences/departments",
            json={"order": ["boulangerie"]},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_save_order_api_rejects_unknown_names(self, as_user):
        resp = as_user.post("/api/preferences/departments", json={"order": ["nope"]})
        assert resp.status_code == 400

    def test_save_order_api_roundtrip(self, as_user):
        resp = as_user.post(
            "/api/preferences/departments",
            json={"order": ["boulangerie", "fruits-legumes"]},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert get_department_order(1) == ["boulangerie", "fruits-legumes"]

    def test_save_form_theme_sets_cookie(self, as_user):
        resp = as_user.post(
            "/preferences",
            data={"theme": "dark"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "theme=dark" in (resp.headers.get("set-cookie") or "")
        assert get_preferences(1)["theme"] == "dark"

    def test_save_form_theme_system_clears_cookie(self, as_user):
        save_preferences(1, {"theme": "dark"})
        resp = as_user.post(
            "/preferences",
            data={"theme": "system"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert get_preferences(1)["theme"] == "system"

    def test_theme_api_requires_login(self, client):
        resp = client.post(
            "/api/preferences/theme",
            json={"theme": "dark"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_theme_api_rejects_invalid_theme(self, as_user):
        resp = as_user.post("/api/preferences/theme", json={"theme": "neon"})
        assert resp.status_code == 400

    def test_theme_api_roundtrip_sets_cookie(self, as_user):
        resp = as_user.post("/api/preferences/theme", json={"theme": "light"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "theme": "light"}
        assert "theme=light" in (resp.headers.get("set-cookie") or "")
        assert get_preferences(1)["theme"] == "light"


class TestLoginAppliesPreferences:
    def test_callback_restores_display_cookies(self, client):
        """À la connexion : langue + thème restaurés en cookies.

        Les autres réglages (unités, impression, départements) sont lus
        en base à chaque requête : appliqués par construction.
        """
        from unittest.mock import AsyncMock, patch

        from recipes.features.auth.services import get_or_create_user

        user_id = get_or_create_user("user123", email="t@example.com", name="TU")
        save_preferences(
            user_id,
            {
                "language": "en",
                "theme": "dark",
                "units": "metric",
                "print_images": True,
                "department_order": ["boulangerie"],
            },
        )
        mock_token = {
            "userinfo": {
                "sub": "user123",
                "email": "t@example.com",
                "name": "TU",
                "groups": [],
            }
        }
        with (
            patch("recipes.features.auth.controllers.OIDC_ENABLED", True),
            patch(
                "recipes.features.auth.controllers.fetch_token",
                new=AsyncMock(return_value=mock_token),
            ),
        ):
            resp = client.get("/auth/callback?code=test", follow_redirects=False)
        assert resp.status_code == 302
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(c.startswith("lang=en") for c in set_cookies)
        assert any(c.startswith("theme=dark") for c in set_cookies)

    def test_callback_defaults_set_lang_cookie_only(self, client):
        """Nouvel usager (préférences par défaut) : cookie lang, pas de thème."""
        from unittest.mock import AsyncMock, patch

        mock_token = {
            "userinfo": {
                "sub": "fresh-user",
                "email": "f@example.com",
                "name": "Fresh",
                "groups": [],
            }
        }
        with (
            patch("recipes.features.auth.controllers.OIDC_ENABLED", True),
            patch(
                "recipes.features.auth.controllers.fetch_token",
                new=AsyncMock(return_value=mock_token),
            ),
        ):
            resp = client.get("/auth/callback?code=test", follow_redirects=False)
        assert resp.status_code == 302
        set_cookies = resp.headers.get_list("set-cookie")
        assert any(c.startswith("lang=") for c in set_cookies)
        assert not any(
            c.startswith("theme=dark") or c.startswith("theme=light") for c in set_cookies
        )


def _insert_structured_recipe() -> int:
    return upsert_recipe(
        {
            "source_file": "/recipes/prefs.docx",
            "file_hash": "prefs1",
            "category": "plat-principal",
            "lang": {
                "title": "Prefs Soup",
                "description": "",
                "steps": [],
                "ingredients": [
                    {
                        "food": "farine",
                        "quantity_min": 1,
                        "quantity_max": None,
                        "unit": "tasse",
                    }
                ],
            },
        }
    )


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


class TestPreferencesIntegration:
    def test_recipe_uses_units_preference_by_default(self, as_user):
        recipe_id = _insert_structured_recipe()
        save_preferences(1, {"units": "metric"})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert 'value="metric" selected' in resp.text

    def test_recipe_explicit_units_win_over_preference(self, as_user):
        recipe_id = _insert_structured_recipe()
        save_preferences(1, {"units": "metric"})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}?units=imperial")
        assert resp.status_code == 200
        assert 'value="imperial" selected' in resp.text

    def test_recipe_applies_print_preferences(self, as_user):
        recipe_id = _insert_structured_recipe()
        save_preferences(1, {"print_images": True, "print_tags": True})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert 'id="print-images" checked' in resp.text
        assert 'id="print-tags" checked' in resp.text
        assert 'id="print-links" checked' not in resp.text
        assert 'id="print-step-ingredients" checked' not in resp.text

    def test_recipe_applies_print_step_ingredients_preference(self, as_user):
        recipe_id = _insert_structured_recipe()
        save_preferences(1, {"print_step_ingredients": True})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert 'id="print-step-ingredients" checked' in resp.text

    def test_save_form_persists_print_step_ingredients(self, as_user):
        resp = as_user.post(
            "/preferences",
            data={"print_step_ingredients": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert get_preferences(1)["print_step_ingredients"] is True

    def test_save_form_persists_show_step_ingredients(self, as_user):
        resp = as_user.post(
            "/preferences",
            data={"show_step_ingredients": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert get_preferences(1)["show_step_ingredients"] is True

    def test_save_form_unchecked_show_step_ingredients_turns_off(self, as_user):
        save_preferences(1, {"show_step_ingredients": True})
        resp = as_user.post(
            "/preferences",
            data={"language": "fr"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert get_preferences(1)["show_step_ingredients"] is False

    def test_preferences_page_renders_show_step_ingredients(self, as_user):
        resp = as_user.get("/preferences")
        assert resp.status_code == 200
        assert 'name="show_step_ingredients"' in resp.text
        assert "Affichage" in resp.text or "Display" in resp.text

    def test_recipe_toggle_checked_by_default(self, as_user):
        recipe_id = _insert_structured_recipe()
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert 'id="toggle-step-ingredients" checked' in resp.text

    def test_recipe_toggle_unchecked_when_preference_off(self, as_user):
        recipe_id = _insert_structured_recipe()
        save_preferences(1, {"show_step_ingredients": False})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert 'id="toggle-step-ingredients"' in resp.text
        assert 'id="toggle-step-ingredients" checked' not in resp.text

    def test_cook_toggle_follows_preference(self, as_user):
        recipe_id = upsert_recipe(
            {
                "source_file": "/recipes/prefs_cook.docx",
                "file_hash": "prefscook1",
                "category": "plat-principal",
                "lang": {
                    "title": "Prefs Cook",
                    "description": "",
                    "steps": [{"text": "Mélanger", "timer_seconds": None}],
                    "ingredients": [],
                },
            }
        )
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}/cook")
        assert resp.status_code == 200
        assert 'id="toggle-cook-step-ingredients" checked' in resp.text
        save_preferences(1, {"show_step_ingredients": False})
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}/cook")
        assert 'id="toggle-cook-step-ingredients"' in resp.text
        assert 'id="toggle-cook-step-ingredients" checked' not in resp.text

    def test_shopping_list_follows_department_order(self, as_user):
        lst = create_shopping_list("Pref List", user_id=1)
        save_preferences(1, {"department_order": ["boulangerie", "fruits-legumes"]})
        resp = as_user.get(f"/shopping/{lst['id']}")
        assert resp.status_code == 200
        pos_boulangerie = resp.text.find("Boulangerie")
        pos_fruits = resp.text.find("Fruits")
        assert pos_boulangerie != -1 and pos_fruits != -1
        assert pos_boulangerie < pos_fruits
