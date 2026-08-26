"""
db.py — SQLite setup, schema, seed data, and query helpers.

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

DB_PATH = Path(os.environ.get("DB_PATH", "/data/recipes.db"))

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_TAG_FAMILIES: list[tuple[str, str, int]] = [
    ("origin", "Origine", 1),
    ("diet", "Régime alimentaire", 2),
    ("protein", "Protéine principale", 3),
    ("cooking_method", "Méthode de cuisson", 4),
]

SEED_TAGS: dict[str, list[tuple[str, str, str | None]]] = {
    "origin": [
        ("asiatique", "Asiatique", None),
        ("japonais", "Japonais", "asiatique"),
        ("chinois", "Chinois", "asiatique"),
        ("coreen", "Coréen", "asiatique"),
        ("thailandais", "Thaïlandais", "asiatique"),
        ("vietnamien", "Vietnamien", "asiatique"),
        ("indien", "Indien", "asiatique"),
        ("europeen", "Européen", None),
        ("francais", "Français", "europeen"),
        ("italien", "Italien", "europeen"),
        ("grec", "Grec", "europeen"),
        ("espagnol", "Espagnol", "europeen"),
        ("allemand", "Allemand", "europeen"),
        ("americain", "Américain", None),
        ("canadien", "Canadien", "americain"),
        ("quebecois", "Québécois", "canadien"),
        ("mexicain", "Mexicain", "americain"),
        ("moyen-oriental", "Moyen-Oriental", None),
        ("libanais", "Libanais", "moyen-oriental"),
        ("israelien", "Israélien", "moyen-oriental"),
        ("africain", "Africain", None),
        ("marocain", "Marocain", "africain"),
        ("ethiopien", "Éthiopien", "africain"),
    ],
    "diet": [
        ("vegetalien", "Végétalien", None),
        ("vegetarien", "Végétarien", None),
        ("pescetarien", "Pescétarien", None),
        ("sans-gluten", "Sans gluten", None),
        ("sans-produits-laitiers", "Sans produits laitiers", None),
        ("cetogene", "Cétogène", None),
        ("faible-en-glucides", "Faible en glucides", None),
        ("paleo", "Paléo", None),
    ],
    "protein": [
        ("poulet", "Poulet", None),
        ("boeuf", "Bœuf", None),
        ("porc", "Porc", None),
        ("agneau", "Agneau", None),
        ("veau", "Veau", None),
        ("poisson", "Poisson", None),
        ("fruits-de-mer", "Fruits de mer", None),
        ("tofu", "Tofu", None),
        ("tempeh", "Tempeh", None),
        ("lentilles", "Lentilles", None),
        ("oeufs", "Œufs", None),
        ("canard", "Canard", None),
    ],
    "cooking_method": [
        ("braise", "Braisé", None),
        ("roti", "Rôti", None),
        ("saute", "Sauté", None),
        ("wok", "Wok", None),
        ("fume", "Fumé", None),
        ("barbecue", "Barbecue", None),
        ("grille", "Grillé", None),
        ("frit", "Frit", None),
        ("mijote", "Mijoté", None),
        ("sans-cuisson", "Sans cuisson", None),
        ("poche", "Poché", None),
        ("vapeur", "Vapeur", None),
    ],
}

SEED_CATEGORIES: list[tuple[str, str, int]] = [
    ("entree", "Entrée", 1),
    ("plat-principal", "Plat principal", 2),
    ("salade", "Salade", 3),
    ("soupe", "Soupe", 4),
    ("sauce", "Sauce", 5),
    ("dessert", "Dessert", 6),
    ("accompagnement", "Accompagnement", 7),
    ("collation", "Collation", 8),
    ("aperitif", "Apéritif", 9),
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
            title        TEXT NOT NULL,
            description  TEXT,
            ingredients  TEXT,
            instructions TEXT,
            servings     REAL,
            source_url   TEXT,
            dropbox_url  TEXT,
            source_file  TEXT NOT NULL UNIQUE,
            file_hash    TEXT NOT NULL,
            manually_edited INTEGER NOT NULL DEFAULT 0,
            connection_id INTEGER REFERENCES dropbox_connections(id),
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tag_families (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tags (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id    INTEGER NOT NULL REFERENCES tag_families(id),
            name         TEXT NOT NULL,
            display_name TEXT NOT NULL,
            parent_id    INTEGER REFERENCES tags(id),
            UNIQUE(family_id, name)
        );

        CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0
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
    rows = conn.execute("PRAGMA table_info(recipes)").fetchall()
    cols = {row[1] for row in rows}
    del rows

    if "tags" in cols:
        conn.execute("ALTER TABLE recipes DROP COLUMN tags")

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


def _create_fts(conn: sqlite3.Connection) -> None:
    fts_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recipes_fts'"
    ).fetchone()

    if fts_exists:
        del fts_exists
        return
    del fts_exists

    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
            title,
            description,
            ingredients
        );

        CREATE TRIGGER recipes_ai AFTER INSERT ON recipes BEGIN
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (new.id, new.title, new.description, new.ingredients);
        END;

        CREATE TRIGGER recipes_au AFTER UPDATE ON recipes BEGIN
            DELETE FROM recipes_fts WHERE rowid = old.id;
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (new.id, new.title, new.description, new.ingredients);
        END;

        CREATE TRIGGER recipes_ad AFTER DELETE ON recipes BEGIN
            DELETE FROM recipes_fts WHERE rowid = old.id;
        END;
    """)

    rows = conn.execute("SELECT id, title, description, ingredients FROM recipes").fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO recipes_fts(rowid, title, description, ingredients) VALUES (?, ?, ?, ?)",
            (row["id"], row["title"], row["description"], row["ingredients"]),
        )
    del rows


def _seed(conn: sqlite3.Connection) -> None:
    for name, display_name, sort_order in SEED_TAG_FAMILIES:
        conn.execute(
            "INSERT OR IGNORE INTO tag_families (name, display_name, sort_order) VALUES (?, ?, ?)",
            (name, display_name, sort_order),
        )

    for family_name, tags in SEED_TAGS.items():
        family = conn.execute(
            "SELECT id FROM tag_families WHERE name = ?", (family_name,)
        ).fetchone()
        if not family:
            continue
        family_id = int(family["id"])
        del family

        for name, display_name, _parent_name in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags (family_id, name, display_name) VALUES (?, ?, ?)",
                (family_id, name, display_name),
            )

        for name, _display_name, parent_name in tags:
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

    for name, display_name, sort_order in SEED_CATEGORIES:
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, display_name, sort_order) VALUES (?, ?, ?)",
            (name, display_name, sort_order),
        )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def upsert_recipe(data: dict[str, object]) -> int:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM recipes WHERE source_file = ?", (data["source_file"],)
        ).fetchone()

        ingredients_json = json.dumps(data.get("ingredients", []))
        category_id = _resolve_category(
            conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None
        raw_connection = data.get("connection_id")
        connection_id = int(str(raw_connection)) if raw_connection is not None else None

        if existing:
            conn.execute(
                """
                UPDATE recipes SET
                    title=?, description=?, ingredients=?, instructions=?,
                    servings=?, category_id=?, source_url=?, dropbox_url=?, file_hash=?,
                    file_modified_at=?, connection_id=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_file=?
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
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
            return int(existing["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO recipes
                    (title, description, ingredients, instructions, servings, category_id,
                     source_url, dropbox_url, source_file, file_hash, file_modified_at,
                     connection_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
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
            return int(cur.lastrowid)


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
        "SELECT id FROM tags WHERE family_id = ? AND display_name = ?", (family_id, display_name)
    ).fetchone()
    if row:
        return int(row["id"])

    conn.execute(
        "INSERT OR IGNORE INTO tags (family_id, name, display_name) VALUES (?, ?, ?)",
        (family_id, name, display_name),
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
        "INSERT INTO categories (name, display_name, sort_order) VALUES (?, ?, ?)",
        (name, display_name, max_order + 1),
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

        ingredients_json = json.dumps(data.get("ingredients", []))
        category_id = _resolve_category(
            conn, str(data["category"]) if data.get("category") else None
        )
        servings = data.get("servings")
        if isinstance(servings, bool) or not isinstance(servings, (int, float)):
            servings = None

        conn.execute(
            """
            UPDATE recipes SET
                title=?, description=?, ingredients=?, instructions=?,
                servings=?, category_id=?, source_url=?,
                manually_edited=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """,
            (
                data["title"],
                data.get("description"),
                ingredients_json,
                data.get("instructions"),
                servings,
                category_id,
                data.get("source_url"),
                recipe_id,
            ),
        )
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


def get_recipe(recipe_id: int) -> dict[str, object] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is None:
            return None

        result: dict[str, object] = dict(row)
        result["ingredients"] = json.loads(str(result.get("ingredients") or "[]"))

        cat_row = conn.execute(
            "SELECT id, name, display_name FROM categories WHERE id = ?",
            (result.get("category_id"),),
        ).fetchone()
        result["category"] = dict(cat_row) if cat_row else None

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
            SELECT tf.name AS family, tf.display_name AS family_display_name,
                   t.id, t.name, t.display_name
            FROM recipe_tags rt
            JOIN tags t ON rt.tag_id = t.id
            JOIN tag_families tf ON t.family_id = tf.id
            WHERE rt.recipe_id = ?
            ORDER BY tf.sort_order, t.display_name
        """,
            (recipe_id,),
        ).fetchall()

        tags_grouped: dict[str, dict[str, Any]] = {}
        for tr in tag_rows:
            fam = tr["family"]
            if fam not in tags_grouped:
                tags_grouped[fam] = {
                    "family_display_name": tr["family_display_name"],
                    "tags": [],
                }
            tags_grouped[fam]["tags"].append(
                {"id": tr["id"], "name": tr["name"], "display_name": tr["display_name"]}
            )
        result["tags"] = tags_grouped

        result["images"] = get_recipe_images(recipe_id)

        return result


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
) -> list[dict[str, object]]:
    """Recherche de recettes.

    `connection_id` filtre par compte Dropbox d'origine ; la valeur sentinelle
    DEFAULT_ACCOUNT_ID sélectionne les recettes du compte par défaut (.env).
    Les recettes issues de connexions masquées sont toujours exclues.
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
                    "r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)"
                )
                params.append(fts_q)
            else:
                # Saisie non vide mais sans mot exploitable (ponctuation seule)
                conditions.append("0")

        if tag_ids:
            # Group tags by family for OR-within-family, AND-between-families logic
            tag_to_family: dict[int, int] = {}
            for tid in tag_ids:
                row = conn.execute("SELECT family_id FROM tags WHERE id = ?", (tid,)).fetchone()
                if row:
                    tag_to_family[tid] = row["family_id"]

            families: dict[int, list[int]] = {}
            for tid, fid in tag_to_family.items():
                families.setdefault(fid, []).append(tid)

            # For each family, create an OR condition (recipe has ANY tag from this family)
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
            SELECT r.*, c.name AS category_name, c.display_name AS category_display_name,
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
        for row in rows:
            d: dict[str, object] = dict(row)
            d["ingredients"] = json.loads(str(d.get("ingredients") or "[]"))
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d["category_display_name"],
                }
            else:
                d["category"] = None

            if d.get("provenance_id") is not None:
                d["provenance"] = {"id": d["provenance_id"], "name": d["provenance_name"]}
            else:
                d["provenance"] = {"id": None, "name": DEFAULT_ACCOUNT_NAME}

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name, t.display_name, tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name
            """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [dict(tr) for tr in tag_rows]

            d["images"] = get_recipe_images(row["id"])

            results.append(d)

        return results


def get_all_tags_grouped() -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tf.name AS family, tf.display_name AS family_display_name,
                   t.id, t.name, t.display_name
            FROM tags t
            JOIN tag_families tf ON t.family_id = tf.id
            JOIN recipe_tags rt ON t.id = rt.tag_id
            GROUP BY t.id
            ORDER BY tf.sort_order, t.display_name
        """
        ).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        fam = row["family"]
        if fam not in result:
            result[fam] = {
                "display_name": row["family_display_name"],
                "tags": [],
            }
        result[fam]["tags"].append(
            {"id": row["id"], "name": row["name"], "display_name": row["display_name"]}
        )
    return result


def get_all_categories(only_used: bool = True) -> list[dict[str, object]]:
    with get_conn() as conn:
        if only_used:
            rows = conn.execute(
                """
                SELECT DISTINCT c.id, c.name, c.display_name
                FROM categories c
                JOIN recipes r ON c.id = r.category_id
                ORDER BY c.sort_order
            """
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, display_name FROM categories ORDER BY sort_order"
            ).fetchall()
    return [dict(r) for r in rows]


def get_existing_tags_for_prompt() -> dict[str, list[dict[str, object]]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT tf.name AS family, t.name, t.display_name, t.parent_id,
                   p.name AS parent_name
            FROM tags t
            JOIN tag_families tf ON t.family_id = tf.id
            LEFT JOIN tags p ON t.parent_id = p.id
            ORDER BY tf.sort_order, t.display_name
        """
        ).fetchall()

    result: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        fam = row["family"]
        if fam not in result:
            result[fam] = []
        result[fam].append(
            {
                "name": row["name"],
                "display_name": row["display_name"],
                "parent_name": row["parent_name"],
            }
        )
    return result


def get_tag_families() -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, display_name FROM tag_families ORDER BY sort_order"
        ).fetchall()
    return [{"name": r["name"], "display_name": r["display_name"]} for r in rows]


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


def get_favorite_recipes(user_id: int) -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.*, c.name AS category_name, c.display_name AS category_display_name
            FROM favorites f
            JOIN recipes r ON f.recipe_id = r.id
            LEFT JOIN categories c ON r.category_id = c.id
            WHERE f.user_id = ?
            ORDER BY f.created_at DESC
        """,
            (user_id,),
        ).fetchall()

        results = []
        for row in rows:
            d: dict[str, object] = dict(row)
            d["ingredients"] = json.loads(str(d.get("ingredients") or "[]"))
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d["category_display_name"],
                }
            else:
                d["category"] = None

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name, t.display_name, tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name
            """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [dict(tr) for tr in tag_rows]
            d["images"] = get_recipe_images(row["id"])
            results.append(d)

        return results


def get_all_recipes_admin(filter: str = "") -> list[dict[str, object]]:
    where = ""
    if filter == "no_tags":
        where = "WHERE NOT EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = r.id)"
    elif filter == "no_category":
        where = "WHERE r.category_id IS NULL"

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.id, r.title, r.source_file, r.created_at, r.updated_at,
                   r.file_modified_at, r.manually_edited,
                   (SELECT COUNT(*) FROM favorites f WHERE f.recipe_id = r.id)
                       AS favorite_count,
                   c.name AS category_name, c.display_name AS category_display_name,
                   pc.name AS provenance
            FROM recipes r
            LEFT JOIN categories c ON r.category_id = c.id
            LEFT JOIN dropbox_connections pc ON pc.id = r.connection_id
            {where}
            ORDER BY r.created_at DESC
        """
        ).fetchall()

        results = []
        for row in rows:
            d: dict[str, object] = dict(row)
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d["category_display_name"],
                }
            else:
                d["category"] = None
            d["provenance"] = str(d["provenance"]) if d.get("provenance") else DEFAULT_ACCOUNT_NAME

            tag_rows = conn.execute(
                """
                SELECT t.id, t.name, t.display_name, tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name
            """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [dict(tr) for tr in tag_rows]
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
    """Insère une nouvelle connexion Dropbox. Retourne None si le nom existe déjà.

    Les identifiants d'application (app key/secret) sont partagés avec le
    compte par défaut et proviennent de l'environnement.
    """
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
    """Supprime une connexion et toutes ses recettes associees.

    Les recettes du compte (et leurs images/etiquettes), ainsi que les entrees
    de traitement (fichiers traites, en erreur, blacklistes) sont supprimees :
    re-ajouter la connexion repartira de zero.
    """
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

DEFAULT_ACCOUNT_ID = -1  # sentinel: recipes with connection_id IS NULL
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
    """Liste des comptes Dropbox ayant au moins une recette visible.

    Le compte par défaut (.env) apparaît sous DEFAULT_ACCOUNT_ID s'il a des
    recettes. Les connexions masquées sont exclues.
    """
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
