"""Tests refacto déduplication — partials et JS partagés.

Vérifie que les pages utilisent le widget minuteur, la barre cuisine
et les scripts partagés au lieu de code dupliqué en ligne.
"""

import html
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recipes.shared.db import get_recipe, init_db, upsert_recipe

TIMER_RECIPE = {
    "title": "Soupe minuteur partagé",
    "description": "Bouillon.",
    "ingredients": [
        {"food": "eau", "quantity_min": 500, "quantity_max": None, "unit": "ml"},
    ],
    "instructions": "Chauffer. Mijoter.",
    "servings": 2,
    "steps": [
        {"text": "Porter à ébullition", "timer_seconds": None, "ingredients": []},
        {"text": "Laisser mijoter", "timer_seconds": 300, "ingredients": []},
    ],
    "source_file": "/recipes/soupe_partagee.docx",
    "file_hash": "fff777",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()


def _page(resp) -> str:
    return html.unescape(resp.text)


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


def _timer_recipe_id() -> int:
    return upsert_recipe(TIMER_RECIPE)


def _owned_list_id(client: TestClient, name: str) -> int:
    """Crée une liste via la route pour que la session anonyme la possède."""
    resp = client.post("/shopping/lists", data={"name": name}, follow_redirects=False)
    assert resp.status_code == 303
    return int(resp.headers["location"].rstrip("/").split("/")[-1])


class TestSharedTimerScript:
    @pytest.mark.parametrize(
        "url", ["/recipe/{id}", "/recipe/{id}/cook", "/recipe/{id}/cook/slides"]
    )
    def test_pages_use_shared_timer_script(self, client, url):
        recipe_id = _timer_recipe_id()
        page = _page(client.get(url.format(id=_slug(recipe_id))))
        assert "/static/js/timers.js" in page
        assert "cook-timer-icon" in page
        # Plus de moteur minuteur dupliqué en ligne.
        assert "function startTimer(t)" not in page
        assert "function playTimerSound()" not in page

    def test_shared_timer_engine_content(self):
        js = Path("static/js/timers.js").read_text(encoding="utf-8")
        assert "createManager" in js
        assert "function formatTime" in js
        assert "requestNotificationPermission" in js
        assert "{{" not in js  # Aucune variable Jinja dans le statique.

    def test_shared_wake_lock_content(self):
        js = Path("static/js/wake_lock.js").read_text(encoding="utf-8")
        assert "RecetteWakeLock" in js
        assert "wakeLock" in js


class TestSharedWakeLockScript:
    @pytest.mark.parametrize(
        "url",
        ["/recipe/{id}/cook", "/recipe/{id}/cook/slides", "/shopping/{id}/cook"],
    )
    def test_cook_pages_use_shared_wake_lock(self, client, url):
        if url.startswith("/shopping"):
            list_id = _owned_list_id(client, "Liste wake")
            page = _page(client.get(url.format(id=list_id)))
        else:
            recipe_id = _timer_recipe_id()
            page = _page(client.get(url.format(id=_slug(recipe_id))))
        assert "/static/js/wake_lock.js" in page
        assert "async function requestWakeLock" not in page


class TestSharedCookBar:
    @pytest.mark.parametrize(
        "url",
        ["/recipe/{id}/cook", "/recipe/{id}/cook/slides", "/shopping/{id}/cook"],
    )
    def test_pages_use_shared_cook_bar(self, client, url):
        if url.startswith("/shopping"):
            list_id = _owned_list_id(client, "Liste barre")
            page = _page(client.get(url.format(id=list_id)))
        else:
            recipe_id = _timer_recipe_id()
            page = _page(client.get(url.format(id=_slug(recipe_id))))
        assert "cook-bar" in page
        assert "cook-exit" in page
        assert "cook-title" in page
