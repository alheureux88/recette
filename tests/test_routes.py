"""Tests for FastAPI routes — index, search, recipe detail."""

import pytest

from recipes.db import (
    get_all_categories,
    get_all_tags_grouped,
    init_db,
    sync_recipe_tags,
    upsert_recipe,
)

SAMPLE = {
    "title": "Poulet Rôti",
    "description": "Simple French roast chicken.",
    "ingredients": ["chicken", "garlic", "thyme", "butter"],
    "instructions": "Season. Roast at 200°C for 1 hour.",
    "category": "plat-principal",
    "tags": {
        "origin": ["francais"],
        "protein": ["poulet"],
        "cooking_method": ["roti"],
    },
    "source_file": "/recipes/poulet.docx",
    "file_hash": "aaa111",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()
    recipe_id = upsert_recipe(SAMPLE)
    sync_recipe_tags(recipe_id, SAMPLE["tags"])


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_index_shows_tag_families(client):
    resp = client.get("/")
    assert "Origine" in resp.text
    assert "Protéine principale" in resp.text


def test_index_shows_categories(client):
    resp = client.get("/")
    assert "Plat principal" in resp.text


def test_search_by_keyword(client):
    resp = client.get("/search?q=chicken")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_search_no_results(client):
    resp = client.get("/search?q=spaghetti")
    assert resp.status_code == 200
    assert "Aucune recette trouvée" in resp.text


def test_search_by_tag(client):
    tags = get_all_tags_grouped()
    francais_id = None
    for fam_data in tags.values():
        for t in fam_data["tags"]:
            if t["name"] == "francais":
                francais_id = t["id"]
                break
    assert francais_id is not None

    resp = client.get(f"/search?tags={francais_id}")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_search_by_category(client):
    categories = get_all_categories()
    plat_id = next(c["id"] for c in categories if c["name"] == "plat-principal")
    resp = client.get(f"/search?category={plat_id}")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_recipe_detail(client):
    resp = client.get("/recipe/1")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text
    assert "garlic" in resp.text
    assert "Plat principal" in resp.text


def test_recipe_detail_not_found(client):
    resp = client.get("/recipe/9999")
    assert resp.status_code == 404


def test_search_empty_query_returns_all(client):
    resp = client.get("/search?q=")
    assert resp.status_code == 200
    assert "Poulet Rôti" in resp.text


def test_search_invalid_category_returns_422(client):
    resp = client.get("/search?category=abc")
    assert resp.status_code == 422


def test_recipe_detail_invalid_id_returns_422(client):
    resp = client.get("/recipe/0")
    assert resp.status_code == 422


def test_recipe_detail_negative_id_returns_422(client):
    resp = client.get("/recipe/-1")
    assert resp.status_code == 422
