"""Tests for the bilingual data layer in db.py."""

import pytest

from recipes.db import (
    get_all_categories,
    get_recipe,
    init_db,
    search_recipes,
    sync_recipe_tags,
    upsert_recipe,
)


def _bilingual_sample(
    title_fr: str = "Tarte aux pommes",
    title_en: str = "Apple tart",
    description_fr: str = "Tarte française classique",
    description_en: str = "Classic French tart",
    instructions_fr: str = "Étape 1\nÉtape 2",
    instructions_en: str = "Step 1\nStep 2",
    ingredients_fr=None,
    ingredients_en=None,
    source_file: str = "/recipes/tarte.docx",
    file_hash: str = "aaa111",
    category: str = "dessert",
    tags=None,
) -> dict:
    if ingredients_fr is None:
        ingredients_fr = [
            {"food": "pommes", "quantity_min": 4, "quantity_max": None, "unit": None},
            {"food": "beurre", "quantity_min": 100, "quantity_max": None, "unit": "g"},
        ]
    if ingredients_en is None:
        ingredients_en = [
            {"food": "apples", "quantity_min": 4, "quantity_max": None, "unit": None},
            {"food": "butter", "quantity_min": 100, "quantity_max": None, "unit": "g"},
        ]
    if tags is None:
        tags = {"origin": ["francais"]}
    return {
        "lang_fr": {
            "title": title_fr,
            "description": description_fr,
            "instructions": instructions_fr,
            "ingredients": ingredients_fr,
        },
        "lang_en": {
            "title": title_en,
            "description": description_en,
            "instructions": instructions_en,
            "ingredients": ingredients_en,
        },
        "category": category,
        "tags": tags,
        "source_file": source_file,
        "file_hash": file_hash,
    }


BILINGUAL_SAMPLE = _bilingual_sample()


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()


def _insert(payload=None) -> int:
    data = payload or BILINGUAL_SAMPLE
    return upsert_recipe(data)


def test_seed_provides_bilingual_tag_family_display_names():
    from recipes.db import get_tag_families

    fr = {f["name"]: f for f in get_tag_families(lang="fr")}
    en = {f["name"]: f for f in get_tag_families(lang="en")}
    assert fr["origin"]["display_name"] == "Origine"
    assert en["origin"]["display_name"] == "Origin"
    assert fr["diet"]["display_name"] == "Régime alimentaire"
    assert en["diet"]["display_name"] == "Diet"
    assert fr["protein"]["display_name"] == "Protéine principale"
    assert en["protein"]["display_name"] == "Main protein"
    assert fr["cooking_method"]["display_name"] == "Méthode de cuisson"
    assert en["cooking_method"]["display_name"] == "Cooking method"


def test_seed_provides_bilingual_category_display_names():
    fr_cats = {c["name"]: c for c in get_all_categories(lang="fr", only_used=False)}
    en_cats = {c["name"]: c for c in get_all_categories(lang="en", only_used=False)}
    assert fr_cats["entree"]["display_name"] == "Entrée"
    assert en_cats["entree"]["display_name"] == "Starter"
    assert fr_cats["dessert"]["display_name"] == "Dessert"
    assert en_cats["dessert"]["display_name"] == "Dessert"


def test_seed_provides_bilingual_tag_display_names():
    from recipes.db import get_all_tags_grouped, sync_recipe_tags

    # `get_all_tags_grouped` only returns tags that are actually used by a
    # recipe, so we insert a recipe using a few seed tags first.
    recipe_id = _insert()
    sync_recipe_tags(
        recipe_id,
        {
            "origin": ["francais"],
            "diet": ["sans-gluten"],
            "protein": ["poulet"],
        },
    )

    fr = {t["name"]: t for fam in get_all_tags_grouped(lang="fr").values() for t in fam["tags"]}
    en = {t["name"]: t for fam in get_all_tags_grouped(lang="en").values() for t in fam["tags"]}
    assert fr["francais"]["display_name"] == "Français"
    assert en["francais"]["display_name"] == "French"
    assert fr["poulet"]["display_name"] == "Poulet"
    assert en["poulet"]["display_name"] == "Chicken"
    assert fr["sans-gluten"]["display_name"] == "Sans gluten"
    assert en["sans-gluten"]["display_name"] == "Gluten-free"


def test_upsert_stores_both_translations():
    recipe_id = _insert()
    fr = get_recipe(recipe_id, lang="fr")
    en = get_recipe(recipe_id, lang="en")
    assert fr is not None and en is not None
    assert fr["title"] == "Tarte aux pommes"
    assert en["title"] == "Apple tart"
    assert fr["description"] == "Tarte française classique"
    assert en["description"] == "Classic French tart"


def test_get_recipe_returns_localized_ingredients():
    recipe_id = _insert()
    fr = get_recipe(recipe_id, lang="fr")
    en = get_recipe(recipe_id, lang="en")
    assert fr["ingredients"][0]["food"] == "pommes"
    assert en["ingredients"][0]["food"] == "apples"
    assert fr["ingredients"][1]["unit"] == "g"
    assert en["ingredients"][1]["unit"] == "g"


def test_get_recipe_returns_localized_category_display():
    recipe_id = _insert()
    fr = get_recipe(recipe_id, lang="fr")
    en = get_recipe(recipe_id, lang="en")
    assert fr["category"]["name"] == "dessert"
    # Both languages have the same display_name here because the seed list
    # uses the same word for the dessert category. We at least verify the
    # field is populated.
    assert fr["category"]["display_name"] == "Dessert"
    assert en["category"]["display_name"] == "Dessert"


def test_get_recipe_returns_localized_tag_display():
    recipe_id = _insert()
    sync_recipe_tags(recipe_id, {"origin": ["francais"]})
    fr = get_recipe(recipe_id, lang="fr")
    en = get_recipe(recipe_id, lang="en")
    # The auto-resolved hierarchy brings the parent tag too; we just need
    # to verify both languages return the same set of tag *keys* and that
    # their `display_name` matches the requested language.
    fr_tag_names = {t["name"] for t in fr["tags"]["origin"]["tags"]}
    en_tag_names = {t["name"] for t in en["tags"]["origin"]["tags"]}
    assert fr_tag_names == en_tag_names
    fr_by_name = {t["name"]: t for t in fr["tags"]["origin"]["tags"]}
    en_by_name = {t["name"]: t for t in en["tags"]["origin"]["tags"]}
    assert fr_by_name["francais"]["display_name"] == "Français"
    assert en_by_name["francais"]["display_name"] == "French"
    assert fr_by_name["europeen"]["display_name"] == "Européen"
    assert en_by_name["europeen"]["display_name"] == "European"


def test_search_returns_localized_results():
    recipe_id = _insert()
    sync_recipe_tags(recipe_id, {"origin": ["francais"]})

    fr = search_recipes(lang="fr")
    en = search_recipes(lang="en")
    assert len(fr) == 1 and len(en) == 1
    assert fr[0]["title"] == "Tarte aux pommes"
    assert en[0]["title"] == "Apple tart"


def test_search_finds_french_words_in_french_translation():
    _insert()
    fr = search_recipes(query="tarte", lang="fr")
    # The FTS index contains both languages so the recipe is found either
    # way; what changes is what gets returned (the localized title).
    assert len(fr) == 1
    assert fr[0]["title"] == "Tarte aux pommes"


def test_search_finds_english_words_in_english_translation():
    _insert()
    en = search_recipes(query="apple", lang="en")
    assert len(en) == 1
    assert en[0]["title"] == "Apple tart"


def test_upsert_legacy_top_level_shape_still_supported():
    """`upsert_recipe` still accepts the legacy single-language shape (used by
    some tests and historical callers) and replicates it across both langs."""
    recipe_id = upsert_recipe(
        {
            "title": "Legacy Recipe",
            "description": "Legacy",
            "ingredients": [{"food": "x", "quantity_min": 1, "quantity_max": None, "unit": None}],
            "instructions": "Step",
            "category": "dessert",
            "tags": {},
            "source_file": "/r/legacy.docx",
            "file_hash": "z",
        }
    )
    fr = get_recipe(recipe_id, lang="fr")
    en = get_recipe(recipe_id, lang="en")
    assert fr["title"] == "Legacy Recipe"
    assert en["title"] == "Legacy Recipe"
