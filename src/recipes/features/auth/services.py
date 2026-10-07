"""Auth feature — database service for user-related operations."""

import sqlite3
from contextlib import nullcontext
from typing import Any

from recipes.shared.db import get_conn


def get_or_create_user(
    subject: str, email: str | None, name: str | None, conn: sqlite3.Connection | None = None
) -> int:
    """Retourne l'id usager pour `subject`, en rafraîchissant email/nom.

    Met à jour la ligne existante si les claims OIDC ont changé (ex.
    Authelia renvoie désormais un autre `name`) : sans ça la table
    `users` garde éternellement les valeurs du premier login.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, email, name FROM users WHERE subject = ?", (subject,)
        ).fetchone()
        if row:
            user_id = int(row["id"])
            if email != row["email"] or name != row["name"]:
                _conn.execute(
                    "UPDATE users SET email = ?, name = ? WHERE id = ?",
                    (email, name, user_id),
                )
            return user_id
        cur = _conn.execute(
            "INSERT INTO users (subject, email, name) VALUES (?, ?, ?)",
            (subject, email, name),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)


def resolve_user_id(
    session_user: dict[str, Any] | None, conn: sqlite3.Connection | None = None
) -> int | None:
    """Return the numeric user ID for a session, self-healing a stale session.

    The session may hold an ID with no matching `users` row (e.g. the
    database was recreated after login) : resolve via `subject` (stable
    OIDC identifier) so writes never use a dangling foreign key.
    """
    if session_user is None:
        return None
    subject = session_user.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raw_id = session_user.get("id")
        try:
            return int(str(raw_id))
        except (TypeError, ValueError):
            return None
    email = session_user.get("email")
    name = session_user.get("name")
    return get_or_create_user(
        subject=subject.strip(),
        email=str(email) if isinstance(email, str) else None,
        name=str(name) if isinstance(name, str) else None,
        conn=conn,
    )
