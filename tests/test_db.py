"""Tests for db.py — schema, upsert, search, tags, categories."""

import pytest

from recipes.db import (
    get_all_categories,
    get_all_tags_grouped,
    get_processed_hash,
    get_recipe,
    init_db,
    mark_processed,
    search_recipes,
    sync_recipe_tags,
    upsert_recipe,
)

SAMPLE = {
    "title": "Tarte Tatin",
    "description": "Classic French upside-down apple tart.",
    "ingredients": ["apples", "butter", "sugar", "puff pastry"],
    "instructions": "Caramelise apples. Top with pastry. Bake. Flip.",
    "category": "dessert",
    "tags": {
        "origin": ["francais"],
        "cooking_method": ["roti"],
    },
    "source_file": "/recipes/tarte_tatin.docx",
    "file_hash": "abc123",
}


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _insert_sample(data=None):
    d = data or SAMPLE
    recipe_id = upsert_recipe(d)
    sync_recipe_tags(recipe_id, d.get("tags", {}))
    return recipe_id


def test_upsert_insert():
    recipe_id = _insert_sample()
    assert isinstance(recipe_id, int)
    assert recipe_id > 0


def test_get_recipe_roundtrip():
    recipe_id = _insert_sample()
    row = get_recipe(recipe_id)
    assert row is not None
    assert row["title"] == "Tarte Tatin"
    assert row["category"] is not None
    assert row["category"]["name"] == "dessert"
    assert "origin" in row["tags"]
    tag_names = [t["name"] for t in row["tags"]["origin"]["tags"]]
    assert "francais" in tag_names


def test_upsert_update():
    recipe_id = _insert_sample()
    updated = {**SAMPLE, "title": "Tarte Tatin Updated", "file_hash": "def456"}
    new_id = upsert_recipe(updated)
    assert new_id == recipe_id
    row = get_recipe(recipe_id)
    assert row["title"] == "Tarte Tatin Updated"


def test_search_by_keyword():
    _insert_sample()
    results = search_recipes(query="apple")
    assert len(results) == 1
    assert results[0]["title"] == "Tarte Tatin"


def test_search_by_tag_id():
    _insert_sample()
    tags_grouped = get_all_tags_grouped()
    francais_tag = None
    for fam_data in tags_grouped.values():
        for t in fam_data["tags"]:
            if t["name"] == "francais":
                francais_tag = t
                break
    assert francais_tag is not None

    results = search_recipes(tag_ids=[francais_tag["id"]])
    assert len(results) == 1


def test_search_by_category():
    _insert_sample()
    categories = get_all_categories()
    dessert_cat = next(c for c in categories if c["name"] == "dessert")
    results = search_recipes(category_id=dessert_cat["id"])
    assert len(results) == 1


def test_search_or_within_family():
    """Selecting multiple tags from the same family should OR them (return recipes with ANY)."""
    # Recipe 1: French origin
    _insert_sample(
        {
            **SAMPLE,
            "title": "French Recipe",
            "tags": {"origin": ["francais"]},
            "source_file": "/r/french.docx",
            "file_hash": "f1",
        }
    )
    # Recipe 2: Italian origin
    _insert_sample(
        {
            **SAMPLE,
            "title": "Italian Recipe",
            "tags": {"origin": ["italien"]},
            "source_file": "/r/italian.docx",
            "file_hash": "f2",
        }
    )

    tags = get_all_tags_grouped()
    francais_id = next(t["id"] for t in tags["origin"]["tags"] if t["name"] == "francais")
    italien_id = next(t["id"] for t in tags["origin"]["tags"] if t["name"] == "italien")

    # Selecting both should return BOTH recipes (OR logic within family)
    results = search_recipes(tag_ids=[francais_id, italien_id])
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert "French Recipe" in titles
    assert "Italian Recipe" in titles


def test_search_and_between_families():
    """Selecting tags from different families should AND them (return recipes with ALL)."""
    # Recipe 1: French + Chicken
    _insert_sample(
        {
            **SAMPLE,
            "title": "French Chicken",
            "tags": {"origin": ["francais"], "protein": ["poulet"]},
            "source_file": "/r/french_chicken.docx",
            "file_hash": "fc1",
        }
    )
    # Recipe 2: French + Beef
    _insert_sample(
        {
            **SAMPLE,
            "title": "French Beef",
            "tags": {"origin": ["francais"], "protein": ["boeuf"]},
            "source_file": "/r/french_beef.docx",
            "file_hash": "fb2",
        }
    )
    # Recipe 3: Italian + Chicken
    _insert_sample(
        {
            **SAMPLE,
            "title": "Italian Chicken",
            "tags": {"origin": ["italien"], "protein": ["poulet"]},
            "source_file": "/r/italian_chicken.docx",
            "file_hash": "ic3",
        }
    )

    tags = get_all_tags_grouped()
    francais_id = next(t["id"] for t in tags["origin"]["tags"] if t["name"] == "francais")
    poulet_id = next(t["id"] for t in tags["protein"]["tags"] if t["name"] == "poulet")

    # Selecting French + Chicken should return only French Chicken (AND logic between families)
    results = search_recipes(tag_ids=[francais_id, poulet_id])
    assert len(results) == 1
    assert results[0]["title"] == "French Chicken"


def test_search_no_match():
    _insert_sample()
    assert search_recipes(query="spaghetti") == []


def test_search_all():
    _insert_sample()
    _insert_sample(
        {**SAMPLE, "title": "Quiche", "source_file": "/recipes/quiche.docx", "file_hash": "zzz"}
    )
    assert len(search_recipes()) == 2


def test_get_all_tags_grouped():
    _insert_sample(
        {
            **SAMPLE,
            "title": "French Recipe",
            "tags": {"origin": ["francais"], "diet": ["vegetarien"]},
            "source_file": "/r/french.docx",
            "file_hash": "f1",
        }
    )
    _insert_sample(
        {
            **SAMPLE,
            "title": "Chicken Recipe",
            "tags": {"protein": ["poulet"], "cooking_method": ["roti"]},
            "source_file": "/r/chicken.docx",
            "file_hash": "f2",
        }
    )
    tags = get_all_tags_grouped()
    assert "origin" in tags
    assert "diet" in tags
    assert "protein" in tags
    assert "cooking_method" in tags
    assert tags["origin"]["display_name"] == "Origine"
    tag_names = [t["name"] for t in tags["origin"]["tags"]]
    assert "francais" in tag_names


def test_get_all_categories():
    _insert_sample({**SAMPLE, "category": "entree", "source_file": "/r/e1.docx", "file_hash": "e1"})
    _insert_sample(
        {**SAMPLE, "category": "plat-principal", "source_file": "/r/p1.docx", "file_hash": "p1"}
    )
    _insert_sample(
        {**SAMPLE, "category": "dessert", "source_file": "/r/d1.docx", "file_hash": "d1"}
    )
    categories = get_all_categories()
    names = [c["name"] for c in categories]
    assert "entree" in names
    assert "plat-principal" in names
    assert "dessert" in names


def test_hierarchy_auto_resolve():
    recipe_id = _insert_sample(
        {
            **SAMPLE,
            "tags": {"origin": ["quebecois"]},
            "source_file": "/r/hierarchy.docx",
            "file_hash": "hhh",
        }
    )
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    origin_tags = [t["name"] for t in recipe["tags"]["origin"]["tags"]]
    assert "quebecois" in origin_tags
    assert "canadien" in origin_tags
    assert "americain" in origin_tags


def test_processed_file_tracking():
    mark_processed("/recipes/tarte.docx", "hash1")
    assert get_processed_hash("/recipes/tarte.docx") == "hash1"

    mark_processed("/recipes/tarte.docx", "hash2")
    assert get_processed_hash("/recipes/tarte.docx") == "hash2"


def test_get_processed_hash_missing():
    assert get_processed_hash("/recipes/nonexistent.docx") is None
