"""Tests for recipe ratings (1-5 stars)."""

import pytest

from recipes.features.recipes.services import (
    delete_rating,
    get_recipe_rating_summaries,
    get_recipe_rating_summary,
    get_user_rated_recipes,
    get_user_rating,
    set_rating,
)
from recipes.shared.db import get_conn, get_recipe, init_db, upsert_recipe


@pytest.fixture(autouse=True)
def setup(temp_db):
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject) VALUES (?, ?)",
            (1, "test-1"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, subject) VALUES (?, ?)",
            (2, "test-2"),
        )


@pytest.fixture()
def as_user(client, monkeypatch):
    fake_user = {"id": 1, "sub": "test-1", "name": "Test", "groups": []}
    monkeypatch.setattr("recipes.shared.auth.OIDC_ENABLED", True)
    monkeypatch.setattr("recipes.shared.web.OIDC_ENABLED", True)
    for namespace in (
        "recipes.shared.auth.get_user",
        "recipes.shared.web.get_user",
        "recipes.features.recipes.controllers.get_user",
    ):
        monkeypatch.setattr(namespace, lambda request: fake_user)
    return client


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


def _insert_sample(source_file: str = "/recipes/poulet.docx", title: str = "Poulet Roti"):
    return upsert_recipe(
        {
            "title": title,
            "description": "Un classique",
            "source_file": source_file,
            "file_hash": f"hash-{source_file}",
            "file_modified_at": "2024-06-15T10:30:00",
        }
    )


class TestRatingServices:
    def test_set_and_get_user_rating(self):
        recipe_id = _insert_sample()
        assert get_user_rating(1, recipe_id) is None
        set_rating(1, recipe_id, 4)
        assert get_user_rating(1, recipe_id) == 4

    def test_revote_overwrites(self):
        recipe_id = _insert_sample()
        set_rating(1, recipe_id, 2)
        set_rating(1, recipe_id, 5)
        assert get_user_rating(1, recipe_id) == 5
        assert get_recipe_rating_summary(recipe_id)["count"] == 1

    def test_rating_out_of_range_rejected(self):
        recipe_id = _insert_sample()
        for bad in (0, 6, -1):
            with pytest.raises(ValueError):
                set_rating(1, recipe_id, bad)

    def test_average_and_count(self):
        recipe_id = _insert_sample()
        set_rating(1, recipe_id, 5)
        set_rating(2, recipe_id, 3)
        summary = get_recipe_rating_summary(recipe_id)
        assert summary["count"] == 2
        assert summary["average"] == pytest.approx(4.0)

    def test_empty_summary(self):
        recipe_id = _insert_sample()
        assert get_recipe_rating_summary(recipe_id) == {"count": 0, "average": None}
        assert get_recipe_rating_summaries([recipe_id]) == {
            recipe_id: {"count": 0, "average": None}
        }
        assert get_recipe_rating_summaries([]) == {}

    def test_delete_rating(self):
        recipe_id = _insert_sample()
        set_rating(1, recipe_id, 4)
        delete_rating(1, recipe_id)
        assert get_user_rating(1, recipe_id) is None
        assert get_recipe_rating_summary(recipe_id)["count"] == 0

    def test_user_rated_recipes_sorted_best_first(self):
        low = _insert_sample("/recipes/soupe.docx", "Soupe")
        high = _insert_sample("/recipes/gateau.docx", "Gateau")
        mid = _insert_sample("/recipes/salade.docx", "Salade")
        set_rating(1, low, 1)
        set_rating(1, high, 5)
        set_rating(1, mid, 3)
        rated = get_user_rated_recipes(1)
        assert [r["user_rating"] for r in rated] == [5, 3, 1]
        assert [r["title"] for r in rated] == ["Gateau", "Salade", "Soupe"]


class TestRatingEndpoints:
    def test_vote_requires_login(self, client):
        recipe_id = _insert_sample()
        resp = client.post(
            f"/ratings/{_slug(recipe_id)}", json={"rating": 4}, follow_redirects=False
        )
        assert resp.status_code in (401, 302)

    def test_delete_requires_login(self, client):
        recipe_id = _insert_sample()
        resp = client.delete(f"/ratings/{_slug(recipe_id)}", follow_redirects=False)
        assert resp.status_code in (401, 302)

    def test_vote_roundtrip(self, as_user):
        recipe_id = _insert_sample()
        slug = _slug(recipe_id)

        resp = as_user.post(f"/ratings/{slug}", json={"rating": 4})
        assert resp.status_code == 200
        assert get_user_rating(1, recipe_id) == 4
        assert "rating-widget" in resp.text

        resp = as_user.post(f"/ratings/{slug}", json={"rating": 5})
        assert resp.status_code == 200
        assert get_user_rating(1, recipe_id) == 5

        resp = as_user.delete(f"/ratings/{slug}")
        assert resp.status_code == 200
        assert get_user_rating(1, recipe_id) is None

    def test_vote_invalid_rating_rejected(self, as_user):
        recipe_id = _insert_sample()
        resp = as_user.post(f"/ratings/{_slug(recipe_id)}", json={"rating": 9})
        assert resp.status_code == 422

    def test_vote_unknown_recipe_404(self, as_user):
        resp = as_user.post("/ratings/recette-inexistante", json={"rating": 4})
        assert resp.status_code == 404

    def test_recipe_page_shows_average(self, as_user):
        recipe_id = _insert_sample()
        slug = _slug(recipe_id)
        set_rating(1, recipe_id, 5)
        set_rating(2, recipe_id, 3)
        page = as_user.get(f"/recipe/{slug}")
        assert page.status_code == 200
        assert "rating-widget" in page.text
        assert "Moyenne" in page.text or "Average" in page.text
        # Pas de "ma note : x/5" dans le résumé : les étoiles l'indiquent déjà.
        # ("Retirer ma note", le bouton ✕, reste légitime.)
        assert "ma note : 5/5" not in page.text
        assert "my rating: 5/5" not in page.text

    def test_my_ratings_page_sorted(self, as_user):
        low = _insert_sample("/recipes/soupe.docx", "Soupe note")
        high = _insert_sample("/recipes/gateau.docx", "Gateau note")
        as_user.post(f"/ratings/{_slug(low)}", json={"rating": 1})
        as_user.post(f"/ratings/{_slug(high)}", json={"rating": 5})
        page = as_user.get("/ratings")
        assert page.status_code == 200
        assert "Gateau note" in page.text
        assert "Soupe note" in page.text
        assert page.text.index("Gateau note") < page.text.index("Soupe note")
        assert "card-meta" in page.text
        assert "ma note" in page.text or "my rating" in page.text

    def test_my_ratings_page_empty(self, as_user):
        page = as_user.get("/ratings")
        assert page.status_code == 200

    def test_my_ratings_requires_login(self, client):
        resp = client.get("/ratings", follow_redirects=False)
        assert resp.status_code == 302


class TestRatingFilter:
    def _seed(self):
        top = _insert_sample("/recipes/top.docx", "Top plat")
        flop = _insert_sample("/recipes/flop.docx", "Flop plat")
        plain = _insert_sample("/recipes/plain.docx", "Sans note")
        set_rating(1, top, 5)
        set_rating(2, top, 4)
        set_rating(1, flop, 2)
        return top, flop, plain

    def test_min_rating_keeps_only_best(self):
        from recipes.shared.db import search_recipes

        top, _flop, _plain = self._seed()
        results = search_recipes(min_rating=4)
        assert [r["title"] for r in results] == ["Top plat"]
        assert top

    def test_min_rating_excludes_unrated(self):
        from recipes.shared.db import search_recipes

        self._seed()
        titles = [r["title"] for r in search_recipes(min_rating=1.5)]
        assert "Sans note" not in titles

    def test_max_rating_keeps_only_worst(self):
        from recipes.shared.db import search_recipes

        self._seed()
        assert [r["title"] for r in search_recipes(max_rating=3)] == ["Flop plat"]

    def test_min_and_max_combined(self):
        from recipes.shared.db import search_recipes

        self._seed()
        assert [r["title"] for r in search_recipes(min_rating=1.5, max_rating=4)] == ["Flop plat"]

    def test_neutral_bounds_are_noop(self):
        from recipes.shared.db import search_recipes

        self._seed()
        assert len(search_recipes()) == 3
        assert len(search_recipes(min_rating=1, max_rating=5)) == 3
        assert len(search_recipes(min_rating=0, max_rating=5)) == 3

    def test_search_endpoint_with_min_rating(self, client):
        self._seed()
        resp = client.get("/search", params={"min_rating": 4})
        assert resp.status_code == 200
        assert "Top plat" in resp.text
        assert "Flop plat" not in resp.text
        assert "Sans note" not in resp.text

    def test_search_endpoint_with_max_rating(self, client):
        self._seed()
        resp = client.get("/search", params={"max_rating": 2})
        assert resp.status_code == 200
        assert "Flop plat" in resp.text
        assert "Top plat" not in resp.text

    def test_search_endpoint_rejects_garbage(self, client):
        resp = client.get("/search", params={"min_rating": "beaucoup"})
        assert resp.status_code == 422
