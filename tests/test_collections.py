"""Tests for the collections feature — services, pages, API, and admin."""

import pytest

from recipes.features.collections.services import (
    add_recipe_to_collection,
    can_edit,
    can_view,
    create_collection,
    delete_collection,
    demote_to_user,
    get_collection_by_id,
    get_collection_by_slug,
    get_collection_by_token,
    get_collection_recipes,
    get_collections_for_recipe,
    is_recipe_in_collection,
    list_featured_collections,
    list_site_collections,
    list_user_collections,
    promote_to_site,
    remove_recipe_from_collection,
    set_cover,
    set_featured,
    update_collection,
)
from recipes.shared.db import (
    get_conn,
    get_recipe,
    init_db,
    sync_recipe_tags,
    upsert_recipe,
)


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject, name) VALUES (?, ?, ?)",
            (1, "test-1", "Test"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject, name) VALUES (?, ?, ?)",
            (2, "test-2", "Other"),
        )


def _make_user(client, monkeypatch, user_id, groups):
    fake_user = {"id": user_id, "sub": f"test-{user_id}", "name": "Test", "groups": groups}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.auth.get_user", lambda request: fake_user)
    monkeypatch.setattr("recipes.shared.web.get_user", lambda request: fake_user)
    monkeypatch.setattr(
        "recipes.features.collections.controllers.get_user", lambda request: fake_user
    )
    monkeypatch.setattr("recipes.features.recipes.controllers.get_user", lambda request: fake_user)
    return client


@pytest.fixture()
def as_user(client, monkeypatch):
    return _make_user(client, monkeypatch, 1, [])


@pytest.fixture()
def as_other(client, monkeypatch):
    return _make_user(client, monkeypatch, 2, [])


@pytest.fixture()
def as_ghost(client, monkeypatch):
    """Session with a user ID that has no `users` row (stale login)."""
    return _make_user(client, monkeypatch, 999, [])


@pytest.fixture()
def as_admin(client, monkeypatch):
    return _make_user(client, monkeypatch, 1, ["owner"])


def _insert_sample(source_file="/recipes/poulet.docx", file_hash="aaa111"):
    return upsert_recipe(
        {
            "title": "Poulet Rôti",
            "description": "Un classique",
            "source_file": source_file,
            "file_hash": file_hash,
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestCollectionServices:
    def test_create_and_fetch(self):
        created = create_collection(1, "Noël", "Recettes de fêtes")
        assert created["slug"] == "noel"
        assert created["share_token"]
        assert get_collection_by_slug("noel") is not None
        assert get_collection_by_token(str(created["share_token"])) is not None

    def test_slug_uniqueness(self):
        first = create_collection(1, "Noël")
        second = create_collection(1, "Noël")
        assert first["slug"] != second["slug"]
        assert second["slug"] == "noel-2"

    def test_create_requires_name(self):
        with pytest.raises(ValueError):
            create_collection(1, "   ")

    def test_add_remove_recipe(self):
        recipe_id = _insert_sample()
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        assert add_recipe_to_collection(collection_id, recipe_id)
        assert is_recipe_in_collection(collection_id, recipe_id)
        recipes = get_collection_recipes(collection_id)
        assert [r["id"] for r in recipes] == [recipe_id]
        remove_recipe_from_collection(collection_id, recipe_id)
        assert not is_recipe_in_collection(collection_id, recipe_id)

    def test_add_missing_recipe(self):
        collection = create_collection(1, "Noël")
        assert not add_recipe_to_collection(int(str(collection["id"])), 9999)

    def test_for_recipe_flags(self):
        recipe_id = _insert_sample()
        mine = create_collection(1, "Noël")
        create_collection(1, "Enfants")
        add_recipe_to_collection(int(str(mine["id"])), recipe_id)
        flags = {c["name"]: c["in_collection"] for c in get_collections_for_recipe(1, recipe_id)}
        assert flags == {"Noël": True, "Enfants": False}

    def test_update_renames_slug(self):
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        assert update_collection(collection_id, "Pâques", "Chocolat")
        assert get_collection_by_slug("paques") is not None

    def test_delete(self):
        collection = create_collection(1, "Noël")
        assert delete_collection(int(str(collection["id"])))
        assert get_collection_by_slug("noel") is None

    def test_promote_demote_feature(self):
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        # Featured requires a site collection.
        assert not set_featured(collection_id, True)
        assert promote_to_site(collection_id)
        assert set_featured(collection_id, True)
        assert [c["id"] for c in list_site_collections()] == [collection_id]
        assert [c["id"] for c in list_featured_collections()] == [collection_id]
        assert demote_to_user(collection_id)
        assert list_site_collections() == []
        assert list_featured_collections() == []

    def test_user_lists_exclude_site(self):
        collection = create_collection(1, "Noël")
        promote_to_site(int(str(collection["id"])))
        assert list_user_collections(1) == []

    def test_permissions(self):
        site = create_collection(1, "Noël")
        promote_to_site(int(str(site["id"])))
        site = get_collection_by_slug("noel")
        assert site is not None
        assert can_view(site, None, False)
        assert can_edit(site, 1, False) is False
        assert can_edit(site, None, True) is True

        private = create_collection(1, "Privée")
        assert can_view(private, 1, False)
        assert not can_view(private, 2, False)
        assert not can_view(private, None, False)
        assert can_view(private, 2, True)
        assert can_edit(private, 1, False)
        assert not can_edit(private, 2, False)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class TestCollectionPages:
    def test_list_page_anonymous(self, client):
        recipe_id = _insert_sample()
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), recipe_id)
        promote_to_site(int(str(collection["id"])))
        resp = client.get("/collections")
        assert resp.status_code == 200
        assert "Noël" in resp.text

    def test_site_detail_anonymous(self, client):
        recipe_id = _insert_sample()
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), recipe_id)
        promote_to_site(int(str(collection["id"])))
        resp = client.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text

    def test_private_detail_hidden_from_anonymous(self, client):
        collection = create_collection(1, "Noël")
        resp = client.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 404

    def test_private_detail_visible_to_owner(self, as_user):
        collection = create_collection(1, "Noël")
        resp = as_user.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 200
        assert "Noël" in resp.text

    def test_mine_list_shows_cover_thumbnail(self, as_user):
        recipe_id = _insert_sample()
        _add_image(recipe_id, "poulet.jpg")
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), recipe_id)
        resp = as_user.get("/collections")
        assert resp.status_code == 200
        assert "collection-mine-thumb" in resp.text
        assert "poulet.jpg" in resp.text

    def test_mine_list_shows_placeholder_without_image(self, as_user):
        recipe_id = _insert_sample()
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), recipe_id)
        resp = as_user.get("/collections")
        assert resp.status_code == 200
        assert "collection-mine-thumb-placeholder" in resp.text

    def test_private_detail_hidden_from_stranger(self, as_other):
        collection = create_collection(1, "Noël")
        resp = as_other.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 404

    def test_shared_link_anonymous(self, client):
        collection = create_collection(1, "Noël")
        resp = client.get(f"/collections/partage/{collection['share_token']}")
        assert resp.status_code == 200
        assert "Noël" in resp.text

    def test_shared_link_unknown_token(self, client):
        assert client.get("/collections/partage/nope").status_code == 404

    def test_homepage_shows_featured(self, client):
        collection = create_collection(1, "Noël")
        promote_to_site(int(str(collection["id"])))
        set_featured(int(str(collection["id"])), True)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Noël" in resp.text
        assert "collection-carousel-prev" in resp.text
        assert "collection-carousel-next" in resp.text

    def test_homepage_hides_unfeatured(self, client):
        collection = create_collection(1, "Noël")
        promote_to_site(int(str(collection["id"])))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Collections à découvrir" not in resp.text

    def test_recipe_page_button_for_user(self, as_user):
        recipe_id = _insert_sample()
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "btn-collection-add" in resp.text

    def test_recipe_page_no_button_anonymous(self, client):
        recipe_id = _insert_sample()
        resp = client.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "btn-collection-add" not in resp.text


# ---------------------------------------------------------------------------
# User API
# ---------------------------------------------------------------------------


class TestCollectionApi:
    def test_create_and_toggle(self, as_user):
        recipe_id = _insert_sample()
        created = as_user.post("/api/collections", json={"name": "Noël", "description": "Fêtes"})
        assert created.status_code == 201
        assert created.json()["description"] == "Fêtes"
        collection_id = created.json()["id"]

        membership = as_user.get(f"/api/recipes/{_slug(recipe_id)}/collections")
        assert membership.status_code == 200
        assert membership.json()[0]["in_collection"] is False

        toggled = as_user.post(f"/api/recipes/{_slug(recipe_id)}/collections/{collection_id}")
        assert toggled.status_code == 200
        assert toggled.json() == {"in_collection": True}

        untoggled = as_user.post(f"/api/recipes/{_slug(recipe_id)}/collections/{collection_id}")
        assert untoggled.json() == {"in_collection": False}

    def test_create_requires_name(self, as_user):
        resp = as_user.post("/api/collections", json={"name": "  "})
        assert resp.status_code == 422

    def test_stale_session_self_heals(self, as_ghost):
        """A session ID with no `users` row must not 500 on create (FK)."""
        created = as_ghost.post("/api/collections", json={"name": "Noël"})
        assert created.status_code == 201
        slug = created.json()["slug"]
        assert as_ghost.get(f"/collections/{slug}").status_code == 200

    def test_recipe_modal_js_is_valid(self, as_user):
        """No raw apostrophe inside the modal JS (would kill the <script>)."""
        recipe_id = _insert_sample()
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "escapeHtml(STR.emptyMine)" in resp.text
        assert "'<p class=\"empty\">' + '" not in resp.text
        # Apostrophe unicode-échappée par tojson : pas de SyntaxError navigateur.
        assert "n\\u0027avez pas encore" in resp.text
        # La modale permet de saisir le petit texte à la création.
        assert "collection-new-desc" in resp.text
        # Plusieurs façons de quitter : ✕, clic hors modale, Échap.
        assert "collection-modal-close" in resp.text
        assert "e.target === modal" in resp.text
        assert "'Escape'" in resp.text

    def test_stranger_cannot_modify(self, as_other):
        collection = create_collection(1, "Noël")
        recipe_id = _insert_sample()
        toggle = as_other.post(f"/api/recipes/{_slug(recipe_id)}/collections/{collection['id']}")
        assert toggle.status_code == 404
        renamed = as_other.put(
            f"/api/collections/{collection['id']}",
            json={"name": "Volée", "description": ""},
        )
        assert renamed.status_code == 404

    def test_rename_and_delete(self, as_user):
        created = as_user.post("/api/collections", json={"name": "Noël"})
        collection_id = created.json()["id"]
        renamed = as_user.put(
            f"/api/collections/{collection_id}",
            json={"name": "Pâques", "description": "Chocolat"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["slug"] == "paques"
        deleted = as_user.delete(f"/api/collections/{collection_id}")
        assert deleted.json() == {"deleted": True}
        assert as_user.get("/collections/paques").status_code == 404


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestCollectionAdmin:
    def test_admin_page(self, as_admin):
        create_collection(1, "Noël")
        resp = as_admin.get("/admin/collections")
        assert resp.status_code == 200
        assert "Noël" in resp.text

    def test_admin_page_forbidden_for_user(self, as_user):
        assert as_user.get("/admin/collections").status_code == 403

    def test_promote_and_feature(self, as_admin):
        collection = create_collection(1, "Noël")
        collection_id = collection["id"]
        promote = as_admin.post(
            f"/admin/collections/{collection_id}/promote", follow_redirects=False
        )
        assert promote.status_code == 303
        feature = as_admin.post(
            f"/admin/collections/{collection_id}/feature", follow_redirects=False
        )
        assert feature.status_code == 303
        assert as_admin.get("/").text.count("Noël") >= 1

    def test_demote_and_delete(self, as_admin):
        collection = create_collection(1, "Noël")
        collection_id = collection["id"]
        as_admin.post(f"/admin/collections/{collection_id}/promote")
        demote = as_admin.post(f"/admin/collections/{collection_id}/demote", follow_redirects=False)
        assert demote.status_code == 303
        delete = as_admin.post(f"/admin/collections/{collection_id}/delete", follow_redirects=False)
        assert delete.status_code == 303
        assert get_collection_by_slug("noel") is None


# ---------------------------------------------------------------------------
# Scoped search + filters
# ---------------------------------------------------------------------------


def _insert_dessert():
    return upsert_recipe(
        {
            "title": "Gâteau Chocolat",
            "description": "Dessert festif",
            "category": "dessert",
            "source_file": "/recipes/gateau.docx",
            "file_hash": "bbb222",
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


def _insert_outsider():
    return upsert_recipe(
        {
            "title": "Poulet Curry",
            "description": "Épicé",
            "source_file": "/recipes/curry.docx",
            "file_hash": "ccc333",
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


def _dessert_category_id() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM categories WHERE name = 'dessert'").fetchone()
        assert row is not None
        return int(row["id"])


class TestCollectionSearch:
    def _site_collection(self):
        poulet = _insert_sample()
        gateau = _insert_dessert()
        _insert_outsider()
        sync_recipe_tags(poulet, {"origin": ["francais"]})
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        add_recipe_to_collection(collection_id, poulet)
        add_recipe_to_collection(collection_id, gateau)
        promote_to_site(collection_id)
        return str(collection["slug"])

    def test_detail_page_has_filter_hooks(self, client):
        slug = self._site_collection()
        resp = client.get(f"/collections/{slug}")
        assert resp.status_code == 200
        assert 'id="filter-panel"' in resp.text
        assert f"/collections/{slug}/search" in resp.text

    def test_search_scoped_to_members(self, client):
        slug = self._site_collection()
        resp = client.get(f"/collections/{slug}/search", params={"q": "poulet"})
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text
        # Membre aussi, mais ne matche pas la recherche.
        assert "Gâteau Chocolat" not in resp.text
        # Matche la recherche mais n'est pas membre.
        assert "Poulet Curry" not in resp.text

    def test_category_filter(self, client):
        slug = self._site_collection()
        resp = client.get(
            f"/collections/{slug}/search", params={"category": str(_dessert_category_id())}
        )
        assert resp.status_code == 200
        assert "Gâteau Chocolat" in resp.text
        assert "Poulet Rôti" not in resp.text

    def test_tag_filter(self, client):
        slug = self._site_collection()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT t.id FROM tags t JOIN tag_families tf ON t.family_id = tf.id "
                "WHERE tf.name = 'origin' AND t.name = 'francais'"
            ).fetchone()
            assert row is not None
            tag_id = int(row["id"])
        resp = client.get(f"/collections/{slug}/search", params={"tags": str(tag_id)})
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text
        assert "Gâteau Chocolat" not in resp.text

    def test_search_private_hidden_from_stranger(self, as_other):
        poulet = _insert_sample()
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), poulet)
        resp = as_other.get(f"/collections/{collection['slug']}/search")
        assert resp.status_code == 404

    def test_search_private_visible_to_owner(self, as_user):
        poulet = _insert_sample()
        collection = create_collection(1, "Noël")
        add_recipe_to_collection(int(str(collection["id"])), poulet)
        resp = as_user.get(f"/collections/{collection['slug']}/search", params={"q": "poulet"})
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text


def _add_image(recipe_id: int, filename: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recipe_images (recipe_id, filename, sort_order) VALUES (?, ?, 0)",
            (recipe_id, filename),
        )


class TestCollectionCover:
    def _two_recipes_collection(self, site: bool = False) -> tuple[int, int, int]:
        first = _insert_sample()
        second = _insert_dessert()
        _add_image(first, "poulet.jpg")
        _add_image(second, "gateau.jpg")
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        add_recipe_to_collection(collection_id, first)
        add_recipe_to_collection(collection_id, second)
        if site:
            promote_to_site(collection_id)
        return collection_id, first, second

    def test_default_cover_is_first_recipe(self):
        collection_id, first, _second = self._two_recipes_collection(site=True)
        site = list_site_collections()
        assert len(site) == 1
        assert site[0]["cover_recipe_slug"] == _slug(first)

    def test_set_cover_uses_chosen_recipe(self):
        collection_id, _first, second = self._two_recipes_collection(site=True)
        assert set_cover(collection_id, second)
        site = list_site_collections()
        assert site[0]["cover_recipe_slug"] == _slug(second)
        assert site[0]["cover_image"] == "gateau.jpg"

    def test_set_cover_rejects_non_member(self):
        collection_id, _first, _second = self._two_recipes_collection(site=True)
        outsider = _insert_outsider()
        assert not set_cover(collection_id, outsider)
        assert not set_cover(9999, outsider)

    def test_set_cover_none_restores_automatic(self):
        collection_id, first, second = self._two_recipes_collection(site=True)
        assert set_cover(collection_id, second)
        assert set_cover(collection_id, None)
        site = list_site_collections()
        assert site[0]["cover_recipe_slug"] == _slug(first)

    def test_remove_cover_recipe_clears_pin(self):
        collection_id, first, second = self._two_recipes_collection(site=True)
        assert set_cover(collection_id, second)
        remove_recipe_from_collection(collection_id, second)
        row = get_collection_by_id(collection_id)
        assert row is not None
        assert row.get("cover_recipe_id") is None
        site = list_site_collections()
        assert site[0]["cover_recipe_slug"] == _slug(first)

    def test_api_owner_can_set_cover(self, as_user):
        collection_id, _first, second = self._two_recipes_collection()
        resp = as_user.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": second})
        assert resp.status_code == 200
        assert int(str(resp.json()["cover_recipe_id"])) == second

    def test_api_owner_can_clear_cover(self, as_user):
        collection_id, _first, second = self._two_recipes_collection()
        as_user.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": second})
        resp = as_user.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": None})
        assert resp.status_code == 200
        assert resp.json()["cover_recipe_id"] is None

    def test_api_rejects_non_member_recipe(self, as_user):
        collection_id, _first, _second = self._two_recipes_collection()
        outsider = _insert_outsider()
        resp = as_user.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": outsider})
        assert resp.status_code == 422

    def test_api_stranger_cannot_set_cover(self, as_other):
        collection_id, _first, second = self._two_recipes_collection(site=True)
        # Site collections are admin-only for edits.
        resp = as_other.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": second})
        assert resp.status_code == 404
        # Private collection owned by user 1: stranger gets 404 too.
        private = create_collection(1, "Privée")
        private_id = int(str(private["id"]))
        poulet = _insert_sample()
        add_recipe_to_collection(private_id, poulet)
        resp = as_other.put(f"/api/collections/{private_id}/cover", json={"recipe_id": poulet})
        assert resp.status_code == 404

    def test_api_admin_can_set_site_cover(self, as_admin):
        collection_id, _first, second = self._two_recipes_collection(site=True)
        resp = as_admin.put(f"/api/collections/{collection_id}/cover", json={"recipe_id": second})
        assert resp.status_code == 200
        assert int(str(resp.json()["cover_recipe_id"])) == second

    def test_detail_page_shows_cover_picker_to_owner(self, as_user):
        collection_id, _first, _second = self._two_recipes_collection()
        collection = get_collection_by_id(collection_id)
        assert collection is not None
        resp = as_user.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 200
        assert "collection-cover-select" in resp.text
        assert "collection-cover-form" in resp.text

    def test_detail_page_hides_cover_picker_from_stranger(self, as_other):
        collection = create_collection(1, "Noël")
        collection_id = int(str(collection["id"]))
        poulet = _insert_sample()
        add_recipe_to_collection(collection_id, poulet)
        resp = as_other.get(f"/collections/{collection['slug']}")
        assert resp.status_code == 404
