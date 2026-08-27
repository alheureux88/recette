"""Tests for tagger.py — LLM prompt building and response parsing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from recipes.db import init_db
from recipes.tagger import (
    build_system_prompt,
    reset_client,
    tag_recipe,
)


def _mock_openai_response(json_str: str) -> MagicMock:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json_str
    mock_response.choices = [mock_choice]
    return mock_response


def _mock_anthropic_response(json_str: str) -> MagicMock:
    mock_response = MagicMock()
    mock_content = MagicMock()
    mock_content.text = json_str
    mock_response.content = [mock_content]
    return mock_response


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    reset_client()


class TestBuildSystemPrompt:
    def test_contains_family_names(self):
        prompt = build_system_prompt()
        assert "Origine" in prompt
        assert "Origin" in prompt
        assert "Régime alimentaire" in prompt
        assert "Diet" in prompt
        assert "Protéine principale" in prompt
        assert "Main protein" in prompt
        assert "Méthode de cuisson" in prompt
        assert "Cooking method" in prompt

    def test_contains_seed_tags(self):
        prompt = build_system_prompt()
        assert "asiatique" in prompt
        assert "japonais" in prompt
        assert "poulet" in prompt
        assert "braise" in prompt

    def test_contains_categories(self):
        prompt = build_system_prompt()
        assert "Catégories disponibles" in prompt
        assert "Available categories" in prompt

    def test_contains_bilingual_json_format_instructions(self):
        prompt = build_system_prompt()
        assert '"title_fr"' in prompt
        assert '"title_en"' in prompt
        assert '"ingredients"' in prompt
        assert '"instructions_fr"' in prompt
        assert '"instructions_en"' in prompt
        assert '"category"' in prompt
        assert '"tags"' in prompt

    def test_contains_structured_ingredient_format(self):
        prompt = build_system_prompt()
        assert '"servings"' in prompt
        assert '"food_fr"' in prompt
        assert '"food_en"' in prompt
        assert '"quantity_min"' in prompt
        assert '"quantity_max"' in prompt
        assert '"unit"' in prompt
        assert '"tasse"' in prompt
        assert '"c. à soupe"' in prompt

    def test_contains_servings_instructions(self):
        prompt = build_system_prompt()
        assert "portions" in prompt
        assert "personnes" in prompt
        assert "explicite" in prompt
        assert "NE DEVINE PAS" in prompt
        assert "N'ESTIME PAS" in prompt

    def test_contains_bilingual_section(self):
        prompt = build_system_prompt()
        assert "bilingue" in prompt or "deux langues" in prompt
        assert "_fr" in prompt
        assert "_en" in prompt


class TestTagRecipe:
    def _mock_openai_response(self, json_str: str) -> MagicMock:
        return _mock_openai_response(json_str)

    def _mock_anthropic_response(self, json_str: str) -> MagicMock:
        return _mock_anthropic_response(json_str)

    def test_parses_bilingual_json(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Tarte Tatin",
                "title_en": "Tarte Tatin",
                "description_fr": "Tarte française classique",
                "description_en": "Classic French tart",
                "servings": 6,
                "ingredients": [
                    {
                        "food_fr": "pommes",
                        "food_en": "apples",
                        "quantity_min": 4,
                        "quantity_max": None,
                        "unit": None,
                    },
                    {
                        "food_fr": "beurre",
                        "food_en": "butter",
                        "quantity_min": 100,
                        "quantity_max": None,
                        "unit": "g",
                    },
                ],
                "instructions_fr": "Étape 1\nÉtape 2",
                "instructions_en": "Step 1\nStep 2",
                "category": "dessert",
                "tags": {"origin": ["francais"]},
                "source_url": None,
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("Some raw recipe text")

        assert result["lang_fr"]["title"] == "Tarte Tatin"
        assert result["lang_en"]["title"] == "Tarte Tatin"
        assert result["category"] == "dessert"
        assert result["servings"] == 6
        assert "origin" in result["tags"]
        # The French ingredients payload should use the French food names.
        assert result["lang_fr"]["ingredients"][0]["food"] == "pommes"
        assert result["lang_fr"]["ingredients"][0]["quantity_min"] == 4.0
        assert result["lang_fr"]["ingredients"][1]["unit"] == "g"
        # And the English payload the English ones.
        assert result["lang_en"]["ingredients"][0]["food"] == "apples"

    def test_falls_back_to_single_language(self):
        """When the LLM returns the legacy single-language shape, both
        language payloads are populated with the same content."""
        recipe_json = json.dumps(
            {
                "title": "Tarte Tatin",
                "description": "Classic",
                "ingredients": [
                    {"food": "apples", "quantity_min": 4, "quantity_max": None, "unit": None},
                ],
                "instructions": "Step 1",
                "category": "dessert",
                "tags": {"origin": ["francais"]},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["lang_fr"]["title"] == "Tarte Tatin"
        assert result["lang_en"]["title"] == "Tarte Tatin"
        assert result["lang_fr"]["ingredients"][0]["food"] == "apples"
        assert result["lang_en"]["ingredients"][0]["food"] == "apples"

    def test_strips_markdown_code_blocks(self):
        recipe_json = json.dumps({"title_fr": "Test Recipe", "tags": {}})
        wrapped = f"```json\n{recipe_json}\n```"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(wrapped)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["lang_fr"]["title"] == "Test Recipe"
        assert result["lang_en"]["title"] == "Test Recipe"

    def test_uses_default_title_when_llm_returns_empty(self):
        recipe_json = json.dumps({"title_fr": "", "title_en": "", "tags": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text", default_title="Fallback Title")

        assert result["lang_fr"]["title"] == "Fallback Title"
        assert result["lang_en"]["title"] == "Fallback Title"

    def test_uses_default_title_when_llm_returns_placeholder(self):
        recipe_json = json.dumps(
            {"title_fr": "Recette sans titre", "title_en": "Recette sans titre", "tags": {}}
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text", default_title="From Filename")

        assert result["lang_fr"]["title"] == "From Filename"
        assert result["lang_en"]["title"] == "From Filename"

    def test_normalizes_tag_values(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "tags": {"origin": ["  Japonais ", "CHINOIS"]},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["tags"]["origin"] == ["chinois", "japonais"]

    def test_normalizes_category(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "category": "  Plat Principal  ",
                "tags": {},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["category"] == "plat-principal"

    def test_rejects_invalid_source_url(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "source_url": "not-a-url",
                "tags": {},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["source_url"] is None

    def test_accepts_valid_source_url(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "source_url": "https://example.com/recipe",
                "tags": {},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["source_url"] == "https://example.com/recipe"

    def test_raises_on_invalid_json(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response("not json")

        with (
            patch("recipes.tagger._get_client", return_value=mock_client),
            pytest.raises(ValueError, match="invalid JSON"),
        ):
            tag_recipe("text")

    def test_sets_defaults_for_missing_fields(self):
        recipe_json = json.dumps({"title_fr": "Minimal", "title_en": "Minimal"})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["lang_fr"]["description"] == ""
        assert result["lang_en"]["description"] == ""
        assert result["lang_fr"]["ingredients"] == []
        assert result["lang_en"]["ingredients"] == []
        assert result["servings"] is None
        assert result["lang_fr"]["instructions"] == ""
        assert result["lang_en"]["instructions"] == ""
        assert result["tags"] == {}
        assert result["category"] is None
        assert result["source_url"] is None

    def test_handles_non_dict_tags(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "tags": "not a dict",
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["tags"] == {}

    def test_handles_non_list_tag_values(self):
        recipe_json = json.dumps(
            {
                "title_fr": "Test",
                "title_en": "Test",
                "tags": {"origin": "not a list"},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["tags"]["origin"] == []

    def test_anthropic_provider(self):
        recipe_json = json.dumps(
            {"title_fr": "Anthropic Recipe", "title_en": "Anthropic Recipe", "tags": {}}
        )

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(recipe_json)

        with (
            patch("recipes.tagger._get_client", return_value=mock_client),
            patch("recipes.tagger._get_provider", return_value="anthropic"),
        ):
            result = tag_recipe("text")

        assert result["lang_fr"]["title"] == "Anthropic Recipe"
        assert result["lang_en"]["title"] == "Anthropic Recipe"
        mock_client.messages.create.assert_called_once()

    def test_sends_full_text(self):
        recipe_json = json.dumps({"title_fr": "Test", "title_en": "Test", "tags": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        long_text = "x" * 10000

        with patch("recipes.tagger._get_client", return_value=mock_client):
            tag_recipe(long_text)

        call_args = mock_client.chat.completions.create.call_args
        user_content = call_args.kwargs["messages"][1]["content"]
        assert len(user_content) == 10000


class TestIngredientNormalization:
    def _tag(self, ingredients: object) -> list[dict[str, object]]:
        recipe_json = json.dumps(
            {"title_fr": "Test", "title_en": "Test", "ingredients": ingredients, "tags": {}}
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(recipe_json)
        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")
        value = result["lang_fr"]["ingredients"]
        assert isinstance(value, list)
        return value

    def test_string_quantities_parsed(self):
        ingredients = [
            {
                "food_fr": "farine",
                "food_en": "flour",
                "quantity_min": "1/2",
                "quantity_max": None,
                "unit": "tasse",
            },
            {
                "food_fr": "sucre",
                "food_en": "sugar",
                "quantity_min": "1 1/2",
                "quantity_max": "2",
                "unit": "tasse",
            },
        ]
        result = self._tag(ingredients)
        assert result[0]["quantity_min"] == 0.5
        assert result[1]["quantity_min"] == 1.5
        assert result[1]["quantity_max"] == 2.0

    def test_string_ingredients_fallback(self):
        result = self._tag(["sel au goût", "poivre"])
        assert result == [
            {"food": "sel au goût", "quantity_min": None, "quantity_max": None, "unit": None},
            {"food": "poivre", "quantity_min": None, "quantity_max": None, "unit": None},
        ]

    def test_name_field_fallback(self):
        result = self._tag([{"name": "farine", "quantity": 2, "unit": "tasse"}])
        assert result == [
            {"food": "farine", "quantity_min": 2.0, "quantity_max": None, "unit": "tasse"}
        ]

    def test_invalid_entries_dropped(self):
        result = self._tag(["", 42, None, {"food_fr": "sel", "food_en": "salt"}])
        assert result == [{"food": "sel", "quantity_min": None, "quantity_max": None, "unit": None}]

    def test_non_list_returns_empty(self):
        assert self._tag("not a list") == []

    def test_invalid_quantities_become_none(self):
        result = self._tag(
            [
                {
                    "food_fr": "sel",
                    "food_en": "salt",
                    "quantity_min": "beaucoup",
                    "quantity_max": -1,
                    "unit": "  ",
                }
            ]
        )
        assert result == [{"food": "sel", "quantity_min": None, "quantity_max": None, "unit": None}]

    def test_falls_back_to_other_language(self):
        """If `food_fr` is missing, the parser falls back on `food_en` for the
        French payload (and vice versa)."""
        ingredients = [
            {"food_en": "flour", "quantity_min": 2, "quantity_max": None, "unit": "tasse"},
        ]
        result = self._tag(ingredients)
        assert result[0]["food"] == "flour"


class TestServingsParsing:
    def _tag(self, servings: object) -> object:
        recipe_json = json.dumps(
            {"title_fr": "Test", "title_en": "Test", "servings": servings, "tags": {}}
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(recipe_json)
        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")
        return result["servings"]

    def test_int_servings(self):
        assert self._tag(4) == 4

    def test_string_servings(self):
        assert self._tag("6") == 6

    def test_float_whole_servings(self):
        assert self._tag(4.0) == 4

    def test_fraction_servings(self):
        assert self._tag(4.5) == 4.5

    def test_text_servings_rejected(self):
        assert self._tag("pour 4 personnes") is None

    def test_zero_servings_rejected(self):
        assert self._tag(0) is None

    def test_negative_servings_rejected(self):
        assert self._tag(-2) is None

    def test_missing_servings(self):
        assert self._tag(None) is None
