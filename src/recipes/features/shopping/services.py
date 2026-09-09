"""Shopping feature — database service for shopping list operations."""

import secrets
import sqlite3
from contextlib import nullcontext

from recipes.shared.db import get_conn
from recipes.shared.models import JsonDict

# ---------------------------------------------------------------------------
# Shopping departments
# ---------------------------------------------------------------------------


def get_shopping_departments(
    lang: str = "fr", conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return all shopping departments ordered by sort_order."""
    col = "display_name_en" if lang == "en" else "display_name_fr"
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            f"""
            SELECT id, name, {col} AS display_name, emoji, sort_order
            FROM shopping_departments
            ORDER BY sort_order, id
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_department_by_name(name: str, conn: sqlite3.Connection | None = None) -> JsonDict | None:
    """Return a department by its technical name."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, name, display_name_fr, display_name_en, emoji, sort_order "
            "FROM shopping_departments WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row) if row else None


def get_department_by_id(dept_id: int, conn: sqlite3.Connection | None = None) -> JsonDict | None:
    """Return a department by its ID."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, name, display_name_fr, display_name_en, emoji, sort_order "
            "FROM shopping_departments WHERE id = ?",
            (dept_id,),
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Shopping lists
# ---------------------------------------------------------------------------


def create_shopping_list(
    name: str,
    user_id: int | None = None,
    share_token: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> JsonDict:
    """Create a new shopping list. Returns the created list."""
    token = share_token or secrets.token_urlsafe(16)
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            """
            INSERT INTO shopping_lists (share_token, name, user_id)
            VALUES (?, ?, ?)
            """,
            (token, name, user_id),
        )
        assert cur.lastrowid is not None
        list_id = int(cur.lastrowid)
        row = _conn.execute("SELECT * FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
        return dict(row) if row else {}


def get_shopping_list_by_id(
    list_id: int, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Return a shopping list by ID."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT * FROM shopping_lists WHERE id = ?", (list_id,)).fetchone()
        return dict(row) if row else None


def get_shopping_list_by_token(
    share_token: str, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Return a shopping list by its share token."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT * FROM shopping_lists WHERE share_token = ?", (share_token,)
        ).fetchone()
        return dict(row) if row else None


def get_user_shopping_lists(
    user_id: int | None, include_done: bool = True, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return shopping lists for a user (or all if user_id is None for anon).

    For logged-in users, returns their lists.
    For anonymous (user_id=None), returns lists without a user_id.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        if user_id is not None:
            query = "SELECT * FROM shopping_lists WHERE user_id = ?"
            params: tuple[object, ...] = (user_id,)
        else:
            query = "SELECT * FROM shopping_lists WHERE user_id IS NULL"
            params = ()

        if not include_done:
            query += " AND all_done_at IS NULL"

        query += " ORDER BY updated_at DESC"
        rows = _conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_shopping_lists_by_ids(
    list_ids: list[int], conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return shopping lists for the given IDs."""
    if not list_ids:
        return []
    placeholders = ",".join("?" for _ in list_ids)
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            f"SELECT * FROM shopping_lists WHERE id IN ({placeholders}) ORDER BY updated_at DESC",
            list_ids,
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_shopping_lists(
    include_done: bool = True, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return all shopping lists (for admin), with user name/email."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        query = """
            SELECT sl.*, u.name AS user_name, u.email AS user_email
            FROM shopping_lists sl
            LEFT JOIN users u ON sl.user_id = u.id
        """
        if not include_done:
            query += " WHERE sl.all_done_at IS NULL"
        query += " ORDER BY sl.updated_at DESC"
        rows = _conn.execute(query).fetchall()
        return [dict(r) for r in rows]


def delete_shopping_list(list_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a shopping list and all its items."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute("DELETE FROM shopping_lists WHERE id = ?", (list_id,))
        return cur.rowcount > 0


def rename_shopping_list(list_id: int, name: str, conn: sqlite3.Connection | None = None) -> None:
    """Rename a shopping list."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            """
            UPDATE shopping_lists SET name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (name, list_id),
        )


def touch_shopping_list(list_id: int, conn: sqlite3.Connection | None = None) -> None:
    """Update the updated_at timestamp."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (list_id,),
        )


def _check_shopping_list_completion(conn: sqlite3.Connection, list_id: int) -> None:
    """Set all_done_at if all items are done, clear it otherwise. Internal version using existing connection."""
    total = conn.execute(
        "SELECT COUNT(*) AS cnt FROM shopping_list_items WHERE list_id = ?",
        (list_id,),
    ).fetchone()
    if not total or total["cnt"] == 0:
        conn.execute(
            """
            UPDATE shopping_lists SET all_done_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (list_id,),
        )
        return

    done = conn.execute(
        """
        SELECT COUNT(*) AS cnt FROM shopping_list_items
        WHERE list_id = ? AND is_done = 1
        """,
        (list_id,),
    ).fetchone()
    if done and done["cnt"] == total["cnt"]:
        conn.execute(
            """
            UPDATE shopping_lists SET all_done_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND all_done_at IS NULL
            """,
            (list_id,),
        )
    else:
        conn.execute(
            """
            UPDATE shopping_lists SET all_done_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (list_id,),
        )


def check_shopping_list_completion(list_id: int, conn: sqlite3.Connection | None = None) -> None:
    """Set all_done_at if all items are done, clear it otherwise."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _check_shopping_list_completion(_conn, list_id)


# ---------------------------------------------------------------------------
# Shopping list items
# ---------------------------------------------------------------------------


def add_shopping_list_item(
    list_id: int,
    department_id: int,
    text: str,
    quantity: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> JsonDict:
    """Add an item to a shopping list."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        max_order = _conn.execute(
            """
            SELECT COALESCE(MAX(sort_order), 0) AS mx
            FROM shopping_list_items
            WHERE list_id = ? AND department_id = ?
            """,
            (list_id, department_id),
        ).fetchone()
        next_order = (max_order["mx"] if max_order else 0) + 1

        cur = _conn.execute(
            """
            INSERT INTO shopping_list_items
                (list_id, department_id, text, quantity, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (list_id, department_id, text, quantity, next_order),
        )
        _conn.execute(
            "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (list_id,),
        )
        assert cur.lastrowid is not None
        item_id = int(cur.lastrowid)
        row = _conn.execute("SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else {}


def get_shopping_list_items(
    list_id: int, lang: str = "fr", conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return all items for a shopping list, grouped by department."""
    dept_col = "display_name_en" if lang == "en" else "display_name_fr"
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            f"""
            SELECT
                i.id, i.list_id, i.department_id, i.text, i.quantity,
                i.is_done, i.sort_order,
                d.name AS department_name,
                d.{dept_col} AS department_display_name,
                d.emoji AS department_emoji
            FROM shopping_list_items i
            JOIN shopping_departments d ON d.id = i.department_id
            WHERE i.list_id = ?
            ORDER BY d.sort_order, d.id, i.sort_order, i.id
            """,
            (list_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def toggle_shopping_list_item(
    item_id: int, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Toggle the is_done flag on an item. Returns updated item."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        item = _conn.execute(
            "SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return None
        new_done = 0 if item["is_done"] else 1
        _conn.execute(
            "UPDATE shopping_list_items SET is_done = ? WHERE id = ?",
            (new_done, item_id),
        )
        list_id = int(item["list_id"])
        _conn.execute(
            "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (list_id,),
        )
        _check_shopping_list_completion(_conn, list_id)
        row = _conn.execute("SELECT * FROM shopping_list_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def remove_shopping_list_item(item_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Remove an item from a shopping list."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        item = _conn.execute(
            "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return False
        list_id = int(item["list_id"])
        _conn.execute("DELETE FROM shopping_list_items WHERE id = ?", (item_id,))
        _conn.execute(
            "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (list_id,),
        )
        _check_shopping_list_completion(_conn, list_id)
        return True


def update_shopping_list_item(
    item_id: int,
    text: str,
    quantity: str | None = None,
    department_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Update an item's text, quantity, and/or department."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        item = _conn.execute(
            "SELECT list_id FROM shopping_list_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return False
        list_id = int(item["list_id"])
        if department_id is not None:
            _conn.execute(
                """
                UPDATE shopping_list_items
                SET text = ?, quantity = ?, department_id = ?
                WHERE id = ?
                """,
                (text, quantity, department_id, item_id),
            )
        else:
            _conn.execute(
                """
                UPDATE shopping_list_items SET text = ?, quantity = ?
                WHERE id = ?
                """,
                (text, quantity, item_id),
            )
        _conn.execute(
            "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (list_id,),
        )
        return True


# ---------------------------------------------------------------------------
# Shopping list cleanup
# ---------------------------------------------------------------------------


def cleanup_expired_shopping_lists(
    retention_days: int = 7, conn: sqlite3.Connection | None = None
) -> int:
    """Delete expired shopping lists.

    - Anonymous lists (user_id IS NULL) older than retention_days.
    - Logged-in user lists where all_done_at is set and older than retention_days.

    Returns the number of lists deleted.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            """
            DELETE FROM shopping_lists
            WHERE
                (user_id IS NULL
                 AND created_at < datetime('now', '-' || ? || ' days'))
                OR
                (all_done_at IS NOT NULL
                 AND all_done_at < datetime('now', '-' || ? || ' days'))
            """,
            (retention_days, retention_days),
        )
        return cur.rowcount
