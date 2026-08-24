"""Tests for Pydantic models."""

import pytest

from recipes.models import SearchQuery


class TestSearchQuery:
    def test_defaults(self):
        query = SearchQuery()
        assert query.q == ""
        assert query.tags == []
        assert query.category is None

    def test_valid_category_int(self):
        query = SearchQuery(category=5)
        assert query.category == 5

    def test_valid_category_string(self):
        query = SearchQuery(category="42")
        assert query.category == 42

    def test_empty_category_string(self):
        query = SearchQuery(category="")
        assert query.category is None

    def test_none_category(self):
        query = SearchQuery(category=None)
        assert query.category is None

    def test_invalid_category_string(self):
        with pytest.raises(ValueError, match="category must be a valid integer"):
            SearchQuery(category="abc")

    def test_tags_list(self):
        query = SearchQuery(tags=[1, 2, 3])
        assert query.tags == [1, 2, 3]

    def test_search_query(self):
        query = SearchQuery(q="poulet")
        assert query.q == "poulet"
