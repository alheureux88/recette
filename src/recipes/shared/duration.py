"""duration.py — Formatage lisible des durées de minuteurs.

Utilisé côté serveur pour le rendu initial des templates (page recette,
mode cuisine, slides) afin d'éviter l'affichage brut en secondes avant
l'hydratation JS. Le format correspond à `formatTime()` dans les templates :
`MM:SS`, ou `H:MM:SS` au-delà d'une heure.
"""


def format_duration(total_seconds: int) -> str:
    """Formate une durée en secondes en `MM:SS` ou `H:MM:SS`.

    Args:
        total_seconds: Durée en secondes (valeurs négatives ramenées à 0).

    Returns:
        Chaîne lisible, ex. `05:00` pour 300, `1:00:00` pour 3600.
    """
    total = int(total_seconds)
    if total < 0:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
