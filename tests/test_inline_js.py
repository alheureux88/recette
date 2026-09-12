"""Syntaxe du JS inline des templates (node --check, skippé sans node).

Garde-fou : une seule erreur de syntaxe dans un <script> suffit à
neutraliser tout le bloc (bouton collections, modales, minuteurs...).
Les blocs sont extraits des pages rendues puis vérifiés avec node.
"""

import re
import shutil
from pathlib import Path

import pytest

from recipes.features.collections.services import (
    add_recipe_to_collection,
    create_collection,
    promote_to_site,
)
from recipes.shared.db import get_recipe, init_db, upsert_recipe

node_bin = shutil.which("node")

needs_node = pytest.mark.skipif(node_bin is None, reason="node introuvable")


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    from recipes.shared.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject, name) VALUES (?, ?, ?)",
            (1, "test-1", "Test"),
        )


@pytest.fixture()
def as_user(client, monkeypatch):
    fake_user = {"id": 1, "sub": "test-1", "name": "Test", "groups": []}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.auth.get_user", lambda request: fake_user)
    monkeypatch.setattr("recipes.shared.web.get_user", lambda request: fake_user)
    for namespace in (
        "recipes.features.recipes.controllers.get_user",
        "recipes.features.collections.controllers.get_user",
    ):
        monkeypatch.setattr(namespace, lambda request: fake_user)
    return client


def _insert_sample() -> int:
    return upsert_recipe(
        {
            "title": "Poulet Rôti",
            "description": "Un classique",
            "source_file": "/recipes/poulet.docx",
            "file_hash": "aaa111",
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


def _inline_scripts(html: str) -> list[str]:
    """Blocs <script> inline (sans src) d'une page rendue."""
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)


def _check_scripts(blocks: list[str], tmp_path: Path) -> None:
    import subprocess

    assert blocks, "aucun bloc <script> inline trouvé"
    for index, block in enumerate(blocks):
        if not block.strip():
            continue
        target = tmp_path / f"inline_{index}.js"
        target.write_text(block, encoding="utf-8")
        proc = subprocess.run(
            [node_bin, "--check", str(target)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"bloc {index} invalide : {proc.stderr.strip()}"


@needs_node
def test_recipe_inline_js(as_user, tmp_path):
    recipe_id = _insert_sample()
    resp = as_user.get(f"/recipe/{_slug(recipe_id)}")
    assert resp.status_code == 200
    _check_scripts(_inline_scripts(resp.text), tmp_path)


@needs_node
def test_collection_pages_inline_js(as_user, tmp_path):
    recipe_id = _insert_sample()
    collection = create_collection(1, "Noël", "Fêtes")
    collection_id = int(str(collection["id"]))
    add_recipe_to_collection(collection_id, recipe_id)
    promote_to_site(collection_id)

    for url in ("/collections", f"/collections/{collection['slug']}", "/"):
        resp = as_user.get(url)
        assert resp.status_code == 200, url
        _check_scripts(_inline_scripts(resp.text), tmp_path)
