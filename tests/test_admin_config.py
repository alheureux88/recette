"""Tests for the admin Configuration tab — extra Dropbox connections."""

from unittest.mock import MagicMock, patch

import pytest

from recipes.db import (
    DEFAULT_ACCOUNT_ID,
    add_dropbox_connection,
    blacklist_and_delete_recipe,
    delete_dropbox_connection,
    get_all_recipes_admin,
    get_dropbox_connection_credentials,
    get_dropbox_connections,
    get_failed_files,
    get_processed_hash,
    get_recipe,
    get_recipe_provenances,
    get_setting,
    init_db,
    is_default_account_active,
    is_default_account_visible,
    mark_processed,
    record_failed_file,
    search_recipes,
    set_default_account_visible,
    set_dropbox_connection_visible,
    set_setting,
    upsert_recipe,
)


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


RECIPE = {
    "title": "Poulet Roti",
    "description": "desc",
    "ingredients": ["poulet"],
    "instructions": "Roter.",
    "category": "plat-principal",
    "tags": {},
    "file_hash": "h1",
}


def _recipe_for(connection_id=None, source=""):
    return {
        **RECIPE,
        "source_file": source or f"/recipes/{connection_id or 'default'}/r.docx",
        "connection_id": connection_id,
    }


class TestProvenanceDb:
    def test_provenance_only_with_multiple_accounts(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(None))
        # Un seul compte avec recettes -> pas de filtre de provenance
        assert get_recipe_provenances() == [
            {"id": DEFAULT_ACCOUNT_ID, "name": "Défaut", "count": 1}
        ]

        upsert_recipe(_recipe_for(conn_id))
        provs = get_recipe_provenances()
        assert len(provs) == 2
        assert {"id": conn_id, "name": "Famille", "count": 1} in provs

    def test_search_filter_by_account(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(None))
        upsert_recipe(_recipe_for(conn_id))

        assert len(search_recipes(connection_id=conn_id)) == 1
        assert len(search_recipes(connection_id=DEFAULT_ACCOUNT_ID)) == 1
        assert len(search_recipes()) == 2

    def test_hidden_connection_hides_recipes(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(conn_id))

        assert len(search_recipes()) == 1
        set_dropbox_connection_visible(conn_id, False)
        assert search_recipes() == []
        assert search_recipes(connection_id=conn_id) == []
        assert get_recipe_provenances() == []

    def test_hidden_default_hides_recipes(self):
        upsert_recipe(_recipe_for(None))
        assert len(search_recipes()) == 1
        set_default_account_visible(False)
        assert search_recipes() == []
        assert get_recipe_provenances() == []

    def test_recipe_provenance_in_detail(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(conn_id))
        recipe = get_recipe(1)
        assert recipe["provenance"] == {"id": conn_id, "name": "Famille"}

        upsert_recipe({**_recipe_for(None), "source_file": "/other/r.docx"})
        assert get_recipe(2)["provenance"] == {"id": None, "name": "Défaut"}

    def test_upsert_updates_connection(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        data = _recipe_for(None, "/recipes/r.docx")
        upsert_recipe(data)
        upsert_recipe({**data, "connection_id": conn_id})
        assert get_recipe(1)["provenance"]["id"] == conn_id


class TestProvenanceRoutes:
    def test_index_has_provenance_filter_with_multiple_accounts(self, admin, client):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(None))
        resp = client.get("/")
        assert "Provenance" not in resp.text

        upsert_recipe(_recipe_for(conn_id))
        resp = client.get("/")
        assert "Provenance" in resp.text
        assert "Famille" in resp.text
        assert f'value="{DEFAULT_ACCOUNT_ID}"' in resp.text

    def test_card_and_detail_show_provenance(self, admin, client):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(conn_id))
        upsert_recipe(_recipe_for(None))
        assert "card-provenance" in client.get("/search").text
        assert "provenance-badge" in client.get("/recipe/1").text

    def test_search_endpoint_filters_by_account(self, admin, client):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(None))
        upsert_recipe(_recipe_for(conn_id))

        assert "Poulet Roti" in client.get(f"/search?account={conn_id}").text
        assert "Poulet Roti" in client.get("/search?account=default").text

    def test_toggle_active(self, admin):
        conn_id = add_dropbox_connection(**CONN_FORM)
        assert get_dropbox_connections()[0]["active"] == 1
        admin.post(f"/admin/config/dropbox/{conn_id}/toggle-active")
        assert get_dropbox_connections()[0]["active"] == 0
        admin.post(f"/admin/config/dropbox/{conn_id}/toggle-active")
        assert get_dropbox_connections()[0]["active"] == 1

    def test_toggle_visible(self, admin):
        conn_id = add_dropbox_connection(**CONN_FORM)
        admin.post(f"/admin/config/dropbox/{conn_id}/toggle-visible")
        assert get_dropbox_connections()[0]["visible"] == 0

    def test_toggle_default_active(self, admin):
        assert is_default_account_active() is True
        admin.post("/admin/config/dropbox/default/toggle-active")
        assert is_default_account_active() is False
        admin.post("/admin/config/dropbox/default/toggle-active")
        assert is_default_account_active() is True

    def test_toggle_default_visible(self, admin):
        assert is_default_account_visible() is True
        admin.post("/admin/config/dropbox/default/toggle-visible")
        assert is_default_account_visible() is False

    def test_config_shows_toggles(self, admin):
        add_dropbox_connection(**CONN_FORM)
        resp = admin.get("/admin?tab=config")
        assert "Defaut" in resp.text or "Défaut" in resp.text
        assert "Arreter" in resp.text
        assert "Masquer les recettes" in resp.text


@pytest.fixture()
def admin(client, monkeypatch):
    monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
    )
    return client


CONN_FORM = {
    "name": "Famille",
    "refresh_token": "rt-123",
    "folder": "/Recettes",
    "file_filter": "*.odt",
}


class TestConnectionDb:
    def test_add_and_list(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        assert conn_id is not None
        conns = get_dropbox_connections()
        assert len(conns) == 1
        assert conns[0]["name"] == "Famille"
        assert "refresh_token" not in conns[0]

    def test_duplicate_name_rejected(self):
        assert add_dropbox_connection(**CONN_FORM) is not None
        assert add_dropbox_connection(**CONN_FORM) is None

    def test_get_credentials(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        creds = get_dropbox_connection_credentials(conn_id)
        assert creds is not None
        assert creds["refresh_token"] == "rt-123"

    def test_delete(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        assert delete_dropbox_connection(conn_id) is True
        assert get_dropbox_connection_credentials(conn_id) is None
        assert delete_dropbox_connection(conn_id) is False

    def test_delete_removes_recipes_and_traces(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        prefix = f"account:{conn_id}:"
        upsert_recipe(_recipe_for(conn_id, f"{prefix}/r1.docx"))
        mark_processed(f"{prefix}/r1.docx", "h")
        record_failed_file(f"{prefix}/bad.xyz", "unsupported")

        assert delete_dropbox_connection(conn_id) is True
        assert search_recipes() == []
        assert get_all_recipes_admin() == []
        assert get_processed_hash(f"{prefix}/r1.docx") is None
        assert get_failed_files() == []

    def test_delete_keeps_other_accounts(self):
        other_id = add_dropbox_connection(name="Autre", refresh_token="rt2")
        upsert_recipe(_recipe_for(other_id))
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(conn_id))

        assert delete_dropbox_connection(conn_id) is True
        titles = [r["title"] for r in get_all_recipes_admin()]
        assert titles == ["Poulet Roti"]


class TestAdminProvenanceColumns:
    def test_admin_table_shows_provenance(self, admin):
        conn_id = add_dropbox_connection(**CONN_FORM)
        upsert_recipe(_recipe_for(None))
        upsert_recipe(_recipe_for(conn_id))
        record_failed_file(f"account:{conn_id}:bad.xyz", "unsupported")
        blacklist_and_delete_recipe(1)

        resp = admin.get("/admin")
        assert "Provenance" in resp.text
        assert ">Défaut</span>" in resp.text
        assert "Famille" in resp.text

    def test_failed_files_provenance(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        record_failed_file(f"account:{conn_id}:a.xyz", "e1")
        record_failed_file("/default/b.xyz", "e2")
        failed = {f["path"]: f["provenance"] for f in get_failed_files()}
        assert failed[f"account:{conn_id}:a.xyz"] == "Famille"
        assert failed["/default/b.xyz"] == "Défaut"

    def test_deleted_connection_cleans_traces(self):
        conn_id = add_dropbox_connection(**CONN_FORM)
        record_failed_file(f"account:{conn_id}:a.xyz", "e")
        assert delete_dropbox_connection(conn_id) is True
        assert get_failed_files() == []


class TestConfigRoutes:
    def test_config_tab_rendered(self, admin):
        resp = admin.get("/admin?tab=config")
        assert resp.status_code == 200
        assert "Connexions Dropbox" in resp.text

    def test_full_page_navigation_lists_connections(self, admin):
        """Regression : la navigation pleine page doit afficher les connexions."""
        add_dropbox_connection(**CONN_FORM)
        resp = admin.get("/admin?tab=config")
        assert "Famille" in resp.text
        assert "/Recettes" in resp.text

    def test_recipes_tab_is_default(self, admin):
        resp = admin.get("/admin")
        assert resp.status_code == 200
        assert "Connexions Dropbox" not in resp.text

    def test_invalid_tab_falls_back(self, admin):
        resp = admin.get("/admin?tab=bogus")
        assert resp.status_code == 200

    def test_add_connection(self, admin):
        resp = admin.post("/admin/config/dropbox", data=CONN_FORM)
        assert resp.status_code == 200
        assert "ajoutee" in resp.text
        assert len(get_dropbox_connections()) == 1

    def test_add_connection_missing_fields(self, admin):
        resp = admin.post(
            "/admin/config/dropbox",
            data={"name": "X", "refresh_token": ""},
        )
        assert resp.status_code == 422
        assert len(get_dropbox_connections()) == 0

    def test_add_duplicate_name(self, admin):
        admin.post("/admin/config/dropbox", data=CONN_FORM)
        resp = admin.post("/admin/config/dropbox", data=CONN_FORM)
        assert resp.status_code == 422
        assert len(get_dropbox_connections()) == 1

    def test_delete_connection(self, admin):
        conn_id = add_dropbox_connection(**CONN_FORM)
        resp = admin.post(f"/admin/config/dropbox/{conn_id}/delete")
        assert resp.status_code == 200
        assert get_dropbox_connections() == []

    def test_delete_nonexistent(self, admin):
        resp = admin.post("/admin/config/dropbox/9999/delete")
        assert resp.status_code == 200
        assert "introuvable" in resp.text

    def test_test_connection_success(self, admin, monkeypatch):
        conn_id = add_dropbox_connection(**CONN_FORM)

        def fake_verify(refresh_token):
            return "Famille — famille@example.com"

        monkeypatch.setattr("recipes.main.verify_connection_credentials", fake_verify)
        resp = admin.post(f"/admin/config/dropbox/{conn_id}/test")
        assert resp.status_code == 200
        assert "validee : Famille — famille@example.com" in resp.text

    def test_test_connection_failure(self, admin, monkeypatch):
        conn_id = add_dropbox_connection(**CONN_FORM)

        def fake_verify(refresh_token):
            raise ValueError("Dropbox token refresh failed")

        monkeypatch.setattr("recipes.main.verify_connection_credentials", fake_verify)
        resp = admin.post(f"/admin/config/dropbox/{conn_id}/test")
        assert resp.status_code == 200
        assert "Echec de connexion" in resp.text


class TestOauthFlow:
    def test_connect_redirects_to_dropbox(self, admin, monkeypatch):
        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "secret-123")
        resp = admin.get("/admin/config/dropbox/connect", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://www.dropbox.com/oauth2/authorize")
        assert "token_access_type=offline" in resp.headers["location"]

    def test_connect_without_app_key(self, admin, monkeypatch):
        monkeypatch.delenv("DROPBOX_APP_KEY", raising=False)
        resp = admin.get("/admin/config/dropbox/connect")
        assert resp.status_code == 422

    def test_callback_exchanges_code(self, admin, monkeypatch):
        # Prime the session state via the connect endpoint
        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "secret-123")
        admin.get("/admin/config/dropbox/connect", follow_redirects=False)

        def fake_exchange(code, redirect_uri):
            assert code == "the-code"
            assert redirect_uri.endswith("/admin/config/dropbox/callback")
            return "rt-from-oauth"

        def fake_verify(refresh_token):
            assert refresh_token == "rt-from-oauth"
            return "Alice — alice@example.com"

        state_resp = admin.get("/admin/config/dropbox/connect", follow_redirects=False)
        location = state_resp.headers["location"]
        state = [p for p in location.split("&") if p.startswith("state=")][0][6:]

        monkeypatch.setattr("recipes.main.exchange_authorization_code", fake_exchange)
        monkeypatch.setattr("recipes.main.verify_connection_credentials", fake_verify)

        resp = admin.get(f"/admin/config/dropbox/callback?code=the-code&state={state}")
        assert resp.status_code == 200
        # Navigation navigateur (sans HX-Request) -> pleine page avec le layout
        assert "page-title" in resp.text
        assert 'name="refresh_token" value="rt-from-oauth"' in resp.text
        assert 'value="Alice"' in resp.text
        # L'etat OAuth a ete consomme
        assert get_setting("dropbox_oauth_state") == ""

    def test_callback_htmx_renders_partial(self, admin, monkeypatch):
        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "secret-123")
        set_setting("dropbox_oauth_state", "st-1")

        monkeypatch.setattr("recipes.main.exchange_authorization_code", lambda c, r: "rt-x")
        monkeypatch.setattr("recipes.main.verify_connection_credentials", lambda rt: "X")

        resp = admin.get(
            "/admin/config/dropbox/callback?code=c&state=st-1",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "page-title" not in resp.text

    def test_callback_state_mismatch(self, admin, monkeypatch):
        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        resp = admin.get(
            "/admin/config/dropbox/callback?code=x&state=wrong",
        )
        assert resp.status_code == 422

    def test_callback_error_param(self, admin):
        resp = admin.get("/admin/config/dropbox/callback?error=access_denied")
        assert resp.status_code == 200
        assert "refusee" in resp.text


class TestPollerMultiAccount:
    def test_env_credentials_detection(self, monkeypatch):
        from recipes.poller import has_env_dropbox_credentials

        monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("DROPBOX_TOKEN", raising=False)
        assert has_env_dropbox_credentials() is False
        monkeypatch.setenv("DROPBOX_TOKEN", "t")
        assert has_env_dropbox_credentials() is True
        monkeypatch.delenv("DROPBOX_TOKEN")
        monkeypatch.setenv("DROPBOX_REFRESH_TOKEN", "r")
        assert has_env_dropbox_credentials() is True

    def test_run_polls_extra_connection(self, temp_db, monkeypatch):
        """run() should scan the folder of each DB connection."""
        import recipes.poller as poller

        monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("DROPBOX_TOKEN", raising=False)

        add_dropbox_connection(**CONN_FORM)

        mock_client = MagicMock()
        captured: dict[str, object] = {}

        def fake_get_client(conn):
            # Regression : le poller doit passer les identifiants complets
            assert "refresh_token" in conn, (
                "get_connection_client doit recevoir les identifiants (refresh_token)"
            )
            return mock_client

        def fake_list(dbx, folder, ff=None):
            captured["client"] = dbx
            captured["folder"] = folder
            return []

        monkeypatch.setattr(poller, "get_connection_client", fake_get_client)
        monkeypatch.setattr(poller, "list_recipe_files", fake_list)
        monkeypatch.setattr(poller, "list_unsupported_files", lambda dbx, folder=[]: [])

        poller.run()

        assert captured["folder"] == "/Recettes"
        assert captured["client"] == mock_client

    def test_verify_connection_credentials(self, monkeypatch):
        from recipes.poller import verify_connection_credentials

        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "secret-123")

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"access_token": "tok", "expires_in": 3600}

        account = MagicMock()
        account.name.display_name = "Alice"
        account.email = "alice@example.com"

        with (
            patch("recipes.poller.requests.post", return_value=mock_response) as mock_post,
            patch("recipes.poller.dropbox.Dropbox") as mock_dropbox,
        ):
            mock_dropbox.return_value.users_get_current_account.return_value = account
            label = verify_connection_credentials("rt")

        assert label == "Alice — alice@example.com"
        assert mock_post.call_args[1]["data"]["client_id"] == "key-123"

    def test_build_and_exchange_oauth(self, monkeypatch):
        from recipes.poller import build_oauth_authorize_url, exchange_authorization_code

        monkeypatch.setenv("DROPBOX_APP_KEY", "key-123")
        monkeypatch.setenv("DROPBOX_APP_SECRET", "secret-123")

        url = build_oauth_authorize_url("http://test/cb", "state-1")
        assert url.startswith("https://www.dropbox.com/oauth2/authorize?")
        assert "client_id=key-123" in url
        assert "response_type=code" in url
        assert "token_access_type=offline" in url

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {}

        with patch("recipes.poller.requests.post", return_value=mock_response) as mock_post:
            with pytest.raises(ValueError, match="did not return"):
                exchange_authorization_code("c", "cb")

            mock_response.json.return_value = {"refresh_token": "rt-new"}
            rt = exchange_authorization_code("code-x", "http://test/cb")

        assert rt == "rt-new"
        data = mock_post.call_args[1]["data"]
        assert data["grant_type"] == "authorization_code"
        assert data["redirect_uri"] == "http://test/cb"
