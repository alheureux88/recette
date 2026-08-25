"""Tests for admin panel — DB functions, routes, and poller blacklist check."""

from unittest.mock import MagicMock, patch

import pytest

from recipes.db import (
    blacklist_and_delete_recipe,
    get_all_recipes_admin,
    get_blacklisted_files,
    get_failed_files,
    init_db,
    is_blacklisted,
    record_failed_file,
    remove_failed_file,
    remove_from_blacklist,
    sync_recipe_tags,
    upsert_recipe,
)

SAMPLE = {
    "title": "Poulet Rôti",
    "description": "Simple French roast chicken.",
    "ingredients": ["chicken", "garlic"],
    "instructions": "Season. Roast.",
    "category": "plat-principal",
    "tags": {
        "origin": ["francais"],
        "protein": ["poulet"],
    },
    "source_file": "/recipes/poulet.docx",
    "file_hash": "aaa111",
    "file_modified_at": "2024-06-15T10:30:00",
}


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _insert_sample(data=None):
    d = data or SAMPLE
    recipe_id = upsert_recipe(d)
    sync_recipe_tags(recipe_id, d.get("tags", {}))
    return recipe_id


class TestBlacklist:
    def test_blacklist_and_delete(self):
        recipe_id = _insert_sample()
        source = blacklist_and_delete_recipe(recipe_id)
        assert source == "/recipes/poulet.docx"
        assert is_blacklisted("/recipes/poulet.docx")

    def test_blacklisted_file_stays_blacklisted(self):
        recipe_id = _insert_sample()
        blacklist_and_delete_recipe(recipe_id)
        assert is_blacklisted("/recipes/poulet.docx")
        assert is_blacklisted("/recipes/nonexistent.docx") is False

    def test_blacklist_nonexistent_recipe(self):
        result = blacklist_and_delete_recipe(9999)
        assert result is None

    def test_blacklist_idempotent(self):
        recipe_id = _insert_sample()
        blacklist_and_delete_recipe(recipe_id)
        blacklist_and_delete_recipe(recipe_id)
        assert is_blacklisted("/recipes/poulet.docx")


class TestGetAllRecipesAdmin:
    def test_returns_all_recipes(self):
        _insert_sample()
        _insert_sample(
            {**SAMPLE, "title": "Tarte", "source_file": "/r/tarte.docx", "file_hash": "bbb"}
        )
        recipes = get_all_recipes_admin()
        assert len(recipes) == 2

    def test_includes_category_and_tags(self):
        _insert_sample()
        recipes = get_all_recipes_admin()
        assert len(recipes) == 1
        r = recipes[0]
        assert r["category"] is not None
        assert r["category"]["name"] == "plat-principal"
        assert len(r["tags"]) > 0

    def test_includes_file_modified_at(self):
        _insert_sample()
        recipes = get_all_recipes_admin()
        assert recipes[0]["file_modified_at"] == "2024-06-15T10:30:00"

    def test_empty_db(self):
        assert get_all_recipes_admin() == []


class TestPollerBlacklistCheck:
    def test_skips_blacklisted_file(self):
        from recipes.poller import process_file

        _insert_sample()
        blacklist_and_delete_recipe(1)

        mock_dbx = MagicMock()
        mock_entry = MagicMock()
        mock_entry.name = "poulet.docx"
        mock_entry.path_lower = "/recipes/poulet.docx"

        with patch("recipes.poller.download_file") as mock_dl:
            process_file(mock_dbx, mock_entry)
            mock_dl.assert_not_called()


class TestAdminRoutes:
    def test_admin_requires_auth(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr("recipes.auth.get_user", lambda request: None)
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 302

    def test_admin_requires_admin_group(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": []},
        )
        resp = client.get("/admin")
        assert resp.status_code == 403

    def test_admin_accessible_to_admin(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        _insert_sample()
        resp = client.get("/admin")
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text

    def test_blacklist_endpoint(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        recipe_id = _insert_sample()
        resp = client.post(f"/admin/blacklist/{recipe_id}")
        assert resp.status_code == 200
        assert is_blacklisted("/recipes/poulet.docx")


class TestBlacklistManagement:
    def test_get_blacklisted_files(self):
        _insert_sample()
        blacklist_and_delete_recipe(1)
        blacklisted = get_blacklisted_files()
        assert len(blacklisted) == 1
        assert blacklisted[0]["path"] == "/recipes/poulet.docx"

    def test_remove_from_blacklist(self):
        _insert_sample()
        blacklist_and_delete_recipe(1)
        assert is_blacklisted("/recipes/poulet.docx")
        remove_from_blacklist("/recipes/poulet.docx")
        assert not is_blacklisted("/recipes/poulet.docx")

    def test_unblacklist_endpoint(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        _insert_sample()
        blacklist_and_delete_recipe(1)
        assert is_blacklisted("/recipes/poulet.docx")
        resp = client.post("/admin/unblacklist?path=/recipes/poulet.docx")
        assert resp.status_code == 200
        assert not is_blacklisted("/recipes/poulet.docx")


class TestFailedFiles:
    def test_record_failed_file(self):
        record_failed_file("/recipes/bad.xyz", "Unsupported extension: .xyz")
        failed = get_failed_files()
        assert len(failed) == 1
        assert failed[0]["path"] == "/recipes/bad.xyz"
        assert "Unsupported" in failed[0]["error"]

    def test_remove_failed_file(self):
        record_failed_file("/recipes/bad.xyz", "Error")
        remove_failed_file("/recipes/bad.xyz")
        failed = get_failed_files()
        assert len(failed) == 0

    def test_retry_failed_endpoint(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        record_failed_file("/recipes/bad.xyz", "Error")
        resp = client.post("/admin/retry-failed?path=/recipes/bad.xyz")
        assert resp.status_code == 200
        failed = get_failed_files()
        assert len(failed) == 0
