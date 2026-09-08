"""Tests for shopping feature — controllers and services."""

import pytest

from recipes.features.shopping.services import (
    add_shopping_list_item,
    cleanup_expired_shopping_lists,
    create_shopping_list,
    delete_shopping_list,
    get_all_shopping_lists,
    get_department_by_id,
    get_department_by_name,
    get_shopping_departments,
    get_shopping_list_by_id,
    get_shopping_list_by_token,
    get_shopping_list_items,
    get_shopping_lists_by_ids,
    get_user_shopping_lists,
    remove_shopping_list_item,
    rename_shopping_list,
    toggle_shopping_list_item,
    touch_shopping_list,
    update_shopping_list_item,
)
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


class TestShoppingDepartments:
    def test_get_departments_fr(self):
        depts = get_shopping_departments(lang="fr")
        assert len(depts) > 0
        assert all("display_name" in d for d in depts)
        assert all("emoji" in d for d in depts)

    def test_get_departments_en(self):
        depts = get_shopping_departments(lang="en")
        assert len(depts) > 0

    def test_get_department_by_name(self):
        dept = get_department_by_name("fruits-legumes")
        assert dept is not None
        assert dept["name"] == "fruits-legumes"

    def test_get_department_by_name_not_found(self):
        dept = get_department_by_name("inexistant")
        assert dept is None

    def test_get_department_by_id(self):
        dept = get_department_by_id(1)
        assert dept is not None

    def test_get_department_by_id_not_found(self):
        dept = get_department_by_id(9999)
        assert dept is None


class TestShoppingLists:
    def test_create_shopping_list(self):
        lst = create_shopping_list("Ma liste")
        assert lst["name"] == "Ma liste"
        assert "id" in lst
        assert "share_token" in lst

    def test_create_shopping_list_with_user(self):
        from recipes.features.auth.services import get_or_create_user

        user_id = get_or_create_user("test-subject", "test@example.com", "Test User")
        lst = create_shopping_list("Liste user", user_id=user_id)
        assert lst["user_id"] == user_id

    def test_get_shopping_list_by_id(self):
        lst = create_shopping_list("Test")
        found = get_shopping_list_by_id(lst["id"])
        assert found is not None
        assert found["name"] == "Test"

    def test_get_shopping_list_by_id_not_found(self):
        found = get_shopping_list_by_id(9999)
        assert found is None

    def test_get_shopping_list_by_token(self):
        lst = create_shopping_list("Test")
        found = get_shopping_list_by_token(lst["share_token"])
        assert found is not None
        assert found["name"] == "Test"

    def test_get_shopping_list_by_token_not_found(self):
        found = get_shopping_list_by_token("invalid-token")
        assert found is None

    def test_get_user_shopping_lists(self):
        from recipes.features.auth.services import get_or_create_user

        user_id = get_or_create_user("user1", "user1@example.com", "User 1")
        create_shopping_list("Liste 1", user_id=user_id)
        create_shopping_list("Liste 2", user_id=user_id)
        lists = get_user_shopping_lists(user_id=user_id)
        assert len(lists) == 2

    def test_get_user_shopping_lists_anonymous(self):
        create_shopping_list("Anon 1")
        create_shopping_list("Anon 2")
        lists = get_user_shopping_lists(user_id=None)
        assert len(lists) == 2

    def test_get_shopping_lists_by_ids(self):
        lst1 = create_shopping_list("Liste 1")
        lst2 = create_shopping_list("Liste 2")
        lists = get_shopping_lists_by_ids([lst1["id"], lst2["id"]])
        assert len(lists) == 2

    def test_get_shopping_lists_by_ids_empty(self):
        lists = get_shopping_lists_by_ids([])
        assert len(lists) == 0

    def test_get_all_shopping_lists(self):
        from recipes.features.auth.services import get_or_create_user

        user1 = get_or_create_user("user1", "user1@example.com", "User 1")
        user2 = get_or_create_user("user2", "user2@example.com", "User 2")
        create_shopping_list("Liste 1", user_id=user1)
        create_shopping_list("Liste 2", user_id=user2)
        lists = get_all_shopping_lists()
        assert len(lists) == 2

    def test_delete_shopping_list(self):
        lst = create_shopping_list("A supprimer")
        assert delete_shopping_list(lst["id"]) is True
        assert get_shopping_list_by_id(lst["id"]) is None

    def test_delete_shopping_list_not_found(self):
        assert delete_shopping_list(9999) is False

    def test_rename_shopping_list(self):
        lst = create_shopping_list("Ancien nom")
        rename_shopping_list(lst["id"], "Nouveau nom")
        updated = get_shopping_list_by_id(lst["id"])
        assert updated["name"] == "Nouveau nom"

    def test_touch_shopping_list(self):
        lst = create_shopping_list("Test")
        touch_shopping_list(lst["id"])
        updated = get_shopping_list_by_id(lst["id"])
        assert updated is not None


class TestShoppingListItems:
    def test_add_item(self):
        lst = create_shopping_list("Test")
        item = add_shopping_list_item(lst["id"], 1, "Pommes", "1 kg")
        assert item["text"] == "Pommes"
        assert item["quantity"] == "1 kg"
        assert item["list_id"] == lst["id"]

    def test_get_items(self):
        lst = create_shopping_list("Test")
        add_shopping_list_item(lst["id"], 1, "Pommes")
        add_shopping_list_item(lst["id"], 2, "Viande")
        items = get_shopping_list_items(lst["id"])
        assert len(items) == 2

    def test_toggle_item(self):
        lst = create_shopping_list("Test")
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        toggled = toggle_shopping_list_item(item["id"])
        assert toggled["is_done"] == 1
        toggled = toggle_shopping_list_item(item["id"])
        assert toggled["is_done"] == 0

    def test_toggle_item_not_found(self):
        result = toggle_shopping_list_item(9999)
        assert result is None

    def test_remove_item(self):
        lst = create_shopping_list("Test")
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        assert remove_shopping_list_item(item["id"]) is True
        items = get_shopping_list_items(lst["id"])
        assert len(items) == 0

    def test_remove_item_not_found(self):
        assert remove_shopping_list_item(9999) is False

    def test_update_item(self):
        lst = create_shopping_list("Test")
        item = add_shopping_list_item(lst["id"], 1, "Pommes", "1 kg")
        assert update_shopping_list_item(item["id"], "Poires", "2 kg", 2) is True

    def test_update_item_not_found(self):
        assert update_shopping_list_item(9999, "Test") is False


class TestShoppingListCompletion:
    def test_all_items_done_marks_list_complete(self):
        lst = create_shopping_list("Test")
        item1 = add_shopping_list_item(lst["id"], 1, "Pommes")
        item2 = add_shopping_list_item(lst["id"], 2, "Viande")
        toggle_shopping_list_item(item1["id"])
        toggle_shopping_list_item(item2["id"])
        updated = get_shopping_list_by_id(lst["id"])
        assert updated["all_done_at"] is not None

    def test_not_all_items_done_keeps_list_incomplete(self):
        lst = create_shopping_list("Test")
        item1 = add_shopping_list_item(lst["id"], 1, "Pommes")
        add_shopping_list_item(lst["id"], 2, "Viande")
        toggle_shopping_list_item(item1["id"])
        updated = get_shopping_list_by_id(lst["id"])
        assert updated["all_done_at"] is None


class TestCleanupExpiredLists:
    def test_cleanup_anonymous_old_lists(self):
        lst = create_shopping_list("Old anon")
        # Simulate old list by updating created_at
        from recipes.shared.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE shopping_lists SET created_at = datetime('now', '-10 days') WHERE id = ?",
                (lst["id"],),
            )
        deleted = cleanup_expired_shopping_lists(retention_days=7)
        assert deleted == 1
        assert get_shopping_list_by_id(lst["id"]) is None

    def test_cleanup_completed_old_lists(self):
        from recipes.features.auth.services import get_or_create_user

        user_id = get_or_create_user("cleanup-user", "cleanup@example.com", "Cleanup User")
        lst = create_shopping_list("Old completed", user_id=user_id)
        item = add_shopping_list_item(lst["id"], 1, "Pommes")
        toggle_shopping_list_item(item["id"])
        # Simulate old completed list
        from recipes.shared.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE shopping_lists SET all_done_at = datetime('now', '-10 days') WHERE id = ?",
                (lst["id"],),
            )
        deleted = cleanup_expired_shopping_lists(retention_days=7)
        assert deleted == 1
