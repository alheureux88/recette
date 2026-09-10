"""Tests for the favorites toggle + favorites page state."""

import pytest

from recipes.features.recipes.services import add_favorite
from recipes.shared.db import get_conn, init_db, upsert_recipe


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject) VALUES (?, ?)",
            (1, "test-1"),
        )


@pytest.fixture()
def as_user(client, monkeypatch):
    fake_user = {"id": 1, "sub": "test-1", "name": "Test", "groups": []}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    for namespace in (
        "recipes.shared.auth.get_user",
        "recipes.shared.web.get_user",
        "recipes.features.recipes.controllers.get_user",
    ):
        monkeypatch.setattr(namespace, lambda request: fake_user)
    return client


def _insert_sample():
    return upsert_recipe(
        {
            "title": "Poulet Rôti",
            "description": "Un classique",
            "source_file": "/recipes/poulet.docx",
            "file_hash": "aaa111",
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


def test_favorites_page_shows_toggled_state(as_user):
    recipe_id = _insert_sample()
    add_favorite(1, recipe_id)

    resp = as_user.get("/favorites")
    assert resp.status_code == 200
    # L'icône doit être togglée dès l'ouverture (pas ☆).
    assert "is-favorite" in resp.text
    assert "★" in resp.text


def test_toggle_favorite_roundtrip(as_user):
    recipe_id = _insert_sample()

    first = as_user.post(f"/favorites/{recipe_id}")
    assert first.status_code == 200
    assert "is-favorite" in first.text
    assert "★" in first.text

    second = as_user.post(f"/favorites/{recipe_id}")
    assert second.status_code == 200
    assert "is-favorite" not in second.text
    assert "☆" in second.text


def test_unfavorited_recipe_disappears_from_favorites_page(as_user):
    recipe_id = _insert_sample()
    add_favorite(1, recipe_id)
    assert as_user.get("/favorites").status_code == 200

    resp = as_user.post(f"/favorites/{recipe_id}")
    assert resp.status_code == 200
    assert "is-favorite" not in resp.text

    page = as_user.get("/favorites")
    assert page.status_code == 200
    assert "Poulet Rôti" not in page.text
