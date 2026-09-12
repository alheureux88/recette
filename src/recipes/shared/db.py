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
from collections.abc import Generator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext
from recipes.shared.models import JsonDict
from recipes.shared.slug import slugify

# Constants used by multiple modules
DEFAULT_ACCOUNT_ID = -1
DEFAULT_ACCOUNT_NAME = "Défaut"

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

SEED_SHOPPING_DEPARTMENTS: list[tuple[str, str, str, int, str]] = [
    ("fruits-legumes", "Fruits et légumes", "Fruits & Vegetables", 1, "🥬"),
    ("boucherie", "Boucherie", "Butcher", 2, "🥩"),
    ("poissonnerie", "Poissonnerie", "Fish counter", 3, "🐟"),
    ("charcuterie", "Charcuterie", "Deli / Charcuterie", 4, "🥓"),
    ("boulangerie", "Boulangerie", "Bakery", 5, "🥖"),
    ("produits-laitiers", "Produits laitiers", "Dairy", 6, "🧀"),
    ("surgelés", "Surgelés", "Frozen goods", 7, "🧊"),
    ("epicerie", "Épicerie", "Grocery / Pantry", 8, "🥫"),
    ("pret-a-manger", "Prêt-à-manger", "Ready to eat", 9, "🥡"),
    ("entretien", "Entretien / Ménage", "Household / Cleaning", 10, "🧹"),
    ("autre", "Autre / Indéterminé", "Other / Unknown", 99, "❓"),
]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Init / migrate
# ---------------------------------------------------------------------------


def init_db() -> None:
    with get_conn() as conn:
        _create_tables(conn)
        _migrate_processed_files(conn)
        _migrate_recipes_visibility(conn)
        _migrate_recipes_slug(conn)
        _create_fts(conn)
        _seed(conn)


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS recipes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            slug         TEXT UNIQUE,
            source       TEXT,
            date         TEXT,
            servings     REAL,
            source_url   TEXT,
            dropbox_url  TEXT,
            source_file  TEXT NOT NULL UNIQUE,
            file_hash    TEXT NOT NULL,
            manually_edited INTEGER NOT NULL DEFAULT 0,
            connection_id INTEGER REFERENCES dropbox_connections(id),
            category_id  INTEGER REFERENCES categories(id),
            file_modified_at DATETIME,
            source_missing INTEGER NOT NULL DEFAULT 0,
            force_visible INTEGER NOT NULL DEFAULT 0,
            tagger_version INTEGER,
            tagger_model TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipe_translations (
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            lang         TEXT NOT NULL,
            title        TEXT NOT NULL,
            description  TEXT,
            steps        TEXT,
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
            dropbox_hash TEXT,
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

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
            endpoint     TEXT NOT NULL UNIQUE,
            subscription TEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Préférences usager : blob JSON extensible (une ligne par usager).
        -- Les nouveaux réglages sont de nouvelles clés JSON avec défauts
        -- côté code : aucune migration de schéma n'est requise.
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            prefs      TEXT NOT NULL DEFAULT '{}',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shopping_departments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            emoji           TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS shopping_lists (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            share_token  TEXT NOT NULL UNIQUE,
            name         TEXT NOT NULL,
            user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            all_done_at  DATETIME
        );

        CREATE TABLE IF NOT EXISTS shopping_list_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id       INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
            department_id INTEGER NOT NULL REFERENCES shopping_departments(id),
            text          TEXT NOT NULL,
            quantity      TEXT,
            is_done       INTEGER NOT NULL DEFAULT 0,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shopping_templates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shopping_template_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id   INTEGER NOT NULL REFERENCES shopping_templates(id) ON DELETE CASCADE,
            department_id INTEGER NOT NULL REFERENCES shopping_departments(id),
            text          TEXT NOT NULL,
            quantity      TEXT,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Collections de recettes : listes nommées avec texte descriptif.
        -- owner_user_id NULL + is_site = 1 : collection du site (visible par tous).
        -- owner renseigné + is_site = 0 : collection privée d'un usager,
        -- partageable en lecture seule via share_token.
        CREATE TABLE IF NOT EXISTS collections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            slug          TEXT UNIQUE,
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            share_token   TEXT NOT NULL UNIQUE,
            is_site       INTEGER NOT NULL DEFAULT 0,
            is_featured   INTEGER NOT NULL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS collection_recipes (
            collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            added_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_id, recipe_id)
        );

        CREATE INDEX IF NOT EXISTS idx_collections_site
            ON collections(is_site, is_featured);
        CREATE INDEX IF NOT EXISTS idx_collection_recipes_recipe
            ON collection_recipes(recipe_id);
    """)


def _migrate_processed_files(conn: sqlite3.Connection) -> None:
    """Ajoute `dropbox_hash` aux bases existantes (sans re-parse)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(processed_files)").fetchall()}
    if "dropbox_hash" not in cols:
        conn.execute("ALTER TABLE processed_files ADD COLUMN dropbox_hash TEXT")


def _migrate_recipes_visibility(conn: sqlite3.Connection) -> None:
    """Ajoute `source_missing` / `force_visible` aux bases existantes."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    if "source_missing" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN source_missing INTEGER NOT NULL DEFAULT 0")
    if "force_visible" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN force_visible INTEGER NOT NULL DEFAULT 0")


def _migrate_recipes_slug(conn: sqlite3.Connection) -> None:
    """Ajoute `slug` aux bases existantes et remplit les valeurs manquantes.

    Les slugs existants sont conservés (stabilité des URL) ; seules les
    lignes sans slug en reçoivent un, dérivé du titre français.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    if "slug" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN slug TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_slug ON recipes(slug)")
    for row in conn.execute("SELECT id FROM recipes WHERE slug IS NULL OR slug = ''").fetchall():
        recipe_id = int(row["id"])
        title_row = conn.execute(
            "SELECT title FROM recipe_translations WHERE recipe_id = ? AND lang = 'fr'",
            (recipe_id,),
        ).fetchone()
        base = slugify(str(title_row["title"]) if title_row and title_row["title"] else "")
        _assign_unique_slug(conn, recipe_id, base)


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

    for name, fr, en, sort_order, emoji in SEED_SHOPPING_DEPARTMENTS:
        conn.execute(
            """
            INSERT OR IGNORE INTO shopping_departments
                (name, display_name_fr, display_name_en, sort_order, emoji)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, fr, en, sort_order, emoji),
        )


# ---------------------------------------------------------------------------
# Localized display helpers
# ---------------------------------------------------------------------------


def _localize_tag(row: sqlite3.Row, lang: str) -> JsonDict:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "id": row["id"],
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_family(row: sqlite3.Row, lang: str) -> JsonDict:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_category(row: sqlite3.Row, lang: str) -> JsonDict:
    col = "display_name_en" if lang == "en" else "display_name_fr"
    return {
        "id": row["id"],
        "name": row["name"],
        "display_name": row[col],
    }


def _localize_recipe_translation(row: sqlite3.Row | None) -> JsonDict:
    if row is None:
        return {"title": "", "description": "", "steps": [], "ingredients": []}
    ingredients_raw = row["ingredients"] or "[]"
    steps_raw = row["steps"] or "[]"
    return {
        "title": str(row["title"] or ""),
        "description": str(row["description"] or ""),
        "steps": json.loads(str(steps_raw)),
        "ingredients": json.loads(str(ingredients_raw)),
    }


# ---------------------------------------------------------------------------
# Recipe write helpers
# ---------------------------------------------------------------------------


def _clean_optional_text(value: object) -> str | None:
    """Normalise un champ texte optionnel (source, date) : vide → None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def upsert_recipe(data: JsonDict, conn: sqlite3.Connection | None = None) -> int:
    """Insert or update a recipe with bilingual translations.

    `data` must contain both `lang_fr` and `lang_en` payloads (or, for tests
    / manual editing, a single-language `lang` payload). A unified
    `tags` dict of `{family: [names]}` and a single `category` key (technical
    name) are also expected.
    """
    payload_fr, payload_en = _extract_translation_payload(data)

    with get_conn() if conn is None else nullcontext(conn) as _conn:
        existing = _conn.execute(
            "SELECT id FROM recipes WHERE source_file = ?", (data["source_file"],)
        ).fetchone()

        category_id = _resolve_category(
            _conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None
        raw_connection = data.get("connection_id")
        connection_id = int(str(raw_connection)) if raw_connection is not None else None
        tagger_version = data.get("tagger_version")
        if isinstance(tagger_version, bool) or not isinstance(tagger_version, int):
            tagger_version = None
        tagger_model = data.get("tagger_model")
        if not isinstance(tagger_model, str) or not tagger_model.strip():
            tagger_model = None

        if existing:
            recipe_id = int(existing["id"])
            _conn.execute(
                """
                UPDATE recipes SET
                    servings=?, category_id=?, source_url=?, dropbox_url=?, file_hash=?,
                    file_modified_at=?, connection_id=?, source_missing=0,
                    source=?, date=?, tagger_version=?, tagger_model=?,
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
                    _clean_optional_text(data.get("source")),
                    _clean_optional_text(data.get("date")),
                    tagger_version,
                    tagger_model,
                    data["source_file"],
                ),
            )
        else:
            cur = _conn.execute(
                """
                INSERT INTO recipes
                    (servings, category_id, source_url, dropbox_url, source_file,
                      file_hash, file_modified_at, connection_id, source_missing,
                      source, date, tagger_version, tagger_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
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
                    _clean_optional_text(data.get("source")),
                    _clean_optional_text(data.get("date")),
                    tagger_version,
                    tagger_model,
                ),
            )
            assert cur.lastrowid is not None
            recipe_id = int(cur.lastrowid)

        _upsert_translation(_conn, recipe_id, "fr", payload_fr)
        _upsert_translation(_conn, recipe_id, "en", payload_en)
        _ensure_recipe_slug(
            _conn,
            recipe_id,
            base_title=str(payload_fr.get("title") or payload_en.get("title") or ""),
            keep_existing=bool(existing),
        )
        return recipe_id


def _ensure_recipe_slug(
    conn: sqlite3.Connection, recipe_id: int, base_title: str, keep_existing: bool
) -> str:
    """Attribue un slug unique à la recette.

    Le slug existant est conservé (stabilité des URL) sauf à la création.
    """
    if keep_existing:
        row = conn.execute("SELECT slug FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is not None and row["slug"]:
            return str(row["slug"])
    return _assign_unique_slug(conn, recipe_id, slugify(base_title))


def _assign_unique_slug(conn: sqlite3.Connection, recipe_id: int, base: str) -> str:
    """Écrit un slug unique (`base`, `base-2`, …) en excluant la recette elle-même."""
    slug = base
    suffix = 2
    while True:
        row = conn.execute("SELECT id FROM recipes WHERE slug = ?", (slug,)).fetchone()
        if row is None or int(row["id"]) == recipe_id:
            break
        slug = f"{base}-{suffix}"
        suffix += 1
    conn.execute("UPDATE recipes SET slug = ? WHERE id = ?", (slug, recipe_id))
    return slug


def get_recipe_id_by_slug(slug: str, conn: sqlite3.Connection | None = None) -> int | None:
    """Retourne l'ID de la recette pour un slug, ou None si inconnu."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM recipes WHERE slug = ?", (slug,)).fetchone()
        return int(row["id"]) if row is not None else None


def get_recipe_slug(recipe_id: int, conn: sqlite3.Connection | None = None) -> str | None:
    """Retourne le slug d'une recette, ou None si absente."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT slug FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is None or not row["slug"]:
            return None
        return str(row["slug"])


def _extract_translation_payload(
    data: JsonDict,
) -> tuple[JsonDict, JsonDict]:
    """Return (payload_fr, payload_en) from the upsert payload.

    Accepts either pre-split `lang_fr`/`lang_en` dicts, a single-language
    `lang` dict (replicated for both sides — used by tests and manual
    edits), or a top-level shape with `title` / `description` /
    `steps` / `ingredients` keys at the root of `data` (legacy
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

    has_legacy_keys = any(k in data for k in ("title", "description", "steps", "ingredients"))
    if has_legacy_keys:
        payload = {
            "title": data.get("title") or "",
            "description": data.get("description") or "",
            "steps": data.get("steps") or [],
            "ingredients": data.get("ingredients") or [],
        }
        return payload, payload
    raise ValueError(
        "upsert_recipe requires lang_fr/lang_en translation dicts (or a 'lang' fallback)"
    )


def _upsert_translation(
    conn: sqlite3.Connection, recipe_id: int, lang: str, payload: JsonDict
) -> None:
    title = str(payload.get("title") or "").strip()
    if not title:
        return
    description = payload.get("description") or ""
    steps = payload.get("steps") or []
    steps_json = json.dumps(steps, ensure_ascii=False)
    ingredients = payload.get("ingredients") or []
    ingredients_json = json.dumps(ingredients, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO recipe_translations
            (recipe_id, lang, title, description, steps, ingredients)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(recipe_id, lang) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            steps=excluded.steps,
            ingredients=excluded.ingredients
        """,
        (recipe_id, lang, title, description, steps_json, ingredients_json),
    )


def sync_recipe_tags(
    recipe_id: int, tags_by_family: dict[str, list[str]], conn: sqlite3.Connection | None = None
) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        tag_ids: set[int] = set()

        for family_name, tag_names in tags_by_family.items():
            family = _conn.execute(
                "SELECT id FROM tag_families WHERE name = ?", (family_name,)
            ).fetchone()
            if not family:
                continue
            family_id = family["id"]

            for tag_name in tag_names:
                tag_id = _resolve_tag(_conn, family_id, tag_name)
                if tag_id is not None:
                    tag_ids.add(tag_id)

        all_ids: set[int] = set(tag_ids)
        for tid in tag_ids:
            _add_ancestors(_conn, tid, all_ids)

        _conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        for tid in all_ids:
            _conn.execute(
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


def is_manually_edited(source_file: str, conn: sqlite3.Connection | None = None) -> bool:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT 1 FROM recipes WHERE source_file = ? AND manually_edited = 1",
            (source_file,),
        ).fetchone()
        return row is not None


def update_recipe_manual(
    recipe_id: int, data: JsonDict, conn: sqlite3.Connection | None = None
) -> bool:
    """Met à jour une recette modifiée via l'écran d'administration.

    Marque la recette comme modifiée manuellement : le poller Dropbox ignorera
    alors les futures mises à jour du fichier source et les signalera dans
    les fichiers en erreur.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False

        payload_fr, payload_en = _extract_translation_payload(data)
        category_id = _resolve_category(
            _conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None

        _conn.execute(
            """
            UPDATE recipes SET
                servings=?, category_id=?, source_url=?,
                source=?, date=?,
                manually_edited=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                servings,
                category_id,
                data.get("source_url"),
                _clean_optional_text(data.get("source")),
                _clean_optional_text(data.get("date")),
                recipe_id,
            ),
        )
        _upsert_translation(_conn, recipe_id, "fr", payload_fr)
        _upsert_translation(_conn, recipe_id, "en", payload_en)
        return True


def update_recipe_category(
    recipe_id: int, category: str | None, conn: sqlite3.Connection | None = None
) -> bool:
    """Édition inline de la catégorie. Marque la recette comme modifiée manuellement."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False
        category_id = _resolve_category(_conn, category or None)
        _conn.execute(
            """
            UPDATE recipes SET category_id=?, manually_edited=1,
                   updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (category_id, recipe_id),
        )
        return True


def update_recipe_tags(
    recipe_id: int, tags_by_family: dict[str, list[str]], conn: sqlite3.Connection | None = None
) -> bool:
    """Édition inline des étiquettes (remplacement complet). Marque la recette manuelle."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return False
        _conn.execute(
            "UPDATE recipes SET manually_edited=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (recipe_id,),
        )
        sync_recipe_tags(recipe_id, tags_by_family, conn=_conn)
    return True


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


# ---------------------------------------------------------------------------
# Recipe read helpers
# ---------------------------------------------------------------------------


def get_recipe(
    recipe_id: int,
    lang: str = DEFAULT_LANGUAGE,
    conn: sqlite3.Connection | None = None,
    include_hidden: bool = False,
) -> JsonDict | None:
    """Retourne une recette, ou None si absente ou masquée.

    Les recettes dont le fichier source a disparu de Dropbox
    (`source_missing`) sont exclues sauf `include_hidden` (admin)
    ou `force_visible` (affichage forcé depuis l'admin).
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        if include_hidden:
            row = _conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        else:
            row = _conn.execute(
                "SELECT * FROM recipes WHERE id = ? AND (source_missing = 0 OR force_visible = 1)",
                (recipe_id,),
            ).fetchone()
        if row is None:
            return None

        result: JsonDict = dict(row)

        translation = _load_translation(_conn, recipe_id, lang)
        result.update(translation)
        result["title"] = translation["title"]  # backwards compat for templates

        cat_row = _conn.execute(
            "SELECT * FROM categories WHERE id = ?",
            (result.get("category_id"),),
        ).fetchone()
        result["category"] = _localize_category(cat_row, lang) if cat_row else None

        pc_row = _conn.execute(
            "SELECT id, name FROM dropbox_connections WHERE id = ?",
            (result.get("connection_id"),),
        ).fetchone()
        result["provenance"] = (
            {"id": pc_row["id"], "name": pc_row["name"]}
            if pc_row
            else {"id": None, "name": gettext("account.default", lang)}
        )

        tag_rows = _conn.execute(
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

        result["images"] = get_recipe_images(recipe_id, conn=_conn)

        return result


def _load_translation(conn: sqlite3.Connection, recipe_id: int, lang: str) -> JsonDict:
    """Return the translation for `lang`, falling back to the other language."""
    row = conn.execute(
        "SELECT title, description, steps, ingredients "
        "FROM recipe_translations WHERE recipe_id = ? AND lang = ?",
        (recipe_id, lang),
    ).fetchone()
    if row is not None:
        return _localize_recipe_translation(row)
    fallback = conn.execute(
        "SELECT title, description, steps, ingredients "
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
    conn: sqlite3.Connection | None = None,
) -> list[JsonDict]:
    """Recherche de recettes.

    `connection_id` filtre par compte Dropbox d'origine ; la valeur sentinelle
    DEFAULT_ACCOUNT_ID sélectionne les recettes du compte par défaut (.env).
    Les recettes issues de connexions masquées sont toujours exclues.
    Les champs textuels sont retournés dans la langue `lang`.
    """
    if tag_ids is None:
        tag_ids = []

    with get_conn() if conn is None else nullcontext(conn) as _conn:
        conditions: list[str] = [
            "NOT EXISTS "
            "(SELECT 1 FROM dropbox_connections dc WHERE dc.id = r.connection_id AND dc.visible = 0)",
            "(r.source_missing = 0 OR r.force_visible = 1)",
        ]
        if not is_default_account_visible(conn=_conn):
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
                row = _conn.execute("SELECT family_id FROM tags WHERE id = ?", (tid,)).fetchone()
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

        rows = _conn.execute(
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
            translation = _load_translation(_conn, int(row["id"]), lang)
            d: JsonDict = dict(row)
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
                d["provenance"] = {"id": None, "name": gettext("account.default", lang)}

            tag_rows = _conn.execute(
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

            d["images"] = get_recipe_images(int(row["id"]), conn=_conn)

            results.append(d)

        return results


def get_all_tags_grouped(
    lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> dict[str, dict[str, Any]]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
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
    only_used: bool = True, lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        if only_used:
            rows = _conn.execute(
                """
                SELECT DISTINCT c.id, c.name,
                       c.display_name_fr, c.display_name_en
                FROM categories c
                JOIN recipes r ON c.id = r.category_id
                ORDER BY c.sort_order
                """
            ).fetchall()
        else:
            rows = _conn.execute(
                "SELECT id, name, display_name_fr, display_name_en "
                "FROM categories ORDER BY sort_order"
            ).fetchall()
    return [_localize_category(r, lang) for r in rows]


def get_existing_tags_for_prompt(
    lang: str = DEFAULT_LANGUAGE,
    conn: sqlite3.Connection | None = None,
) -> dict[str, list[JsonDict]]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
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

    result: dict[str, list[JsonDict]] = {}
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


def get_tag_families(
    lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT name, display_name_fr, display_name_en FROM tag_families ORDER BY sort_order"
        ).fetchall()
    return [_localize_family(r, lang) for r in rows]


def mark_processed(
    path: str,
    file_hash: str,
    dropbox_hash: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Mémorise un fichier traité.

    `dropbox_hash` (rev / content_hash Dropbox) permet de sauter le
    téléchargement au prochain poll ; `None` conserve la valeur stockée.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        if dropbox_hash is None:
            _conn.execute(
                """
                INSERT INTO processed_files (path, file_hash) VALUES (?, ?)
                ON CONFLICT(path) DO UPDATE SET file_hash=excluded.file_hash,
                    processed_at=CURRENT_TIMESTAMP
                """,
                (path, file_hash),
            )
        else:
            _conn.execute(
                """
                INSERT INTO processed_files (path, file_hash, dropbox_hash) VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET file_hash=excluded.file_hash,
                    dropbox_hash=excluded.dropbox_hash, processed_at=CURRENT_TIMESTAMP
                """,
                (path, file_hash, dropbox_hash),
            )


def get_processed_hash(path: str, conn: sqlite3.Connection | None = None) -> str | None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT file_hash FROM processed_files WHERE path = ?", (path,)
        ).fetchone()
        return row["file_hash"] if row else None


def get_processed_dropbox_hash(path: str, conn: sqlite3.Connection | None = None) -> str | None:
    """Retourne le rev / content_hash Dropbox stocké, ou None si inconnu."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        try:
            row = _conn.execute(
                "SELECT dropbox_hash FROM processed_files WHERE path = ?", (path,)
            ).fetchone()
        except sqlite3.OperationalError:
            # Base antérieure à la migration (colonne absente).
            return None
        if not row:
            return None
        value = row["dropbox_hash"]
        return str(value) if value is not None else None


def reconcile_account_files(
    connection_id: int | None,
    present_paths: set[str],
    conn: sqlite3.Connection | None = None,
) -> tuple[int, int]:
    """Marque les recettes dont le fichier source a disparu / réapparu.

    `present_paths` contient les `source_file` vus sur Dropbox pour ce
    compte (`connection_id`, None = compte par défaut). Retourne
    (nouveaux_manquants, réapparus).
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        if connection_id is None:
            scope = "connection_id IS NULL"
            scope_params: tuple[object, ...] = ()
        else:
            scope = "connection_id = ?"
            scope_params = (connection_id,)
        if present_paths:
            placeholders = ", ".join("?" for _ in present_paths)
            missing_cur = _conn.execute(
                f"UPDATE recipes SET source_missing = 1, updated_at = CURRENT_TIMESTAMP"
                f" WHERE {scope} AND source_missing = 0 AND source_file NOT IN ({placeholders})",
                (*scope_params, *sorted(present_paths)),
            )
            present_cur = _conn.execute(
                f"UPDATE recipes SET source_missing = 0, updated_at = CURRENT_TIMESTAMP"
                f" WHERE {scope} AND source_missing = 1 AND source_file IN ({placeholders})",
                (*scope_params, *sorted(present_paths)),
            )
        else:
            missing_cur = _conn.execute(
                f"UPDATE recipes SET source_missing = 1, updated_at = CURRENT_TIMESTAMP"
                f" WHERE {scope} AND source_missing = 0",
                scope_params,
            )
            present_cur = None
        missing = missing_cur.rowcount if missing_cur.rowcount is not None else 0
        reappeared = present_cur.rowcount if present_cur and present_cur.rowcount is not None else 0
        return missing, reappeared


def get_orphaned_recipes(
    lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Recettes dont le fichier source a disparu de Dropbox (admin)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            """
            SELECT r.id, r.source_file, r.force_visible,
                   pc.id AS provenance_id, pc.name AS provenance_name
            FROM recipes r
            LEFT JOIN dropbox_connections pc ON pc.id = r.connection_id
            WHERE r.source_missing = 1
            ORDER BY r.updated_at DESC
            """
        ).fetchall()
        results: list[JsonDict] = []
        for row in rows:
            translation = _load_translation(_conn, int(row["id"]), lang)
            d: JsonDict = dict(row)
            d["title"] = translation["title"]
            if row["provenance_id"] is not None:
                d["provenance"] = {"id": row["provenance_id"], "name": row["provenance_name"]}
            else:
                d["provenance"] = {"id": None, "name": gettext("account.default", lang)}
            results.append(d)
        return results


def set_recipe_force_visible(
    recipe_id: int, visible: bool, conn: sqlite3.Connection | None = None
) -> bool:
    """Force l'affichage d'une recette orpheline (ou l'annule)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "UPDATE recipes SET force_visible = ? WHERE id = ?",
            (1 if visible else 0, recipe_id),
        )
        return cur.rowcount > 0


def save_recipe_images(
    recipe_id: int, image_filenames: list[str], conn: sqlite3.Connection | None = None
) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute("DELETE FROM recipe_images WHERE recipe_id = ?", (recipe_id,))
        for idx, filename in enumerate(image_filenames):
            _conn.execute(
                "INSERT INTO recipe_images (recipe_id, filename, sort_order) VALUES (?, ?, ?)",
                (recipe_id, filename, idx),
            )


def get_recipe_images(recipe_id: int, conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT id, filename, sort_order FROM recipe_images WHERE recipe_id = ? ORDER BY sort_order",
            (recipe_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings (used by search_recipes)
# ---------------------------------------------------------------------------


def get_setting(key: str, default: str = "", conn: sqlite3.Connection | None = None) -> str:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default


def is_default_account_visible(conn: sqlite3.Connection | None = None) -> bool:
    return get_setting("default_visible", "1", conn=conn) != "0"
