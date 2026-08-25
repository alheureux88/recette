"""
units.py — Quantités normalisées, conversion d'unités et formatage des ingrédients.

Un ingrédient structuré (tel que retourné par le LLM et stocké en base) :

    {"food": "farine", "quantity_min": 1.5, "quantity_max": 2, "unit": "tasse"}

- quantity_min / quantity_max : nombres ("quantity_max" est null si quantité unique)
- unit : unité de mesure (null si absente)

Systèmes de conversion :
- "original" : conserve l'unité telle quelle
- "metric"   : convertit les unités convertibles vers g / kg / ml / l
- "imperial" : convertit les unités convertibles vers lb / oz / tasse / c. à soupe / c. à thé

Conventions canadiennes : 1 tasse = 250 ml, 1 c. à soupe = 15 ml, 1 c. à thé = 5 ml.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

# --- Facteurs de base -------------------------------------------------------

_G = 1.0  # gramme
_KG = 1000.0
_OZ = 28.349523125
_LB = 453.59237
_ML = 1.0
_L = 1000.0
_TASSE = 250.0
_C_A_SOUPE = 15.0
_C_A_THE = 5.0
_OZ_LIQUIDE = 29.5735296

_LB_SEUIL = 0.75 * _LB  # en dessous de 3/4 lb, on affiche en onces
_TASSE_SEUIL = _TASSE / 4  # en dessous de 1/4 tasse, on affiche en c. à soupe


@dataclass(frozen=True)
class UniteDef:
    canonical: str  # nom d'affichage au singulier
    dimension: str  # "masse" | "volume"
    facteur: float  # facteur vers l'unité de base (g ou ml)
    systeme: str  # "metric" | "imperial"
    pluriel: str  # forme plurielle


_UNITES: dict[str, UniteDef] = {
    alias: definition
    for aliases, definition in [
        (
            ("g", "gr", "gramme", "grammes"),
            UniteDef("g", "masse", _G, "metric", "g"),
        ),
        (("kg", "kilogramme", "kilogrammes"), UniteDef("kg", "masse", _KG, "metric", "kg")),
        (("oz", "once", "onces"), UniteDef("oz", "masse", _OZ, "imperial", "oz")),
        (
            ("lb", "lbs", "livre", "livres", "pound", "pounds"),
            UniteDef("lb", "masse", _LB, "imperial", "lb"),
        ),
        (("ml", "millilitre", "millilitres"), UniteDef("ml", "volume", _ML, "metric", "ml")),
        (("l", "litre", "litres"), UniteDef("l", "volume", _L, "metric", "l")),
        (
            ("tasse", "tasses", "cup", "cups"),
            UniteDef("tasse", "volume", _TASSE, "imperial", "tasses"),
        ),
        (
            ("c. à soupe", "c à soupe", "cs", "tbsp", "tbsps", "tbs", "tablespoon", "tablespoons"),
            UniteDef("c. à soupe", "volume", _C_A_SOUPE, "imperial", "c. à soupe"),
        ),
        (
            ("c. à thé", "c à thé", "ct", "tsp", "tsps", "teaspoon", "teaspoons"),
            UniteDef("c. à thé", "volume", _C_A_THE, "imperial", "c. à thé"),
        ),
        (
            ("oz liquide", "once liquide", "onces liquides", "fl oz", "floz"),
            UniteDef("oz liquide", "volume", _OZ_LIQUIDE, "imperial", "oz liquide"),
        ),
    ]
    for alias in aliases
}

SYSTEMES = ("original", "metric", "imperial")


def _lookup(unit: str) -> UniteDef | None:
    return _UNITES.get(unit.strip().lower().rstrip("."))


# --- Analyse des quantités ---------------------------------------------------


def _fraction_vers_float(texte: str) -> float | None:
    try:
        return float(Fraction(texte))
    except (ValueError, ZeroDivisionError):
        return None


def parse_quantity(valeur: object) -> float | None:
    """Convertit une quantité (int, float ou chaîne comme "1/2", "1 1/2", "0,5") en float."""
    resultat: float | None = None
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        resultat = float(valeur)
    elif isinstance(valeur, str):
        texte = valeur.strip().replace(",", ".")
        if not texte:
            return None
        resultat = _fraction_vers_float(texte)
        if resultat is None and " " in texte:
            # fraction mixte : "1 1/2"
            entier, _, fraction = texte.partition(" ")
            partie_entiere = _fraction_vers_float(entier)
            partie_fraction = _fraction_vers_float(fraction)
            if partie_entiere is not None and partie_fraction is not None:
                resultat = partie_entiere + partie_fraction
    else:
        return None
    if resultat is None or resultat < 0:
        return None
    return resultat


# --- Formatage ---------------------------------------------------------------

_DENOMINATEURS = (1, 2, 3, 4, 8)
_TOLERANCE = 0.1


def _format_decimal(valeur: float) -> str:
    if valeur >= 100:
        valeur = float(round(valeur))
    elif valeur >= 10:
        valeur = round(valeur, 1)
    else:
        valeur = round(valeur, 2)
    if valeur == int(valeur):
        return str(int(valeur))
    return f"{valeur:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _format_fraction(valeur: float) -> str:
    """Formate une valeur en entier ou fraction culinaire ("1 1/2"), sinon en décimal."""
    if valeur < 0:
        return _format_decimal(valeur)
    for denom in _DENOMINATEURS:
        scalaire = valeur * denom
        arrondi = round(scalaire)
        if arrondi < 0:
            continue
        if abs(scalaire - arrondi) <= _TOLERANCE:
            entier, reste = divmod(arrondi, denom)
            if reste == 0:
                return str(entier)
            if entier == 0:
                return f"{reste}/{denom}"
            return f"{entier} {reste}/{denom}"
    return _format_decimal(valeur)


def _format(valeur: float, fractions: bool) -> str:
    return _format_fraction(valeur) if fractions else _format_decimal(valeur)


_VOYELLES = "aàâeéèêëiîïoôuùû"
_H_ASPIRE = ("haricot",)  # le h aspiré ne prend pas d'élision ("de haricots")


def _de(aliment: str) -> str:
    """Préposition "de" avec élision devant une voyelle ou un h muet ("d'huile", "de farine")."""
    if not aliment:
        return aliment
    premiere = aliment[0].lower()
    if premiere in _VOYELLES or (premiere == "h" and not aliment.lower().startswith(_H_ASPIRE)):
        return f"d'{aliment}"
    return f"de {aliment}"


def _avec_pluriel(unite: str, quantite: float | None) -> str:
    """Retourne l'unité au singulier ou pluriel selon la quantité affichée."""
    definition = _lookup(unite)
    if definition is None:
        if quantite is not None and quantite > 1 and not unite.endswith(("s", "x", "z")):
            return unite + "s"
        return unite
    if quantite is not None and quantite > 1:
        return definition.pluriel
    return definition.canonical


# --- Conversion --------------------------------------------------------------


def _choisir_metrique(dimension: str, base: float) -> UniteDef:
    if dimension == "masse":
        return _UNITES["kg"] if base >= _KG else _UNITES["g"]
    return _UNITES["l"] if base >= _L else _UNITES["ml"]


def _choisir_imperial(dimension: str, base: float) -> UniteDef:
    if dimension == "masse":
        return _UNITES["lb"] if base >= _LB_SEUIL else _UNITES["oz"]
    if base >= _TASSE_SEUIL:
        return _UNITES["tasse"]
    return _UNITES["c. à soupe"] if base >= _C_A_SOUPE else _UNITES["c. à thé"]


def _convertir(
    qmin: float | None, qmax: float | None, unite: str | None, systeme: str
) -> tuple[float | None, float | None, str | None, bool]:
    """Convertit une paire quantités + unité vers le système demandé.

    Retourne (qmin, qmax, unité d'affichage, utiliser des fractions).
    Les unités inconnues (pincée, gousse, ...) ne sont jamais converties.
    """
    definition = _lookup(unite) if unite else None

    if definition is None:
        # Unité absente ou non convertible : on affiche telle quelle, avec fractions.
        return qmin, qmax, unite, True

    if systeme not in ("metric", "imperial"):
        # Mode "original" : pas de conversion, décimal pour les unités métriques.
        return qmin, qmax, definition.canonical, definition.systeme != "metric"

    fractions = systeme != "metric"

    quantites = [q for q in (qmin, qmax) if q is not None]
    if not quantites:
        return qmin, qmax, definition.canonical, fractions

    # L'unité cible est choisie d'après la plus grande quantité de la plage
    # (g -> kg, ml -> l, oz -> lb, c. à thé -> c. à soupe -> tasse...).
    base = max(quantites) * definition.facteur

    if systeme == "metric":
        cible = _choisir_metrique(definition.dimension, base)
    else:
        cible = _choisir_imperial(definition.dimension, base)

    if definition.systeme == systeme and cible is definition:
        return qmin, qmax, definition.canonical, fractions

    nouveau_min = qmin * definition.facteur / cible.facteur if qmin is not None else None
    nouveau_max = qmax * definition.facteur / cible.facteur if qmax is not None else None
    return nouveau_min, nouveau_max, cible.canonical, fractions


# --- Formatage d'un ingrédient -----------------------------------------------


def format_ingredient(
    ingredient: object, multiplicateur: float = 1.0, systeme: str = "original"
) -> str:
    """Formate un ingrédient pour l'affichage.

    `multiplicateur` met à l'échelle les quantités (ex: changer le nombre de portions),
    `systeme` vaut "original", "metric" ou "imperial". Les ingrédients hérités stockés
    en chaînes simples sont retournés tels quels.
    """
    if isinstance(ingredient, str):
        return ingredient
    if not isinstance(ingredient, dict):
        return ""

    aliment = str(ingredient.get("food") or "").strip()
    unite_brute = ingredient.get("unit")
    unite = unite_brute.strip() if isinstance(unite_brute, str) and unite_brute.strip() else None

    qmin = parse_quantity(ingredient.get("quantity_min"))
    qmax = parse_quantity(ingredient.get("quantity_max"))
    if qmin is not None and qmax is not None and qmax < qmin:
        qmin, qmax = qmax, qmin

    if qmin is not None:
        qmin *= multiplicateur
    if qmax is not None:
        qmax *= multiplicateur
    if qmin is not None and qmax is not None and abs(qmax - qmin) < 0.01:
        qmax = None  # plage réduite à une seule valeur après multiplication

    qmin, qmax, unite_affichee, fractions = _convertir(qmin, qmax, unite, systeme)

    texte_quantite = ""
    reference_pluriel: float | None = None
    if qmin is not None and qmax is not None:
        texte_quantite = f"{_format(qmin, fractions)} à {_format(qmax, fractions)}"
        reference_pluriel = qmax
    elif qmin is not None:
        texte_quantite = _format(qmin, fractions)
        reference_pluriel = qmin

    texte_unite = _avec_pluriel(unite_affichee, reference_pluriel) if unite_affichee else ""

    if not texte_quantite and not texte_unite:
        return aliment
    if not texte_unite:
        return f"{texte_quantite} {aliment}".strip()
    if not texte_quantite:
        return f"{texte_unite} {_de(aliment)}".strip() if aliment else texte_unite
    if aliment:
        return f"{texte_quantite} {texte_unite} {_de(aliment)}"
    return f"{texte_quantite} {texte_unite}"
