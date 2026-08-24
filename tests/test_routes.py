"""Tests for FastAPI routes — index, search, recipe detail."""

import pytest

from recipes.db import init_db, upsert_recipe

SAMPLE = {
    "title": "Poulet Rôti",
    "description": "Simple French roast chicken.",
    "ingredients": ["chicken", "garlic", "thyme", "butter"],
    "instructions": "Season. Roast at 200°C for 1 hour.",
    "tags": ["chicken", "french", "dinner", "baked"],
    "source_file": "/recipes/poulet.docx",
    "file_hash": "aaa111",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()
    upsert_recipe(SAMPLE)


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_index_shows_tags(client):
    resp = client.get("/")
    assert "french" in resp.text
    assert "dinner" in resp.text


def test_search_by_keyword(client):
    resp = client.get("/search?q=chicken")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_search_no_results(client):
    resp = client.get("/search?q=spaghetti")
    assert resp.status_code == 200
    assert "Aucune recette trouvée" in resp.text


def test_search_by_tag(client):
    resp = client.get("/search?tags=french")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_recipe_detail(client):
    resp = client.get("/recipe/1")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text
    assert "garlic" in resp.text


def test_recipe_detail_not_found(client):
    resp = client.get("/recipe/9999")
    assert resp.status_code == 404


def test_search_empty_query_returns_all(client):
    resp = client.get("/search?q=")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text
