"""0003 - Recipe ratings (1-5 stars per user).

One row per (user, recipe): re-voting overwrites the previous rating
(UPSERT in services). Averages per recipe are computed on the fly
(AVG + COUNT), no denormalized column to keep in sync.

Migration files must stay pure ASCII: yoyo reads them with the locale
default encoding, which breaks non-ASCII bytes on some platforms.
"""

from yoyo import step

__depends__ = {"0002_seed_reference_data"}

steps = [
    step(
        """CREATE TABLE IF NOT EXISTS recipe_ratings (
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            rating       INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, recipe_id)
        )""",
        "DROP TABLE IF EXISTS recipe_ratings",
    ),
    step(
        """CREATE INDEX IF NOT EXISTS idx_recipe_ratings_recipe
            ON recipe_ratings(recipe_id)""",
        "DROP INDEX IF EXISTS idx_recipe_ratings_recipe",
    ),
    step(
        """CREATE INDEX IF NOT EXISTS idx_recipe_ratings_user
            ON recipe_ratings(user_id, rating DESC)""",
        "DROP INDEX IF EXISTS idx_recipe_ratings_user",
    ),
]
