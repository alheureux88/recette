"""Tests for db.py — schema, upsert, search, tags."""

import json

import pytest

from recipes.db import (
    get_all_tags,
    get_processed_hash,
    get_recipe,
    init_db,
    mark_processed,
    search_recipes,
    upsert_recipe,
)

SAMPLE = {
    "title": "Tarte Tatin",
    "description": "Classic French upside-down apple tart.",
    "ingredients": ["apples", "butter", "sugar", "puff pastry"],
    "instructions": "Caramelise apples. Top with pastry. Bake. Flip.",
    "tags": ["dessert", "french", "baked"],
    "source_file": "/recipes/tarte_tatin.docx",
    "file_hash": "abc123",
}


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def test_upsert_insert():
    recipe_id = upsert_recipe(SAMPLE)
    assert isinstance(recipe_id, int)
    assert recipe_id > 0


def test_get_recipe_roundtrip():
    recipe_id = upsert_recipe(SAMPLE)
    row = get_recipe(recipe_id)
    assert row is not None
    assert row["title"] == "Tarte Tatin"
    assert sorted(json.loads(row["tags"])) == ["baked", "dessert", "french"]


def test_upsert_update():
    recipe_id = upsert_recipe(SAMPLE)
    updated = {**SAMPLE, "title": "Tarte Tatin Updated", "file_hash": "def456"}
    new_id = upsert_recipe(updated)
    assert new_id == recipe_id  # same row, not a new one
    row = get_recipe(recipe_id)
    assert row["title"] == "Tarte Tatin Updated"


def test_search_by_keyword():
    upsert_recipe(SAMPLE)
    results = search_recipes(query="apple")
    assert len(results) == 1
    assert results[0]["title"] == "Tarte Tatin"


def test_search_by_tag():
    upsert_recipe(SAMPLE)
    results = search_recipes(tags=["dessert"])
    assert len(results) == 1


def test_search_no_match():
    upsert_recipe(SAMPLE)
    assert search_recipes(query="spaghetti") == []


def test_search_all():
    upsert_recipe(SAMPLE)
    upsert_recipe(
        {**SAMPLE, "title": "Quiche", "source_file": "/recipes/quiche.docx", "file_hash": "zzz"}
    )
    assert len(search_recipes()) == 2


def test_get_all_tags():
    upsert_recipe(SAMPLE)
    upsert_recipe(
        {
            **SAMPLE,
            "title": "Quiche",
            "source_file": "/r/q.docx",
            "file_hash": "zzz",
            "tags": ["french", "savory"],
        }
    )
    tags = get_all_tags()
    assert "french" in tags
    assert "dessert" in tags
    assert "savory" in tags
    assert tags == sorted(tags)


def test_processed_file_tracking():
    mark_processed("/recipes/tarte.docx", "hash1")
    assert get_processed_hash("/recipes/tarte.docx") == "hash1"

    mark_processed("/recipes/tarte.docx", "hash2")  # update
    assert get_processed_hash("/recipes/tarte.docx") == "hash2"


def test_get_processed_hash_missing():
    assert get_processed_hash("/recipes/nonexistent.docx") is None
