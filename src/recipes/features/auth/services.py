"""Auth feature — database service for user-related operations."""

import sqlite3
from contextlib import nullcontext

from recipes.shared.db import get_conn


def get_or_create_user(
    subject: str, email: str | None, name: str | None, conn: sqlite3.Connection | None = None
) -> int:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute("SELECT id FROM users WHERE subject = ?", (subject,)).fetchone()
        if row:
            return int(row["id"])
        cur = _conn.execute(
            "INSERT INTO users (subject, email, name) VALUES (?, ?, ?)",
            (subject, email, name),
        )
        assert cur.lastrowid is not None
        return int(cur.lastrowid)
