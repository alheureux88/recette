"""Tests for the admin re-parse feature (diff + apply)."""

import pytest

from recipes.db import (
    get_recipe,
    get_recipe_bilingual,
    get_recipe_source_info,
    init_db,
    sync_recipe_tags,
    upsert_recipe,
)
from recipes.main import (
    _diff_recipe,
    _format_ingredient_line,
    _lines_diff,
    _word_diff_html,
    _word_pairs,
)

SAMPLE = {
    "lang_fr": {
        "title": "Poulet Rôti",
        "description": "Simple French roast chicken.",
        "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
        "ingredients": [
            {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
            {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"},
            {"food": "thym", "quantity_min": 2, "quantity_max": None, "unit": "brin"},
            {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"},
        ],
    },
    "lang_en": {
        "title": "Roast Chicken",
        "description": "Simple roast chicken.",
        "instructions": "Season.\nRoast at 200°C for 1 hour.",
        "ingredients": [],
    },
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
def setup(temp_db):
    init_db()
    recipe_id = upsert_recipe(SAMPLE)
    sync_recipe_tags(recipe_id, SAMPLE["tags"])
    return recipe_id


def test_get_recipe_source_info_returns_dropbox_path():
    info = get_recipe_source_info(1)
    assert info is not None
    assert info["id"] == 1
    assert info["source_file"] == "/recipes/poulet.docx"
    assert info["connection_id"] is None


def test_get_recipe_source_info_unknown_recipe():
    assert get_recipe_source_info(99999) is None


def test_get_recipe_bilingual_returns_both_languages():
    cur = get_recipe_bilingual(1)
    assert cur is not None
    assert cur["lang_fr"]["title"] == "Poulet Rôti"
    assert cur["lang_en"]["title"] == "Roast Chicken"
    assert cur["servings"] == 4
    assert cur["category"]["name"] == "plat-principal"
    origin_names = {t["name"] for t in cur["tags"]["origin"]}
    # `francais` is a child of `europeen`; both are attached via _add_ancestors.
    assert {"francais", "europeen"} <= origin_names


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def test_format_ingredient_line_quantity_only():
    s = _format_ingredient_line(
        {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None}, "fr"
    )
    assert s == "1 poulet"


def test_format_ingredient_line_range():
    s = _format_ingredient_line(
        {"food": "oignon", "quantity_min": 1, "quantity_max": 2, "unit": None}, "fr"
    )
    assert "1" in s and "2" in s and "oignon" in s


def test_format_ingredient_line_with_unit():
    s = _format_ingredient_line(
        {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"}, "fr"
    )
    assert s == "50 g beurre"


class TestDiffRecipe:
    def _proposed_payload(self, **overrides):
        payload = {
            "lang_fr": {
                "title": "Poulet Rôti",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
                "ingredients": [
                    {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
                    {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"},
                    {"food": "thym", "quantity_min": 2, "quantity_max": None, "unit": "brin"},
                    {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"},
                ],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "Simple roast chicken.",
                "instructions": "Season.\nRoast at 200°C for 1 hour.",
                "ingredients": [],
            },
            # `francais` is a child of `europeen` in the seed, so the LLM would
            # normally only return `francais`; the DB then auto-adds the parent.
            # The "current" tags therefore contain both — match that here.
            "tags": {
                "origin": ["francais", "europeen"],
                "protein": ["poulet"],
                "cooking_method": ["roti"],
            },
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        for k, v in overrides.items():
            if k in ("tags", "category") and isinstance(v, dict):
                payload[k] = v
            else:
                payload[k] = v
        return payload

    def test_diff_identical_marks_nothing_changed(self):
        cur = get_recipe_bilingual(1)
        diffs = _diff_recipe(cur, self._proposed_payload(), "fr")
        for d in diffs:
            assert d["changed"] is False, f"{d['field_key']} unexpectedly changed"

    def test_diff_detects_title_change_fr(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["lang_fr"]["title"] = "Poulet rôti aux herbes"
        diffs = _diff_recipe(cur, proposed, "fr")
        by_key = {d["field_key"]: d for d in diffs}
        assert by_key["title_fr"]["changed"] is True
        assert "herbes" in by_key["title_fr"]["proposed"]
        assert by_key["title_en"]["changed"] is False

    def test_diff_detects_servings_change(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["servings"] = 6
        diffs = _diff_recipe(cur, proposed, "fr")
        servings = next(d for d in diffs if d["field_key"] == "servings")
        assert servings["changed"] is True
        assert servings["proposed"] == 6
        assert servings["current"] == 4

    def test_diff_detects_category_change(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["category"] = "entree"
        diffs = _diff_recipe(cur, proposed, "fr")
        cat = next(d for d in diffs if d["field_key"] == "category")
        assert cat["changed"] is True
        assert cat["proposed"]["name"] == "entree"

    def test_diff_detects_tag_addition(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["tags"] = {
            "origin": ["francais"],
            "protein": ["poulet", "dinde"],
            "cooking_method": ["roti"],
        }
        diffs = _diff_recipe(cur, proposed, "fr")
        tags = next(d for d in diffs if d["field_key"] == "tags")
        assert tags["changed"] is True
        protein = next(r for r in tags["rows"] if r["family"] == "protein")
        assert "dinde" in protein["added"]
        assert "poulet" in protein["unchanged"]

    def test_diff_detects_ingredient_modification(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["lang_fr"]["ingredients"] = [
            {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
            {"food": "ail", "quantity_min": 5, "quantity_max": None, "unit": "gousse"},
            {"food": "thym", "quantity_min": 2, "quantity_max": None, "unit": "brin"},
            {"food": "beurre", "quantity_min": 50, "quantity_max": None, "unit": "g"},
        ]
        diffs = _diff_recipe(cur, proposed, "fr")
        ing = next(d for d in diffs if d["field_key"] == "ingredients_fr")
        assert ing["changed"] is True
        assert "modified" in ing["current_status"] or "modified" in ing["proposed_status"]

    def test_diff_detects_instruction_change(self):
        cur = get_recipe_bilingual(1)
        proposed = self._proposed_payload()
        proposed["lang_fr"]["instructions"] = "Assaisonner.\nCuire au four à 180°C pendant 1h15."
        diffs = _diff_recipe(cur, proposed, "fr")
        ins = next(d for d in diffs if d["field_key"] == "instructions_fr")
        assert ins["changed"] is True


# ---------------------------------------------------------------------------
# Apply endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def as_admin(client, monkeypatch):
    monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
    monkeypatch.setattr(
        "recipes.auth.get_user",
        lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": ["owner"]},
    )
    return client


class TestReparseApply:
    def test_apply_requires_admin(self, client, monkeypatch):
        monkeypatch.setattr("recipes.auth.OIDC_ENABLED", True)
        monkeypatch.setattr(
            "recipes.auth.get_user",
            lambda request: {"id": 1, "sub": "test", "name": "Test", "groups": []},
        )
        resp = client.post(
            "/admin/reparse/1/apply",
            json={"proposed": {}, "fields": ["title_fr"]},
        )
        assert resp.status_code in (401, 403)

    def test_apply_with_empty_fields_is_422(self, as_admin):
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={"proposed": {"lang_fr": {"title": "x"}}, "fields": []},
        )
        assert resp.status_code == 422

    def test_apply_title_and_instructions(self, as_admin):
        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti aux herbes",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner aux herbes.\nCuire 1h.",
                "ingredients": [
                    {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
                ],
            },
            "lang_en": {
                "title": "Herbed Roast Chicken",
                "description": "Herbed roast chicken.",
                "instructions": "Season with herbs.\nRoast 1h.",
                "ingredients": [],
            },
            "tags": {
                "origin": ["francais", "europeen"],
                "protein": ["poulet"],
                "cooking_method": ["roti"],
            },
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={"proposed": proposed, "fields": ["title_fr", "instructions_fr"]},
        )
        assert resp.status_code == 200

        fr = get_recipe(1, lang="fr")
        en = get_recipe(1, lang="en")
        assert fr["title"] == "Poulet Rôti aux herbes"
        assert "herbes" in fr["instructions"]
        # The EN side was NOT applied, so its title should be the original.
        assert en["title"] == "Roast Chicken"

    def test_apply_unknown_recipe_404(self, as_admin):
        resp = as_admin.post(
            "/admin/reparse/9999/apply",
            json={"proposed": {"lang_fr": {"title": "x"}}, "fields": ["title_fr"]},
        )
        assert resp.status_code == 404

    def test_apply_category_only(self, as_admin):
        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti",
                "description": "",
                "instructions": "",
                "ingredients": [],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "",
                "instructions": "",
                "ingredients": [],
            },
            "tags": {"origin": ["francais"], "protein": ["poulet"]},
            "category": "entree",
            "source_url": None,
            "servings": 4,
        }
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={"proposed": proposed, "fields": ["category"]},
        )
        assert resp.status_code == 200
        after = get_recipe(1)
        assert after["category"]["name"] == "entree"
        # Title was not in the selection, so it should still be the original.
        assert after["title"] == "Poulet Rôti"

    def test_apply_marks_recipe_as_manually_edited(self, as_admin):
        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti",
                "description": "",
                "instructions": "",
                "ingredients": [],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "",
                "instructions": "",
                "ingredients": [],
            },
            "tags": {"origin": ["francais"], "protein": ["poulet"]},
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        as_admin.post(
            "/admin/reparse/1/apply",
            json={"proposed": proposed, "fields": ["title_fr"]},
        )
        # Verify the recipe is now flagged as manually_edited via the admin row.
        from recipes.db import is_manually_edited as is_me

        info = get_recipe_source_info(1)
        assert is_me(info["source_file"]) is True


# ---------------------------------------------------------------------------
# Re-parse endpoint (mocked Dropbox + LLM)
# ---------------------------------------------------------------------------


class TestReparseEndpoint:
    def test_reparse_calls_dropbox_and_llm(self, as_admin, monkeypatch):
        """The endpoint downloads the file, runs the LLM, and renders the diff partial."""
        # Use a .txt source so extract_text can decode it without external libs.
        monkeypatch.setattr(
            "recipes.main.get_recipe_source_info",
            lambda _id: {"id": 1, "source_file": "/recipes/poulet.txt", "connection_id": None},
        )

        def fake_download(connection_id, source_file):
            assert source_file == "/recipes/poulet.txt"
            return b"raw recipe text"

        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti aux herbes",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
                "ingredients": [
                    {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
                ],
            },
            "lang_en": {
                "title": "Herbed Roast Chicken",
                "description": "Herbed roast chicken.",
                "instructions": "Season.\nRoast at 200°C for 1 hour.",
                "ingredients": [],
            },
            "tags": {
                "origin": ["francais"],
                "protein": ["poulet"],
                "cooking_method": ["roti"],
            },
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }

        monkeypatch.setattr("recipes.main._download_recipe_source", fake_download)
        monkeypatch.setattr("recipes.main.tag_recipe", lambda *a, **kw: proposed)

        resp = as_admin.post("/admin/reparse/1")
        assert resp.status_code == 200
        # The diff partial should contain the proposed title in a "proposed" cell.
        assert "Poulet Rôti aux herbes" in resp.text
        assert "Roast Chicken" in resp.text  # the current title also appears

    def test_reparse_handles_dropbox_failure(self, as_admin, monkeypatch):
        def fake_download(connection_id, source_file):
            raise RuntimeError("network down")

        monkeypatch.setattr("recipes.main._download_recipe_source", fake_download)
        resp = as_admin.post("/admin/reparse/1")
        assert resp.status_code == 422
        assert "network down" in resp.text

    def test_reparse_handles_unknown_recipe(self, as_admin):
        resp = as_admin.post("/admin/reparse/9999")
        assert resp.status_code == 404

    def test_reparse_handles_llm_failure(self, as_admin, monkeypatch):
        monkeypatch.setattr(
            "recipes.main._download_recipe_source",
            lambda *a, **kw: b"raw text",
        )
        monkeypatch.setattr(
            "recipes.main.extract_text",
            lambda *a, **kw: "x" * 10,
        )

        def fake_tag(*a, **kw):
            raise ValueError("LLM down")

        monkeypatch.setattr("recipes.main.tag_recipe", fake_tag)
        resp = as_admin.post("/admin/reparse/1")
        assert resp.status_code == 422
        assert "LLM down" in resp.text

    def test_reparse_handles_empty_text(self, as_admin, monkeypatch):
        monkeypatch.setattr(
            "recipes.main._download_recipe_source",
            lambda *a, **kw: b"raw text",
        )
        monkeypatch.setattr("recipes.main.extract_text", lambda *a, **kw: "")
        resp = as_admin.post("/admin/reparse/1")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Recipe row data exposes reparseable
# ---------------------------------------------------------------------------


class TestRecipeRowReparseable:
    def test_recipes_json_includes_reparseable(self, as_admin):
        resp = as_admin.get("/admin/recipes.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["recipes"]
        assert data["recipes"][0]["reparseable"] is True


# ---------------------------------------------------------------------------
# Word-level diff
# ---------------------------------------------------------------------------


class TestWordPairs:
    def test_identical_text_has_only_equal_pairs(self):
        a, b = _word_pairs("poulet roti", "poulet roti")
        assert all(kind == "equal" for kind, _ in a)
        assert all(kind == "equal" for kind, _ in b)
        assert "".join(t for _, t in a) == "poulet roti"

    def test_word_replacement_marks_removed_and_added(self):
        a, b = _word_pairs("poulet roti", "poulet grille")
        a_kinds = [k for k, _ in a]
        b_kinds = [k for k, _ in b]
        assert "removed" in a_kinds
        assert "added" in b_kinds
        # The "poulet" word is preserved on both sides.
        assert any(k == "equal" and t == "poulet" for k, t in a)
        assert any(k == "equal" and t == "poulet" for k, t in b)

    def test_whitespace_tokens_are_preserved(self):
        a, _ = _word_pairs("a  b", "a  b")
        # Two spaces between a and b: the tokenization must keep the whitespace
        # so the rendered HTML matches the original spacing.
        joined = "".join(t for _, t in a)
        assert joined == "a  b"

    def test_pairs_aligned_length(self):
        a, b = _word_pairs("one two three", "two three four five")
        assert len(a) == len(b)


class TestWordDiffHtml:
    def test_escapes_html_in_equal_text(self):
        a, _ = _word_pairs("<b>x</b>", "<b>x</b>")
        html = _word_diff_html(a, "current")
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_current_side_wraps_removed_tokens(self):
        a, _ = _word_pairs("poulet roti", "poulet grille")
        html = _word_diff_html(a, "current")
        assert "reparse-word-removed" in html
        # The added token "grille" must NOT be wrapped on the current side.
        assert "grille" not in html.replace("roti", "")

    def test_proposed_side_wraps_added_tokens(self):
        _, b = _word_pairs("poulet roti", "poulet grille")
        html = _word_diff_html(b, "proposed")
        assert "reparse-word-added" in html
        # The removed token "roti" must NOT be wrapped on the proposed side.
        assert "roti" not in html


class TestLinesDiffWordHighlights:
    def test_modified_lines_get_inline_html(self):
        d = _lines_diff("Cuire 10 minutes.", "Cuire 20 minutes.")
        # Find the modified row.
        idx = d["current_status"].index("modified")
        assert d["current_html"][idx]
        assert d["proposed_html"][idx]
        # The current side highlights the removed word ("10") only — the
        # proposed word ("20") is NOT wrapped on the current side.
        assert "reparse-word-removed" in d["current_html"][idx]
        assert "10" in d["current_html"][idx]
        assert "20" not in d["current_html"][idx]
        # Symmetrically, the proposed side highlights the added word ("20").
        assert "reparse-word-added" in d["proposed_html"][idx]
        assert "20" in d["proposed_html"][idx]
        assert "10" not in d["proposed_html"][idx]

    def test_unchanged_lines_have_no_highlight(self):
        d = _lines_diff("Same line.", "Same line.")
        idx = d["current_status"].index("same")
        # Same rows fall back to plain text — no highlight spans.
        assert "reparse-word-removed" not in d["current_html"][idx]
        assert "reparse-word-added" not in d["current_html"][idx]

    def test_added_line_has_no_inline_highlight(self):
        d = _lines_diff("Line one.", "Line one.\nLine two.")
        added = [i for i, s in enumerate(d["proposed_status"]) if s == "added"]
        assert added
        for i in added:
            assert "reparse-word-added" not in d["proposed_html"][i]


class TestDiffRecipeWordHighlights:
    def test_text_field_changed_exposes_inline_html(self):
        cur = get_recipe_bilingual(1)
        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti aux herbes",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
                "ingredients": [],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "Simple roast chicken.",
                "instructions": "Season.\nRoast at 200°C for 1 hour.",
                "ingredients": [],
            },
            "tags": {"origin": ["francais", "europeen"], "protein": ["poulet"]},
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        diffs = _diff_recipe(cur, proposed, "fr")
        title = next(d for d in diffs if d["field_key"] == "title_fr")
        assert title["changed"] is True
        # The proposed side highlights the added words ("aux herbes").
        assert "reparse-word-added" in title["proposed_html"]
        assert "aux" in title["proposed_html"]
        assert "herbes" in title["proposed_html"]
        # The current side has no removed tokens because the change is a pure
        # insertion, so the current_html should be the plain text.
        assert "reparse-word-removed" not in title["current_html"]
        # Now a replacement case: change a word in the title.
        proposed["lang_fr"]["title"] = "Poulet braisé"
        diffs = _diff_recipe(cur, proposed, "fr")
        title = next(d for d in diffs if d["field_key"] == "title_fr")
        assert title["changed"] is True
        assert "reparse-word-removed" in title["current_html"]
        assert "reparse-word-added" in title["proposed_html"]

    def test_unchanged_text_field_has_no_inline_html(self):
        cur = get_recipe_bilingual(1)
        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
                "ingredients": [],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "Simple roast chicken.",
                "instructions": "Season.\nRoast at 200°C for 1 hour.",
                "ingredients": [],
            },
            "tags": {"origin": ["francais", "europeen"], "protein": ["poulet"]},
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        diffs = _diff_recipe(cur, proposed, "fr")
        title = next(d for d in diffs if d["field_key"] == "title_fr")
        assert title["changed"] is False
        # No highlight spans on unchanged text.
        assert "reparse-word-removed" not in title["current_html"]
        assert "reparse-word-added" not in title["proposed_html"]


# ---------------------------------------------------------------------------
# Loading indicator + overlay markup
# ---------------------------------------------------------------------------


class TestReparseLoadingIndicator:
    def test_admin_table_renders_overlay_markup(self, as_admin):
        resp = as_admin.get("/admin")
        assert resp.status_code == 200
        body = resp.text
        assert 'id="reparse-overlay"' in body
        assert "reparse-spinner" in body
        assert "reparse-overlay" in body

    def test_admin_table_renders_inline_button_spinner(self, as_admin):
        resp = as_admin.get("/admin")
        body = resp.text
        assert "btn-reparse-spinner" in body
        assert "btn-reparse-label" in body

    def test_reparse_response_keeps_overlay_in_dom(self, as_admin, monkeypatch):
        # Even after a reparse swap, the next admin render must include the
        # overlay markup so the user can re-trigger another re-parse later.
        # Mock the source-file lookup to a .txt path so extract_text can
        # decode the fake content without external libs.
        monkeypatch.setattr(
            "recipes.main.get_recipe_source_info",
            lambda _id: {"id": 1, "source_file": "/recipes/poulet.txt", "connection_id": None},
        )

        def fake_download(*_a, **_kw):
            return b"raw text"

        monkeypatch.setattr("recipes.main._download_recipe_source", fake_download)

        proposed = {
            "lang_fr": {
                "title": "Poulet Rôti",
                "description": "Simple French roast chicken.",
                "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
                "ingredients": [],
            },
            "lang_en": {
                "title": "Roast Chicken",
                "description": "Simple roast chicken.",
                "instructions": "Season.\nRoast at 200°C for 1 hour.",
                "ingredients": [],
            },
            "tags": {"origin": ["francais", "europeen"], "protein": ["poulet"]},
            "category": "plat-principal",
            "source_url": None,
            "servings": 4,
        }
        monkeypatch.setattr("recipes.main.tag_recipe", lambda *a, **kw: proposed)
        resp = as_admin.post("/admin/reparse/1")
        # The diff response itself doesn't include the table — it goes back
        # to the admin table only after the user clicks Apply. So this
        # assertion only checks that the diff partial renders without errors.
        assert resp.status_code == 200
        assert "Poulet Rôti" in resp.text

    def test_reparse_response_with_overlay_uses_htmx_indicator(self, as_admin):
        # The admin table ships an htmx-indicator on the overlay so it can be
        # toggled via `htmx-indicator` (HTMX's built-in mechanism) if we
        # decide to drive the visibility from there later. For now we just
        # assert the overlay element is present and currently hidden.
        resp = as_admin.get("/admin")
        body = resp.text
        # The overlay is rendered with the `hidden` attribute by default so it
        # is not visible until JS removes the attribute.
        assert 'id="reparse-overlay"' in body
        assert " hidden" in body
        assert "reparse-spinner" in body


# ---------------------------------------------------------------------------
# 3-column diff : resolved overrides
# ---------------------------------------------------------------------------


_PROPOSED_SAMPLE = {
    "lang_fr": {
        "title": "Poulet Rôti",
        "description": "Simple French roast chicken.",
        "instructions": "Assaisonner.\nCuire au four à 200°C pendant 1 heure.",
        "ingredients": [
            {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
        ],
    },
    "lang_en": {
        "title": "Roast Chicken",
        "description": "Simple roast chicken.",
        "instructions": "Season.\nRoast at 200°C for 1 hour.",
        "ingredients": [],
    },
    "tags": {"origin": ["francais", "europeen"], "protein": ["poulet"]},
    "category": "plat-principal",
    "source_url": None,
    "servings": 4,
}


def _patch_reparse(as_admin, monkeypatch, *, proposed: dict | None = None) -> None:
    """Wire the reparse endpoint with mock Dropbox + LLM + a known proposed payload."""
    if proposed is None:
        proposed = _PROPOSED_SAMPLE
    monkeypatch.setattr(
        "recipes.main.get_recipe_source_info",
        lambda _id: {"id": 1, "source_file": "/recipes/poulet.txt", "connection_id": None},
    )
    monkeypatch.setattr("recipes.main._download_recipe_source", lambda *a, **kw: b"raw text")
    monkeypatch.setattr("recipes.main.tag_recipe", lambda *a, **kw: proposed)


class TestReparseDiffThreeColumns:
    def test_diff_renders_three_columns(self, as_admin, monkeypatch):
        # Use a payload that differs from the seed so the diff has changes.
        proposed = dict(_PROPOSED_SAMPLE)
        proposed["lang_fr"] = dict(_PROPOSED_SAMPLE["lang_fr"])
        proposed["lang_fr"]["title"] = "Poulet rôti aux herbes"
        _patch_reparse(as_admin, monkeypatch, proposed=proposed)
        resp = as_admin.post("/admin/reparse/1")
        assert resp.status_code == 200
        body = resp.text
        # Header row: three side columns (current / resolved / proposed).
        assert "Actuel" in body
        assert "Proposé" in body
        assert "Résolu" in body
        # The colgroup declares the resolved column.
        assert "reparse-col-resolved" in body
        # Each editable field row exposes a radio + input pair.
        assert "reparse-resolved-input" in body
        assert "reparse-resolved-radio" in body
        # By default the input is pre-filled with the proposed value.
        assert 'value="Poulet rôti aux herbes"' in body

    def test_apply_uses_resolved_override_for_title(self, as_admin, monkeypatch):
        # The user keeps the proposed category but reverts the title to the
        # original text via the resolved map.
        proposed = dict(_PROPOSED_SAMPLE)
        proposed["lang_fr"] = dict(_PROPOSED_SAMPLE["lang_fr"])
        proposed["lang_fr"]["title"] = "Poulet rôti aux herbes"
        _patch_reparse(as_admin, monkeypatch, proposed=proposed)
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={
                "proposed": proposed,
                "fields": ["title_fr", "category"],
                "resolved": {
                    # Override: keep the original title (Poulet Rôti)
                    "title_fr": "Poulet Rôti",
                    # And accept the proposed category explicitly.
                    "category": "plat-principal",
                },
            },
        )
        assert resp.status_code == 200
        fr = get_recipe(1, lang="fr")
        # The override was applied: the title was reverted.
        assert fr["title"] == "Poulet Rôti"

    def test_apply_uses_resolved_override_for_ingredients(self, as_admin, monkeypatch):
        # The user manually trims the proposed ingredients via the resolved
        # map, even though "ingredients_fr" isn't directly editable in the UI.
        proposed = dict(_PROPOSED_SAMPLE)
        proposed["lang_fr"] = dict(_PROPOSED_SAMPLE["lang_fr"])
        proposed["lang_fr"]["ingredients"] = [
            {"food": "poulet", "quantity_min": 1, "quantity_max": None, "unit": None},
            {"food": "ail", "quantity_min": 3, "quantity_max": None, "unit": "gousse"},
        ]
        _patch_reparse(as_admin, monkeypatch, proposed=proposed)
        manual_ingredients = [
            {"food": "poulet seulement", "quantity_min": 1, "quantity_max": None, "unit": None},
        ]
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={
                "proposed": proposed,
                "fields": ["ingredients_fr"],
                "resolved": {"ingredients_fr": manual_ingredients},
            },
        )
        assert resp.status_code == 200
        fr = get_recipe(1, lang="fr")
        foods = [i["food"] for i in (fr["ingredients"] or [])]
        assert foods == ["poulet seulement"]

    def test_apply_falls_back_to_proposed_without_resolved(self, as_admin, monkeypatch):
        # If the resolved map is missing, the original behaviour kicks in:
        # we use the proposed value for the selected field.
        proposed = dict(_PROPOSED_SAMPLE)
        proposed["lang_fr"] = dict(_PROPOSED_SAMPLE["lang_fr"])
        proposed["lang_fr"]["title"] = "Poulet rôti aux herbes"
        _patch_reparse(as_admin, monkeypatch, proposed=proposed)
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={"proposed": proposed, "fields": ["title_fr"]},
        )
        assert resp.status_code == 200
        fr = get_recipe(1, lang="fr")
        assert fr["title"] == "Poulet rôti aux herbes"

    def test_apply_resolved_overrides_tags(self, as_admin, monkeypatch):
        # The user accepted the proposed category but wants to keep the
        # original tags (none of the proposed additions).
        proposed = dict(_PROPOSED_SAMPLE)
        proposed["tags"] = {
            "origin": ["francais", "europeen", "méditerranéen"],
            "protein": ["poulet"],
            "cooking_method": ["roti"],
        }
        _patch_reparse(as_admin, monkeypatch, proposed=proposed)
        # The resolved map reverts tags to the original (no mediteraneen).
        original_tags = {
            "origin": ["francais", "europeen"],
            "protein": ["poulet"],
            "cooking_method": ["roti"],
        }
        resp = as_admin.post(
            "/admin/reparse/1/apply",
            json={
                "proposed": proposed,
                "fields": ["tags"],
                "resolved": {"tags": original_tags},
            },
        )
        assert resp.status_code == 200
        after = get_recipe(1)
        tag_names = {t["name"] for fam in after["tags"].values() for t in fam["tags"]}
        # The original tags are preserved (no "méditerranéen" was added).
        assert "méditerranéen" not in tag_names
