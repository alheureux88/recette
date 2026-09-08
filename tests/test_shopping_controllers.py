"""Tests for shopping feature — HTTP routes."""

import pytest

from recipes.features.shopping.services import (
    add_shopping_list_item,
    create_shopping_list,
)
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


class TestShoppingRoutes:
    def test_shopping_lists_page_anonymous(self, client):
        resp = client.get("/shopping")
        assert resp.status_code == 200

    def test_shopping_list_create(self, client):
        resp = client.post("/shopping/lists", data={"name": "Test List"}, follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_list_detail(self, client):
        lst = create_shopping_list("Test Detail")
        resp = client.get(f"/shopping/{lst['id']}")
        assert resp.status_code == 200

    def test_shopping_list_detail_not_found(self, client):
        resp = client.get("/shopping/9999")
        assert resp.status_code == 404

    def test_shopping_list_cook_mode(self, client):
        lst = create_shopping_list("Cook Mode")
        resp = client.get(f"/shopping/{lst['id']}/cook")
        assert resp.status_code == 200

    def test_shopping_list_delete(self, client):
        lst = create_shopping_list("To Delete")
        resp = client.post(f"/shopping/lists/{lst['id']}/delete", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_list_rename(self, client):
        lst = create_shopping_list("Old Name")
        resp = client.post(
            f"/shopping/lists/{lst['id']}/rename", data={"name": "New Name"}, follow_redirects=False
        )
        assert resp.status_code == 303

    def test_shopping_list_add_item(self, client):
        lst = create_shopping_list("Add Item")
        resp = client.post(
            f"/shopping/lists/{lst['id']}/items",
            data={"department_id": 1, "text": "Pommes", "quantity": "1 kg"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_shopping_item_toggle(self, client):
        lst = create_shopping_list("Toggle")
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/toggle", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_item_remove(self, client):
        lst = create_shopping_list("Remove")
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        resp = client.post(f"/shopping/items/{item['id']}/remove", follow_redirects=False)
        assert resp.status_code == 303

    def test_shopping_item_update(self, client):
        lst = create_shopping_list("Update")
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        resp = client.post(
            f"/shopping/items/{item['id']}/update",
            data={"text": "Poires", "quantity": "2 kg", "department_id": 2},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_shopping_shared_list(self, client):
        lst = create_shopping_list("Shared")
        resp = client.get(f"/shopping/shared/{lst['share_token']}")
        assert resp.status_code == 200

    def test_shopping_shared_list_not_found(self, client):
        resp = client.get("/shopping/shared/invalid-token")
        assert resp.status_code == 404
