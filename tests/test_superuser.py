"""Tests for the super-user role — content admin without system administration."""

import pytest

from recipes.features.admin.services import (
    get_superuser_groups,
    set_superuser_groups,
)
from recipes.shared import auth as auth_module
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _as_groups(monkeypatch, groups):
    user = {"id": 1, "sub": "test", "name": "Test", "groups": groups}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.auth.get_user", lambda request: user)
    # `shared.web` a importé ces noms par valeur : il faut les patcher aussi
    # pour que le contexte des templates (`user`, `auth_enabled`) suive.
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.get_user", lambda request: user)


@pytest.fixture()
def as_superuser(client, monkeypatch):
    set_superuser_groups("editors")
    _as_groups(monkeypatch, ["editors"])
    return client


@pytest.fixture()
def as_admin(client, monkeypatch):
    _as_groups(monkeypatch, ["owner"])
    return client


@pytest.fixture()
def as_user(client, monkeypatch):
    _as_groups(monkeypatch, [])
    return client


class TestSuperuserGroups:
    def test_empty_by_default(self):
        assert get_superuser_groups() == []

    def test_set_and_get_roundtrip(self):
        assert set_superuser_groups("editors, famille") == ["editors", "famille"]
        assert get_superuser_groups() == ["editors", "famille"]

    def test_set_normalizes_duplicates_and_spaces(self):
        assert set_superuser_groups(" b ,a,b, ") == ["b", "a"]
        assert get_superuser_groups() == ["b", "a"]

    def test_env_groups_are_included(self, monkeypatch):
        monkeypatch.setenv("SUPERUSER_GROUPS", "envgroup")
        assert "envgroup" in auth_module.get_superuser_groups()

    def test_singular_env_fallback(self, monkeypatch):
        monkeypatch.delenv("SUPERUSER_GROUPS", raising=False)
        monkeypatch.setenv("SUPERUSER_GROUP", "oldgroup")
        assert "oldgroup" in auth_module.get_superuser_groups()


class TestRoleHelpers:
    def test_is_superuser(self, monkeypatch):
        from fastapi import Request

        set_superuser_groups("editors")
        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(
            "recipes.shared.auth.get_user",
            lambda req: {"id": 1, "sub": "t", "groups": ["editors"]},
        )
        assert auth_module.is_superuser(request) is True
        assert auth_module.can_admin_content(request) is True
        assert auth_module.is_admin(request) is False

    def test_plain_user_has_no_rights(self, monkeypatch):
        from fastapi import Request

        set_superuser_groups("editors")
        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(
            "recipes.shared.auth.get_user",
            lambda req: {"id": 1, "sub": "t", "groups": []},
        )
        assert auth_module.is_superuser(request) is False
        assert auth_module.can_admin_content(request) is False

    def test_full_admin_is_content_admin_but_not_superuser(self, monkeypatch):
        from fastapi import Request

        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(
            "recipes.shared.auth.get_user",
            lambda req: {"id": 1, "sub": "t", "groups": ["owner"]},
        )
        assert auth_module.is_admin(request) is True
        assert auth_module.can_admin_content(request) is True


class TestContentAdminRoutes:
    def test_superuser_can_open_admin_recipes(self, as_superuser):
        resp = as_superuser.get("/admin")
        assert resp.status_code == 200

    def test_superuser_can_read_recipes_json(self, as_superuser):
        assert as_superuser.get("/admin/recipes.json").status_code == 200

    def test_superuser_can_open_shopping_admin(self, as_superuser):
        assert as_superuser.get("/admin/shopping").status_code == 200

    def test_superuser_can_open_collections_admin(self, as_superuser):
        assert as_superuser.get("/admin/collections").status_code == 200

    def test_superuser_blocked_from_system_config(self, as_superuser):
        assert as_superuser.get("/admin/config").status_code == 403

    def test_superuser_blocked_from_system_actions(self, as_superuser):
        assert as_superuser.post("/admin/config/model", data={"llm_model": "x"}).status_code == 403
        assert (
            as_superuser.post(
                "/admin/config/superusers", data={"superuser_groups": "a"}
            ).status_code
            == 403
        )

    def test_plain_user_still_blocked(self, as_user):
        assert as_user.get("/admin").status_code == 403
        assert as_user.get("/admin/shopping").status_code == 403
        assert as_user.get("/admin/collections").status_code == 403

    def test_full_admin_keeps_system_access(self, as_admin):
        assert as_admin.get("/admin/config").status_code == 200
        assert as_admin.get("/admin").status_code == 200


class TestSuperuserConfigRoute:
    def test_admin_can_set_superuser_groups(self, as_admin):
        resp = as_admin.post(
            "/admin/config/superusers", data={"superuser_groups": "editors, famille"}
        )
        assert resp.status_code == 200
        assert get_superuser_groups() == ["editors", "famille"]

    def test_admin_can_clear_superuser_groups(self, as_admin):
        set_superuser_groups("editors")
        resp = as_admin.post("/admin/config/superusers", data={"superuser_groups": ""})
        assert resp.status_code == 200
        assert get_superuser_groups() == []

    def test_config_page_shows_superusers_section(self, as_admin):
        resp = as_admin.get("/admin/config")
        assert resp.status_code == 200
        assert 'name="superuser_groups"' in resp.text

    def test_newly_configured_group_gains_access(self, as_admin, client, monkeypatch):
        as_admin.post("/admin/config/superusers", data={"superuser_groups": "redac"})
        _as_groups(monkeypatch, ["redac"])
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/config").status_code == 403


class TestNavigation:
    def test_superuser_nav_hides_system_link(self, as_superuser):
        body = as_superuser.get("/").text
        assert "/admin/shopping" in body
        assert "/admin/collections" in body
        assert "/admin/config" not in body

    def test_admin_nav_shows_system_link(self, as_admin):
        body = as_admin.get("/").text
        assert "/admin/config" in body
