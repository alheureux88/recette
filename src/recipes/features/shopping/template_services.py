"""Shopping templates — database service (logged-in users only)."""

import sqlite3
from contextlib import nullcontext

from recipes.shared.db import get_conn
from recipes.shared.models import JsonDict


def create_template(name: str, user_id: int, conn: sqlite3.Connection | None = None) -> JsonDict:
    """Create a new shopping template for a user."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "INSERT INTO shopping_templates (user_id, name) VALUES (?, ?)",
            (user_id, name),
        )
        assert cur.lastrowid is not None
        template_id = int(cur.lastrowid)
        row = _conn.execute(
            "SELECT * FROM shopping_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_template_by_id(template_id: int, conn: sqlite3.Connection | None = None) -> JsonDict | None:
    """Return a template by ID."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT * FROM shopping_templates WHERE id = ?", (template_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_templates(user_id: int, conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    """Return all templates for a user, newest first."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT * FROM shopping_templates WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_template(template_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a template and all its items."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute("DELETE FROM shopping_templates WHERE id = ?", (template_id,))
        return cur.rowcount > 0


def rename_template(template_id: int, name: str, conn: sqlite3.Connection | None = None) -> None:
    """Rename a template."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "UPDATE shopping_templates SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, template_id),
        )


def add_template_item(
    template_id: int,
    department_id: int,
    text: str,
    quantity: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> JsonDict:
    """Add an item to a template."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        max_order = _conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS mx FROM shopping_template_items"
            " WHERE template_id = ? AND department_id = ?",
            (template_id, department_id),
        ).fetchone()
        next_order = (max_order["mx"] if max_order else 0) + 1
        cur = _conn.execute(
            "INSERT INTO shopping_template_items"
            " (template_id, department_id, text, quantity, sort_order)"
            " VALUES (?, ?, ?, ?, ?)",
            (template_id, department_id, text, quantity, next_order),
        )
        _conn.execute(
            "UPDATE shopping_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (template_id,),
        )
        assert cur.lastrowid is not None
        item_id = int(cur.lastrowid)
        row = _conn.execute(
            "SELECT * FROM shopping_template_items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_template_items(
    template_id: int, lang: str = "fr", conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Return all items for a template, ordered by department."""
    dept_col = "display_name_en" if lang == "en" else "display_name_fr"
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            f"""
            SELECT
                i.id, i.template_id, i.department_id, i.text, i.quantity,
                i.sort_order,
                d.name AS department_name,
                d.{dept_col} AS department_display_name,
                d.emoji AS department_emoji
            FROM shopping_template_items i
            JOIN shopping_departments d ON d.id = i.department_id
            WHERE i.template_id = ?
            ORDER BY d.sort_order, d.id, i.sort_order, i.id
            """,
            (template_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def remove_template_item(item_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Remove an item from a template."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        item = _conn.execute(
            "SELECT template_id FROM shopping_template_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return False
        template_id = int(item["template_id"])
        _conn.execute("DELETE FROM shopping_template_items WHERE id = ?", (item_id,))
        _conn.execute(
            "UPDATE shopping_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (template_id,),
        )
        return True


def update_template_item(
    item_id: int,
    text: str,
    quantity: str | None = None,
    department_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Update a template item's text, quantity, and/or department."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        item = _conn.execute(
            "SELECT template_id FROM shopping_template_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not item:
            return False
        if department_id is not None:
            _conn.execute(
                "UPDATE shopping_template_items SET text = ?, quantity = ?,"
                " department_id = ? WHERE id = ?",
                (text, quantity, department_id, item_id),
            )
        else:
            _conn.execute(
                "UPDATE shopping_template_items SET text = ?, quantity = ? WHERE id = ?",
                (text, quantity, item_id),
            )
        _conn.execute(
            "UPDATE shopping_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (int(item["template_id"]),),
        )
        return True


def seed_list_from_template(
    list_id: int, template_id: int, conn: sqlite3.Connection | None = None
) -> int:
    """Copy all template items into a shopping list. Returns items copied."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT department_id, text, quantity, sort_order"
            " FROM shopping_template_items WHERE template_id = ?"
            " ORDER BY sort_order, id",
            (template_id,),
        ).fetchall()
        for row in rows:
            _conn.execute(
                "INSERT INTO shopping_list_items"
                " (list_id, department_id, text, quantity, sort_order)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    list_id,
                    int(row["department_id"]),
                    str(row["text"]),
                    row["quantity"],
                    int(row["sort_order"]),
                ),
            )
        if rows:
            _conn.execute(
                "UPDATE shopping_lists SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (list_id,),
            )
        return len(rows)
