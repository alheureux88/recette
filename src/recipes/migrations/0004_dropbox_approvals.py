"""0004 - Dropbox connection approvals.

Super-users may propose a Dropbox connection, but it stays inactive and
invisible until an owner approves it:

- `proposed_by_user_id`: author of the proposal (NULL = created by an owner).
- `status`: 'pending' | 'approved' | 'rejected' (defaults to 'approved'
  so pre-existing connections keep working).

Migration files must stay pure ASCII: yoyo reads them with the locale
default encoding, which breaks non-ASCII bytes on some platforms.
"""

from yoyo import step

__depends__ = {"0003_recipe_ratings"}

steps = [
    step(
        """ALTER TABLE dropbox_connections
            ADD COLUMN proposed_by_user_id INTEGER
            REFERENCES users(id) ON DELETE SET NULL""",
        "ALTER TABLE dropbox_connections DROP COLUMN proposed_by_user_id",
    ),
    step(
        """ALTER TABLE dropbox_connections
            ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'""",
        "ALTER TABLE dropbox_connections DROP COLUMN status",
    ),
    step(
        """CREATE INDEX IF NOT EXISTS idx_dropbox_connections_status
            ON dropbox_connections(status)""",
        "DROP INDEX IF EXISTS idx_dropbox_connections_status",
    ),
]
