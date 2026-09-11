"""Tests des slugs d'URL descriptifs."""

import pytest

from recipes.shared.db import (
    get_recipe_id_by_slug,
    get_recipe_slug,
    init_db,
    upsert_recipe,
)
from recipes.shared.slug import slugify


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()


def _recipe(title, source="r.docx", file_hash="h1"):
    return upsert_recipe(
        {
            "title": title,
            "description": "",
            "steps": [],
            "ingredients": [],
            "source_file": f"/recipes/{source}",
            "file_hash": file_hash,
        }
    )


class TestSlugify:
    def test_simple_title(self):
        assert slugify("Gratin Dauphinois") == "gratin-dauphinois"

    def test_accents_stripped(self):
        assert slugify("Crème brûlée") == "creme-brulee"

    def test_ligatures_expanded(self):
        assert slugify("Œufs cocotte") == "oeufs-cocotte"

    def test_punctuation_collapsed(self):
        assert slugify("Poulet rôti (facile) !") == "poulet-roti-facile"

    def test_empty_title_falls_back(self):
        assert slugify("") == "recette"
        assert slugify("!!!") == "recette"


class TestRecipeSlugs:
    def test_upsert_assigns_slug_from_french_title(self):
        recipe_id = _recipe("Soupe à l'oignon", "soupe.docx", "h2")
        assert get_recipe_slug(recipe_id) == "soupe-a-l-oignon"
        assert get_recipe_id_by_slug("soupe-a-l-oignon") == recipe_id

    def test_duplicate_titles_get_unique_suffix(self):
        first = _recipe("Gratin", "g1.docx", "h3")
        second = _recipe("Gratin", "g2.docx", "h4")
        assert get_recipe_slug(first) == "gratin"
        assert get_recipe_slug(second) == "gratin-2"

    def test_slug_stable_when_title_changes(self):
        recipe_id = _recipe("Ancien titre", "t.docx", "h5")
        assert get_recipe_slug(recipe_id) == "ancien-titre"
        _recipe("Nouveau titre", "t.docx", "h6")
        assert get_recipe_slug(recipe_id) == "ancien-titre"

    def test_unknown_slug_returns_none(self):
        assert get_recipe_id_by_slug("plat-inexistant") is None


class TestSlugPages:
    def test_detail_cook_slides_use_slug(self, client):
        recipe_id = _recipe("Cake citron", "cake.docx", "h7")
        for url in (
            "/recipe/cake-citron",
            "/recipe/cake-citron/cook",
            "/recipe/cake-citron/cook/slides",
        ):
            assert client.get(url).status_code == 200, url
        assert recipe_id > 0

    def test_unknown_slug_returns_404(self, client):
        assert client.get("/recipe/plat-inexistant").status_code == 404
        assert client.get("/recipe/9999").status_code == 404
