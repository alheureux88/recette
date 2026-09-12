"""0001 - Initial schema (V1 baseline).

Exact snapshot of the schema produced by `init_db()` before yoyo was
introduced: final `CREATE TABLE` statements (including `slug`,
`source_missing`, `force_visible`, `cover_recipe_id`, `is_hidden` and
`dropbox_hash` columns), indexes, the FTS5 `recipes_fts` table and its
triggers.

Existing databases are being deleted (V1): no data migration is needed,
this migration only ever applies to fresh databases.

Migration files must stay pure ASCII: yoyo reads them with the locale
default encoding, which breaks non-ASCII bytes on some platforms.
"""

from yoyo import step

steps = [
    step(
        """CREATE TABLE IF NOT EXISTS recipes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            slug         TEXT UNIQUE,
            source       TEXT,
            date         TEXT,
            servings     REAL,
            source_url   TEXT,
            dropbox_url  TEXT,
            source_file  TEXT NOT NULL UNIQUE,
            file_hash    TEXT NOT NULL,
            manually_edited INTEGER NOT NULL DEFAULT 0,
            connection_id INTEGER REFERENCES dropbox_connections(id),
            category_id  INTEGER REFERENCES categories(id),
            file_modified_at DATETIME,
            source_missing INTEGER NOT NULL DEFAULT 0,
            force_visible INTEGER NOT NULL DEFAULT 0,
            tagger_version INTEGER,
            tagger_model TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS recipes",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS recipe_translations (
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            lang         TEXT NOT NULL,
            title        TEXT NOT NULL,
            description  TEXT,
            steps        TEXT,
            ingredients  TEXT,
            PRIMARY KEY (recipe_id, lang)
        )""",
        "DROP TABLE IF EXISTS recipe_translations",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS tag_families (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0
        )""",
        "DROP TABLE IF EXISTS tag_families",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS tags (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id       INTEGER NOT NULL REFERENCES tag_families(id),
            name            TEXT NOT NULL,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            parent_id       INTEGER REFERENCES tags(id),
            UNIQUE(family_id, name)
        )""",
        "DROP TABLE IF EXISTS tags",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (recipe_id, tag_id)
        )""",
        "DROP TABLE IF EXISTS recipe_tags",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS categories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0
        )""",
        "DROP TABLE IF EXISTS categories",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS processed_files (
            path         TEXT PRIMARY KEY,
            file_hash    TEXT NOT NULL,
            dropbox_hash TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS processed_files",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS recipe_images (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            filename     TEXT NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            is_hidden    INTEGER NOT NULL DEFAULT 0,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS recipe_images",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subject      TEXT NOT NULL UNIQUE,
            email        TEXT,
            name         TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS users",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS favorites (
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recipe_id    INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, recipe_id)
        )""",
        "DROP TABLE IF EXISTS favorites",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS blacklist (
            path         TEXT PRIMARY KEY,
            blacklisted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS blacklist",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS failed_files (
            path         TEXT PRIMARY KEY,
            error        TEXT NOT NULL,
            failed_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS failed_files",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS dropbox_connections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            refresh_token TEXT NOT NULL,
            folder        TEXT NOT NULL DEFAULT '',
            file_filter   TEXT NOT NULL DEFAULT '',
            active        INTEGER NOT NULL DEFAULT 1,
            visible       INTEGER NOT NULL DEFAULT 1,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS dropbox_connections",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        "DROP TABLE IF EXISTS app_settings",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
            endpoint     TEXT NOT NULL UNIQUE,
            subscription TEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS push_subscriptions",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS user_preferences (
            user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            prefs      TEXT NOT NULL DEFAULT '{}',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS user_preferences",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS shopping_departments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            display_name_fr TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            emoji           TEXT NOT NULL DEFAULT ''
        )""",
        "DROP TABLE IF EXISTS shopping_departments",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS shopping_lists (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            share_token  TEXT NOT NULL UNIQUE,
            name         TEXT NOT NULL,
            user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            all_done_at  DATETIME
        )""",
        "DROP TABLE IF EXISTS shopping_lists",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS shopping_list_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id       INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
            department_id INTEGER NOT NULL REFERENCES shopping_departments(id),
            text          TEXT NOT NULL,
            quantity      TEXT,
            is_done       INTEGER NOT NULL DEFAULT 0,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS shopping_list_items",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS shopping_templates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS shopping_templates",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS shopping_template_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id   INTEGER NOT NULL REFERENCES shopping_templates(id) ON DELETE CASCADE,
            department_id INTEGER NOT NULL REFERENCES shopping_departments(id),
            text          TEXT NOT NULL,
            quantity      TEXT,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS shopping_template_items",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS collections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            slug          TEXT UNIQUE,
            name          TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            owner_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            share_token   TEXT NOT NULL UNIQUE,
            is_site       INTEGER NOT NULL DEFAULT 0,
            is_featured   INTEGER NOT NULL DEFAULT 0,
            cover_recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "DROP TABLE IF EXISTS collections",
    ),
    step(
        """CREATE TABLE IF NOT EXISTS collection_recipes (
            collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            added_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_id, recipe_id)
        )""",
        "DROP TABLE IF EXISTS collection_recipes",
    ),
    step(
        """CREATE INDEX IF NOT EXISTS idx_collections_site
            ON collections(is_site, is_featured)""",
        "DROP INDEX IF EXISTS idx_collections_site",
    ),
    step(
        """CREATE INDEX IF NOT EXISTS idx_collection_recipes_recipe
            ON collection_recipes(recipe_id)""",
        "DROP INDEX IF EXISTS idx_collection_recipes_recipe",
    ),
    step(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_slug ON recipes(slug)",
        "DROP INDEX IF EXISTS idx_recipes_slug",
    ),
    step(
        """CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
            title,
            description,
            ingredients
        )""",
        "DROP TABLE IF EXISTS recipes_fts",
    ),
    step(
        """CREATE TRIGGER rt_ai AFTER INSERT ON recipe_translations BEGIN
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (CASE new.lang
                        WHEN 'en' THEN new.recipe_id * 2 + 1
                        ELSE new.recipe_id * 2
                    END,
                    new.title, new.description, new.ingredients);
        END""",
        "DROP TRIGGER IF EXISTS rt_ai",
    ),
    step(
        """CREATE TRIGGER rt_ad AFTER DELETE ON recipe_translations BEGIN
            DELETE FROM recipes_fts WHERE rowid = CASE old.lang
                        WHEN 'en' THEN old.recipe_id * 2 + 1
                        ELSE old.recipe_id * 2
                    END;
        END""",
        "DROP TRIGGER IF EXISTS rt_ad",
    ),
    step(
        """CREATE TRIGGER rt_au AFTER UPDATE ON recipe_translations BEGIN
            DELETE FROM recipes_fts WHERE rowid = CASE old.lang
                        WHEN 'en' THEN old.recipe_id * 2 + 1
                        ELSE old.recipe_id * 2
                    END;
            INSERT INTO recipes_fts(rowid, title, description, ingredients)
            VALUES (CASE new.lang
                        WHEN 'en' THEN new.recipe_id * 2 + 1
                        ELSE new.recipe_id * 2
                    END,
                    new.title, new.description, new.ingredients);
        END""",
        "DROP TRIGGER IF EXISTS rt_au",
    ),
]
