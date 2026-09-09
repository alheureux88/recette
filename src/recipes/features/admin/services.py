"""Admin feature — database service for admin-related operations."""

import sqlite3
from contextlib import nullcontext

from recipes.shared.db import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_ACCOUNT_NAME,
    get_conn,
    get_setting,
    is_default_account_visible,
)
from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext

# Re-export for backwards compatibility
__all__ = [
    "DEFAULT_ACCOUNT_ID",
    "DEFAULT_ACCOUNT_NAME",
    "get_setting",
    "is_default_account_visible",
    "blacklist_and_delete_recipe",
    "is_blacklisted",
    "get_blacklisted_files",
    "remove_from_blacklist",
    "record_failed_file",
    "get_failed_files",
    "remove_failed_file",
    "get_dropbox_connections",
    "add_dropbox_connection",
    "get_dropbox_connection_credentials",
    "delete_dropbox_connection",
    "set_dropbox_connection_active",
    "set_dropbox_connection_visible",
    "set_setting",
    "delete_setting",
    "is_default_account_active",
    "set_default_account_active",
    "set_default_account_visible",
    "get_recipe_provenances",
]

# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


def blacklist_and_delete_recipe(
    recipe_id: int, conn: sqlite3.Connection | None = None
) -> str | None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT source_file FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return None
        source_file = str(row["source_file"])

        _conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        _conn.execute("DELETE FROM recipe_images WHERE recipe_id = ?", (recipe_id,))
        _conn.execute(
            """
            INSERT INTO blacklist (path) VALUES (?)
            ON CONFLICT(path) DO UPDATE SET blacklisted_at=CURRENT_TIMESTAMP
            """,
            (source_file,),
        )
        return source_file


def is_blacklisted(path: str, conn: sqlite3.Connection | None = None) -> bool:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT 1 FROM blacklist WHERE path = ?", (path,)).fetchone()
        return row is not None


def get_blacklisted_files(
    conn: sqlite3.Connection | None = None, lang: str = DEFAULT_LANGUAGE
) -> list[dict[str, object]]:

    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT path, blacklisted_at FROM blacklist ORDER BY blacklisted_at DESC"
        ).fetchall()
    conns = _connection_names(conn=_conn)
    result = [dict(r) for r in rows]
    for r in result:
        r["provenance"] = _provenance_from_path(str(r["path"]), conns, lang)
    return result


def remove_from_blacklist(path: str, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute("DELETE FROM blacklist WHERE path = ?", (path,))
        _conn.execute("DELETE FROM processed_files WHERE path = ?", (path,))


# ---------------------------------------------------------------------------
# Failed files
# ---------------------------------------------------------------------------


def record_failed_file(path: str, error: str, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            """
            INSERT INTO failed_files (path, error) VALUES (?, ?)
            ON CONFLICT(path) DO UPDATE SET error=excluded.error, failed_at=CURRENT_TIMESTAMP
            """,
            (path, error),
        )


def get_failed_files(
    conn: sqlite3.Connection | None = None, lang: str = DEFAULT_LANGUAGE
) -> list[dict[str, object]]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT path, error, failed_at FROM failed_files ORDER BY failed_at DESC"
        ).fetchall()
    conns = _connection_names(conn=_conn)
    result = [dict(r) for r in rows]
    for r in result:
        r["provenance"] = _provenance_from_path(str(r["path"]), conns, lang)
    return result


def remove_failed_file(path: str, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute("DELETE FROM failed_files WHERE path = ?", (path,))


# ---------------------------------------------------------------------------
# Dropbox connections
# ---------------------------------------------------------------------------


def get_dropbox_connections(conn: sqlite3.Connection | None = None) -> list[dict[str, object]]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT id, name, folder, file_filter, active, visible, created_at "
            "FROM dropbox_connections ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_dropbox_connection(
    name: str,
    refresh_token: str,
    folder: str = "",
    file_filter: str = "",
    conn: sqlite3.Connection | None = None,
) -> int | None:
    """Insert a new Dropbox connection. Returns None if name already exists."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        existing = _conn.execute(
            "SELECT id FROM dropbox_connections WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            return None
        cur = _conn.execute(
            """
            INSERT INTO dropbox_connections
                (name, refresh_token, folder, file_filter)
            VALUES (?, ?, ?, ?)
            """,
            (name, refresh_token, folder, file_filter),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def get_dropbox_connection_credentials(
    connection_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, object] | None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, name, refresh_token, folder, file_filter "
            "FROM dropbox_connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_dropbox_connection(connection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a connection and all its associated recipes."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id FROM dropbox_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        if not row:
            return False

        _conn.execute("DELETE FROM recipes WHERE connection_id = ?", (connection_id,))
        prefix = f"account:{connection_id}:%"
        _conn.execute("DELETE FROM processed_files WHERE path LIKE ?", (prefix,))
        _conn.execute("DELETE FROM failed_files WHERE path LIKE ?", (prefix,))
        _conn.execute("DELETE FROM blacklist WHERE path LIKE ?", (prefix,))
        _conn.execute("DELETE FROM dropbox_connections WHERE id = ?", (connection_id,))
        return True


def set_dropbox_connection_active(
    connection_id: int, active: bool, conn: sqlite3.Connection | None = None
) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "UPDATE dropbox_connections SET active = ? WHERE id = ?",
            (1 if active else 0, connection_id),
        )


def set_dropbox_connection_visible(
    connection_id: int, visible: bool, conn: sqlite3.Connection | None = None
) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "UPDATE dropbox_connections SET visible = ? WHERE id = ?",
            (1 if visible else 0, connection_id),
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def set_setting(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def delete_setting(key: str, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def is_default_account_active(conn: sqlite3.Connection | None = None) -> bool:
    return get_setting("default_active", "1", conn=conn) != "0"


def set_default_account_active(active: bool, conn: sqlite3.Connection | None = None) -> None:
    set_setting("default_active", "1" if active else "0", conn=conn)


def set_default_account_visible(visible: bool, conn: sqlite3.Connection | None = None) -> None:
    set_setting("default_visible", "1" if visible else "0", conn=conn)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _provenance_from_path(
    path: str, conn_names: dict[int, str], lang: str = DEFAULT_LANGUAGE
) -> str:
    """Get the origin account name from a source path (prefix 'account:<id>:')."""
    if path.startswith("account:"):
        try:
            conn_id = int(path.split(":")[1])
        except (IndexError, ValueError):
            return "?"
        return conn_names.get(conn_id, "?")
    return gettext("account.default", lang)


def _connection_names(conn: sqlite3.Connection | None = None) -> dict[int, str]:
    return {int(str(c["id"])): str(c["name"]) for c in get_dropbox_connections(conn=conn)}


def get_recipe_provenances(conn: sqlite3.Connection | None = None) -> list[dict[str, object]]:
    """List of Dropbox accounts that have at least one visible recipe."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
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
            WHERE connection_id IS NULL
              AND COALESCE((SELECT value FROM app_settings WHERE key = 'default_visible'), '1') != '0'
            HAVING COUNT(id) > 0

            ORDER BY count DESC
            """,
        ).fetchall()
        return [dict(r) for r in rows]
