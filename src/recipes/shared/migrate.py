"""migrate.py — Application des migrations yoyo au démarrage.

Le schéma SQLite et les données de référence (taxonomie V1 : familles/tags,
catégories, rayons) sont versionnés via yoyo (`recipes/migrations/`,
embarqué dans le package pour fonctionner en install éditable comme en
wheel). `init_db()` ne fait qu'appliquer les migrations en attente.
"""

import os
from pathlib import Path

from yoyo import get_backend, read_migrations

_MIGRATIONS_ENV_VAR = "RECIPES_MIGRATIONS_DIR"

# shared/migrate.py -> shared -> recipes : les migrations vivent dans le
# package, donc ce chemin est valide en éditable comme en wheel installé.
MIGRATIONS_DIR = Path(
    os.environ.get(_MIGRATIONS_ENV_VAR, Path(__file__).resolve().parent.parent / "migrations")
)


def database_url(db_path: Path) -> str:
    """Construit l'URL yoyo pour un fichier SQLite.

    Trois slashes + chemin POSIX absolu (donne quatre slashes sur Unix,
    `sqlite:///C:/...` sur Windows).
    """
    return f"sqlite:///{db_path.resolve().as_posix()}"


def apply_migrations(db_path: Path, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Applique les migrations yoyo en attente sur `db_path`.

    Idempotent : les migrations déjà appliquées sont ignorées (table
    `_yoyo_migration`). Crée le dossier parent si nécessaire. Échoue
    explicitement si le dossier des migrations est introuvable (jamais
    de base vide silencieuse).
    """
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"Dossier de migrations introuvable : {migrations_dir}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backend = get_backend(database_url(db_path))
    migrations = read_migrations(str(migrations_dir))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
