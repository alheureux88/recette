"""
units.py — Quantités normalisées, conversion d'unités et formatage des ingrédients.

Un ingrédient structuré (tel que retourné par le LLM et stocké en base) :

    {"food": "farine", "quantity_min": 1.5, "quantity_max": 2, "unit": "tasse"}

- quantity_min / quantity_max : nombres ("quantity_max" est null si quantité unique)
- unit : unité de mesure (null si absente), stockée sous sa clé canonique
  française (utilisée comme clé de regroupement et de conversion). L'affichage
  est localisé : `tasse` → "tasse"/"tasses" en français, "cup"/"cups" en anglais.

Systèmes de conversion :
- "original" : conserve l'unité telle quelle
- "metric"   : convertit les unités convertibles vers g / kg / ml / l
- "imperial" : convertit les unités convertibles vers lb / oz / tasse / c. à soupe / c. à thé

Conventions canadiennes : 1 tasse = 250 ml, 1 c. à soupe = 15 ml, 1 c. à thé = 5 ml.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from recipes.i18n import DEFAULT_LANGUAGE

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
class _UnitForm:
    """Forme affichée d'une unité dans une langue donnée."""

    singular: str
    plural: str  # vide si la forme courte est invariable ("c", "tbsp", "tsp")


@dataclass(frozen=True)
class UniteDef:
    canonical: str  # nom d'affichage au singulier (FR par défaut)
    dimension: str  # "masse" | "volume"
    facteur: float  # facteur vers l'unité de base (g ou ml)
    systeme: str  # "metric" | "imperial"
    pluriel: str  # forme plurielle française
    forms: dict[str, _UnitForm]  # formes localisées par langue


_UNITES: dict[str, UniteDef] = {
    alias: definition
    for aliases, definition in [
        (
            ("g", "gr", "gramme", "grammes"),
            UniteDef(
                "g",
                "masse",
                _G,
                "metric",
                "g",
                {"fr": _UnitForm("g", "g"), "en": _UnitForm("g", "g")},
            ),
        ),
        (
            ("kg", "kilogramme", "kilogrammes"),
            UniteDef(
                "kg",
                "masse",
                _KG,
                "metric",
                "kg",
                {"fr": _UnitForm("kg", "kg"), "en": _UnitForm("kg", "kg")},
            ),
        ),
        (
            ("oz", "once", "onces"),
            UniteDef(
                "oz",
                "masse",
                _OZ,
                "imperial",
                "oz",
                {"fr": _UnitForm("oz", "oz"), "en": _UnitForm("oz", "oz")},
            ),
        ),
        (
            ("lb", "lbs", "livre", "livres", "pound", "pounds"),
            UniteDef(
                "lb",
                "masse",
                _LB,
                "imperial",
                "lb",
                {"fr": _UnitForm("lb", "lb"), "en": _UnitForm("lb", "lb")},
            ),
        ),
        (
            ("ml", "millilitre", "millilitres"),
            UniteDef(
                "ml",
                "volume",
                _ML,
                "metric",
                "ml",
                {"fr": _UnitForm("ml", "ml"), "en": _UnitForm("ml", "ml")},
            ),
        ),
        (
            ("l", "litre", "litres"),
            UniteDef(
                "l",
                "volume",
                _L,
                "metric",
                "l",
                {"fr": _UnitForm("l", "l"), "en": _UnitForm("l", "l")},
            ),
        ),
        (
            ("tasse", "tasses", "cup", "cups"),
            UniteDef(
                "tasse",
                "volume",
                _TASSE,
                "imperial",
                "tasses",
                {
                    "fr": _UnitForm("tasse", "tasses"),
                    "en": _UnitForm("cup", "cups"),
                },
            ),
        ),
        (
            (
                "c. à soupe",
                "c à soupe",
                "cs",
                "tbsp",
                "tbsps",
                "tbs",
                "tablespoon",
                "tablespoons",
            ),
            UniteDef(
                "c. à soupe",
                "volume",
                _C_A_SOUPE,
                "imperial",
                "c. à soupe",
                {
                    "fr": _UnitForm("c. à soupe", "c. à soupe"),
                    "en": _UnitForm("tbsp", ""),
                },
            ),
        ),
        (
            (
                "c. à thé",
                "c à thé",
                "ct",
                "tsp",
                "tsps",
                "teaspoon",
                "teaspoons",
            ),
            UniteDef(
                "c. à thé",
                "volume",
                _C_A_THE,
                "imperial",
                "c. à thé",
                {
                    "fr": _UnitForm("c. à thé", "c. à thé"),
                    "en": _UnitForm("tsp", ""),
                },
            ),
        ),
        (
            ("oz liquide", "once liquide", "onces liquides", "fl oz", "floz"),
            UniteDef(
                "oz liquide",
                "volume",
                _OZ_LIQUIDE,
                "imperial",
                "oz liquide",
                {
                    "fr": _UnitForm("oz liquide", "oz liquide"),
                    "en": _UnitForm("fl oz", "fl oz"),
                },
            ),
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


# Prépositions reliant l'unité à l'aliment.
#
# Français : "de" avec élision devant voyelle ou h muet ("d'huile", "de farine").
# Anglais : "of" — pas d'élision ("of flour", "of milk"). Certaines formulations
# idiomatiques (sugar, salt, water, ...) pourraient omettre la préposition, mais
# on garde "of" partout pour la lisibilité et la cohérence.

_VOYELLES = "aàâeéèêëiîïoôuùû"
_H_ASPIRE = ("haricot",)  # le h aspiré ne prend pas d'élision ("de haricots")


def _preposition(lang: str, aliment: str) -> str:
    """Préposition reliant l'unité à l'aliment selon la langue.

    Retourne la préposition avec son séparateur d'espace final (ou vide si
    pas de préposition). Le format final colle simplement la préposition
    à l'aliment, ce qui gère naturellement l'élision française
    ("d'huile" -> "d'" + "huile") et la préposition anglaise ("of flour" ->
    "of " + "flour").
    """
    if lang == "en":
        return "of " if aliment else ""
    if not aliment:
        return ""
    premiere = aliment[0].lower()
    if premiere in _VOYELLES or (premiere == "h" and not aliment.lower().startswith(_H_ASPIRE)):
        return "d'"
    return "de "


def _de(aliment: str) -> str:
    """Rétrocompatibilité : préposition française par défaut."""
    return _preposition(DEFAULT_LANGUAGE, aliment)


def _pluriel_inconnu_fr(unite: str, quantite: float | None) -> str:
    """Pluriel français pour une unité non canonique (gousse, pincée, ...)."""
    if quantite is not None and quantite > 1 and not unite.endswith(("s", "x", "z")):
        return unite + "s"
    return unite


def _pluriel_inconnu_en(unite: str, quantite: float | None) -> str:
    """Pluriel anglais basique pour une unité non canonique (clove, pinch, ...).

    On applique les règles usuelles : -s, -es (sibilantes), -y -> -ies.
    Suffisant pour les cas usuels ; le LLM doit de toute façon émettre
    l'unité au singulier dans `unit`.
    """
    if quantite is None or quantite <= 1:
        return unite
    lower = unite.lower()
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        return unite + "es"
    if lower.endswith("y") and len(unite) > 1 and unite[-2] not in "aeiou":
        return unite[:-1] + "ies"
    return unite + "s"


def _avec_pluriel(unite: str, quantite: float | None, lang: str = DEFAULT_LANGUAGE) -> str:
    """Retourne l'unité au singulier ou pluriel selon la quantité affichée et la langue."""
    definition = _lookup(unite)
    if definition is None:
        if lang == "en":
            return _pluriel_inconnu_en(unite, quantite)
        return _pluriel_inconnu_fr(unite, quantite)

    form = definition.forms.get(lang) or definition.forms[DEFAULT_LANGUAGE]
    singulier = form.singular
    pluriel = form.plural or singulier  # vide = forme courte invariable
    if quantite is not None and quantite > 1:
        return pluriel
    return singulier


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

    Retourne (qmin, qmax, unité canonique d'affichage, utiliser des fractions).
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
    ingredient: object,
    multiplicateur: float = 1.0,
    systeme: str = "original",
    lang: str = DEFAULT_LANGUAGE,
) -> str:
    """Formate un ingrédient pour l'affichage.

    `multiplicateur` met à l'échelle les quantités (ex: changer le nombre de portions).
    `systeme` vaut "original", "metric" ou "imperial".
    `lang` localise l'unité affichée et la préposition ("de farine" / "of flour").
    Les ingrédients hérités stockés en chaînes simples sont retournés tels quels.
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
        sep = "to" if lang == "en" else "à"
        texte_quantite = f"{_format(qmin, fractions)} {sep} {_format(qmax, fractions)}"
        reference_pluriel = qmax
    elif qmin is not None:
        texte_quantite = _format(qmin, fractions)
        reference_pluriel = qmin

    texte_unite = _avec_pluriel(unite_affichee, reference_pluriel, lang) if unite_affichee else ""
    preposition = _preposition(lang, aliment)

    if not texte_quantite and not texte_unite:
        return aliment
    if not texte_unite:
        return f"{texte_quantite} {aliment}".strip()
    if not texte_quantite:
        if aliment:
            return f"{texte_unite} {preposition}{aliment}".strip()
        return texte_unite
    if aliment:
        return f"{texte_quantite} {texte_unite} {preposition}{aliment}"
    return f"{texte_quantite} {texte_unite}"
