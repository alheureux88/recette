"""Tests for FastAPI routes — index, search, recipe detail."""

import html

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
    "ingredients": [
        {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
        {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"},
        {"food": "thym", "quantity_min": 2, "quantity_max": None, "unit": "brin"},
        {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"},
    ],
    "instructions": "Season. Roast at 200°C for 1 hour.",
    "servings": 4,
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


def _page(resp) -> str:
    """Texte HTML déséchappé (Jinja transforme les apostrophes en &#39;)."""
    return html.unescape(resp.text)


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
    assert "ail" in resp.text
    assert "Plat principal" in resp.text


def test_recipe_detail_shows_formatted_ingredients_and_controls(client):
    resp = client.get("/recipe/1")
    assert resp.status_code == 200
    page = _page(resp)
    assert "Portions" in page
    assert "Unités" in page
    assert "3 gousses d'ail" in page
    assert "50 g de beurre" in page
    assert 'value="4"' in page


def test_recipe_detail_servings_scaling(client):
    resp = client.get("/recipe/1?servings=8")
    assert resp.status_code == 200
    assert "100 g de beurre" in _page(resp)


def test_recipe_detail_units_imperial(client):
    resp = client.get("/recipe/1?units=imperial")
    assert resp.status_code == 200
    assert "1 3/4 oz de beurre" in _page(resp)


def test_recipe_detail_invalid_params_fall_back(client):
    resp = client.get("/recipe/1?servings=abc&units=bogus")
    assert resp.status_code == 200
    assert "50 g de beurre" in _page(resp)


def test_recipe_ingredients_partial(client):
    resp = client.get("/recipe/1/ingredients")
    assert resp.status_code == 200
    page = _page(resp)
    assert "Ingrédients" in page
    assert "3 gousses d'ail" in page
    assert 'hx-get="/recipe/1/ingredients"' in page


def test_recipe_ingredients_partial_scales_servings(client):
    resp = client.get("/recipe/1/ingredients?servings=2")
    assert resp.status_code == 200
    assert "25 g de beurre" in _page(resp)


def test_recipe_ingredients_partial_metric(client):
    resp = client.get("/recipe/1/ingredients?servings=8&units=metric")
    assert resp.status_code == 200
    assert "100 g de beurre" in _page(resp)


def test_recipe_ingredients_partial_imperial(client):
    resp = client.get("/recipe/1/ingredients?units=imperial")
    assert resp.status_code == 200
    assert "1 3/4 oz de beurre" in _page(resp)


def test_recipe_ingredients_partial_invalid_params_fall_back(client):
    resp = client.get("/recipe/1/ingredients?servings=&units=metric")
    assert resp.status_code == 200
    assert "50 g de beurre" in _page(resp)


def _insert_without_servings(**overrides: object) -> int:
    data = {
        **SAMPLE,
        "title": "Soupe sans portions",
        "servings": None,
        "source_file": "/recipes/sans_portions.docx",
        "file_hash": "bbb222",
    }
    data.update(overrides)
    recipe_id = upsert_recipe(data)
    sync_recipe_tags(recipe_id, SAMPLE["tags"])
    return recipe_id


def test_recipe_detail_multiplier_when_no_servings(client):
    recipe_id = _insert_without_servings()
    resp = client.get(f"/recipe/{recipe_id}?multiplier=2")
    page = _page(resp)
    assert "Multiplicateur" in page
    assert "Portions" not in page
    assert "100 g de beurre" in page
    assert 'value="2"' in page


def test_recipe_detail_multiplier_ignored_when_servings_known(client):
    resp = client.get("/recipe/1?multiplier=3")
    assert "50 g de beurre" in _page(resp)


def test_recipe_ingredients_multiplier_scales(client):
    recipe_id = _insert_without_servings()
    resp = client.get(f"/recipe/{recipe_id}/ingredients?multiplier=1.5")
    assert resp.status_code == 200
    assert "75 g de beurre" in _page(resp)


def test_recipe_ingredients_multiplier_with_units(client):
    recipe_id = _insert_without_servings()
    resp = client.get(f"/recipe/{recipe_id}/ingredients?multiplier=2&units=imperial")
    assert resp.status_code == 200
    assert "3 1/2 oz de beurre" in _page(resp)


def test_recipe_ingredients_multiplier_invalid_falls_back(client):
    recipe_id = _insert_without_servings()
    resp = client.get(f"/recipe/{recipe_id}/ingredients?multiplier=abc")
    assert resp.status_code == 200
    assert "50 g de beurre" in _page(resp)


def test_recipe_ingredients_multiplier_fraction_value(client):
    recipe_id = _insert_without_servings()
    resp = client.get(f"/recipe/{recipe_id}/ingredients?multiplier=0.5")
    assert resp.status_code == 200
    assert "25 g de beurre" in _page(resp)
    assert 'value="0.5"' in resp.text


def test_recipe_ingredients_controls_hidden_for_legacy_strings(client):
    recipe_id = _insert_without_servings(
        ingredients=["du beurre", "de l'ail", "sel au goût"],
    )
    resp = client.get(f"/recipe/{recipe_id}/ingredients")
    page = _page(resp)
    assert "Multiplicateur" not in page
    assert "Unités" not in page
    assert "du beurre" in page


def test_recipe_ingredients_partial_not_found(client):
    resp = client.get("/recipe/999/ingredients")
    assert resp.status_code == 404


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


# ---------------------------------------------------------------------------
# Localized ingredient rendering (units + preposition)
# ---------------------------------------------------------------------------
_EN_SAMPLE_INGREDIENTS_FR = [
    {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
    {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"},
    {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"},
]
_EN_SAMPLE_INGREDIENTS_EN = [
    {"food": "chicken", "quantity_min": 1, "quantity_max": None, "unit": None},
    {"food": "garlic", "quantity_min": 3, "quantity_max": None, "unit": "clove"},
    {"food": "butter", "quantity_min": 50, "quantity_max": None, "unit": "g"},
]


def _insert_bilingual_recipe(source_file: str, file_hash: str) -> int:
    data = {
        "lang_fr": {
            "title": "Poulet Rôti",
            "description": "Simple French roast chicken.",
            "instructions": "Season. Roast at 200°C for 1 hour.",
            "ingredients": _EN_SAMPLE_INGREDIENTS_FR,
        },
        "lang_en": {
            "title": "Roast Chicken",
            "description": "Simple French roast chicken.",
            "instructions": "Season. Roast at 200°C for 1 hour.",
            "ingredients": _EN_SAMPLE_INGREDIENTS_EN,
        },
        "category": "plat-principal",
        "tags": {"origin": ["francais"], "protein": ["poulet"], "cooking_method": ["roti"]},
        "source_file": source_file,
        "file_hash": file_hash,
    }
    recipe_id = upsert_recipe(data)
    sync_recipe_tags(recipe_id, data["tags"])
    return recipe_id


def test_recipe_detail_en_uses_english_units_and_preposition(client):
    recipe_id = _insert_bilingual_recipe("/r/en1.docx", "en1")
    resp = client.get(f"/recipe/{recipe_id}", cookies={"lang": "en"})
    page = _page(resp)
    assert resp.status_code == 200
    assert "3 cloves of garlic" in page
    assert "50 g of butter" in page
    assert "1 chicken" in page


def test_recipe_detail_en_imperial_units(client):
    recipe_id = _insert_bilingual_recipe("/r/en2.docx", "en2")
    resp = client.get(f"/recipe/{recipe_id}?units=imperial", cookies={"lang": "en"})
    page = _page(resp)
    assert "1 3/4 oz of butter" in page


def test_recipe_detail_fr_keeps_french_units(client):
    """The same recipe rendered in French still shows French units and d' elision."""
    recipe_id = _insert_bilingual_recipe("/r/en3.docx", "en3")
    page = _page(client.get(f"/recipe/{recipe_id}"))
    assert "3 gousses d'ail" in page
    assert "50 g de beurre" in page


def test_recipe_detail_en_tbsp_unit(client):
    data = {
        "lang_fr": {
            "title": "Vinaigrette",
            "description": "",
            "instructions": "",
            "ingredients": [
                {"food": "huile", "quantity_min": 3, "quantity_max": None, "unit": "c. à soupe"},
            ],
        },
        "lang_en": {
            "title": "Vinaigrette",
            "description": "",
            "instructions": "",
            "ingredients": [
                {"food": "oil", "quantity_min": 3, "quantity_max": None, "unit": "c. à soupe"},
            ],
        },
        "category": "sauce",
        "tags": {},
        "source_file": "/r/vinaigrette.docx",
        "file_hash": "vinaigrette",
    }
    recipe_id = upsert_recipe(data)
    fr = _page(client.get(f"/recipe/{recipe_id}"))
    en = _page(client.get(f"/recipe/{recipe_id}", cookies={"lang": "en"}))
    assert "3 c. à soupe d'huile" in fr
    assert "3 tbsp of oil" in en


def test_recipe_detail_en_with_cup_unit(client):
    data = {
        "lang_fr": {
            "title": "Crêpes",
            "description": "",
            "instructions": "",
            "ingredients": [
                {"food": "farine", "quantity_min": 1, "quantity_max": None, "unit": "tasse"},
                {"food": "lait", "quantity_min": 2, "quantity_max": None, "unit": "tasse"},
            ],
        },
        "lang_en": {
            "title": "Pancakes",
            "description": "",
            "instructions": "",
            "ingredients": [
                {"food": "flour", "quantity_min": 1, "quantity_max": None, "unit": "tasse"},
                {"food": "milk", "quantity_min": 2, "quantity_max": None, "unit": "tasse"},
            ],
        },
        "category": "dessert",
        "tags": {},
        "source_file": "/r/crepes.docx",
        "file_hash": "crepes",
    }
    recipe_id = upsert_recipe(data)
    resp = client.get(f"/recipe/{recipe_id}", cookies={"lang": "en"})
    page = _page(resp)
    assert "1 cup of flour" in page
    assert "2 cups of milk" in page
