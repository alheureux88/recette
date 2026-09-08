"""Push feature — database service for push subscription operations."""

import json
import sqlite3
from contextlib import nullcontext
from typing import Any

from recipes.shared.db import get_conn


def save_push_subscription(
    user_id: int | None,
    endpoint: str,
    subscription: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> int:
    """Save or update a push subscription.

    Args:
        user_id: Optional user ID (null for anonymous users).
        endpoint: The push endpoint URL (unique identifier).
        subscription: The full subscription object from the browser.

    Returns:
        The subscription ID.
    """
    subscription_json = json.dumps(subscription, ensure_ascii=False)
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        existing = _conn.execute(
            "SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        ).fetchone()
        if existing:
            _conn.execute(
                """
                UPDATE push_subscriptions
                SET subscription = ?, user_id = ?
                WHERE endpoint = ?
                """,
                (subscription_json, user_id, endpoint),
            )
            return int(existing["id"])
        else:
            cur = _conn.execute(
                """
                INSERT INTO push_subscriptions (user_id, endpoint, subscription)
                VALUES (?, ?, ?)
                """,
                (user_id, endpoint, subscription_json),
            )
            assert cur.lastrowid is not None
            return int(cur.lastrowid)


def get_push_subscription(
    endpoint: str, conn: sqlite3.Connection | None = None
) -> dict[str, object] | None:
    """Get a push subscription by endpoint."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT id, user_id, endpoint, subscription FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["subscription"] = json.loads(str(result["subscription"]))
        except (json.JSONDecodeError, TypeError):
            return None
        return result


def delete_push_subscription(endpoint: str, conn: sqlite3.Connection | None = None) -> bool:
    """Delete a push subscription by endpoint."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        cur = _conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        return cur.rowcount > 0


def get_all_push_subscriptions(conn: sqlite3.Connection | None = None) -> list[dict[str, object]]:
    """Get all push subscriptions (for cleanup/testing)."""
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT id, user_id, endpoint, subscription FROM push_subscriptions"
        ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            try:
                result["subscription"] = json.loads(str(result["subscription"]))
            except (json.JSONDecodeError, TypeError):
                continue
            results.append(result)
        return results
