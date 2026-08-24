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
            source_url   TEXT,
            dropbox_url  TEXT,
            source_file  TEXT NOT NULL UNIQUE,
            file_hash    TEXT NOT NULL,
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
    """)


def _migrate(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(recipes)").fetchall()
    cols = {row[1] for row in rows}
    del rows

    if "tags" in cols:
        conn.execute("ALTER TABLE recipes DROP COLUMN tags")

    if "category_id" not in cols:
        conn.execute("ALTER TABLE recipes ADD COLUMN category_id INTEGER REFERENCES categories(id)")


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

        if existing:
            conn.execute(
                """
                UPDATE recipes SET
                    title=?, description=?, ingredients=?, instructions=?,
                    category_id=?, source_url=?, dropbox_url=?, file_hash=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_file=?
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
                    category_id,
                    data.get("source_url"),
                    data.get("dropbox_url"),
                    data["file_hash"],
                    data["source_file"],
                ),
            )
            return int(existing["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO recipes
                    (title, description, ingredients, instructions, category_id,
                     source_url, dropbox_url, source_file, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
                    category_id,
                    data.get("source_url"),
                    data.get("dropbox_url"),
                    data["source_file"],
                    data["file_hash"],
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
    row = conn.execute(
        "SELECT id FROM tags WHERE family_id = ? AND name = ?", (family_id, name)
    ).fetchone()
    if row:
        return int(row["id"])

    display_name = name.replace("-", " ").title()
    cur = conn.execute(
        "INSERT INTO tags (family_id, name, display_name) VALUES (?, ?, ?)",
        (family_id, name, display_name),
    )
    return int(cur.lastrowid) if cur.lastrowid else None


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

        return result


def search_recipes(
    query: str = "",
    tag_ids: list[int] | None = None,
    category_id: int | None = None,
) -> list[dict[str, object]]:
    if tag_ids is None:
        tag_ids = []

    with get_conn() as conn:
        conditions: list[str] = []
        params: list[object] = []

        if query:
            conditions.append("r.id IN (SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?)")
            params.append(query)

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

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = conn.execute(
            f"""
            SELECT r.*, c.name AS category_name, c.display_name AS category_display_name
            FROM recipes r
            LEFT JOIN categories c ON r.category_id = c.id
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


def get_all_tags_grouped() -> dict[str, dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tf.name AS family, tf.display_name AS family_display_name,
                   t.id, t.name, t.display_name
            FROM tags t
            JOIN tag_families tf ON t.family_id = tf.id
            JOIN recipe_tags rt ON t.id = rt.tag_id
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


def get_all_categories() -> list[dict[str, object]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT c.id, c.name, c.display_name
            FROM categories c
            JOIN recipes r ON c.id = r.category_id
            ORDER BY c.sort_order
        """
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
