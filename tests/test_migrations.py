"""Tests for yoyo migrations — baseline schema, seed data and idempotence."""

import sqlite3
from pathlib import Path

from recipes.shared.migrate import MIGRATIONS_DIR, apply_migrations, database_url


def test_database_url_points_at_file(tmp_path: Path):
    url = database_url(tmp_path / "test.db")
    assert url.startswith("sqlite:///")
    assert url.endswith("test.db")


def test_migration_files_are_pure_ascii():
    """Yoyo lit les migrations avec l'encodage locale (cp1252 sur Windows) :
    tout octet non-ASCII y planterait ou corromprait les données."""
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        raw = path.read_bytes()
        assert all(b < 128 for b in raw), path.name


def test_apply_migrations_fails_loudly_without_sources(tmp_path: Path):
    """Dossier introuvable : erreur explicite, jamais de base vide silencieuse."""
    import pytest

    with pytest.raises(FileNotFoundError):
        apply_migrations(tmp_path / "x.db", tmp_path / "no-such-dir")


def test_apply_migrations_creates_baseline_schema(tmp_path: Path):
    db_file = tmp_path / "fresh.db"
    apply_migrations(db_file)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for expected in (
            "recipes",
            "recipe_translations",
            "tag_families",
            "tags",
            "recipe_tags",
            "categories",
            "processed_files",
            "recipe_images",
            "users",
            "favorites",
            "blacklist",
            "failed_files",
            "dropbox_connections",
            "app_settings",
            "push_subscriptions",
            "user_preferences",
            "shopping_departments",
            "shopping_lists",
            "shopping_list_items",
            "shopping_templates",
            "shopping_template_items",
            "collections",
            "collection_recipes",
            "recipes_fts",
        ):
            assert expected in tables, expected

        # Colonnes historiquement ajoutées par migrations : présentes d'emblée.
        recipe_cols = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}
        assert {"slug", "source_missing", "force_visible"} <= recipe_cols
        assert "dropbox_hash" in {
            row["name"] for row in conn.execute("PRAGMA table_info(processed_files)")
        }
        assert "cover_recipe_id" in {
            row["name"] for row in conn.execute("PRAGMA table_info(collections)")
        }
        assert "is_hidden" in {
            row["name"] for row in conn.execute("PRAGMA table_info(recipe_images)")
        }

        triggers = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {"rt_ai", "rt_ad", "rt_au"} <= triggers
        indexes = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"idx_recipes_slug", "idx_collections_site", "idx_collection_recipes_recipe"} <= {
            name for name in indexes if not name.startswith("sqlite_")
        }
    finally:
        conn.close()


def test_apply_migrations_is_idempotent(tmp_path: Path):
    """Ré-appliquer ne change rien et ne perd pas les données."""
    db_file = tmp_path / "idempotent.db"
    apply_migrations(db_file)

    conn = sqlite3.connect(str(db_file))
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", ("k", "v"))
    conn.commit()
    before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    conn.close()

    apply_migrations(db_file)

    conn = sqlite3.connect(str(db_file))
    try:
        assert conn.execute("SELECT value FROM app_settings WHERE key = 'k'").fetchone()[0] == "v"
        after = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        # Seule la table de suivi yoyo peut avoir changé (ligne de log).
        assert {row[0] for row in before if row[0]} <= {row[0] for row in after if row[0]}
    finally:
        conn.close()


def test_apply_migrations_seeds_reference_data(tmp_path: Path):
    """0002 insère la taxonomie V1, hiérarchie des tags incluse."""
    db_file = tmp_path / "seeded.db"
    apply_migrations(db_file)

    conn = sqlite3.connect(str(db_file))
    try:
        families = {
            row[0]: row[1] for row in conn.execute("SELECT name, id FROM tag_families").fetchall()
        }
        assert set(families) == {"origin", "diet", "protein", "cooking_method"}
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 55
        # Hiérarchie : quebecois -> canadien -> americain.
        parent = conn.execute(
            "SELECT p.name FROM tags t JOIN tags p ON t.parent_id = p.id"
            " WHERE t.name = 'quebecois'",
        ).fetchone()[0]
        assert parent == "canadien"
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 9
        assert conn.execute("SELECT COUNT(*) FROM shopping_departments").fetchone()[0] == 11
    finally:
        conn.close()


def test_rollback_seed_keeps_user_rows(tmp_path: Path):
    """Le rollback de 0002 ne supprime que les lignes seed."""
    from yoyo import get_backend, read_migrations

    db_file = tmp_path / "rollback.db"
    apply_migrations(db_file)

    conn = sqlite3.connect(str(db_file))
    origin_id = conn.execute("SELECT id FROM tag_families WHERE name = 'origin'").fetchone()[0]
    conn.execute(
        "INSERT INTO tags (family_id, name, display_name_fr, display_name_en)"
        " VALUES (?, 'custom', 'Custom', 'Custom')",
        (origin_id,),
    )
    conn.execute(
        "INSERT INTO categories (name, display_name_fr, display_name_en, sort_order)"
        " VALUES ('custom-cat', 'Custom', 'Custom', 99)",
    )
    conn.commit()
    conn.close()

    backend = get_backend(database_url(db_file))
    migrations = read_migrations(str(MIGRATIONS_DIR))
    seed = next(m for m in migrations if m.id == "0002_seed_reference_data")
    with backend.lock():
        backend.rollback_migrations([seed])

    conn = sqlite3.connect(str(db_file))
    try:
        assert conn.execute("SELECT COUNT(*) FROM tag_families").fetchone()[0] == 0
        assert [row[0] for row in conn.execute("SELECT name FROM tags")] == ["custom"]
        assert [row[0] for row in conn.execute("SELECT name FROM categories")] == ["custom-cat"]
        assert conn.execute("SELECT COUNT(*) FROM shopping_departments").fetchone()[0] == 0
    finally:
        conn.close()
