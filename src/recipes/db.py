"""
db.py — SQLite setup, schema, seed data, and query helpers.

Internationalization model:
  - The recipe table holds non-translated structural fields (servings,
    source_url, file metadata, ...).
  - All user-facing text (title, description, instructions, ingredients) lives
    in `recipe_translations` keyed by language.
  - Tag families, tags, and categories keep a stable technical `name` (used as
    key in code/prompts) and provide `display_name_fr` / `display_name_en`
    for rendering.

Tag system:
  - tag_families: origin, diet, protein, cooking_method
  - tags: belong to a family, optionally hierarchical (parent_id)
  - recipe_tags: many-to-many link
  - categories: single per recipe (entree, plat-principal, salade, etc.)
"""

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from recipes.i18n import DEFAULT_LANGUAGE

DB_PATH = Path(os.environ.get("DB_PATH", "/data/recipes.db"))

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_TAG_FAMILIES: list[tuple[str, str, str, int]] = [
    ("origin", "Origine", "Origin", 1),
    ("diet", "Régime alimentaire", "Diet", 2),
    ("protein", "Protéine principale", "Main protein", 3),
    ("cooking_method", "Méthode de cuisson", "Cooking method", 4),
]

# (name technique, display_name_fr, display_name_en, parent_name)
SEED_TAGS: dict[str, list[tuple[str, str, str, str | None]]] = {
    "origin": [
        ("asiatique", "Asiatique", "Asian", None),
        ("japonais", "Japonais", "Japanese", "asiatique"),
        ("chinois", "Chinois", "Chinese", "asiatique"),
        ("coreen", "Coréen", "Korean", "asiatique"),
        ("thailandais", "Thaïlandais", "Thai", "asiatique"),
        ("vietnamien", "Vietnamien", "Vietnamese", "asiatique"),
        ("indien", "Indien", "Indian", "asiatique"),
        ("europeen", "Européen", "European", None),
        ("francais", "Français", "French", "europeen"),
        ("italien", "Italien", "Italian", "europeen"),
        ("grec", "Grec", "Greek", "europeen"),
        ("espagnol", "Espagnol", "Spanish", "europeen"),
        ("allemand", "Allemand", "German", "europeen"),
        ("americain", "Américain", "American", None),
        ("canadien", "Canadien", "Canadian", "americain"),
        ("quebecois", "Québécois", "Québécois", "canadien"),
        ("mexicain", "Mexicain", "Mexican", "americain"),
        ("moyen-oriental", "Moyen-Oriental", "Middle Eastern", None),
        ("libanais", "Libanais", "Lebanese", "moyen-oriental"),
        ("israelien", "Israélien", "Israeli", "moyen-oriental"),
        ("africain", "Africain", "African", None),
        ("marocain", "Marocain", "Moroccan", "africain"),
        ("ethiopien", "Éthiopien", "Ethiopian", "africain"),
    ],
    "diet": [
        ("vegetalien", "Végétalien", "Vegan", None),
        ("vegetarien", "Végétarien", "Vegetarian", None),
        ("pescetarien", "Pescétarien", "Pescatarian", None),
        ("sans-gluten", "Sans gluten", "Gluten-free", None),
        ("sans-produits-laitiers", "Sans produits laitiers", "Dairy-free", None),
        ("cetogene", "Cétogène", "Keto", None),
        ("faible-en-glucides", "Faible en glucides", "Low-carb", None),
        ("paleo", "Paléo", "Paleo", None),
    ],
    "protein": [
        ("poulet", "Poulet", "Chicken", None),
        ("boeuf", "Bœuf", "Beef", None),
        ("porc", "Porc", "Pork", None),
        ("agneau", "Agneau", "Lamb", None),
        ("veau", "Veau", "Veal", None),
        ("poisson", "Poisson", "Fish", None),
        ("fruits-de-mer", "Fruits de mer", "Seafood", None),
        ("tofu", "Tofu", "Tofu", None),
        ("tempeh", "Tempeh", "Tempeh", None),
        ("lentilles", "Lentilles", "Lentils", None),
        ("oeufs", "Œufs", "Eggs", None),
        ("canard", "Canard", "Duck", None),
    ],
    "cooking_method": [
        ("braise", "Braisé", "Braised", None),
        ("roti", "Rôti", "Roasted", None),
        ("saute", "Sauté", "Sautéed", None),
        ("wok", "Wok", "Stir-fried", None),
        ("fume", "Fumé", "Smoked", None),
        ("barbecue", "Barbecue", "Barbecue", None),
        ("grille", "Grillé", "Grilled", None),
        ("frit", "Frit", "Fried", None),
        ("mijote", "Mijoté", "Slow-cooked", None),
        ("sans-cuisson", "Sans cuisson", "No-cook", None),
        ("poche", "Poché", "Poached", None),
        ("vapeur", "Vapeur", "Steamed", None),
    ],
}

SEED_CATEGORIES: list[tuple[str, str, str, int]] = [
    ("entree", "Entrée", "Starter", 1),
    ("plat-principal", "Plat principal", "Main course", 2),
    ("salade", "Salade", "Salad", 3),
    ("soupe", "Soupe", "Soup", 4),
    ("sauce", "Sauce", "Sauce", 5),
    ("dessert", "Dessert", "Dessert", 6),
    ("accompagnement", "Accompagnement", "Side dish", 7),
    ("collation", "Collation", "Snack", 8),
    ("aperitif", "Apéritif", "Appetizer", 9),
]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Init / migrate
# ---------------------------------------------------------------------------


def init_db() -> None:
    with get_conn() as conn:
        _create_tables(conn)
        _migrate(conn)
        _create_fts(conn)
        _seed(conn)


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recipes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            servings     REAL,
            source_url   TEXT,
            dropbox_url  TEXT,
            source_file  TEXT NOT NULL UNIQUE,
            file_hash    TEXT NOT NULL,
            manually_edited INTEGER NOT NULL DEFAULT 0,
            connection_id INTEGER REFERENCES dropbox_connections(id),
            category_id  INTEGER REFERENCES categories(id),
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipe_translations (
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            lang         TEXT NOT NULL,
            title        TEXT NOT NULL,
            description  TEXT,
            instructions TEXT,
            ingredients  TEXT,
            PRIMARY KEY (recipe_id, lang)
        );

        CREATE TABLE IF NOT EXISTS tag_families (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tags (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id       INTEGER NOT NULL REFERENCES tag_families(id),
            name            TEXT NOT NULL,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            parent_id       INTEGER REFERENCES tags(id),
            UNIQUE(family_id, name)
        );

        CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS processed_files (
            path         TEXT PRIMARY KEY,
            file_hash    TEXT NOT NULL,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipe_images (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            filename     TEXT NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subject      TEXT NOT NULL UNIQUE,
            email        TEXT,
            name         TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS favorites (
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, recipe_id)
        );

        CREATE TABLE IF NOT EXISTS blacklist (
            path         TEXT PRIMARY KEY,
            blacklisted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS failed_files (
            path         TEXT PRIMARY KEY,
            error        TEXT NOT NULL,
            failed_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dropbox_connections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            refresh_token TEXT NOT NULL,
            folder        TEXT NOT NULL DEFAULT '',
            file_filter   TEXT NOT NULL DEFAULT '',
            active        INTEGER NOT NULL DEFAULT 1,
            visible       INTEGER NOT NULL DEFAULT 1,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older single-language schema to the bilingual layout.

    When the legacy `recipes` table is present, its title/description/
    ingredients/instructions columns are copied into `recipe_translations`
    using the legacy French content for both the `fr` and `en` rows (the LLM
    will refresh `en` on the next ingestion). This migration is a no-op for
    fresh databases.
    """
    legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes'"
    ).fetchone()
    if legacy is None:
        return

    cols = {row[1] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}

    if "title" in cols:
        # Backfill translations from legacy columns, then drop them.
        conn.execute(
            """
            INSERT OR IGNORE INTO recipe_translations
                (recipe_id, lang, title, description, instructions, ingredients)
            SELECT id, 'fr', title, description, instructions, ingredients
            FROM recipes
            WHERE title IS NOT NULL
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO recipe_translations
                (recipe_id, lang, title, description, instructions, ingredients)
            SELECT id, 'en', title, description, instructions, ingredients
            FROM recipes
            WHERE title IS NOT NULL
            """
        )

        # recipes_fts was created against the legacy columns; rebuild on the
        # translations table instead. We drop it now; _create_fts() will
        # recreate it against the new schema.
        conn.execute("DROP TRIGGER IF EXISTS recipes_ai")
        conn.execute("DROP TRIGGER IF EXISTS recipes_au")
        conn.execute("DROP TRIGGER IF EXISTS recipes_ad")
        conn.execute("DROP TABLE IF EXISTS recipes_fts")

        # Use a copy-then-swap to drop the legacy text columns atomically.
        conn.execute("ALTER TABLE recipes RENAME TO recipes_legacy")
        conn.execute(
            """
            CREATE TABLE recipes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                servings     REAL,
                source_url   TEXT,
                dropbox_url  TEXT,
                source_file  TEXT NOT NULL UNIQUE,
                file_hash    TEXT NOT NULL,
                manually_edited INTEGER NOT NULL DEFAULT 0,
                connection_id INTEGER REFERENCES dropbox_connections(id),
                category_id  INTEGER REFERENCES categories(id),
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO recipes
                (id, servings, source_url, dropbox_url, source_file, file_hash,
                 manually_edited, connection_id, category_id, created_at, updated_at)
            SELECT id, servings, source_url, dropbox_url, source_file, file_hash,
                   COALESCE(manually_edited, 0), connection_id, category_id,
                   created_at, updated_at
            FROM recipes_legacy
            """
        )
        conn.execute("DROP TABLE recipes_legacy")
        return

    # New-schema recipes table: just make sure optional columns exist.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    if "category_id" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN category_id INTEGER REFERENCES categories(id)")
    if "file_modified_at" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN file_modified_at DATETIME")
    if "manually_edited" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN manually_edited INTEGER NOT NULL DEFAULT 0")
    if "connection_id" not in cols:
        conn.execute(
            "ALTER TABLE recipes ADD COLUMN connection_id INTEGER REFERENCES dropbox_connections(id)"
        )

    family_cols = {row[1] for row in conn.execute("PRAGMA table_info(tag_families)").fetchall()}
    if "display_name" in family_cols and "display_name_fr" not in family_cols:
        conn.execute("ALTER TABLE tag_families RENAME TO tag_families_legacy")
        conn.execute(
            """
            CREATE TABLE tag_families (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                display_name_fr TEXT NOT NULL,
                display_name_en TEXT NOT NULL,
                sort_order      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tag_families (id, name, display_name_fr, display_name_en, sort_order)
            SELECT id, name, display_name, display_name, sort_order
            FROM tag_families_legacy
            """
        )
        conn.execute("DROP TABLE tag_families_legacy")

    tag_cols = {row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()}
    if "display_name" in tag_cols and "display_name_fr" not in tag_cols:
        conn.execute("ALTER TABLE tags RENAME TO tags_legacy")
        conn.execute(
            """
            CREATE TABLE tags (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                family_id       INTEGER NOT NULL REFERENCES tag_families(id),
                name            TEXT NOT NULL,
                display_name_fr TEXT NOT NULL,
                display_name_en TEXT NOT NULL,
                parent_id       INTEGER REFERENCES tags(id),
                UNIQUE(family_id, name)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tags (id, family_id, name, display_name_fr, display_name_en, parent_id)
            SELECT id, family_id, name, display_name, display_name, parent_id
            FROM tags_legacy
            """
        )
        conn.execute("DROP TABLE tags_legacy")

    cat_cols = {row[1] for row in conn.execute("PRAGMA table_info(categories)").fetchall()}
    if "display_name" in cat_cols and "display_name_fr" not in cat_cols:
        conn.execute("ALTER TABLE categories RENAME TO categories_legacy")
        conn.execute(
            """
            CREATE TABLE categories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                display_name_fr TEXT NOT NULL,
                display_name_en TEXT NOT NULL,
                sort_order      INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO categories (id, name, display_name_fr, display_name_en, sort_order)
            SELECT id, name, display_name, display_name, sort_order
            FROM categories_legacy
            """
        )
        conn.execute("DROP TABLE categories_legacy")


def _create_fts(conn: sqlite3.Connection) -> None:
    fts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes_fts'"
    ).fetchone()

    if fts_exists:
        del fts_exists
        return
    del fts_exists

    # The FTS table stores one row per (recipe_id, lang) pair; the rowid is
    # built from the recipe_id so the recipes_fts MATCH query can be
    # translated back to recipe ids via `rowid / 2`.
    #   fr rowid = recipe_id * 2
    #   en rowid = recipe_id * 2 + 1
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
            title,
            description,
            ingredients
        );

        CREATE TRIGGER rt_ai AFTER INSERT ON recipe_translations BEGIN
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (CASE new.lang
                        WHEN 'en' THEN new.recipe_id * 2 + 1
                        ELSE new.recipe_id * 2
                    END,
                    new.title, new.description, new.ingredients);
        END;

        CREATE TRIGGER rt_ad AFTER DELETE ON recipe_translations BEGIN
            DELETE FROM recipes_fts WHERE rowid = CASE old.lang
                        WHEN 'en' THEN old.recipe_id * 2 + 1
                        ELSE old.recipe_id * 2
                    END;
        END;

        CREATE TRIGGER rt_au AFTER UPDATE ON recipe_translations BEGIN
            DELETE FROM recipes_fts WHERE rowid = CASE old.lang
                        WHEN 'en' THEN old.recipe_id * 2 + 1
                        ELSE old.recipe_id * 2
                    END;
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (CASE new.lang
                        WHEN 'en' THEN new.recipe_id * 2 + 1
                        ELSE new.recipe_id * 2
                    END,
                    new.title, new.description, new.ingredients);
        END;
    """)

    rows = conn.execute(
        "SELECT recipe_id, lang, title, description, ingredients FROM recipe_translations"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO recipes_fts(rowid, title, description, ingredients) VALUES (?, ?, ?, ?)",
            (
                _fts_rowid(int(row["recipe_id"]), str(row["lang"])),
                row["title"],
                row["description"],
                row["ingredients"],
            ),
        )
    del rows


_LANG_ORDINAL = {"fr": 0, "en": 1}


def _fts_rowid(recipe_id: int, lang: str) -> int:
    """Composite FTS rowid: 2 * recipe_id + lang offset.

    Recipe ids start at 1 so even ids map to the `fr` row and odd ids to `en`.
    """
    offset = _LANG_ORDINAL.get(lang, 0)
    return recipe_id * 2 + offset


def _seed(conn: sqlite3.Connection) -> None:
    for name, fr, en, sort_order in SEED_TAG_FAMILIES:
        conn.execute(
            """
            INSERT OR IGNORE INTO tag_families
                (name, display_name_fr, display_name_en, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (name, fr, en, sort_order),
        )

    for family_name, tags in SEED_TAGS.items():
        family = conn.execute(
            "SELECT id FROM tag_families WHERE name = ?", (family_name,)
        ).fetchone()
        if not family:
            continue
        family_id = int(family["id"])
        del family

        for name, fr, en, _parent in tags:
            conn.execute(
                """
                INSERT OR IGNORE INTO tags
                    (family_id, name, display_name_fr, display_name_en)
                VALUES (?, ?, ?, ?)
                """,
                (family_id, name, fr, en),
            )

        for name, _fr, _en, parent_name in tags:
            if parent_name:
                parent = conn.execute(
                    "SELECT id FROM tags WHERE family_id = ? AND name = ?",
                    (family_id, parent_name),
                ).fetchone()
                if parent:
                    parent_id = int(parent["id"])
                    del parent
                    conn.execute(
                        "UPDATE tags SET parent_id = ? WHERE family_id = ? AND name = ?",
                        (parent_id, family_id, name),
                    )

    for name, fr, en, sort_order in SEED_CATEGORIES:
        conn.execute(
            """
            INSERT OR IGNORE INTO categories
                (name, display_name_fr, display_name_en, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (name, fr, en, sort_order),
        )


# ---------------------------------------------------------------------------
# Localized display helpers
# ---------------------------------------------------------------------------


def _localize_tag(row: sqlite3.Row, lang: str) -> dict[str, object]:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "id": row["id"],
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_family(row: sqlite3.Row, lang: str) -> dict[str, object]:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_category(row: sqlite3.Row, lang: str) -> dict[str, object]:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "id": row["id"],
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_recipe_translation(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return {"title": "", "description": "", "instructions": "", "ingredients": []}
    ingredients_raw = row["ingredients"] or "[]"
    return {
        "title": str(row["title"] or ""),
        "description": str(row["description"] or ""),
        "instructions": str(row["instructions"] or ""),
        "ingredients": json.loads(str(ingredients_raw)),
    }


# ---------------------------------------------------------------------------
# Recipe write helpers
# ---------------------------------------------------------------------------


def upsert_recipe(data: dict[str, object]) -> int:
    """Insert or update a recipe with bilingual translations.

    `data` must contain both `lang_fr` and `lang_en` payloads (or, for tests
    / manual editing, a single-language `lang` payload). A unified
    `tags` dict of `{family: [names]}` and a single `category` key (technical
    name) are also expected.
    """
    payload_fr, payload_en = _extract_translation_payload(data)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM recipes WHERE source_file = ?", (data["source_file"],)
        ).fetchone()

        category_id = _resolve_category(
            conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None
        raw_connection = data.get("connection_id")
        connection_id = int(str(raw_connection)) if raw_connection is not None else None

        if existing:
            recipe_id = int(existing["id"])
            conn.execute(
                """
                UPDATE recipes SET
                    servings=?, category_id=?, source_url=?, dropbox_url=?, file_hash=?,
                    file_modified_at=?, connection_id=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_file=?
                """,
                (
                    servings,
                    category_id,
                    data.get("source_url"),
                    data.get("dropbox_url"),
                    data["file_hash"],
                    data.get("file_modified_at"),
                    connection_id,
                    data["source_file"],
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO recipes
                    (servings, category_id, source_url, dropbox_url, source_file,
                     file_hash, file_modified_at, connection_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    servings,
                    category_id,
                    data.get("source_url"),
                    data.get("dropbox_url"),
                    data["source_file"],
                    data["file_hash"],
                    data.get("file_modified_at"),
                    connection_id,
                ),
            )
            assert cur.lastrowid is not None
            recipe_id = int(cur.lastrowid)

        _upsert_translation(conn, recipe_id, "fr", payload_fr)
        _upsert_translation(conn, recipe_id, "en", payload_en)
        return recipe_id


def _extract_translation_payload(
    data: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Return (payload_fr, payload_en) from the upsert payload.

    Accepts either pre-split `lang_fr`/`lang_en` dicts, a single-language
    `lang` dict (replicated for both sides — used by tests and manual
    edits), or a top-level shape with `title` / `description` /
    `instructions` / `ingredients` keys at the root of `data` (legacy
    single-language shape used by historical tests).
    """
    if "lang_fr" in data or "lang_en" in data:
        fr = data.get("lang_fr") or {}
        en = data.get("lang_en") or {}
        if not isinstance(fr, dict) or not isinstance(en, dict):
            raise ValueError("lang_fr/lang_en must be dicts")
        return fr, en
    legacy = data.get("lang")
    if isinstance(legacy, dict):
        return legacy, legacy

    has_legacy_keys = any(
        k in data for k in ("title", "description", "instructions", "ingredients")
    )
    if has_legacy_keys:
        payload = {
            "title": data.get("title") or "",
            "description": data.get("description") or "",
            "instructions": data.get("instructions") or "",
            "ingredients": data.get("ingredients") or [],
        }
        return payload, payload
    raise ValueError(
        "upsert_recipe requires lang_fr/lang_en translation dicts (or a 'lang' fallback)"
    )


def _upsert_translation(
    conn: sqlite3.Connection, recipe_id: int, lang: str, payload: dict[str, object]
) -> None:
    title = str(payload.get("title") or "").strip()
    if not title:
        return
    description = payload.get("description") or ""
    instructions = payload.get("instructions") or ""
    ingredients = payload.get("ingredients") or []
    ingredients_json = json.dumps(ingredients, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO recipe_translations
            (recipe_id, lang, title, description, instructions, ingredients)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(recipe_id, lang) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            instructions=excluded.instructions,
            ingredients=excluded.ingredients
        """,
        (recipe_id, lang, title, description, instructions, ingredients_json),
    )


def sync_recipe_tags(recipe_id: int, tags_by_family: dict[str, list[str]]) -> None:
    with get_conn() as conn:
        tag_ids: set[int] = set()

        for family_name, tag_names in tags_by_family.items():
            family = conn.execute(
                "SELECT id FROM tag_families WHERE name = ?", (family_name,)
            ).fetchone()
            if not family:
                continue
            family_id = family["id"]

            for tag_name in tag_names:
                tag_id = _resolve_tag(conn, family_id, tag_name)
                if tag_id is not None:
                    tag_ids.add(tag_id)

        all_ids: set[int] = set(tag_ids)
        for tid in tag_ids:
            _add_ancestors(conn, tid, all_ids)

        conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        for tid in all_ids:
            conn.execute(
                "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                (recipe_id, tid),
            )


def _resolve_tag(conn: sqlite3.Connection, family_id: int, name: str) -> int | None:
    display_name = name.replace("-", " ").title()

    row = conn.execute(
        "SELECT id FROM tags WHERE family_id = ? AND name = ?", (family_id, name)
    ).fetchone()
    if row:
        return int(row["id"])

    row = conn.execute(
        "SELECT id FROM tags WHERE family_id = ? AND display_name_fr = ?",
        (family_id, display_name),
    ).fetchone()
    if row:
        return int(row["id"])

    conn.execute(
        """
        INSERT OR IGNORE INTO tags
            (family_id, name, display_name_fr, display_name_en)
        VALUES (?, ?, ?, ?)
        """,
        (family_id, name, display_name, display_name),
    )
    row = conn.execute(
        "SELECT id FROM tags WHERE family_id = ? AND name = ?", (family_id, name)
    ).fetchone()
    return int(row["id"]) if row else None


def _resolve_category(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])

    display_name = name.replace("-", " ").title()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM categories").fetchone()[0]
    cur = conn.execute(
        """
        INSERT INTO categories
            (name, display_name_fr, display_name_en, sort_order)
        VALUES (?, ?, ?, ?)
        """,
        (name, display_name, display_name, max_order + 1),
    )
    return int(cur.lastrowid) if cur.lastrowid else None


def _add_ancestors(conn: sqlite3.Connection, tag_id: int, collected: set[int]) -> None:
    row = conn.execute("SELECT parent_id FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if row and row["parent_id"]:
        parent_id = int(row["parent_id"])
        if parent_id not in collected:
            collected.add(parent_id)
            _add_ancestors(conn, parent_id, collected)


def is_manually_edited(source_file: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM recipes WHERE source_file = ? AND manually_edited = 1",
            (source_file,),
        ).fetchone()
        return row is not None


def update_recipe_manual(recipe_id: int, data: dict[str, object]) -> bool:
    """Met à jour une recette modifiée via l'écran d'administration.

    Marque la recette comme modifiée manuellement : le poller Dropbox ignorera
    alors les futures mises à jour du fichier source et les signalera dans
    les fichiers en erreur.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False

        payload_fr, payload_en = _extract_translation_payload(data)
        category_id = _resolve_category(
            conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None

        conn.execute(
            """
            UPDATE recipes SET
                servings=?, category_id=?, source_url=?,
                manually_edited=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                servings,
                category_id,
                data.get("source_url"),
                recipe_id,
            ),
        )
        _upsert_translation(conn, recipe_id, "fr", payload_fr)
        _upsert_translation(conn, recipe_id, "en", payload_en)
        return True


def update_recipe_category(recipe_id: int, category: str | None) -> bool:
    """Édition inline de la catégorie. Marque la recette comme modifiée manuellement."""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False
        category_id = _resolve_category(conn, category or None)
        conn.execute(
            """
            UPDATE recipes SET category_id=?, manually_edited=1,
                   updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (category_id, recipe_id),
        )
        return True


def update_recipe_tags(recipe_id: int, tags_by_family: dict[str, list[str]]) -> bool:
    """Édition inline des étiquettes (remplacement complet). Marque la recette manuelle."""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE recipes SET manually_edited=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (recipe_id,),
        )
    sync_recipe_tags(recipe_id, tags_by_family)
    return True


def bulk_update_category(recipe_ids: list[int], category: str | None) -> int:
    """Change la catégorie d'un lot de recettes et les marque comme modifiées."""
    if not recipe_ids:
        return 0
    with get_conn() as conn:
        category_id = _resolve_category(conn, category or None)
        placeholders = ", ".join("?" for _ in recipe_ids)
        cur = conn.execute(
            f"""
            UPDATE recipes SET category_id=?, manually_edited=1,
                   updated_at=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            (category_id, *recipe_ids),
        )
        return cur.rowcount


def _resolve_tag_ids(
    conn: sqlite3.Connection,
    tags_by_family: dict[str, list[str]],
    create: bool,
) -> set[int]:
    """Résout des clés family/noms en ids d'étiquettes. Crée si `create`, sinon ignore."""
    tag_ids: set[int] = set()
    for family_name, names in tags_by_family.items():
        family = conn.execute(
            "SELECT id FROM tag_families WHERE name = ?", (family_name,)
        ).fetchone()
        if not family:
            continue
        family_id = int(family["id"])
        for name in names:
            if create:
                tag_id = _resolve_tag(conn, family_id, name)
            else:
                row = conn.execute(
                    "SELECT id FROM tags WHERE family_id = ? AND name = ?",
                    (family_id, name),
                ).fetchone()
                tag_id = int(row["id"]) if row else None
            if tag_id is not None:
                tag_ids.add(tag_id)
    return tag_ids


def bulk_update_tags(
    recipe_ids: list[int],
    add_by_family: dict[str, list[str]],
    remove_by_family: dict[str, list[str]],
) -> int:
    """Ajoute/retire des étiquettes sur un lot de recettes et les marque comme modifiées.

    Retourne le nombre de recettes touchées (0 si aucune étiquette valide).
    """
    if not recipe_ids:
        return 0
    with get_conn() as conn:
        add_ids = _resolve_tag_ids(conn, add_by_family, create=True)
        remove_ids = _resolve_tag_ids(conn, remove_by_family, create=False)

        placeholders = ", ".join("?" for _ in recipe_ids)
        for tag_id in add_ids:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id)
                SELECT r.id, ? FROM recipes r WHERE r.id IN ({placeholders})
                """,
                (tag_id, *recipe_ids),
            )
        if remove_ids:
            tag_placeholders = ", ".join("?" for _ in remove_ids)
            conn.execute(
                f"""
                DELETE FROM recipe_tags
                WHERE tag_id IN ({tag_placeholders}) AND recipe_id IN ({placeholders})
                """,
                (*remove_ids, *recipe_ids),
            )

        if not add_ids and not remove_ids:
            return 0

        conn.execute(
            f"""
            UPDATE recipes SET manually_edited=1, updated_at=CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            (*recipe_ids,),
        )
        return len(recipe_ids)


# ---------------------------------------------------------------------------
# Recipe read helpers
# ---------------------------------------------------------------------------


def get_recipe(recipe_id: int, lang: str = DEFAULT_LANGUAGE) -> dict[str, object] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is None:
            return None

        result: dict[str, object] = dict(row)

        translation = _load_translation(conn, recipe_id, lang)
        result.update(translation)
        result["title"] = translation["title"]  # backwards compat for templates

        cat_row = conn.execute(
            "SELECT * FROM categories WHERE id = ?",
            (result.get("category_id"),),
        ).fetchone()
        result["category"] = _localize_category(cat_row, lang) if cat_row else None

        pc_row = conn.execute(
            "SELECT id, name FROM dropbox_connections WHERE id = ?",
            (result.get("connection_id"),),
        ).fetchone()
        result["provenance"] = (
            {"id": pc_row["id"], "name": pc_row["name"]}
            if pc_row
            else {"id": None, "name": DEFAULT_ACCOUNT_NAME}
        )

        tag_rows = conn.execute(
            """
            SELECT tf.name AS family,
                   tf.display_name_fr AS family_display_name_fr,
                   tf.display_name_en AS family_display_name_en,
                   t.id, t.name,
                   t.display_name_fr, t.display_name_en
            FROM recipe_tags rt
            JOIN tags t ON rt.tag_id = t.id
            JOIN tag_families tf ON t.family_id = tf.id
            WHERE rt.recipe_id = ?
            ORDER BY tf.sort_order, t.display_name_fr
            """,
            (recipe_id,),
        ).fetchall()

        family_col = "family_display_name_en" if lang == "en" else "family_display_name_fr"
        tags_grouped: dict[str, dict[str, Any]] = {}
        for tr in tag_rows:
            fam = tr["family"]
            if fam not in tags_grouped:
                tags_grouped[fam] = {
                    "family_display_name": tr[family_col],
                    "tags": [],
                }
            tags_grouped[fam]["tags"].append(_localize_tag(tr, lang))
        result["tags"] = tags_grouped

        result["images"] = get_recipe_images(recipe_id)

        return result


def _load_translation(conn: sqlite3.Connection, recipe_id: int, lang: str) -> dict[str, object]:
    """Return the translation for `lang`, falling back to the other language."""
    row = conn.execute(
        "SELECT title, description, instructions, ingredients "
        "FROM recipe_translations WHERE recipe_id = ? AND lang = ?",
        (recipe_id, lang),
    ).fetchone()
    if row is not None:
        return _localize_recipe_translation(row)
    fallback = conn.execute(
        "SELECT title, description, instructions, ingredients "
        "FROM recipe_translations WHERE recipe_id = ? AND lang = ?",
        (recipe_id, "fr" if lang == "en" else "en"),
    ).fetchone()
    if fallback is not None:
        return _localize_recipe_translation(fallback)
    return _localize_recipe_translation(None)


# French-only stop-words for FTS (matches the previous behavior). FTS is shared
# across languages; users may still type French words against English recipes
# and benefit from partial matches.
_FTS_STOPWORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "de",
        "des",
        "du",
        "un",
        "une",
        "et",
        "ou",
        "au",
        "aux",
        "en",
        "dans",
        "sur",
        "avec",
        "sans",
        "pour",
        "par",
        "est",
        "ce",
        "sa",
        "son",
        "ma",
        "ta",
        "l",
        "d",
        "n",
        "s",
        "c",
        "j",
        "m",
        "t",
        "qu",
        "qui",
        "que",
        "quoi",
        "dont",
        "ne",
        "pas",
        "plus",
        "moins",
        "tres",
        "trop",
        "aussi",
        "comme",
        "si",
        "oui",
        "non",
    ]
)


def _fts_query(query: str) -> str:
    """Convertit une saisie utilisateur en requete FTS5 souple.

    Chaque mot significatif (>= 2 caracteres, hors stop-words francais) devient
    un terme de prefixe ("tarte aux po" -> '"tarte"* "po"*'), combine en AND
    implicite : correspondances partielles, pluriels et accents de syntaxe
    (apostrophes, tirets) sont geres.
    """
    words = [
        w
        for w in re.findall(r"\w+", query, re.UNICODE)
        if len(w) >= 2 and w.lower() not in _FTS_STOPWORDS
    ]
    return " ".join(f'"{word}"*' for word in words)


def search_recipes(
    query: str = "",
    tag_ids: list[int] | None = None,
    category_id: int | None = None,
    connection_id: int | None = None,
    lang: str = DEFAULT_LANGUAGE,
) -> list[dict[str, object]]:
    """Recherche de recettes.

    `connection_id` filtre par compte Dropbox d'origine ; la valeur sentinelle
    DEFAULT_ACCOUNT_ID sélectionne les recettes du compte par défaut (.env).
    Les recettes issues de connexions masquées sont toujours exclues.
    Les champs textuels sont retournés dans la langue `lang`.
    """
    if tag_ids is None:
        tag_ids = []

    with get_conn() as conn:
        conditions: list[str] = [
            "NOT EXISTS "
            "(SELECT 1 FROM dropbox_connections dc WHERE dc.id = r.connection_id AND dc.visible = 0)"
        ]
        if not is_default_account_visible():
            conditions.append("r.connection_id IS NOT NULL")
        params: list[object] = []

        if query.strip():
            fts_q = _fts_query(query)
            if fts_q:
                conditions.append(
                    "r.id IN (SELECT (rowid / 2) FROM recipes_fts WHERE recipes_fts MATCH ?)"
                )
                params.append(fts_q)
            else:
                conditions.append("0")

        if tag_ids:
            tag_to_family: dict[int, int] = {}
            for tid in tag_ids:
                row = conn.execute("SELECT family_id FROM tags WHERE id = ?", (tid,)).fetchone()
                if row:
                    tag_to_family[tid] = row["family_id"]

            families: dict[int, list[int]] = {}
            for tid, fid in tag_to_family.items():
                families.setdefault(fid, []).append(tid)

            for _fid, tids in families.items():
                placeholders = ", ".join("?" for _ in tids)
                conditions.append(
                    f"r.id IN (SELECT recipe_id FROM recipe_tags WHERE tag_id IN ({placeholders}))"
                )
                params.extend(tids)

        if category_id:
            conditions.append("r.category_id = ?")
            params.append(category_id)

        if connection_id == DEFAULT_ACCOUNT_ID:
            conditions.append("r.connection_id IS NULL")
        elif connection_id is not None:
            conditions.append("r.connection_id = ?")
            params.append(connection_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = conn.execute(
            f"""
            SELECT r.*, c.name AS category_name,
                   c.display_name_fr AS category_display_name_fr,
                   c.display_name_en AS category_display_name_en,
                   pc.id AS provenance_id, pc.name AS provenance_name
            FROM recipes r
            LEFT JOIN categories c ON r.category_id = c.id
            LEFT JOIN dropbox_connections pc ON pc.id = r.connection_id
            {where}
            ORDER BY r.updated_at DESC
            """,
            params,
        ).fetchall()

        results = []
        cat_col = "category_display_name_en" if lang == "en" else "category_display_name_fr"
        for row in rows:
            translation = _load_translation(conn, int(row["id"]), lang)
            d: dict[str, object] = dict(row)
            d.update(translation)
            d["title"] = translation["title"]
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d[cat_col],
                }
            else:
                d["category"] = None

            if d.get("provenance_id") is not None:
                d["provenance"] = {"id": d["provenance_id"], "name": d["provenance_name"]}
            else:
                d["provenance"] = {"id": None, "name": DEFAULT_ACCOUNT_NAME}

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name,
                       t.display_name_fr, t.display_name_en,
                       tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name_fr
                """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [_localize_tag(tr, lang) | {"family": tr["family"]} for tr in tag_rows]

            d["images"] = get_recipe_images(int(row["id"]))

            results.append(d)

        return results


def get_all_tags_grouped(lang: str = DEFAULT_LANGUAGE) -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tf.name AS family,
                   tf.display_name_fr AS family_display_name_fr,
                   tf.display_name_en AS family_display_name_en,
                   t.id, t.name,
                   t.display_name_fr, t.display_name_en
            FROM tags t
            JOIN tag_families tf ON t.family_id = tf.id
            JOIN recipe_tags rt ON t.id = rt.tag_id
            GROUP BY t.id
            ORDER BY tf.sort_order, t.display_name_fr
            """
        ).fetchall()

    family_col = "family_display_name_en" if lang == "en" else "family_display_name_fr"
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        fam = row["family"]
        if fam not in result:
            result[fam] = {
                "display_name": row[family_col],
                "tags": [],
            }
        result[fam]["tags"].append(_localize_tag(row, lang))
    return result


def get_all_categories(
    only_used: bool = True, lang: str = DEFAULT_LANGUAGE
) -> list[dict[str, object]]:
    with get_conn() as conn:
        if only_used:
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.name,
                       c.display_name_fr, c.display_name_en
                FROM categories c
                JOIN recipes r ON c.id = r.category_id
                ORDER BY c.sort_order
                """
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, display_name_fr, display_name_en "
                "FROM categories ORDER BY sort_order"
            ).fetchall()
    return [_localize_category(r, lang) for r in rows]


def get_existing_tags_for_prompt(
    lang: str = DEFAULT_LANGUAGE,
) -> dict[str, list[dict[str, object]]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tf.name AS family,
                   t.name, t.display_name_fr, t.display_name_en,
                   t.parent_id,
                   p.name AS parent_name
            FROM tags t
            JOIN tag_families tf ON t.family_id = tf.id
            LEFT JOIN tags p ON t.parent_id = p.id
            ORDER BY tf.sort_order, t.display_name_fr
            """
        ).fetchall()

    result: dict[str, list[dict[str, object]]] = {}
    tag_col = "display_name_en" if lang == "en" else "display_name_fr"
    for row in rows:
        fam = row["family"]
        if fam not in result:
            result[fam] = []
        result[fam].append(
            {
                "name": row["name"],
                "display_name": row[tag_col],
                "parent_name": row["parent_name"],
            }
        )
    return result


def get_tag_families(lang: str = DEFAULT_LANGUAGE) -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, display_name_fr, display_name_en FROM tag_families ORDER BY sort_order"
        ).fetchall()
    return [_localize_family(r, lang) for r in rows]


def mark_processed(path: str, file_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO processed_files (path, file_hash) VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET file_hash=excluded.file_hash, processed_at=CURRENT_TIMESTAMP
            """,
            (path, file_hash),
        )


def get_processed_hash(path: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT file_hash FROM processed_files WHERE path = ?", (path,)
        ).fetchone()
        return row["file_hash"] if row else None


def save_recipe_images(recipe_id: int, image_filenames: list[str]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM recipe_images WHERE recipe_id = ?", (recipe_id,))
        for idx, filename in enumerate(image_filenames):
            conn.execute(
                "INSERT INTO recipe_images (recipe_id, filename, sort_order) VALUES (?, ?, ?)",
                (recipe_id, filename, idx),
            )


def get_recipe_images(recipe_id: int) -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, sort_order FROM recipe_images WHERE recipe_id = ? ORDER BY sort_order",
            (recipe_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_or_create_user(subject: str, email: str | None, name: str | None) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE subject = ?", (subject,)).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO users (subject, email, name) VALUES (?, ?, ?)",
            (subject, email, name),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def is_favorite(user_id: int, recipe_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, recipe_id),
        ).fetchone()
        return row is not None


def add_favorite(user_id: int, recipe_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, recipe_id) VALUES (?, ?)",
            (user_id, recipe_id),
        )


def remove_favorite(user_id: int, recipe_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, recipe_id),
        )


def get_user_favorite_ids(user_id: int) -> set[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT recipe_id FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {int(r["recipe_id"]) for r in rows}


def get_favorite_recipes(user_id: int, lang: str = DEFAULT_LANGUAGE) -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.name AS category_name,
                   c.display_name_fr AS category_display_name_fr,
                   c.display_name_en AS category_display_name_en
            FROM favorites f
            JOIN recipes r ON f.recipe_id = r.id
            LEFT JOIN categories c ON r.category_id = c.id
            WHERE f.user_id = ?
            ORDER BY f.created_at DESC
            """,
            (user_id,),
        ).fetchall()

        results = []
        cat_col = "category_display_name_en" if lang == "en" else "category_display_name_fr"
        for row in rows:
            translation = _load_translation(conn, int(row["id"]), lang)
            d: dict[str, object] = dict(row)
            d.update(translation)
            d["title"] = translation["title"]
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d[cat_col],
                }
            else:
                d["category"] = None

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name,
                       t.display_name_fr, t.display_name_en,
                       tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name_fr
                """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [_localize_tag(tr, lang) | {"family": tr["family"]} for tr in tag_rows]
            d["images"] = get_recipe_images(int(row["id"]))
            results.append(d)

        return results


def get_all_recipes_admin(
    filter: str = "", lang: str = DEFAULT_LANGUAGE
) -> list[dict[str, object]]:
    where = ""
    if filter == "no_tags":
        where = "WHERE NOT EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = r.id)"
    elif filter == "no_category":
        where = "WHERE r.category_id IS NULL"

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.id, r.source_file, r.created_at, r.updated_at,
                   r.file_modified_at, r.manually_edited,
                   (SELECT COUNT(*) FROM favorites f WHERE f.recipe_id = r.id)
                       AS favorite_count,
                   c.name AS category_name,
                   c.display_name_fr AS category_display_name_fr,
                   c.display_name_en AS category_display_name_en,
                   pc.name AS provenance
            FROM recipes r
            LEFT JOIN categories c ON r.category_id = c.id
            LEFT JOIN dropbox_connections pc ON pc.id = r.connection_id
            {where}
            ORDER BY r.created_at DESC
            """
        ).fetchall()

        results = []
        cat_col = "category_display_name_en" if lang == "en" else "category_display_name_fr"
        for row in rows:
            translation = _load_translation(conn, int(row["id"]), lang)
            d: dict[str, object] = dict(row)
            d["title"] = translation["title"]
            d["description"] = translation["description"]
            d["instructions"] = translation["instructions"]
            d["ingredients"] = translation["ingredients"]
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d[cat_col],
                }
            else:
                d["category"] = None
            d["provenance"] = str(d["provenance"]) if d.get("provenance") else DEFAULT_ACCOUNT_NAME

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name,
                       t.display_name_fr, t.display_name_en,
                       tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name_fr
                """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [_localize_tag(tr, lang) | {"family": tr["family"]} for tr in tag_rows]
            results.append(d)

        return results


def blacklist_and_delete_recipe(recipe_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT source_file FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return None
        source_file = str(row["source_file"])

        conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        conn.execute("DELETE FROM recipe_images WHERE recipe_id = ?", (recipe_id,))
        conn.execute(
            """
            INSERT INTO blacklist (path) VALUES (?)
            ON CONFLICT(path) DO UPDATE SET blacklisted_at=CURRENT_TIMESTAMP
            """,
            (source_file,),
        )
        return source_file


def is_blacklisted(path: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM blacklist WHERE path = ?", (path,)).fetchone()
        return row is not None


def _provenance_from_path(path: str, conn_names: dict[int, str]) -> str:
    """Nom du compte d'origine d'un chemin source (prefixe 'account:<id>:')."""
    if path.startswith("account:"):
        try:
            conn_id = int(path.split(":")[1])
        except (IndexError, ValueError):
            return "?"
        return conn_names.get(conn_id, "?")
    return DEFAULT_ACCOUNT_NAME


def _connection_names() -> dict[int, str]:
    return {int(str(c["id"])): str(c["name"]) for c in get_dropbox_connections()}


def get_blacklisted_files() -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path, blacklisted_at FROM blacklist ORDER BY blacklisted_at DESC"
        ).fetchall()
    conns = _connection_names()
    result = [dict(r) for r in rows]
    for r in result:
        r["provenance"] = _provenance_from_path(str(r["path"]), conns)
    return result


def remove_from_blacklist(path: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM blacklist WHERE path = ?", (path,))
        conn.execute("DELETE FROM processed_files WHERE path = ?", (path,))


def record_failed_file(path: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO failed_files (path, error) VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET error=excluded.error, failed_at=CURRENT_TIMESTAMP
            """,
            (path, error),
        )


def get_failed_files() -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path, error, failed_at FROM failed_files ORDER BY failed_at DESC"
        ).fetchall()
    conns = _connection_names()
    result = [dict(r) for r in rows]
    for r in result:
        r["provenance"] = _provenance_from_path(str(r["path"]), conns)
    return result


def remove_failed_file(path: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM failed_files WHERE path = ?", (path,))


# ---------------------------------------------------------------------------
# Dropbox connections (extra accounts configured at runtime)
# ---------------------------------------------------------------------------


def get_dropbox_connections() -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, folder, file_filter, active, visible, created_at "
            "FROM dropbox_connections ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_dropbox_connection(
    name: str,
    refresh_token: str,
    folder: str = "",
    file_filter: str = "",
) -> int | None:
    """Insère une nouvelle connexion Dropbox. Retourne None si le nom existe déjà."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM dropbox_connections WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            return None
        cur = conn.execute(
            """
            INSERT INTO dropbox_connections
                (name, refresh_token, folder, file_filter)
            VALUES (?, ?, ?, ?)
            """,
            (name, refresh_token, folder, file_filter),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_dropbox_connection_credentials(connection_id: int) -> dict[str, object] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, refresh_token, folder, file_filter "
            "FROM dropbox_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_dropbox_connection(connection_id: int) -> bool:
    """Supprime une connexion et toutes ses recettes associees."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM dropbox_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        if not row:
            return False

        conn.execute("DELETE FROM recipes WHERE connection_id = ?", (connection_id,))
        prefix = f"account:{connection_id}:%"
        conn.execute("DELETE FROM processed_files WHERE path LIKE ?", (prefix,))
        conn.execute("DELETE FROM failed_files WHERE path LIKE ?", (prefix,))
        conn.execute("DELETE FROM blacklist WHERE path LIKE ?", (prefix,))
        conn.execute("DELETE FROM dropbox_connections WHERE id = ?", (connection_id,))
        return True


def set_dropbox_connection_active(connection_id: int, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE dropbox_connections SET active = ? WHERE id = ?",
            (1 if active else 0, connection_id),
        )


def set_dropbox_connection_visible(connection_id: int, visible: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE dropbox_connections SET visible = ? WHERE id = ?",
            (1 if visible else 0, connection_id),
        )


# ---------------------------------------------------------------------------
# App settings (key/value) — used for the default (.env) Dropbox account flags
# ---------------------------------------------------------------------------

DEFAULT_ACCOUNT_ID = -1
DEFAULT_ACCOUNT_NAME = "Défaut"


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def delete_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def is_default_account_active() -> bool:
    return get_setting("default_active", "1") != "0"


def set_default_account_active(active: bool) -> None:
    set_setting("default_active", "1" if active else "0")


def is_default_account_visible() -> bool:
    return get_setting("default_visible", "1") != "0"


def set_default_account_visible(visible: bool) -> None:
    set_setting("default_visible", "1" if visible else "0")


def get_recipe_provenances() -> list[dict[str, object]]:
    """Liste des comptes Dropbox ayant au moins une recette visible."""
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id AS id, c.name AS name, COUNT(r.id) AS count
            FROM recipes r
            JOIN dropbox_connections c ON c.id = r.connection_id
            WHERE c.visible = 1
            GROUP BY c.id, c.name

            UNION ALL

            SELECT {DEFAULT_ACCOUNT_ID} AS id, '{DEFAULT_ACCOUNT_NAME}' AS name,
                   COUNT(id) AS count
            FROM recipes
            WHERE connection_id IS NULL AND
                  {int(is_default_account_visible())} = 1

            ORDER BY name
            """
        ).fetchall()
        return [dict(r) for r in rows if r["count"] > 0]
