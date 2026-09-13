"""Tests for tagger versioning and admin retag endpoints."""

import json
from unittest.mock import MagicMock, patch

import pytest

from recipes.features.auth.services import get_or_create_user
from recipes.shared.db import get_recipe, init_db, upsert_recipe
from recipes.shared.tagger import TAGGER_VERSION, reset_client, tag_recipe

SAMPLE = {
    "title": "Poulet Rôti",
    "description": "Simple roast chicken.",
    "ingredients": [],
    "steps": [],
    "category": "plat-principal",
    "tags": {"protein": ["poulet"]},
    "source_file": "/recettes/poulet.txt",
    "file_hash": "aaa111",
}


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    reset_client()


@pytest.fixture()
def as_admin(client, monkeypatch):
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.shared.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
    )
    return client


@pytest.fixture()
def as_user(client, monkeypatch):
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.shared.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": []},
    )
    return client


def _mock_openai_response(json_str: str) -> MagicMock:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json_str
    mock_response.choices = [mock_choice]
    return mock_response


class TestTaggerVersion:
    def test_tag_recipe_returns_version_and_model(self):
        payload = json.dumps({"title_fr": "Test", "title_en": "Test", "tags": {}})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(payload)
        with patch("recipes.shared.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")
        assert result["tagger_version"] == TAGGER_VERSION
        assert result["tagger_model"] == "fake-model"

    def test_upsert_stores_version_and_model(self):
        rid = upsert_recipe({**SAMPLE, "tagger_version": 3, "tagger_model": "gpt-x"})
        from recipes.shared.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT tagger_version, tagger_model FROM recipes WHERE id = ?", (rid,)
            ).fetchone()
        assert int(row["tagger_version"]) == 3
        assert str(row["tagger_model"]) == "gpt-x"

    def test_upsert_without_version_stores_null(self):
        rid = upsert_recipe({**SAMPLE})
        from recipes.shared.db import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT tagger_version, tagger_model FROM recipes WHERE id = ?", (rid,)
            ).fetchone()
        assert row["tagger_version"] is None
        assert row["tagger_model"] is None


class TestRecipesJsonTagger:
    def test_current_recipe_not_outdated(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        rid = upsert_recipe({**SAMPLE, "tagger_version": TAGGER_VERSION})
        resp = as_admin.get("/admin/recipes.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tagger_version"] == TAGGER_VERSION
        row = next(r for r in data["recipes"] if r["id"] == rid)
        assert row["tagger_version"] == TAGGER_VERSION
        assert row["tagger_outdated"] is False

    def test_old_and_missing_version_outdated(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        r_old = upsert_recipe(
            {**SAMPLE, "tagger_version": TAGGER_VERSION - 1, "tagger_model": "old"}
        )
        r_none = upsert_recipe({**SAMPLE, "source_file": "/recettes/autre.txt", "file_hash": "bbb"})
        rows = {r["id"]: r for r in as_admin.get("/admin/recipes.json").json()["recipes"]}
        assert rows[r_old]["tagger_outdated"] is True
        assert rows[r_old]["tagger_model"] == "old"
        assert rows[r_none]["tagger_version"] is None
        assert rows[r_none]["tagger_outdated"] is True


def _mock_retag_stack(raw_text="Poulet rôti\nPour 4 personnes"):
    """Patch dropbox download + parse; real tag_recipe payload-build via mocked LLM."""
    fake_dbx = MagicMock()
    fake_content = b"fake recipe bytes"
    new_payload = {
        "title_fr": "Poulet Retaggé",
        "title_en": "Retagged Chicken",
        "description_fr": "Nouvelle description",
        "description_en": "New description",
        "servings": 4,
        "ingredients": [],
        "steps_fr": [],
        "steps_en": [],
        "category": "plat-principal",
        "tags": {"protein": ["boeuf"]},
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(
        json.dumps(new_payload)
    )
    return (
        patch("recipes.shared.poller._get_dropbox_client", return_value=fake_dbx),
        patch("recipes.shared.poller.download_file", return_value=fake_content),
        patch("recipes.shared.parsers.extract_text", return_value=raw_text),
        patch("recipes.shared.tagger._get_client", return_value=mock_client),
    )


class TestRetagService:
    def test_retag_updates_recipe_and_resets_manual(self):
        from recipes.features.recipes.services import retag_recipe
        from recipes.shared.db import update_recipe_manual

        rid = upsert_recipe({**SAMPLE, "tagger_version": TAGGER_VERSION - 1})
        assert update_recipe_manual(rid, {**SAMPLE, "title": "Titre manuel"})

        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            result = retag_recipe(rid)
        finally:
            for p in patches:
                p.stop()

        assert result["tagger_version"] == TAGGER_VERSION
        assert result["scopes"] == ["category", "content", "meta", "tags"]
        recipe = get_recipe(rid)
        assert recipe is not None
        assert recipe["manually_edited"] == 0
        assert "boeuf" in {t["name"] for t in recipe["tags"].get("protein", {}).get("tags", [])}

    def test_retag_unknown_recipe_raises(self):
        from recipes.features.recipes.services import retag_recipe

        with pytest.raises(ValueError, match="not found"):
            retag_recipe(9999)

    def test_retag_invalid_scope_raises(self):
        from recipes.features.recipes.services import retag_recipe

        rid = upsert_recipe({**SAMPLE})
        with pytest.raises(ValueError, match="Unknown retag scope"):
            retag_recipe(rid, scopes=["nope"])

    def test_retag_tags_only_keeps_content_and_category(self):
        from recipes.features.recipes.services import retag_recipe
        from recipes.shared.db import sync_recipe_tags

        rid = upsert_recipe({**SAMPLE, "category": "dessert"})
        sync_recipe_tags(rid, {"protein": ["poulet"]})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            result = retag_recipe(rid, scopes=["tags"])
        finally:
            for p in patches:
                p.stop()

        assert result["scopes"] == ["tags"]
        recipe = get_recipe(rid)
        assert recipe is not None
        # Tags réappliqués depuis le LLM...
        assert "boeuf" in {t["name"] for t in recipe["tags"].get("protein", {}).get("tags", [])}
        # ...mais titre et catégorie d'origine conservés.
        assert recipe["title"] == "Poulet Rôti"
        assert recipe["category"] is not None
        assert recipe["category"]["name"] == "dessert"

    def test_retag_category_only_keeps_tags(self):
        from recipes.features.recipes.services import retag_recipe
        from recipes.shared.db import sync_recipe_tags

        rid = upsert_recipe({**SAMPLE, "category": "dessert"})
        sync_recipe_tags(rid, {"protein": ["poulet"]})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            result = retag_recipe(rid, scopes=["category"])
        finally:
            for p in patches:
                p.stop()

        assert result["scopes"] == ["category"]
        recipe = get_recipe(rid)
        assert recipe is not None
        # Le LLM dit "plat-principal" : seule la catégorie change.
        assert recipe["category"] is not None
        assert recipe["category"]["name"] == "plat-principal"
        assert "poulet" in {t["name"] for t in recipe["tags"].get("protein", {}).get("tags", [])}
        assert "boeuf" not in {t["name"] for t in recipe["tags"].get("protein", {}).get("tags", [])}

    def test_retag_content_only_updates_texts(self):
        from recipes.features.recipes.services import retag_recipe
        from recipes.shared.db import sync_recipe_tags

        rid = upsert_recipe({**SAMPLE, "category": "dessert"})
        sync_recipe_tags(rid, {"protein": ["poulet"]})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            result = retag_recipe(rid, scopes=["content"])
        finally:
            for p in patches:
                p.stop()

        assert result["scopes"] == ["content"]
        recipe = get_recipe(rid)
        assert recipe is not None
        assert recipe["title"] == "Poulet Retaggé"
        assert recipe["category"] is not None
        assert recipe["category"]["name"] == "dessert"
        assert "poulet" in {t["name"] for t in recipe["tags"].get("protein", {}).get("tags", [])}


class TestRetagRoutes:
    def test_retag_route_ok(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        rid = upsert_recipe({**SAMPLE, "tagger_version": TAGGER_VERSION - 1})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            resp = as_admin.post(f"/admin/retag/{rid}", json={})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["tagger_version"] == TAGGER_VERSION
        assert body["tagger_outdated"] is False

    def test_retag_route_404(self, as_admin):
        resp = as_admin.post("/admin/retag/9999", json={})
        assert resp.status_code == 404

    def test_retag_route_requires_admin(self, as_user):
        rid = upsert_recipe({**SAMPLE})
        resp = as_user.post(f"/admin/retag/{rid}", json={})
        assert resp.status_code == 403

    def test_bulk_retag_mixed_results(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        r1 = upsert_recipe({**SAMPLE})
        r2 = upsert_recipe({**SAMPLE, "source_file": "/recettes/tarte.txt", "file_hash": "bbb"})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            resp = as_admin.post("/admin/retag-bulk", json={"ids": [r1, r2, 9999]})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        body = resp.json()
        assert body["updated"] == 2
        by_id = {r["id"]: r for r in body["results"]}
        assert by_id[r1]["ok"] is True
        assert by_id[r2]["ok"] is True
        assert by_id[9999]["ok"] is False

    def test_retag_route_with_scopes(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        rid = upsert_recipe({**SAMPLE, "category": "dessert"})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            resp = as_admin.post(f"/admin/retag/{rid}", json={"scopes": ["tags"]})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["scopes"] == ["tags"]
        recipe = get_recipe(rid)
        assert recipe is not None
        assert recipe["category"] is not None
        assert recipe["category"]["name"] == "dessert"

    def test_retag_route_invalid_scopes_422(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        rid = upsert_recipe({**SAMPLE})
        resp = as_admin.post(f"/admin/retag/{rid}", json={"scopes": ["nope"]})
        assert resp.status_code == 422

    def test_bulk_retag_with_scopes(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        r1 = upsert_recipe({**SAMPLE, "category": "dessert"})
        patches = _mock_retag_stack()
        for p in patches:
            p.start()
        try:
            resp = as_admin.post("/admin/retag-bulk", json={"ids": [r1], "scopes": ["tags"]})
        finally:
            for p in patches:
                p.stop()
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    def test_bulk_retag_invalid_scopes_422(self, as_admin):
        get_or_create_user("test", "test@example.com", "Test")
        resp = as_admin.post("/admin/retag-bulk", json={"ids": [1], "scopes": ["nope"]})
        assert resp.status_code == 422

    def test_bulk_retag_requires_admin(self, as_user):
        resp = as_user.post("/admin/retag-bulk", json={"ids": [1]})
        assert resp.status_code == 403
