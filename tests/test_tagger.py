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


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    reset_client()


class TestBuildSystemPrompt:
    def test_contains_family_names(self):
        prompt = build_system_prompt()
        assert "Origine" in prompt
        assert "Régime alimentaire" in prompt
        assert "Protéine principale" in prompt
        assert "Méthode de cuisson" in prompt

    def test_contains_seed_tags(self):
        prompt = build_system_prompt()
        assert "asiatique" in prompt
        assert "japonais" in prompt
        assert "poulet" in prompt
        assert "braise" in prompt

    def test_contains_categories(self):
        prompt = build_system_prompt()
        assert "Catégories disponibles" in prompt

    def test_contains_json_format_instructions(self):
        prompt = build_system_prompt()
        assert '"title"' in prompt
        assert '"ingredients"' in prompt
        assert '"instructions"' in prompt
        assert '"category"' in prompt
        assert '"tags"' in prompt


class TestTagRecipe:
    def _mock_openai_response(self, json_str: str) -> MagicMock:
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json_str
        mock_response.choices = [mock_choice]
        return mock_response

    def _mock_anthropic_response(self, json_str: str) -> MagicMock:
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = json_str
        mock_response.content = [mock_content]
        return mock_response

    def test_parses_valid_json(self):
        recipe_json = json.dumps(
            {
                "title": "Tarte Tatin",
                "description": "Classic French tart",
                "ingredients": ["apples", "butter"],
                "instructions": "Step 1\nStep 2",
                "category": "dessert",
                "tags": {"origin": ["francais"]},
                "source_url": None,
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("Some raw recipe text")

        assert result["title"] == "Tarte Tatin"
        assert result["category"] == "dessert"
        assert "origin" in result["tags"]

    def test_strips_markdown_code_blocks(self):
        recipe_json = json.dumps({"title": "Test Recipe", "tags": {}})
        wrapped = f"```json\n{recipe_json}\n```"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(wrapped)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["title"] == "Test Recipe"

    def test_uses_default_title_when_llm_returns_empty(self):
        recipe_json = json.dumps({"title": "", "tags": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text", default_title="Fallback Title")

        assert result["title"] == "Fallback Title"

    def test_uses_default_title_when_llm_returns_placeholder(self):
        recipe_json = json.dumps({"title": "Recette sans titre", "tags": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text", default_title="From Filename")

        assert result["title"] == "From Filename"

    def test_normalizes_tag_values(self):
        recipe_json = json.dumps(
            {
                "title": "Test",
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
                "title": "Test",
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
                "title": "Test",
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
                "title": "Test",
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
        recipe_json = json.dumps({"title": "Minimal"})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["description"] == ""
        assert result["ingredients"] == []
        assert result["instructions"] == ""
        assert result["tags"] == {}
        assert result["category"] is None
        assert result["source_url"] is None

    def test_handles_non_dict_tags(self):
        recipe_json = json.dumps(
            {
                "title": "Test",
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
                "title": "Test",
                "tags": {"origin": "not a list"},
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        with patch("recipes.tagger._get_client", return_value=mock_client):
            result = tag_recipe("text")

        assert result["tags"]["origin"] == []

    def test_anthropic_provider(self):
        recipe_json = json.dumps({"title": "Anthropic Recipe", "tags": {}})

        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_anthropic_response(recipe_json)

        with (
            patch("recipes.tagger._get_client", return_value=mock_client),
            patch("recipes.tagger._get_provider", return_value="anthropic"),
        ):
            result = tag_recipe("text")

        assert result["title"] == "Anthropic Recipe"
        mock_client.messages.create.assert_called_once()

    def test_sends_full_text(self):
        recipe_json = json.dumps({"title": "Test", "tags": {}})

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_openai_response(recipe_json)

        long_text = "x" * 10000

        with patch("recipes.tagger._get_client", return_value=mock_client):
            tag_recipe(long_text)

        call_args = mock_client.chat.completions.create.call_args
        user_content = call_args.kwargs["messages"][1]["content"]
        assert len(user_content) == 10000
