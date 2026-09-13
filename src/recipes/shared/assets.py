"""assets.py — Versionnement des assets statiques par hash (cache-busting).

Le service worker PWA et les caches HTTP navigateur gardaient l'ancien
CSS/JS après un déploiement (URL inchangée). Chaque asset est haché
(MD5, 8 chars) **une fois au startup** (import module) et servi avec
`?v=<hash>` : toute modification change l'URL donc force un fetch frais.

Responsabilité unique : calcul + cache des hashs, rien de HTTP/Jinja.
L'exposition Jinja (`css_v`, `asset_url`) se fait dans `shared.web`.
"""

import hashlib
from pathlib import Path

HASH_LEN = 8
FALLBACK_VERSION = "dev"

STATIC_DIR_CANDIDATES: tuple[Path, ...] = (
    Path("static"),
    Path(__file__).resolve().parents[3] / "static",
)


def _resolve_static_dir() -> Path:
    """Retourne le premier dossier `static/` existant (cwd puis racine repo)."""
    for candidate in STATIC_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return STATIC_DIR_CANDIDATES[0]


STATIC_DIR: Path = _resolve_static_dir()

_hash_cache: dict[str, str] = {}


def _hash_bytes(data: bytes) -> str:
    """MD5 tronqué à HASH_LEN chars."""
    return hashlib.md5(data).hexdigest()[:HASH_LEN]


def asset_version(relative_path: str) -> str:
    """Hash MD5 (8 chars) d'un asset sous `static/`, calculé une fois.

    Args:
        relative_path: Chemin relatif à `static/`, ex. `"css/style.css"`.

    Returns:
        Le hash, ou `"dev"` si le fichier est absent (tests / dev partiel).
    """
    cached = _hash_cache.get(relative_path)
    if cached is not None:
        return cached
    try:
        version = _hash_bytes((STATIC_DIR / relative_path).read_bytes())
    except OSError:
        version = FALLBACK_VERSION
    _hash_cache[relative_path] = version
    return version


def asset_url(relative_path: str) -> str:
    """URL publique versionnée d'un asset, ex. `/static/css/style.css?v=abc`."""
    return f"/static/{relative_path}?v={asset_version(relative_path)}"


CSS_VERSION: str = asset_version("css/style.css")
