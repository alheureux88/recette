"""Recipes feature — database service for recipe-related operations."""

import sqlite3
from contextlib import nullcontext

from recipes.shared.db import (
    _load_translation,
    _localize_tag,
    _resolve_tag_ids,
    get_conn,
    get_recipe_images,
)
from recipes.shared.i18n import DEFAULT_LANGUAGE, gettext
from recipes.shared.models import JsonDict


def is_favorite(user_id: int, recipe_id: int, conn: sqlite3.Connection | None = None) -> bool:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        row = _conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, recipe_id),
        ).fetchone()
        return row is not None


def add_favorite(user_id: int, recipe_id: int, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id, recipe_id) VALUES (?, ?)",
            (user_id, recipe_id),
        )


def remove_favorite(user_id: int, recipe_id: int, conn: sqlite3.Connection | None = None) -> None:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        _conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, recipe_id),
        )


def get_user_favorite_ids(user_id: int, conn: sqlite3.Connection | None = None) -> set[int]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            "SELECT recipe_id FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {int(r["recipe_id"]) for r in rows}


def get_favorite_recipes(
    user_id: int, lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            """
            SELECT r.*, c.name AS category_name,
                   c.display_name_fr AS category_display_name_fr,
                   c.display_name_en AS category_display_name_en
            FROM favorites f
            JOIN recipes r ON f.recipe_id = r.id
            LEFT JOIN categories c ON r.category_id = c.id
            WHERE f.user_id = ?
              AND (r.source_missing = 0 OR r.force_visible = 1)
            ORDER BY f.created_at DESC
            """,
            (user_id,),
        ).fetchall()

        results = []
        cat_col = "category_display_name_en" if lang == "en" else "category_display_name_fr"
        for row in rows:
            translation = _load_translation(_conn, int(row["id"]), lang)
            d: JsonDict = dict(row)
            d.update(translation)
            d["title"] = translation["title"]
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d[cat_col],
                }
            else:
                d["category"] = None

            tag_rows = _conn.execute(
                """
                SELECT t.id, t.name,
                       t.display_name_fr, t.display_name_en,
                       tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name_fr
                """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [_localize_tag(tr, lang) | {"family": tr["family"]} for tr in tag_rows]
            d["images"] = get_recipe_images(int(row["id"]), conn=_conn)
            results.append(d)

        return results


def get_all_recipes_admin(
    filter: str = "", lang: str = DEFAULT_LANGUAGE, conn: sqlite3.Connection | None = None
) -> list[JsonDict]:

    where = ""
    if filter == "no_tags":
        where = "WHERE NOT EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = r.id)"
    elif filter == "no_category":
        where = "WHERE r.category_id IS NULL"
    elif filter == "no_tags_no_category":
        where = "WHERE r.category_id IS NULL AND NOT EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = r.id)"

    with get_conn() if conn is None else nullcontext(conn) as _conn:
        rows = _conn.execute(
            f"""
            SELECT r.*, c.name AS category_name,
                   c.display_name_fr AS category_display_name_fr,
                   c.display_name_en AS category_display_name_en,
                   pc.id AS provenance_id, pc.name AS provenance_name,
                   (SELECT COUNT(*) FROM favorites f WHERE f.recipe_id = r.id) AS favorite_count
            FROM recipes r
            LEFT JOIN categories c ON r.category_id = c.id
            LEFT JOIN dropbox_connections pc ON pc.id = r.connection_id
            {where}
            ORDER BY r.created_at DESC
            """,
        ).fetchall()

        results = []
        cat_col = "category_display_name_en" if lang == "en" else "category_display_name_fr"
        for row in rows:
            translation = _load_translation(_conn, int(row["id"]), lang)
            d: JsonDict = dict(row)
            d.update(translation)
            d["title"] = translation["title"]
            if d.get("category_name"):
                d["category"] = {
                    "name": d["category_name"],
                    "display_name": d[cat_col],
                }
            else:
                d["category"] = None

            if d.get("provenance_id") is not None:
                d["provenance"] = d["provenance_name"]
            else:
                d["provenance"] = gettext("account.default", lang)

            tag_rows = _conn.execute(
                """
                SELECT t.id, t.name,
                       t.display_name_fr, t.display_name_en,
                       tf.name AS family
                FROM recipe_tags rt
                JOIN tags t ON rt.tag_id = t.id
                JOIN tag_families tf ON t.family_id = tf.id
                WHERE rt.recipe_id = ?
                ORDER BY tf.sort_order, t.display_name_fr
                """,
                (row["id"],),
            ).fetchall()
            d["tags"] = [_localize_tag(tr, lang) | {"family": tr["family"]} for tr in tag_rows]
            d["images"] = get_recipe_images(int(row["id"]), conn=_conn)
            results.append(d)

        return results


def bulk_update_category(
    recipe_ids: list[int], category: str | None, conn: sqlite3.Connection | None = None
) -> int:
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        from recipes.shared.db import _resolve_category

        cat_id = _resolve_category(_conn, category)
        count = 0
        for rid in recipe_ids:
            cur = _conn.execute(
                "UPDATE recipes SET category_id = ?, manually_edited = 1 WHERE id = ?",
                (cat_id, rid),
            )
            count += cur.rowcount
        return count


def bulk_update_tags(
    recipe_ids: list[int],
    add_tags: dict[str, list[str]],
    remove_tags: dict[str, list[str]],
    conn: sqlite3.Connection | None = None,
) -> int:
    if not recipe_ids:
        return 0
    with get_conn() if conn is None else nullcontext(conn) as _conn:
        add_ids = _resolve_tag_ids(_conn, add_tags, create=True)
        remove_ids = _resolve_tag_ids(_conn, remove_tags, create=False)

        if not add_ids and not remove_ids:
            return 0

        count = 0
        for rid in recipe_ids:
            for tag_id in remove_ids:
                _conn.execute(
                    "DELETE FROM recipe_tags WHERE recipe_id = ? AND tag_id = ?",
                    (rid, tag_id),
                )
            for tag_id in add_ids:
                _conn.execute(
                    "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                    (rid, tag_id),
                )
            _conn.execute("UPDATE recipes SET manually_edited = 1 WHERE id = ?", (rid,))
            count += 1
        return count
