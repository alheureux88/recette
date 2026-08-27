"""Tests for admin panel — DB functions, routes, and poller blacklist check."""

from unittest.mock import MagicMock, patch

import pytest

from recipes.db import (
    add_favorite,
    blacklist_and_delete_recipe,
    bulk_update_category,
    bulk_update_tags,
    get_all_recipes_admin,
    get_blacklisted_files,
    get_failed_files,
    get_or_create_user,
    get_processed_hash,
    get_recipe,
    init_db,
    is_blacklisted,
    is_manually_edited,
    mark_processed,
    record_failed_file,
    remove_failed_file,
    remove_from_blacklist,
    sync_recipe_tags,
    update_recipe_category,
    update_recipe_manual,
    update_recipe_tags,
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


@pytest.fixture()
def as_admin(client, monkeypatch):
    monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
    )
    return client


@pytest.fixture()
def as_user(client, monkeypatch):
    monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": []},
    )
    return client


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
        # Les recettes sont rendues cote client (Tabulator) via /admin/recipes.json
        assert 'id="recipes-table"' in resp.text

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


class TestUpdateRecipeManual:
    def test_updates_fields_and_sets_manual_flag(self):
        recipe_id = _insert_sample()
        assert not is_manually_edited("/recipes/poulet.docx")

        update_recipe_manual(
            recipe_id,
            {
                "title": "Poulet Rôti modifié",
                "description": "Nouvelle description",
                "ingredients": [
                    {"food": "poulet", "quantity_min": 1.5, "quantity_max": None, "unit": "kg"}
                ],
                "instructions": "Nouvelles instructions",
                "servings": 6,
                "category": "dessert",
                "source_url": "https://example.com",
            },
        )

        recipe = get_recipe(recipe_id)
        assert recipe["title"] == "Poulet Rôti modifié"
        assert recipe["description"] == "Nouvelle description"
        assert recipe["instructions"] == "Nouvelles instructions"
        assert recipe["servings"] == 6
        assert recipe["category"]["name"] == "dessert"
        assert recipe["source_url"] == "https://example.com"
        assert recipe["ingredients"] == [
            {"food": "poulet", "quantity_min": 1.5, "quantity_max": None, "unit": "kg"}
        ]
        assert is_manually_edited("/recipes/poulet.docx")

    def test_invalid_servings_becomes_none(self):
        recipe_id = _insert_sample()
        update_recipe_manual(
            recipe_id,
            {"title": "T", "ingredients": [], "servings": "abc", "category": None},
        )
        assert get_recipe(recipe_id)["servings"] is None

    def test_nonexistent_recipe_returns_false(self):
        assert update_recipe_manual(9999, {"title": "T", "ingredients": []}) is False

    def test_admin_query_exposes_manual_flag(self):
        recipe_id = _insert_sample()
        assert get_all_recipes_admin()[0]["manually_edited"] == 0
        update_recipe_manual(recipe_id, {"title": "T", "ingredients": []})
        assert get_all_recipes_admin()[0]["manually_edited"] == 1


class TestPollerManualEditCheck:
    def _make_entry(self):
        entry = MagicMock()
        entry.name = "poulet.docx"
        entry.path_lower = "/recipes/poulet.docx"
        entry.client_modified = None
        return entry

    def test_skips_manually_edited_recipe(self):
        from recipes.poller import process_file

        recipe_id = _insert_sample()
        update_recipe_manual(recipe_id, {"title": "Manuel", "ingredients": []})

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = b"changed content"
        mock_dbx.files_download.return_value = (None, mock_response)

        with patch("recipes.poller.tag_recipe") as mock_tag:
            process_file(mock_dbx, self._make_entry())
            mock_tag.assert_not_called()

        failed = get_failed_files()
        assert any("manuellement" in f["error"] for f in failed)
        assert get_recipe(recipe_id)["title"] == "Manuel"

    def test_non_manual_recipe_still_processed(self):
        from recipes.poller import process_file

        _insert_sample()

        mock_dbx = MagicMock()
        mock_response = MagicMock()
        mock_response.content = b"changed content"
        mock_dbx.files_download.return_value = (None, mock_response)
        mock_dbx.sharing_list_shared_links.return_value = MagicMock(links=[])

        with (
            patch("recipes.poller.extract_text", return_value="Recipe text"),
            patch(
                "recipes.poller.tag_recipe",
                return_value={
                    "lang_fr": {
                        "title": "Poulet Rôti",
                        "description": None,
                        "ingredients": [],
                        "instructions": None,
                    },
                    "lang_en": {
                        "title": "Poulet Rôti",
                        "description": None,
                        "ingredients": [],
                        "instructions": None,
                    },
                    "tags": {},
                    "category": None,
                    "source_url": None,
                },
            ) as mock_tag,
        ):
            process_file(mock_dbx, self._make_entry())
            mock_tag.assert_called_once()


class TestAdminEditRoutes:
    def test_edit_form_requires_admin(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": []},
        )
        resp = client.get("/admin/edit/1")
        assert resp.status_code == 403

    def test_edit_form_shows_recipe(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        recipe_id = _insert_sample()
        resp = client.get(f"/admin/edit/{recipe_id}")
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text
        assert "ing_food" in resp.text

    def test_edit_form_unknown_recipe(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        resp = client.get("/admin/edit/9999")
        assert resp.status_code == 404

    def test_edit_save_updates_and_marks_manual(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        recipe_id = _insert_sample()

        resp = client.post(
            f"/admin/edit/{recipe_id}",
            data={
                "title": "Poulet modifié",
                "description": "Desc",
                "instructions": "Étape 1.\nÉtape 2.",
                "servings": "4",
                "category": "dessert",
                "source_url": "https://example.com",
                "ing_min": ["1,5", ""],
                "ing_max": ["2", ""],
                "ing_unit": ["tasse", ""],
                "ing_food": ["farine", "sel au goût"],
                "tags_origin": ["francais"],
                "tags_protein": ["poulet"],
            },
        )
        assert resp.status_code == 200

        recipe = get_recipe(recipe_id)
        assert recipe["title"] == "Poulet modifié"
        assert recipe["servings"] == 4
        assert recipe["category"]["name"] == "dessert"
        assert recipe["ingredients"] == [
            {"food": "farine", "quantity_min": 1.5, "quantity_max": 2.0, "unit": "tasse"},
            {"food": "sel au goût", "quantity_min": None, "quantity_max": None, "unit": None},
        ]
        assert is_manually_edited("/recipes/poulet.docx")
        origin_names = [t["name"] for t in recipe["tags"]["origin"]["tags"]]
        assert "francais" in origin_names

    def test_edit_save_requires_title(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
        )
        recipe_id = _insert_sample()
        resp = client.post(f"/admin/edit/{recipe_id}", data={"title": ""})
        assert resp.status_code == 422


class TestAdminQuickFilters:
    def _insert(self, **overrides):
        data = {**SAMPLE, **overrides}
        recipe_id = upsert_recipe(data)
        sync_recipe_tags(recipe_id, data.get("tags", {}))
        return recipe_id

    def test_filter_no_tags(self):
        self._insert()
        self._insert(
            title="Sans tags",
            source_file="/r/sans_tags.docx",
            file_hash="ccc",
            tags={},
        )
        recipes = get_all_recipes_admin("no_tags")
        assert [r["title"] for r in recipes] == ["Sans tags"]

    def test_filter_no_category(self):
        self._insert()
        self._insert(
            title="Sans catégorie",
            source_file="/r/sans_cat.docx",
            file_hash="ddd",
            category=None,
        )
        recipes = get_all_recipes_admin("no_category")
        assert [r["title"] for r in recipes] == ["Sans catégorie"]

    def test_filter_no_tags_keeps_categorized(self):
        self._insert(title="Catégorisée sans tags", tags={}, category="dessert")
        recipes = get_all_recipes_admin("no_tags")
        assert [r["title"] for r in recipes] == ["Catégorisée sans tags"]

    def test_invalid_filter_returns_all(self):
        self._insert()
        assert len(get_all_recipes_admin("bogus")) == 1

    def test_filter_chips_rendered(self, as_admin):
        _insert_sample()
        resp = as_admin.get("/admin")
        assert "Sans étiquettes" in resp.text
        assert "Sans catégorie" in resp.text


class TestRecipeAdminData:
    def test_recipes_json_payload(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        recipe_id = _insert_sample()
        add_favorite(1, recipe_id)

        resp = as_admin.get("/admin/recipes.json")
        assert resp.status_code == 200

        data = resp.json()
        row = next(r for r in data["recipes"] if r["id"] == recipe_id)
        assert row["title"] == "Poulet Rôti"
        assert row["category_name"] == "plat-principal"
        assert row["category_display_name"]
        assert row["favorite_count"] == 1
        assert row["manually_edited"] is False
        assert {"family": "origin", "name": "francais"} in [
            {"family": t["family"], "name": t["name"]} for t in row["tags"]
        ]
        assert [c["name"] for c in data["categories"]]
        assert "origin" in data["tags"]

    def test_recipes_json_requires_admin(self, as_user):
        resp = as_user.get("/admin/recipes.json")
        assert resp.status_code == 403

    def test_files_json_payload(self, as_admin):
        record_failed_file("/r/bad.xyz", "unsupported")
        _insert_sample()
        blacklist_and_delete_recipe(1)

        resp = as_admin.get("/admin/files.json")
        assert resp.status_code == 200

        data = resp.json()
        assert data["blacklisted"][0]["path"] == "/recipes/poulet.docx"
        assert data["blacklisted"][0]["provenance"] == "Défaut"
        assert data["failed"][0]["path"] == "/r/bad.xyz"
        assert data["failed"][0]["error"] == "unsupported"
        assert data["failed"][0]["provenance"] == "Défaut"

    def test_files_json_requires_admin(self, as_user):
        resp = as_user.get("/admin/files.json")
        assert resp.status_code == 403


class TestUpdateCategoryAndTags:
    def test_update_category_sets_manual_flag(self):
        recipe_id = _insert_sample()
        assert update_recipe_category(recipe_id, "dessert")
        recipe = get_recipe(recipe_id)
        assert recipe["category"]["name"] == "dessert"
        assert is_manually_edited("/recipes/poulet.docx")

    def test_update_category_none_clears_category(self):
        recipe_id = _insert_sample()
        update_recipe_category(recipe_id, None)
        assert get_recipe(recipe_id)["category"] is None

    def test_update_category_unknown_recipe(self):
        assert update_recipe_category(9999, "dessert") is False

    def test_update_tags_replaces_and_marks_manual(self):
        recipe_id = _insert_sample()
        assert update_recipe_tags(recipe_id, {"protein": ["boeuf"]})
        tags = get_recipe(recipe_id)["tags"]
        assert set(tags.keys()) == {"protein"}
        protein_names = {t["name"] for t in tags["protein"]["tags"]}
        assert protein_names == {"boeuf"}
        assert is_manually_edited("/recipes/poulet.docx")

    def test_update_tags_unknown_recipe(self):
        assert update_recipe_tags(9999, {"protein": ["boeuf"]}) is False


class TestBulkOperations:
    def test_bulk_update_category(self):
        r1 = _insert_sample()
        r2 = _insert_sample(
            {**SAMPLE, "title": "Tarte", "source_file": "/r/tarte.docx", "file_hash": "bbb"}
        )
        assert bulk_update_category([r1, r2], "soupe") == 2
        for rid in (r1, r2):
            recipe = get_recipe(rid)
            assert recipe["category"]["name"] == "soupe"
            assert recipe["manually_edited"] == 1

    def test_bulk_update_category_empty_ids(self):
        assert bulk_update_category([], "soupe") == 0

    def test_bulk_add_and_remove_tags(self):
        r1 = _insert_sample()
        r2 = _insert_sample(
            {**SAMPLE, "title": "Tarte", "source_file": "/r/tarte.docx", "file_hash": "bbb"}
        )
        assert (
            bulk_update_tags(
                [r1, r2],
                {"diet": ["vegetarien"]},
                {"protein": ["poulet"]},
            )
            == 2
        )

        for rid in (r1, r2):
            tags = get_recipe(rid)["tags"]
            family_names = {f: {t["name"] for t in info["tags"]} for f, info in tags.items()}
            assert "vegetarien" in family_names.get("diet", set())
            assert "poulet" not in family_names.get("protein", set())
            assert "francais" in family_names.get("origin", set())

    def test_bulk_unknown_tags_touches_nothing(self):
        r1 = _insert_sample()
        assert bulk_update_tags([r1], {}, {"diet": ["inexistant"]}) == 0
        assert get_recipe(r1)["manually_edited"] == 0


class TestInlineAndBulkRoutes:
    def test_inline_category_route(self, as_admin):
        recipe_id = _insert_sample()
        resp = as_admin.post(f"/admin/inline/{recipe_id}/category", json={"category": "soupe"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert get_recipe(recipe_id)["category"]["name"] == "soupe"
        assert is_manually_edited("/recipes/poulet.docx")

    def test_inline_tags_route(self, as_admin):
        recipe_id = _insert_sample()
        resp = as_admin.post(f"/admin/inline/{recipe_id}/tags", json={"tags": ["protein:porc"]})
        assert resp.status_code == 200
        tags = get_recipe(recipe_id)["tags"]
        assert set(tags.keys()) == {"protein"}
        assert {t["name"] for t in tags["protein"]["tags"]} == {"porc"}

    def test_inline_unknown_recipe_404(self, as_admin):
        resp = as_admin.post("/admin/inline/9999/category", json={"category": "soupe"})
        assert resp.status_code == 404

    def test_inline_requires_admin(self, as_user):
        resp = as_user.post("/admin/inline/1/category", json={"category": "soupe"})
        assert resp.status_code == 403

    def test_bulk_routes(self, as_admin):
        r1 = _insert_sample()
        r2 = _insert_sample(
            {**SAMPLE, "title": "Tarte", "source_file": "/r/tarte.docx", "file_hash": "bbb"}
        )
        resp = as_admin.post("/admin/bulk/category", json={"ids": [r1, r2], "category": "entree"})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        resp = as_admin.post(
            "/admin/bulk/tags",
            json={"ids": [r1], "add": ["diet:paleo"], "remove": []},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert "paleo" in {
            t["name"] for t in get_recipe(r1)["tags"].get("diet", {}).get("tags", [])
        }

    def test_bulk_requires_admin(self, as_user):
        resp = as_user.post("/admin/bulk/category", json={"ids": [1], "category": None})
        assert resp.status_code == 403


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

    def test_unblacklist_clears_processed_hash(self):
        """Regression: unblacklist must drop the processed_files entry too,
        otherwise the poller skips the re-ingestion thinking nothing changed.
        """
        _insert_sample()
        mark_processed("/recipes/poulet.docx", "abc123")
        blacklist_and_delete_recipe(1)
        assert get_processed_hash("/recipes/poulet.docx") == "abc123"
        remove_from_blacklist("/recipes/poulet.docx")
        assert get_processed_hash("/recipes/poulet.docx") is None

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
