"""Preferences feature — per-user settings stored as an extensible JSON blob.

Storage model (no schema migration needed for new settings):
  - One row per user in `user_preferences(user_id, prefs, updated_at)`.
  - `prefs` is a JSON object; each setting is a key with a default defined
    in `DEFAULT_PREFERENCES`.
  - Adding a setting = new key + default + validation below. Unknown keys
    found in stored JSON are preserved on read but ignored by validators,
    so old code tolerates data written by newer code.
"""

import json
import sqlite3
from contextlib import nullcontext
from typing import Any

from recipes.shared.db import get_conn
from recipes.shared.i18n import SUPPORTED_LANGUAGES
from recipes.shared.models import JsonDict

VALID_UNITS: tuple[str, ...] = ("original", "metric", "imperial")

VALID_THEMES: tuple[str, ...] = ("light", "dark", "system")

PRINT_KEYS: tuple[str, ...] = (
    "print_images",
    "print_tags",
    "print_description",
    "print_links",
    "print_step_ingredients",
)

DEFAULT_PREFERENCES: JsonDict = {
    "language": "fr",
    "units": "original",
    "theme": "system",
    "print_images": False,
    "print_tags": False,
    "print_description": False,
    "print_links": False,
    "print_step_ingredients": False,
    "show_step_ingredients": True,
    "department_order": [],
}


def _stored_prefs(user_id: int, conn: sqlite3.Connection) -> JsonDict:
    """Raw stored prefs dict (empty if the user has no row yet)."""
    row = conn.execute(
        "SELECT prefs FROM user_preferences WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {}
    try:
        raw = json.loads(str(row["prefs"] or "{}"))
    except (ValueError, TypeError):
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def get_preferences(user_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Return the user's preferences merged over defaults.

    Always returns every known key: missing or invalid stored values fall
    back to `DEFAULT_PREFERENCES` so callers never need `dict.get`.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        stored = _stored_prefs(user_id, _conn)
    merged: dict[str, Any] = dict(DEFAULT_PREFERENCES)
    language = stored.get("language")
    if isinstance(language, str) and language in SUPPORTED_LANGUAGES:
        merged["language"] = language
    units = stored.get("units")
    if isinstance(units, str) and units in VALID_UNITS:
        merged["units"] = units
    theme = stored.get("theme")
    if isinstance(theme, str) and theme in VALID_THEMES:
        merged["theme"] = theme
    for key in PRINT_KEYS:
        value = stored.get(key)
        if isinstance(value, bool):
            merged[key] = value
    show_step_ingredients = stored.get("show_step_ingredients")
    if isinstance(show_step_ingredients, bool):
        merged["show_step_ingredients"] = show_step_ingredients
    order = stored.get("department_order")
    if isinstance(order, list):
        merged["department_order"] = [str(name) for name in order if isinstance(name, str)]
    return merged


def save_preferences(
    user_id: int, patch: JsonDict, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Validate `patch` and persist it, returning the merged preferences.

    Only known keys are written; unknown keys are ignored (forward
    compatibility). Invalid values are ignored (previous value kept).
    `department_order` keeps only non-empty string names, deduplicated,
    order preserved.
    """
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        stored = _stored_prefs(user_id, _conn)

        language = patch.get("language")
        if isinstance(language, str) and language.strip().lower() in SUPPORTED_LANGUAGES:
            stored["language"] = language.strip().lower()

        units = patch.get("units")
        if isinstance(units, str) and units.strip().lower() in VALID_UNITS:
            stored["units"] = units.strip().lower()

        theme = patch.get("theme")
        if isinstance(theme, str) and theme.strip().lower() in VALID_THEMES:
            stored["theme"] = theme.strip().lower()

        for key in PRINT_KEYS:
            if key in patch:
                stored[key] = _to_bool(patch[key])

        if "show_step_ingredients" in patch:
            stored["show_step_ingredients"] = _to_bool(patch["show_step_ingredients"])

        if "department_order" in patch:
            order = patch["department_order"]
            if isinstance(order, list):
                seen: set[str] = set()
                cleaned: list[str] = []
                for name in order:
                    if not isinstance(name, str):
                        continue
                    candidate = name.strip()
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        cleaned.append(candidate)
                stored["department_order"] = cleaned

        _conn.execute(
            """
            INSERT INTO user_preferences (user_id, prefs, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                prefs=excluded.prefs, updated_at=CURRENT_TIMESTAMP
            """,
            (user_id, json.dumps(stored, ensure_ascii=False)),
        )
        return get_preferences(user_id, conn=_conn)


def get_department_order(user_id: int | None, conn: sqlite3.Connection | None = None) -> list[str]:
    """Return the user's department display order (empty = global default)."""
    if user_id is None:
        return []
    prefs = get_preferences(user_id, conn=conn)
    order = prefs.get("department_order")
    return list(order) if isinstance(order, list) else []


def _to_bool(value: object) -> bool:
    """Lenient boolean coercion for HTML checkboxes and JSON payloads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes", "oui")
    return False
