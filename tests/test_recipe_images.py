"""Tests for recipe photos — primary choice, hide/show, and re-sync preservation."""

import pytest

from recipes.shared.db import (
    get_recipe,
    get_recipe_images,
    init_db,
    save_recipe_images,
    set_primary_recipe_image,
    set_recipe_image_hidden,
    upsert_recipe,
)


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


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


def _make_recipe(source_file="/recipes/poulet.docx", file_hash="aaa111"):
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


def _filenames(recipe_id: int, include_hidden: bool = False) -> list[str]:
    return [
        str(img["filename"]) for img in get_recipe_images(recipe_id, include_hidden=include_hidden)
    ]


class TestPrimaryImage:
    def test_first_image_is_primary_by_default(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        assert _filenames(recipe_id) == ["a.jpg", "b.jpg"]

    def test_set_primary_moves_image_first(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg", "c.jpg"])
        images = get_recipe_images(recipe_id)
        second_id = int(str(images[1]["id"]))
        assert set_primary_recipe_image(recipe_id, second_id)
        assert _filenames(recipe_id) == ["b.jpg", "a.jpg", "c.jpg"]

    def test_set_primary_rejects_foreign_image(self):
        recipe_id = _make_recipe()
        other_id = _make_recipe("/recipes/gateau.docx", "bbb222")
        save_recipe_images(recipe_id, ["a.jpg"])
        save_recipe_images(other_id, ["z.jpg"])
        foreign_id = int(str(get_recipe_images(other_id)[0]["id"]))
        assert not set_primary_recipe_image(recipe_id, foreign_id)
        assert not set_primary_recipe_image(9999, foreign_id)
        assert _filenames(recipe_id) == ["a.jpg"]

    def test_recipe_cover_follows_primary(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        assert set_primary_recipe_image(recipe_id, int(str(images[1]["id"])))
        recipe = get_recipe(recipe_id)
        assert recipe is not None
        assert recipe["images"][0]["filename"] == "b.jpg"


class TestHideImage:
    def test_hidden_excluded_from_public(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        assert set_recipe_image_hidden(recipe_id, int(str(images[0]["id"])), True)
        assert _filenames(recipe_id) == ["b.jpg"]
        assert _filenames(recipe_id, include_hidden=True) == ["a.jpg", "b.jpg"]

    def test_unhide_restores_image(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        first_id = int(str(images[0]["id"]))
        assert set_recipe_image_hidden(recipe_id, first_id, True)
        assert set_recipe_image_hidden(recipe_id, first_id, False)
        assert _filenames(recipe_id) == ["a.jpg", "b.jpg"]

    def test_hidden_rejects_foreign_image(self):
        recipe_id = _make_recipe()
        other_id = _make_recipe("/recipes/gateau.docx", "bbb222")
        save_recipe_images(recipe_id, ["a.jpg"])
        save_recipe_images(other_id, ["z.jpg"])
        foreign_id = int(str(get_recipe_images(other_id)[0]["id"]))
        assert not set_recipe_image_hidden(recipe_id, foreign_id, True)
        assert _filenames(recipe_id) == ["a.jpg"]

    def test_hidden_primary_falls_back_to_next_visible(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        assert set_recipe_image_hidden(recipe_id, int(str(images[0]["id"])), True)
        recipe = get_recipe(recipe_id)
        assert recipe is not None
        assert recipe["images"][0]["filename"] == "b.jpg"


class TestResyncPreservation:
    def test_resync_keeps_primary_and_hidden(self):
        """Re-saving the same file list (retag / poller) keeps admin choices."""
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg", "c.jpg"])
        images = get_recipe_images(recipe_id)
        by_name = {str(img["filename"]): int(str(img["id"])) for img in images}
        assert set_primary_recipe_image(recipe_id, by_name["c.jpg"])
        assert set_recipe_image_hidden(recipe_id, by_name["b.jpg"], True)
        # Re-sync as the poller / retag would.
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg", "c.jpg"])
        assert _filenames(recipe_id) == ["c.jpg", "a.jpg"]
        all_images = get_recipe_images(recipe_id, include_hidden=True)
        assert [str(img["filename"]) for img in all_images] == ["c.jpg", "a.jpg", "b.jpg"]
        assert int(str(all_images[2]["is_hidden"])) == 1

    def test_resync_appends_new_and_drops_missing(self):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        assert set_recipe_image_hidden(recipe_id, int(str(images[0]["id"])), True)
        save_recipe_images(recipe_id, ["b.jpg", "d.jpg"])
        assert _filenames(recipe_id) == ["b.jpg", "d.jpg"]
        assert _filenames(recipe_id, include_hidden=True) == ["b.jpg", "d.jpg"]


class TestAdminImageApi:
    def test_admin_can_set_primary(self, as_admin):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        resp = as_admin.post(
            f"/admin/recipes/{recipe_id}/images/primary",
            json={"image_id": images[1]["id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert _filenames(recipe_id) == ["b.jpg", "a.jpg"]

    def test_admin_can_hide_and_show(self, as_admin):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        hide = as_admin.post(
            f"/admin/recipes/{recipe_id}/images/visibility",
            json={"image_id": images[0]["id"], "hidden": True},
        )
        assert hide.status_code == 200
        assert _filenames(recipe_id) == ["b.jpg"]
        show = as_admin.post(
            f"/admin/recipes/{recipe_id}/images/visibility",
            json={"image_id": images[0]["id"], "hidden": False},
        )
        assert show.status_code == 200
        assert _filenames(recipe_id) == ["a.jpg", "b.jpg"]

    def test_admin_unknown_image_404(self, as_admin):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg"])
        resp = as_admin.post(f"/admin/recipes/{recipe_id}/images/primary", json={"image_id": 9999})
        assert resp.status_code == 404
        resp = as_admin.post(
            f"/admin/recipes/{recipe_id}/images/visibility",
            json={"image_id": 9999, "hidden": True},
        )
        assert resp.status_code == 404

    def test_admin_unknown_recipe_404(self, as_admin):
        resp = as_admin.post("/admin/recipes/9999/images/primary", json={"image_id": 1})
        assert resp.status_code == 404

    def test_user_cannot_manage_images(self, as_user):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        resp = as_user.post(
            f"/admin/recipes/{recipe_id}/images/primary",
            json={"image_id": images[1]["id"]},
        )
        assert resp.status_code == 403
        resp = as_user.post(
            f"/admin/recipes/{recipe_id}/images/visibility",
            json={"image_id": images[0]["id"], "hidden": True},
        )
        assert resp.status_code == 403


class TestAdminImageBlock:
    def test_admin_sees_management_block(self, as_admin):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        resp = as_admin.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "admin-images" in resp.text
        assert '<details class="admin-images">' in resp.text
        assert "data-primary" in resp.text
        assert "data-visibility" in resp.text

    def test_user_does_not_see_management_block(self, as_user):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "admin-images-grid" not in resp.text

    def test_hidden_image_absent_from_page(self, as_admin):
        recipe_id = _make_recipe()
        save_recipe_images(recipe_id, ["a.jpg", "b.jpg"])
        images = get_recipe_images(recipe_id)
        assert set_recipe_image_hidden(recipe_id, int(str(images[0]["id"])), True)
        resp = as_admin.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        # Hidden in the public gallery, still listed in the admin block.
        assert resp.text.count("a.jpg") == 1
        assert "b.jpg" in resp.text
