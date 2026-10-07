"""Admin feature — database service for admin-related operations."""

import json
import sqlite3
import time
from contextlib import nullcontext
from typing import Literal, TypeAlias

from recipes.shared.db import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_ACCOUNT_NAME,
    get_conn,
    get_setting,
    is_default_account_visible,
)
from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext
from recipes.shared.models import JsonDict

DropboxConnectionStatus: TypeAlias = Literal["pending", "approved", "rejected"]
DropboxOAuthPurpose: TypeAlias = Literal["add", "propose"]

#: Proposal lifecycle states for `dropbox_connections.status`.
DROPBOX_STATUS_PENDING: DropboxConnectionStatus = "pending"
DROPBOX_STATUS_APPROVED: DropboxConnectionStatus = "approved"
DROPBOX_STATUS_REJECTED: DropboxConnectionStatus = "rejected"

#: Max pending Dropbox proposals per user (anti-spam).
MAX_PENDING_PROPOSALS_PER_USER = 3

#: Lifetime of a Dropbox OAuth flow state (1h, same as Dropbox codes).
DROPBOX_OAUTH_STATE_TTL_SECONDS = 3600

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
    "list_unapproved_dropbox_connections",
    "list_user_dropbox_proposals",
    "count_pending_dropbox_proposals",
    "approve_dropbox_connection",
    "reject_dropbox_connection",
    "save_dropbox_oauth_state",
    "pop_dropbox_oauth_state",
    "set_setting",
    "delete_setting",
    "is_default_account_active",
    "set_default_account_active",
    "set_default_account_visible",
    "get_recipe_provenances",
    "get_superuser_groups",
    "set_superuser_groups",
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
) -> list[JsonDict]:

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
) -> list[JsonDict]:
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


def get_dropbox_connections(conn: sqlite3.Connection | None = None) -> list[JsonDict]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT id, name, folder, file_filter, active, visible, "
            "proposed_by_user_id, status, created_at "
            "FROM dropbox_connections ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def add_dropbox_connection(
    name: str,
    refresh_token: str,
    folder: str = "",
    file_filter: str = "",
    conn: sqlite3.Connection | None = None,
    *,
    active: bool = True,
    visible: bool = True,
    proposed_by_user_id: int | None = None,
    status: DropboxConnectionStatus = DROPBOX_STATUS_APPROVED,
) -> int | None:
    """Insert a new Dropbox connection. Returns None if name already exists."""
    if status not in (DROPBOX_STATUS_PENDING, DROPBOX_STATUS_APPROVED, DROPBOX_STATUS_REJECTED):
        raise ValueError(f"Unknown Dropbox connection status: {status!r}")
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        existing = _conn.execute(
            "SELECT id FROM dropbox_connections WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            return None
        cur = _conn.execute(
            """
            INSERT INTO dropbox_connections
                (name, refresh_token, folder, file_filter,
                 active, visible, proposed_by_user_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                refresh_token,
                folder,
                file_filter,
                1 if active else 0,
                1 if visible else 0,
                proposed_by_user_id,
                status,
            ),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def list_unapproved_dropbox_connections(
    conn: sqlite3.Connection | None = None,
) -> list[JsonDict]:
    """Pending + rejected proposals, with proposer identity.

    The proposer is NULL when the connection was created by an owner
    directly (no proposal) or when the authoring user was deleted.
    Pending rows come first so owners see what needs a decision.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            """
            SELECT c.id, c.name, c.folder, c.file_filter, c.status, c.created_at,
                   u.name AS proposer_name, u.email AS proposer_email
            FROM dropbox_connections c
            LEFT JOIN users u ON u.id = c.proposed_by_user_id
            WHERE c.status != 'approved'
            ORDER BY c.status ASC, c.created_at
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_user_dropbox_proposals(
    user_id: int, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    """Proposals authored by a user (any status, newest first)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT id, name, folder, file_filter, active, visible, status, created_at "
            "FROM dropbox_connections WHERE proposed_by_user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_pending_dropbox_proposals(user_id: int, conn: sqlite3.Connection | None = None) -> int:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT COUNT(*) AS n FROM dropbox_connections "
            "WHERE proposed_by_user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
        assert row is not None
        return int(row["n"])


def approve_dropbox_connection(connection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Approve a pending proposal: approved + active + visible. False if missing."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "UPDATE dropbox_connections "
            "SET status = 'approved', active = 1, visible = 1 "
            "WHERE id = ? AND status = 'pending'",
            (connection_id,),
        )
        return cur.rowcount > 0


def reject_dropbox_connection(connection_id: int, conn: sqlite3.Connection | None = None) -> bool:
    """Reject a pending proposal (row kept so the proposer sees the outcome)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute(
            "UPDATE dropbox_connections SET status = 'rejected' "
            "WHERE id = ? AND status = 'pending'",
            (connection_id,),
        )
        return cur.rowcount > 0


def save_dropbox_oauth_state(
    state: str,
    user_sub: str,
    purpose: DropboxOAuthPurpose,
    verifier: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Remember a Dropbox OAuth flow (per-state, so concurrent flows coexist).

    Replaces the historical single global `dropbox_oauth_state` setting :
    each `state` maps to its author (`sub`) and purpose (`add` for owners,
    `propose` for content-admins). Expired entries are pruned on save.
    """
    delete_setting("dropbox_oauth_state", conn=conn)
    delete_setting("dropbox_oauth_verifier", conn=conn)
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        raw = get_setting("dropbox_oauth_states", "{}", conn=_conn)
        try:
            states = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            states = {}
        if not isinstance(states, dict):
            states = {}
        now = time.time()
        states = {
            key: entry
            for key, entry in states.items()
            if isinstance(entry, dict)
            and now - float(entry.get("created_at", 0)) < DROPBOX_OAUTH_STATE_TTL_SECONDS
        }
        states[state] = {
            "sub": user_sub,
            "purpose": purpose,
            "verifier": verifier,
            "created_at": now,
        }
        set_setting("dropbox_oauth_states", json.dumps(states), conn=_conn)


def pop_dropbox_oauth_state(
    state: str | None, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    """Consume a Dropbox OAuth flow state (single-use). None if unknown."""
    if not state:
        return None
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        raw = get_setting("dropbox_oauth_states", "{}", conn=_conn)
        try:
            states = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return None
        if not isinstance(states, dict):
            return None
        entry = states.pop(state, None)
        set_setting("dropbox_oauth_states", json.dumps(states), conn=_conn)
        if not isinstance(entry, dict):
            return None
        if time.time() - float(entry.get("created_at", 0)) >= DROPBOX_OAUTH_STATE_TTL_SECONDS:
            return None
        return dict(entry)


def get_dropbox_connection_credentials(
    connection_id: int, conn: sqlite3.Connection | None = None
) -> JsonDict | None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, name, refresh_token, folder, file_filter, status "
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
# Super-users (groupes OIDC avec droits d'administration du contenu)
# ---------------------------------------------------------------------------


def _normalize_group_list(raw: str | list[str]) -> list[str]:
    """Normalise une liste de groupes OIDC (CSV ou liste) : trim + déduplique."""
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def get_superuser_groups(conn: sqlite3.Connection | None = None) -> list[str]:
    """Groupes OIDC super-users configurés en DB (hors valeur env)."""
    return _normalize_group_list(get_setting("superuser_groups", "", conn=conn))


def set_superuser_groups(
    groups: str | list[str], conn: sqlite3.Connection | None = None
) -> list[str]:
    """Remplace la liste des groupes OIDC super-users (stockée en CSV)."""
    normalized = _normalize_group_list(groups)
    set_setting("superuser_groups", ",".join(normalized), conn=conn)
    return normalized


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


def get_recipe_provenances(conn: sqlite3.Connection | None = None) -> list[JsonDict]:
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
