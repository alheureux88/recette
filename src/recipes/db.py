"""
db.py — SQLite setup and query helpers
Uses FTS5 for full-text search on title, description, and ingredients.
"""

import contextlib
import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "/data/recipes.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recipes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                description  TEXT,
                ingredients  TEXT,    -- JSON list of strings
                instructions TEXT,
                tags         TEXT,    -- JSON list of strings
                source_url   TEXT,    -- original website URL extracted by LLM (nullable)
                dropbox_url  TEXT,    -- Dropbox shared link to the original file (nullable)
                source_file  TEXT NOT NULL UNIQUE,
                file_hash    TEXT NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Full-text search index
            CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
                title,
                description,
                ingredients,
                tags,
                content='recipes',
                content_rowid='id'
            );

            -- Keep FTS in sync with recipes table
            CREATE TRIGGER IF NOT EXISTS recipes_ai AFTER INSERT ON recipes BEGIN
                INSERT INTO recipes_fts(rowid, title, description, ingredients, tags)
                VALUES (new.id, new.title, new.description, new.ingredients, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS recipes_au AFTER UPDATE ON recipes BEGIN
                INSERT INTO recipes_fts(recipes_fts, rowid, title, description, ingredients, tags)
                VALUES ('delete', old.id, old.title, old.description, old.ingredients, old.tags);
                INSERT INTO recipes_fts(rowid, title, description, ingredients, tags)
                VALUES (new.id, new.title, new.description, new.ingredients, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS recipes_ad AFTER DELETE ON recipes BEGIN
                INSERT INTO recipes_fts(recipes_fts, rowid, title, description, ingredients, tags)
                VALUES ('delete', old.id, old.title, old.description, old.ingredients, old.tags);
            END;

            -- Track which Dropbox files we've already processed
            CREATE TABLE IF NOT EXISTS processed_files (
                path         TEXT PRIMARY KEY,
                file_hash    TEXT NOT NULL,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migrate existing databases — add new columns if they don't exist yet
        existing = {row[1] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()}
        if "source_url" not in existing:
            conn.execute("ALTER TABLE recipes ADD COLUMN source_url TEXT")
        if "dropbox_url" not in existing:
            conn.execute("ALTER TABLE recipes ADD COLUMN dropbox_url TEXT")


def upsert_recipe(data: dict[str, object]) -> int:
    """Insert or update a recipe. Returns the recipe id."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM recipes WHERE source_file = ?", (data["source_file"],)
        ).fetchone()

        ingredients_json = json.dumps(data.get("ingredients", []))
        tags_json = json.dumps(data.get("tags", []))

        if existing:
            conn.execute(
                """
                UPDATE recipes SET
                    title=?, description=?, ingredients=?, instructions=?,
                    tags=?, source_url=?, dropbox_url=?, file_hash=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE source_file=?
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
                    tags_json,
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
                    (title, description, ingredients, instructions, tags,
                     source_url, dropbox_url, source_file, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    data["title"],
                    data.get("description"),
                    ingredients_json,
                    data.get("instructions"),
                    tags_json,
                    data.get("source_url"),
                    data.get("dropbox_url"),
                    data["source_file"],
                    data["file_hash"],
                ),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid


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


def search_recipes(query: str = "", tags: list[str] | None = None) -> list[sqlite3.Row]:
    if tags is None:
        tags = []
    with get_conn() as conn:
        if query and tags:
            tag_conditions = " AND ".join("tags LIKE ?" for _ in tags)
            tag_params = [f'%"{t}"%' for t in tags]
            rows = conn.execute(
                f"""
                SELECT r.* FROM recipes r
                JOIN recipes_fts f ON r.id = f.rowid
                WHERE recipes_fts MATCH ?
                AND {tag_conditions}
                ORDER BY rank
            """,
                [query] + tag_params,
            ).fetchall()
        elif query:
            rows = conn.execute(
                """
                SELECT r.* FROM recipes r
                JOIN recipes_fts f ON r.id = f.rowid
                WHERE recipes_fts MATCH ?
                ORDER BY rank
            """,
                [query],
            ).fetchall()
        elif tags:
            tag_conditions = " AND ".join("tags LIKE ?" for _ in tags)
            tag_params = [f'%"{t}"%' for t in tags]
            rows = conn.execute(
                f"""
                SELECT * FROM recipes WHERE {tag_conditions}
                ORDER BY updated_at DESC
            """,
                tag_params,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM recipes ORDER BY updated_at DESC").fetchall()
        return rows


def get_recipe(recipe_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if row is None:
            return None
        return row  # type: ignore[no-any-return]


def get_all_tags() -> list[str]:
    """Return a deduplicated sorted list of all tags across all recipes."""
    with get_conn() as conn:
        rows = conn.execute("SELECT tags FROM recipes").fetchall()
    all_tags: set[str] = set()
    for row in rows:
        with contextlib.suppress(json.JSONDecodeError):
            all_tags.update(json.loads(row["tags"] or "[]"))
    return sorted(all_tags)
