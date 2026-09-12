"""Collections feature — database service for recipe collections.

A collection is a named list of recipes with a short description. It is
either a *site* collection (visible to everyone) or a *user* collection
(private to its owner, shareable read-only via `share_token`). An admin
can promote a user collection to a site collection and flag site
collections as featured for the homepage carousel.
"""

import secrets
import sqlite3
from contextlib import nullcontext

from recipes.shared.db import get_conn, get_recipe
from recipes.shared.i18n import DEFAULT_LANGUAGE
from recipes.shared.models import JsonDict
from recipes.shared.slug import slugify

MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000


def _unique_collection_slug(
    conn: sqlite3.Connection, base: str, exclude_id: int | None = None
) -> str:
    """Return a unique collection slug (`base`, `base-2`, …)."""
    slug = base or "collection"
    suffix = 2
    while True:
        row = conn.execute("SELECT id FROM collections WHERE slug = ?", (slug,)).fetchone()
        if row is None or (exclude_id is not None and int(str(row["id"])) == exclude_id):
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def create_collection(
    owner_user_id: int | None,
    name: str,
    description: str = "",
    conn: sqlite3.Connection | None = None,
) -> JsonDict:
    """Create a user collection and return it."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("name required")
    clean_desc = description.strip()
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        token = secrets.token_urlsafe(16)
        cur = _conn.execute(
            """
            INSERT INTO collections (name, description, owner_user_id, share_token)
            VALUES (?, ?, ?, ?)
            """,
            (
                clean_name[:MAX_NAME_LENGTH],
                clean_desc[:MAX_DESCRIPTION_LENGTH],
                owner_user_id,
                token,
            ),
        )
        assert cur.lastrowid is not None
        collection_id = int(cur.lastrowid)
        slug = _unique_collection_slug(_conn, slugify(clean_name), exclude_id=collection_id)
        _conn.execute("UPDATE collections SET slug = ? WHERE id = ?", (slug, collection_id))
        row = _conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        return dict(row) if row else {}


def get_collection_by_id(
    collection_id: int, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Return a collection by ID, or None."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        return dict(row) if row else None


def get_collection_by_slug(slug: str, conn: sqlite3.Connection | None = None) -> JsonDict | None:
    """Return a collection by slug, or None."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT * FROM collections WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None


def get_collection_by_token(
    share_token: str, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Return a collection by its share token, or None."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT * FROM collections WHERE share_token = ?", (share_token,)
        ).fetchone()
        return dict(row) if row else None


def _with_counts(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[JsonDict]:
    """Attach `recipe_count` to raw collection rows."""
    results: list[JsonDict] = []
    for row in rows:
        d: JsonDict = dict(row)
        count = conn.execute(
            "SELECT COUNT(*) FROM collection_recipes WHERE collection_id = ?",
            (int(str(d["id"])),),
        ).fetchone()[0]
        d["recipe_count"] = int(count)
        results.append(d)
    return results


def list_site_collections(conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    """Return all site collections, featured first then newest."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT * FROM collections WHERE is_site = 1 ORDER BY is_featured DESC, created_at DESC"
        ).fetchall()
        return _attach_covers(_conn, _with_counts(_conn, list(rows)))


def list_featured_collections(conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    """Return site collections flagged for the homepage carousel."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT * FROM collections WHERE is_site = 1 AND is_featured = 1 "
            "ORDER BY created_at DESC"
        ).fetchall()
        return _attach_covers(_conn, _with_counts(_conn, list(rows)))


def list_user_collections(
    owner_user_id: int, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return the private collections of a user."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT * FROM collections WHERE owner_user_id = ? AND is_site = 0 "
            "ORDER BY created_at DESC",
            (owner_user_id,),
        ).fetchall()
        return _attach_covers(_conn, _with_counts(_conn, list(rows)))


def list_all_collections(conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    """Return every collection (admin view), with owner name and counts."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            """
            SELECT c.*, u.name AS owner_name, u.email AS owner_email
            FROM collections c
            LEFT JOIN users u ON u.id = c.owner_user_id
            ORDER BY c.is_site DESC, c.created_at DESC
            """
        ).fetchall()
        return _with_counts(_conn, list(rows))


def _attach_covers(conn: sqlite3.Connection, collections: list[JsonDict]) -> list[JsonDict]:
    """Attach a cover (first recipe's slug + image) to each collection.

    V1 derives the cover from the first added recipe — no user upload.
    """
    for collection in collections:
        row = conn.execute(
            """
            SELECT r.slug AS recipe_slug, ri.filename AS image
            FROM collection_recipes cr
            JOIN recipes r ON r.id = cr.recipe_id
            LEFT JOIN recipe_images ri ON ri.recipe_id = r.id
            WHERE cr.collection_id = ?
              AND (r.source_missing = 0 OR r.force_visible = 1)
            ORDER BY cr.added_at ASC, ri.sort_order ASC
            LIMIT 1
            """,
            (int(str(collection["id"])),),
        ).fetchone()
        collection["cover_recipe_slug"] = row["recipe_slug"] if row else None
        collection["cover_image"] = row["image"] if row and row["image"] else None
    return collections


def update_collection(
    collection_id: int,
    name: str,
    description: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Rename / re-describe a collection (slug follows the name)."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("name required")
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if row is None:
            return False
        slug = _unique_collection_slug(_conn, slugify(clean_name), exclude_id=collection_id)
        _conn.execute(
            "UPDATE collections SET name = ?, description = ?, slug = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (
                clean_name[:MAX_NAME_LENGTH],
                description.strip()[:MAX_DESCRIPTION_LENGTH],
                slug,
                collection_id,
            ),
        )
        return True


def delete_collection(collection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a collection and its memberships."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        return cur.rowcount > 0


def add_recipe_to_collection(
    collection_id: int, recipe_id: int, conn: sqlite3.Connection | None = None
) -> bool:
    """Add a recipe to a collection. Returns False if either is missing."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        coll = _conn.execute("SELECT id FROM collections WHERE id = ?", (collection_id,)).fetchone()
        rec = _conn.execute(
            "SELECT id FROM recipes WHERE id = ? AND (source_missing = 0 OR force_visible = 1)",
            (recipe_id,),
        ).fetchone()
        if coll is None or rec is None:
            return False
        _conn.execute(
            "INSERT OR IGNORE INTO collection_recipes (collection_id, recipe_id) VALUES (?, ?)",
            (collection_id, recipe_id),
        )
        _conn.execute(
            "UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (collection_id,),
        )
        return True


def remove_recipe_from_collection(
    collection_id: int, recipe_id: int, conn: sqlite3.Connection | None = None
) -> None:
    """Remove a recipe from a collection (no-op if absent)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "DELETE FROM collection_recipes WHERE collection_id = ? AND recipe_id = ?",
            (collection_id, recipe_id),
        )
        _conn.execute(
            "UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (collection_id,),
        )


def is_recipe_in_collection(
    collection_id: int, recipe_id: int, conn: sqlite3.Connection | None = None
) -> bool:
    """Return True if the recipe belongs to the collection."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT 1 FROM collection_recipes WHERE collection_id = ? AND recipe_id = ?",
            (collection_id, recipe_id),
        ).fetchone()
        return row is not None


def get_collections_for_recipe(
    owner_user_id: int, recipe_id: int, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return the user's collections with an `in_collection` flag for a recipe."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT * FROM collections WHERE owner_user_id = ? ORDER BY created_at DESC",
            (owner_user_id,),
        ).fetchall()
        results: list[JsonDict] = []
        for row in rows:
            d: JsonDict = dict(row)
            d["in_collection"] = is_recipe_in_collection(int(str(d["id"])), recipe_id, conn=_conn)
            results.append(d)
        return results


def get_collection_recipe_ids(
    collection_id: int, conn: sqlite3.Connection | None = None
) -> list[int]:
    """Return the recipe IDs of a collection, in added order."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT recipe_id FROM collection_recipes WHERE collection_id = ? ORDER BY added_at ASC",
            (collection_id,),
        ).fetchall()
        return [int(str(row["recipe_id"])) for row in rows]


def get_collection_recipes(
    collection_id: int, lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return the visible recipes of a collection, in added order."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT recipe_id FROM collection_recipes WHERE collection_id = ? ORDER BY added_at ASC",
            (collection_id,),
        ).fetchall()
        results: list[JsonDict] = []
        for row in rows:
            recipe = get_recipe(int(str(row["recipe_id"])), lang=lang, conn=_conn)
            if recipe is not None:
                results.append(recipe)
        return results


def promote_to_site(collection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Upgrade a user collection to a site collection (admin only)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "UPDATE collections SET is_site = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (collection_id,),
        )
        return cur.rowcount > 0


def demote_to_user(collection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Revert a site collection to a private user collection (admin only)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "UPDATE collections SET is_site = 0, is_featured = 0, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (collection_id,),
        )
        return cur.rowcount > 0


def set_featured(
    collection_id: int, featured: bool, conn: sqlite3.Connection | None = None
) -> bool:
    """Flag a site collection for the homepage carousel (admin only)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT is_site FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        if row is None or not int(str(row["is_site"])):
            return False
        _conn.execute(
            "UPDATE collections SET is_featured = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if featured else 0, collection_id),
        )
        return True


def can_view(collection: JsonDict, user_id: int | None, is_admin: bool) -> bool:
    """Return True if the user may view the collection page by ID/slug."""
    if int(str(collection.get("is_site", 0))):
        return True
    if is_admin:
        return True
    owner = collection.get("owner_user_id")
    return owner is not None and user_id is not None and int(str(owner)) == user_id


def can_edit(collection: JsonDict, user_id: int | None, is_admin: bool) -> bool:
    """Return True if the user may modify the collection or its recipes."""
    if int(str(collection.get("is_site", 0))):
        return is_admin
    if is_admin:
        return True
    owner = collection.get("owner_user_id")
    return owner is not None and user_id is not None and int(str(owner)) == user_id
