"""Tests for shopping feature — HTTP routes."""

import pytest
from fastapi.testclient import TestClient

from recipes.features.shopping.services import (
    add_shopping_list_item,
    create_shopping_list,
)
from recipes.shared.db import get_recipe, init_db, upsert_recipe


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


def _create_owned_list(client: TestClient, name: str = "Test List") -> int:
    """Create a list via the route so the anon session owns it."""
    resp = client.post("/shopping/lists", data={"name": name}, follow_redirects=False)
    assert resp.status_code == 303
    location = resp.headers["location"]
    return int(location.rstrip("/").split("/")[-1])


class TestShoppingRoutes:
    def test_shopping_lists_page_anonymous(self, client):
        resp = client.get("/shopping")
        assert resp.status_code == 200

    def test_shopping_list_create(self, client):
        resp = client.post("/shopping/lists", data={"name": "Test List"}, follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_list_detail(self, client):
        list_id = _create_owned_list(client, "Test Detail")
        resp = client.get(f"/shopping/{list_id}")
        assert resp.status_code == 200

    def test_shopping_list_detail_not_found(self, client):
        resp = client.get("/shopping/9999")
        assert resp.status_code == 404

    def test_shopping_list_detail_forbidden_for_stranger(self, client):
        """An anonymous session must not view another session's list by ID."""
        victim = create_shopping_list("Victim")
        resp = client.get(f"/shopping/{victim['id']}")
        assert resp.status_code == 404

    def test_shopping_list_cook_mode(self, client):
        list_id = _create_owned_list(client, "Cook Mode")
        resp = client.get(f"/shopping/{list_id}/cook")
        assert resp.status_code == 200

    def test_shopping_list_cook_forbidden_for_stranger(self, client):
        victim = create_shopping_list("Victim Cook")
        resp = client.get(f"/shopping/{victim['id']}/cook")
        assert resp.status_code == 404

    def test_shopping_list_delete(self, client):
        list_id = _create_owned_list(client, "To Delete")
        resp = client.post(f"/shopping/lists/{list_id}/delete", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_list_delete_forbidden_for_stranger(self, client):
        victim = create_shopping_list("Victim Delete")
        resp = client.post(f"/shopping/lists/{victim['id']}/delete", follow_redirects=False)
        assert resp.status_code in (403, 404)

    def test_shopping_list_rename(self, client):
        list_id = _create_owned_list(client, "Old Name")
        resp = client.post(
            f"/shopping/lists/{list_id}/rename", data={"name": "New Name"}, follow_redirects=False
        )
        assert resp.status_code == 303

    def test_shopping_list_add_item(self, client):
        list_id = _create_owned_list(client, "Add Item")
        resp = client.post(
            f"/shopping/lists/{list_id}/items",
            data={"department_id": 1, "text": "Pommes", "quantity": "1 kg"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_shopping_item_toggle(self, client):
        list_id = _create_owned_list(client, "Toggle")
        item = add_shopping_list_item(list_id, 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/toggle", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_item_toggle_forbidden_for_stranger(self, client):
        victim = create_shopping_list("Victim Toggle")
        item = add_shopping_list_item(victim["id"], 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/toggle", follow_redirects=False)
        assert resp.status_code == 404

    def test_shopping_item_remove(self, client):
        list_id = _create_owned_list(client, "Remove")
        item = add_shopping_list_item(list_id, 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/remove", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_item_remove_forbidden_for_stranger(self, client):
        victim = create_shopping_list("Victim Remove")
        item = add_shopping_list_item(victim["id"], 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/remove", follow_redirects=False)
        assert resp.status_code == 404

    def test_shopping_item_update(self, client):
        list_id = _create_owned_list(client, "Update")
        item = add_shopping_list_item(list_id, 1, "Pommes")
        resp = client.post(
            f"/shopping/items/{item['id']}/update",
            data={"text": "Poires", "quantity": "2 kg", "department_id": 2},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_shopping_item_update_forbidden_for_stranger(self, client):
        victim = create_shopping_list("Victim Update")
        item = add_shopping_list_item(victim["id"], 1, "Pommes")
        resp = client.post(
            f"/shopping/items/{item['id']}/update",
            data={"text": "Poires", "quantity": "2 kg", "department_id": 2},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_shopping_shared_list(self, client):
        lst = create_shopping_list("Shared")
        resp = client.get(f"/shopping/shared/{lst['share_token']}")
        assert resp.status_code == 200

    def test_shopping_shared_list_not_found(self, client):
        resp = client.get("/shopping/shared/invalid-token")
        assert resp.status_code == 404


class TestShoppingAccessControl:
    def test_can_edit_owned_list(self):
        from starlette.requests import Request

        from recipes.features.shopping.controllers import _can_edit_shopping_list

        scope = {"type": "http", "session": {"user": {"id": 5}}, "headers": []}
        assert _can_edit_shopping_list(Request(scope), {"id": 1, "user_id": 5}) is True
        assert _can_edit_shopping_list(Request(scope), {"id": 2, "user_id": 6}) is False

    def test_can_edit_anon_list_requires_session(self):
        from starlette.requests import Request

        from recipes.features.shopping.controllers import _can_edit_shopping_list

        owner_scope = {"type": "http", "session": {"shopping_list_ids": [10]}, "headers": []}
        stranger_scope = {"type": "http", "session": {"shopping_list_ids": []}, "headers": []}
        anon_list: dict[str, object] = {"id": 10, "user_id": None}
        assert _can_edit_shopping_list(Request(owner_scope), anon_list) is True
        assert _can_edit_shopping_list(Request(stranger_scope), anon_list) is False

    def test_logged_user_cannot_edit_anon_list(self):
        from starlette.requests import Request

        from recipes.features.shopping.controllers import _can_edit_shopping_list

        scope = {"type": "http", "session": {"user": {"id": 7}}, "headers": []}
        assert _can_edit_shopping_list(Request(scope), {"id": 10, "user_id": None}) is False

    def test_recipe_modal_shows_only_session_lists(self, client):
        """Anonymous recipe page must not leak other sessions' lists."""
        victim = create_shopping_list("Victim Modal")
        own_id = _create_owned_list(client, "Own Modal")
        recipe_id = upsert_recipe(
            {
                "title": "Modal Test",
                "description": "",
                "steps": [],
                "ingredients": [],
                "servings": 1,
                "category": None,
                "tags": {},
                "source_file": "/recipes/modal.docx",
                "file_hash": "modal1",
            }
        )
        resp = client.get(f"/recipe/{_slug(recipe_id)}")
        assert resp.status_code == 200
        assert "Own Modal" in resp.text
        assert str(victim["name"]) not in resp.text
        assert own_id > 0
