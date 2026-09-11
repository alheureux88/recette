"""slug.py — Slugs d'URL descriptifs pour les recettes.

`/recipe/gratin-dauphinois` plutôt que `/recipe/12`. N'utilise que la
stdlib (normalisation NFKD + petite table de ligatures).
"""

import re
import unicodedata

# Ligatures/speciales sans décomposition NFKD (le reste — accents, ç, ñ —
# est géré par la normalisation).
_LIGATURES = {"œ": "oe", "æ": "ae", "ß": "ss", "ø": "o"}


def slugify(title: str) -> str:
    """Convertit un titre en slug d'URL (`Gratin Dauphinois` → `gratin-dauphinois`).

    Args:
        title: Titre brut (français accentué accepté).

    Returns:
        Slug minuscule ASCII à segments séparés par `-`, ou `"recette"`
        si rien d'exploitable (titre vide ou sans lettres/chiffres).
    """
    lowered = title.lower()
    for ligature, replacement in _LIGATURES.items():
        lowered = lowered.replace(ligature, replacement)
    normalized = unicodedata.normalize("NFKD", lowered)
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "recette"
