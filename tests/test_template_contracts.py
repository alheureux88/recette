"""Contrats templates — clés i18n, hooks JS et smoke fr/en.

Ces tests vérifient ce que les tests unitaires ne voient pas :
- toute clé `_()` / `ngettext()` utilisée en template existe en fr ET en en ;
- les hooks `data-*` pilotés par le JS partagé existent dans le partial ;
- les pages principales répondent 200 (pas 500) dans les deux langues.
"""

import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recipes.shared.db import get_recipe, init_db, upsert_recipe
from recipes.shared.i18n import gettext

TEMPLATES = Path("templates")

TIMER_RECIPE = {
    "title": "Soupe contrats",
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
    "source_file": "/recipes/soupe_contrats.docx",
    "file_hash": "ggg888",
}


@pytest.fixture(autouse=True)
def seed(temp_db):
    init_db()


def _slug(recipe_id: int) -> str:
    recipe = get_recipe(recipe_id)
    assert recipe is not None
    return str(recipe["slug"])


def _template_keys() -> tuple[set[str], set[str]]:
    """Clés `_('...')` et `ngettext('...', '...', ...)` de tous les templates.

    Les clés composées dynamiquement (`_('préfixe_' ~ var)`) sont exclues :
    invérifiables statiquement, elles doivent rester l'exception.
    """
    simple: set[str] = set()
    plural: set[str] = set()
    dynamic: set[str] = set()
    for path in list(TEMPLATES.glob("*.html")) + list(TEMPLATES.glob("partials/*.html")):
        text = path.read_text(encoding="utf-8")
        simple.update(re.findall(r"_\(\s*['\"]([^'\"]+)['\"]", text))
        dynamic.update(re.findall(r"_\(\s*['\"]([^'\"]+)['\"]\s*~", text))
        for sing, plur in re.findall(
            r"ngettext\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", text
        ):
            plural.add(sing)
            plural.add(plur)
    return simple - dynamic, plural


class TestTemplateI18nContracts:
    def test_gettext_keys_exist_in_fr_and_en(self):
        simple, plural = _template_keys()
        assert simple, "aucune clé _() trouvée — regex cassée ?"
        missing = [
            key
            for key in sorted(simple | plural)
            if gettext(key, "fr") == key or gettext(key, "en") == key
        ]
        assert missing == [], f"clés i18n manquantes : {missing}"

    def test_ngettext_forms_covered(self):
        _, plural = _template_keys()
        assert plural, "aucune clé ngettext trouvée — regex cassée ?"


class TestTimerWidgetContract:
    def test_js_hooks_exist_in_partial(self):
        partial = (TEMPLATES / "partials" / "timer_widget.html").read_text(encoding="utf-8")
        engine = Path("static/js/timers.js").read_text(encoding="utf-8")
        provided_roles = set(re.findall(r'data-role="([^"]+)"', partial))
        # data-step-index / data-default-duration → dataset.stepIndex / dataset.defaultDuration
        provided_datasets = {
            "stepIndex": "data-step-index" in partial,
            "defaultDuration": "data-default-duration" in partial,
        }
        needed_roles = set(re.findall(r"\[data-role=\"([^\"]+)\"\]", engine))
        assert needed_roles, "aucun hook data-role dans timers.js — regex cassée ?"
        assert needed_roles <= provided_roles, (
            f"hooks JS sans markup : {needed_roles - provided_roles}"
        )
        assert all(provided_datasets.values()), "data-step-index/default-duration manquants"

    def test_widget_classes_exist_in_css(self):
        css = Path("static/css/style.css").read_text(encoding="utf-8")
        for cls in ("cook-timer", "cook-timer-display", "cook-timer-controls", "cook-timer-btn"):
            assert f".{cls}" in css, f"classe .{cls} sans style"


class TestSmokeFrEn:
    @pytest.mark.parametrize("lang", ["fr", "en"])
    @pytest.mark.parametrize(
        "url",
        [
            "/",
            "/shopping",
            "/recipe/{id}",
            "/recipe/{id}/cook",
            "/recipe/{id}/cook/slides",
        ],
    )
    def test_pages_render_without_500(self, client: TestClient, url: str, lang: str):
        recipe_id = upsert_recipe(TIMER_RECIPE)
        resp = client.get(url.format(id=_slug(recipe_id)), headers={"Accept-Language": lang})
        assert resp.status_code == 200, f"{url} [{lang}] → {resp.status_code}"
        assert "Traceback" not in resp.text
        page = html.unescape(resp.text)
        # Pas de clé i18n non résolue affichée telle quelle dans le HTML visible.
        assert "cook.timer_" not in page
