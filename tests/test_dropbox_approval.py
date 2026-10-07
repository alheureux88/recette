"""Tests for Dropbox proposals by super-users with owner approval."""

from unittest.mock import MagicMock, patch

import pytest

from recipes.features.admin.services import (
    DROPBOX_STATUS_PENDING,
    MAX_PENDING_PROPOSALS_PER_USER,
    add_dropbox_connection,
    approve_dropbox_connection,
    count_pending_dropbox_proposals,
    get_dropbox_connections,
    list_unapproved_dropbox_connections,
    list_user_dropbox_proposals,
    pop_dropbox_oauth_state,
    reject_dropbox_connection,
    save_dropbox_oauth_state,
    set_superuser_groups,
)
from recipes.features.auth.services import get_or_create_user
from recipes.shared.db import init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _make_user(sub, name="U", email=None):
    return get_or_create_user(subject=sub, email=email or f"{sub}@example.com", name=name)


def _superuser_dict():
    return {
        "id": 1,
        "sub": "super-sub",
        "name": "Sue",
        "email": "super-sub@example.com",
        "groups": ["editors"],
    }


def _owner_dict():
    return {
        "id": 2,
        "sub": "owner-sub",
        "name": "Owen",
        "email": "owner-sub@example.com",
        "groups": ["owner"],
    }


def _auth_as(monkeypatch, user):
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.auth.get_user", lambda request: user)
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.get_user", lambda request: user)


@pytest.fixture()
def superuser_client(client, monkeypatch):
    set_superuser_groups("editors")
    _make_user("super-sub", "Sue")
    _auth_as(monkeypatch, _superuser_dict())
    return client


@pytest.fixture()
def owner_client(client, monkeypatch):
    _make_user("owner-sub", "Owen")
    _auth_as(monkeypatch, _owner_dict())
    return client


def _propose(name="Fam", uid=1):
    return add_dropbox_connection(
        name=name,
        refresh_token="rt",
        folder="/R",
        file_filter="",
        active=False,
        visible=False,
        proposed_by_user_id=uid,
        status=DROPBOX_STATUS_PENDING,
    )


class TestProposalServices:
    def test_add_validates_status(self):
        with pytest.raises(ValueError):
            add_dropbox_connection(name="X", refresh_token="t", status="nope")
        assert add_dropbox_connection(name="X", refresh_token="t") is not None
        assert add_dropbox_connection(name="X", refresh_token="t") is None

    def test_proposal_is_inactive_invisible_pending(self):
        uid = _make_user("s1")
        conn_id = _propose(uid=uid)
        assert conn_id is not None
        row = {c["id"]: c for c in get_dropbox_connections()}[conn_id]
        assert row["active"] == 0
        assert row["visible"] == 0
        assert row["status"] == "pending"
        assert row["proposed_by_user_id"] == uid

    def test_unapproved_lists_pending_first_with_proposer(self):
        uid = _make_user("s3", name="Amy", email="amy@example.com")
        add_dropbox_connection(name="Ok", refresh_token="t")
        pending_id = _propose(name="P", uid=uid)
        add_dropbox_connection(
            name="R",
            refresh_token="t",
            active=False,
            visible=False,
            proposed_by_user_id=uid,
            status="rejected",
        )
        rows = list_unapproved_dropbox_connections()
        assert [r["id"] for r in rows] == [pending_id, rows[1]["id"]]
        assert rows[0]["status"] == "pending"
        assert rows[1]["status"] == "rejected"
        assert rows[0]["proposer_name"] == "Amy"
        assert rows[0]["proposer_email"] == "amy@example.com"

    def test_approve_reject_transitions(self):
        uid = _make_user("s4")
        conn_id = _propose(uid=uid)
        assert count_pending_dropbox_proposals(uid) == 1
        assert reject_dropbox_connection(999999) is False
        assert approve_dropbox_connection(conn_id) is True
        assert approve_dropbox_connection(conn_id) is False
        assert count_pending_dropbox_proposals(uid) == 0
        row = {c["id"]: c for c in get_dropbox_connections()}[conn_id]
        assert (row["status"], row["active"], row["visible"]) == ("approved", 1, 1)

        conn_id2 = _propose(name="P2", uid=uid)
        assert reject_dropbox_connection(conn_id2) is True
        assert reject_dropbox_connection(conn_id2) is False
        assert approve_dropbox_connection(conn_id2) is False
        mine = {p["id"]: p for p in list_user_dropbox_proposals(uid)}
        assert mine[conn_id2]["status"] == "rejected"

    def test_oauth_state_single_use_and_unknown(self):
        save_dropbox_oauth_state("st1", "sub-a", "propose", "verifier-1")
        save_dropbox_oauth_state("st2", "sub-b", "add", "verifier-2")
        flow = pop_dropbox_oauth_state("st1")
        assert flow is not None
        assert flow["sub"] == "sub-a"
        assert flow["purpose"] == "propose"
        assert flow["verifier"] == "verifier-1"
        assert pop_dropbox_oauth_state("st1") is None
        assert pop_dropbox_oauth_state("nope") is None
        assert pop_dropbox_oauth_state(None) is None
        # The other flow survives the first pop.
        assert pop_dropbox_oauth_state("st2") is not None

    def test_oauth_state_expires(self, monkeypatch):
        import time as time_module

        import recipes.features.admin.services as services

        save_dropbox_oauth_state("st-exp", "sub-a", "propose", "v")
        real_time = time_module.time
        monkeypatch.setattr(services.time, "time", lambda: real_time() + 7200)
        assert pop_dropbox_oauth_state("st-exp") is None


class TestProposalPages:
    def test_anonymous_is_redirected_to_login(self, client):
        # 401 -> handler redirects anonymous browsers to / (OIDC disabled here).
        resp = client.get("/admin/dropbox", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    def test_plain_user_gets_403(self, client, monkeypatch):
        _auth_as(
            monkeypatch,
            {"id": 9, "sub": "plain", "name": "P", "groups": []},
        )
        assert client.get("/admin/dropbox").status_code == 403

    def test_superuser_page_ok_but_no_config(self, superuser_client):
        resp = superuser_client.get("/admin/dropbox")
        assert resp.status_code == 200
        assert "dropbox/connect" in resp.text
        assert superuser_client.get("/admin/config").status_code == 403

    def test_propose_creates_pending(self, superuser_client):
        resp = superuser_client.post(
            "/admin/dropbox",
            data={"name": "Fam", "refresh_token": "rt", "folder": "/R", "file_filter": ""},
        )
        assert resp.status_code == 200
        rows = get_dropbox_connections()
        assert len(rows) == 1
        assert (rows[0]["status"], rows[0]["active"], rows[0]["visible"]) == ("pending", 0, 0)

    def test_propose_requires_fields(self, superuser_client):
        resp = superuser_client.post("/admin/dropbox", data={"name": "", "refresh_token": ""})
        assert resp.status_code == 422
        assert get_dropbox_connections() == []

    def test_propose_limit(self, superuser_client):
        for i in range(MAX_PENDING_PROPOSALS_PER_USER):
            resp = superuser_client.post(
                "/admin/dropbox",
                data={"name": f"F{i}", "refresh_token": "rt"},
            )
            assert resp.status_code == 200
        resp = superuser_client.post(
            "/admin/dropbox", data={"name": "TooMany", "refresh_token": "rt"}
        )
        assert resp.status_code == 422
        assert len(get_dropbox_connections()) == MAX_PENDING_PROPOSALS_PER_USER

    def test_cannot_delete_others_or_approved(self, client, monkeypatch):
        _make_user("super-sub", "Sue")
        _make_user("owner-sub", "Owen")
        set_superuser_groups("editors")
        _auth_as(monkeypatch, _superuser_dict())
        own_id = _propose(name="Mine", uid=_make_user("super-sub"))
        assert own_id is not None
        other_id = add_dropbox_connection(name="Theirs", refresh_token="t")
        assert other_id is not None
        # Someone else's connection (even approved) is not deletable here.
        assert client.post(f"/admin/dropbox/{other_id}/delete").status_code == 404
        # Owner approves the own proposal: proposer can no longer delete it here.
        _auth_as(monkeypatch, _owner_dict())
        assert client.post(f"/admin/config/dropbox/{own_id}/approve").status_code == 200
        _auth_as(monkeypatch, _superuser_dict())
        assert client.post(f"/admin/dropbox/{own_id}/delete").status_code == 404
        # Unknown id.
        assert client.post("/admin/dropbox/999999/delete").status_code == 404

    def test_delete_own_pending(self, superuser_client):
        uid = _make_user("super-sub")
        conn_id = _propose(uid=uid)
        assert conn_id is not None
        resp = superuser_client.post(f"/admin/dropbox/{conn_id}/delete")
        assert resp.status_code == 200
        assert get_dropbox_connections() == []


class TestOwnerApproval:
    def test_approve_reject_flow(self, client, monkeypatch):
        _make_user("super-sub", "Sue")
        _make_user("owner-sub", "Owen")
        set_superuser_groups("editors")
        _auth_as(monkeypatch, _superuser_dict())
        client.post("/admin/dropbox", data={"name": "Fam", "refresh_token": "rt"})
        conn_id = get_dropbox_connections()[0]["id"]
        # Super-user cannot approve.
        assert client.post(f"/admin/config/dropbox/{conn_id}/approve").status_code == 403
        # Approve activates + shows.
        _auth_as(monkeypatch, _owner_dict())
        resp = client.post(f"/admin/config/dropbox/{conn_id}/approve")
        assert resp.status_code == 200
        row = get_dropbox_connections()[0]
        assert (row["status"], row["active"], row["visible"]) == ("approved", 1, 1)
        # Approving twice fails.
        assert client.post(f"/admin/config/dropbox/{conn_id}/approve").status_code == 404

    def test_reject_keeps_row(self, client, monkeypatch):
        _make_user("super-sub", "Sue")
        _make_user("owner-sub", "Owen")
        set_superuser_groups("editors")
        _auth_as(monkeypatch, _superuser_dict())
        client.post("/admin/dropbox", data={"name": "Fam", "refresh_token": "rt"})
        conn_id = get_dropbox_connections()[0]["id"]
        assert client.post(f"/admin/config/dropbox/{conn_id}/reject").status_code == 403
        _auth_as(monkeypatch, _owner_dict())
        assert client.post(f"/admin/config/dropbox/{conn_id}/reject").status_code == 200
        assert get_dropbox_connections()[0]["status"] == "rejected"

    def test_toggle_blocked_while_pending(self, client, monkeypatch):
        _make_user("super-sub", "Sue")
        _make_user("owner-sub", "Owen")
        set_superuser_groups("editors")
        _auth_as(monkeypatch, _superuser_dict())
        client.post("/admin/dropbox", data={"name": "Fam", "refresh_token": "rt"})
        conn_id = get_dropbox_connections()[0]["id"]
        _auth_as(monkeypatch, _owner_dict())
        assert client.post(f"/admin/config/dropbox/{conn_id}/toggle-active").status_code == 422
        assert client.post(f"/admin/config/dropbox/{conn_id}/toggle-visible").status_code == 422
        assert get_dropbox_connections()[0]["active"] == 0

    def test_pending_shown_in_config(self, client, monkeypatch):
        _make_user("super-sub", "Sue")
        _make_user("owner-sub", "Owen")
        set_superuser_groups("editors")
        _auth_as(monkeypatch, _superuser_dict())
        client.post("/admin/dropbox", data={"name": "Fam", "refresh_token": "rt"})
        _auth_as(monkeypatch, _owner_dict())
        resp = client.get("/admin/config")
        assert resp.status_code == 200
        assert "Fam" in resp.text


class TestProposalOAuthFlow:
    def test_connect_saves_state_and_redirects(self, superuser_client):
        with patch(
            "recipes.features.admin.dropbox_controllers.build_oauth_authorize_url",
            return_value="https://dropbox.test/auth",
        ):
            resp = superuser_client.get("/admin/dropbox/connect", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://dropbox.test/auth"
        flow = pop_dropbox_oauth_state(self._only_state())
        assert flow is not None
        assert flow["sub"] == "super-sub"
        assert flow["purpose"] == "propose"

    @staticmethod
    def _only_state():
        import json

        from recipes.features.admin.services import get_setting

        states = json.loads(get_setting("dropbox_oauth_states", "{}"))
        assert len(states) == 1
        return next(iter(states))

    def test_callback_propose_renders_prefill(self, superuser_client):
        save_dropbox_oauth_state("st-pro", "super-sub", "propose", "verifier-x")
        with (
            patch(
                "recipes.features.admin.controllers.exchange_authorization_code",
                return_value="rt-new",
            ),
            patch(
                "recipes.features.admin.controllers.verify_connection_credentials",
                return_value="Sue Doe",
            ),
        ):
            resp = superuser_client.get("/admin/config/dropbox/callback?state=st-pro&code=abc")
        assert resp.status_code == 200
        assert "rt-new" in resp.text
        # State is single-use.
        assert pop_dropbox_oauth_state("st-pro") is None

    def test_callback_rejects_other_user_state(self, owner_client):
        save_dropbox_oauth_state("st-pro", "super-sub", "propose", "verifier-x")
        with (
            patch(
                "recipes.features.admin.controllers.exchange_authorization_code",
                return_value="rt-new",
            ),
            patch(
                "recipes.features.admin.controllers.verify_connection_credentials",
                return_value="X",
            ),
        ):
            resp = owner_client.get("/admin/config/dropbox/callback?state=st-pro&code=abc")
        assert resp.status_code == 403

    def test_callback_add_requires_owner(self, client, monkeypatch):
        _make_user("semi-sub", "Semi")
        _auth_as(
            monkeypatch,
            {"id": 7, "sub": "semi-sub", "name": "Semi", "groups": ["editors"]},
        )
        from recipes.features.admin.services import set_superuser_groups

        set_superuser_groups("editors")
        save_dropbox_oauth_state("st-add", "semi-sub", "add", "verifier-x")
        with (
            patch(
                "recipes.features.admin.controllers.exchange_authorization_code",
                return_value="rt-new",
            ),
            patch(
                "recipes.features.admin.controllers.verify_connection_credentials",
                return_value="X",
            ),
        ):
            resp = client.get("/admin/config/dropbox/callback?state=st-add&code=abc")
        assert resp.status_code == 403

    def test_callback_unknown_state(self, superuser_client):
        resp = superuser_client.get("/admin/config/dropbox/callback?state=nope&code=abc")
        assert resp.status_code == 422


class TestPollerSkipsUnapproved:
    def test_run_ignores_pending(self, setup, monkeypatch):
        import recipes.shared.poller as poller_module

        uid = _make_user("s5")
        conn_id = _propose(name="P", uid=uid)
        assert conn_id is not None
        seen = []

        monkeypatch.setattr(poller_module, "has_env_dropbox_credentials", lambda: False)
        monkeypatch.setattr(poller_module, "_poll_account", lambda *a, **k: seen.append(a) or set())
        poller_module.run()
        assert seen == []

    def test_run_polls_approved(self, setup, monkeypatch):
        import recipes.shared.poller as poller_module

        conn_id = add_dropbox_connection(name="A", refresh_token="t")
        assert conn_id is not None
        seen = []

        monkeypatch.setattr(poller_module, "has_env_dropbox_credentials", lambda: False)
        monkeypatch.setattr(
            poller_module,
            "get_connection_client",
            lambda *a, **k: MagicMock(),
        )
        monkeypatch.setattr(poller_module, "_poll_account", lambda *a, **k: seen.append(a) or set())
        poller_module.run()
        assert len(seen) == 1
