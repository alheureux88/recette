"""Auth feature — database service for user-related operations."""

import sqlite3
from contextlib import nullcontext

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
