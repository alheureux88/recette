"""Tests for shopping templates — services and HTTP routes."""

import pytest

from recipes.features.shopping.services import (
    create_shopping_list,
    get_shopping_list_items,
)
from recipes.features.shopping.template_services import (
    add_template_item,
    create_template,
    delete_template,
    get_template_by_id,
    get_template_items,
    get_user_templates,
    remove_template_item,
    rename_template,
    seed_list_from_template,
    update_template_item,
)
from recipes.shared.db import get_conn, init_db


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    with get_conn() as conn:
        for uid in (1, 2):
            conn.execute(
                "INSERT OR IGNORE INTO users (id, subject) VALUES (?, ?)",
                (uid, f"tpl-user-{uid}"),
            )


@pytest.fixture()
def as_user(client, monkeypatch):
    fake_user = {"id": 1, "sub": "tpl-user-1", "name": "Test", "groups": []}
    import recipes.features.recipes.controllers as recipes_ctrl
    import recipes.features.shopping.controllers as shopping_ctrl
    import recipes.features.shopping.template_controllers as tpl_ctrl
    import recipes.shared.auth as auth_mod
    import recipes.shared.web as web_mod

    monkeypatch.setattr(auth_mod, "get_user", lambda request: fake_user)
    monkeypatch.setattr(auth_mod, "OIDC_ENABLED", True)
    monkeypatch.setattr(web_mod, "get_user", lambda request: fake_user)
    monkeypatch.setattr(shopping_ctrl, "get_user", lambda request: fake_user)
    monkeypatch.setattr(recipes_ctrl, "get_user", lambda request: fake_user)
    monkeypatch.setattr(tpl_ctrl.auth_module, "get_user", lambda request: fake_user)
    monkeypatch.setattr(tpl_ctrl.auth_module, "OIDC_ENABLED", True)
    return client


class TestTemplateServices:
    def test_create_and_list_multiple_per_user(self):
        create_template("Hebdo", 1)
        create_template("Fête", 1)
        templates = get_user_templates(1)
        assert len(templates) == 2

    def test_templates_isolated_per_user(self):
        create_template("Mine", 1)
        assert get_user_templates(2) == []

    def test_rename_and_delete(self):
        tpl = create_template("Vieux", 1)
        rename_template(tpl["id"], "Neuf")
        assert get_template_by_id(tpl["id"])["name"] == "Neuf"
        assert delete_template(tpl["id"]) is True
        assert get_template_by_id(tpl["id"]) is None

    def test_items_crud(self):
        tpl = create_template("Hebdo", 1)
        item = add_template_item(tpl["id"], 1, "Lait", "1 L")
        assert item["text"] == "Lait"
        items = get_template_items(tpl["id"])
        assert len(items) == 1
        assert update_template_item(item["id"], "Lait bio", "2 L", 1) is True
        assert remove_template_item(item["id"]) is True
        assert get_template_items(tpl["id"]) == []

    def test_seed_list_from_template(self):
        tpl = create_template("Hebdo", 1)
        add_template_item(tpl["id"], 1, "Lait", "1 L")
        add_template_item(tpl["id"], 5, "Pain")
        lst = create_shopping_list("Courses", user_id=1)
        copied = seed_list_from_template(lst["id"], tpl["id"])
        assert copied == 2
        items = get_shopping_list_items(lst["id"])
        assert {i["text"] for i in items} == {"Lait", "Pain"}


class TestTemplatePages:
    def test_page_requires_login(self, client):
        resp = client.get("/shopping/templates", follow_redirects=False)
        assert resp.status_code == 302

    def test_page_renders_for_user(self, as_user):
        resp = as_user.get("/shopping/templates")
        assert resp.status_code == 200
        assert "Mod" in resp.text or "template" in resp.text.lower()

    def test_create_template(self, as_user):
        resp = as_user.post("/shopping/templates", data={"name": "Hebdo"}, follow_redirects=False)
        assert resp.status_code == 303
        assert get_user_templates(1)

    def test_cannot_view_other_user_template(self, as_user):
        tpl = create_template("Autre", 2)
        resp = as_user.get(f"/shopping/templates/{tpl['id']}", follow_redirects=False)
        assert resp.status_code == 404

    def test_create_list_seeded_with_template(self, as_user):
        tpl = create_template("Hebdo", 1)
        add_template_item(tpl["id"], 1, "Lait", "1 L")
        resp = as_user.post(
            "/shopping/lists",
            data={"name": "Courses", "template_id": str(tpl["id"])},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        list_id = int(resp.headers["location"].rstrip("/").split("/")[-1])
        items = get_shopping_list_items(list_id)
        assert [i["text"] for i in items] == ["Lait"]

    def test_create_list_ignores_other_user_template(self, as_user):
        tpl = create_template("Autre", 2)
        add_template_item(tpl["id"], 1, "Lait")
        resp = as_user.post(
            "/shopping/lists",
            data={"name": "Courses", "template_id": str(tpl["id"])},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        list_id = int(resp.headers["location"].rstrip("/").split("/")[-1])
        assert get_shopping_list_items(list_id) == []

    def test_from_recipe_seeds_new_list(self, as_user):
        from recipes.shared.db import upsert_recipe

        tpl = create_template("Hebdo", 1)
        add_template_item(tpl["id"], 1, "Lait")
        recipe_id = upsert_recipe(
            {
                "title": "Soupe",
                "description": "",
                "steps": [],
                "ingredients": [{"food": "carottes", "department": "fruits-legumes"}],
                "servings": 1,
                "category": None,
                "tags": {},
                "source_file": "/recipes/soupe.docx",
                "file_hash": "soupe1",
            }
        )
        resp = as_user.post(
            f"/api/shopping/from-recipe?recipe_id={recipe_id}",
            json={
                "ingredient_indices": [0],
                "new_list_name": "Courses recette",
                "template_id": tpl["id"],
            },
        )
        assert resp.status_code == 200
        list_id = resp.json()["list_id"]
        texts = {i["text"] for i in get_shopping_list_items(list_id)}
        assert "Lait" in texts
        assert "carottes" in texts


class TestTemplateTheme:
    """Non-régression thème clair/sombre (voir AGENTS.md).

    Chaque contrôle de la page détail doit porter une classe CSS
    thémée existante : un <input>/<button> sans classe retombe sur
    les couleurs natives du navigateur et casse le mode sombre.
    """

    def test_detail_uses_themed_classes(self, as_user):
        import re

        tpl = create_template("Hebdo", 1)
        add_template_item(tpl["id"], 1, "Lait", "1 L")
        resp = as_user.get(f"/shopping/templates/{tpl['id']}")
        assert resp.status_code == 200
        # Classes thémées attendues (formulaires d'ajout, renommage, édition).
        for cls in (
            "shopping-dept-add-input",
            "shopping-dept-add-qty",
            "shopping-dept-add-btn",
            "shopping-rename-input",
            "shopping-rename-save",
            "shopping-rename-cancel",
            "shopping-item-edit-qty",
            "shopping-item-edit-text",
            "shopping-item-save-btn",
            "shopping-item-remove",
        ):
            assert cls in resp.text, f"classe manquante : {cls}"
        # Aucun contrôle texte/bouton sans classe ni sélecteur thémé.
        bare_inputs = re.findall(
            r"<(?:input|select|textarea|button)(?![^>]*\bclass=)[^>]*>",
            resp.text,
        )
        # Seuls les <input type="hidden"> (invisibles) sont tolérés sans classe.
        visible_bare = [
            tag for tag in bare_inputs if not re.search(r'type\s*=\s*["\']hidden["\']', tag)
        ]
        assert visible_bare == [], f"contrôles sans classe : {visible_bare}"

    def test_base_css_safety_net(self):
        from pathlib import Path

        css = Path("static/css/style.css").read_text(encoding="utf-8")
        assert "Base form controls" in css
        assert "var(--surface)" in css
        assert "var(--text)" in css
