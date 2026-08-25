"""
tagger.py — Send raw recipe text to an LLM (OpenAI or Anthropic) and get back
structured data with tags grouped by family and a category.
"""

import json
import os
import threading
from typing import Any

from recipes.db import get_all_categories, get_existing_tags_for_prompt
from recipes.units import parse_quantity

_client: Any = None
_client_lock = threading.Lock()


def reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        api_key = os.environ["LLM_API_KEY"]

        if provider == "anthropic":
            from anthropic import Anthropic

            _client = Anthropic(api_key=api_key)
        else:
            from openai import OpenAI

            base_url = os.environ.get("LLM_BASE_URL")
            _client = (
                OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
            )

        return _client


def _get_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "openai").lower()


def _get_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


FAMILY_DISPLAY_NAMES: dict[str, str] = {
    "origin": "Origine",
    "diet": "Régime alimentaire",
    "protein": "Protéine principale",
    "cooking_method": "Méthode de cuisson",
}


def build_system_prompt() -> str:
    existing_tags = get_existing_tags_for_prompt()
    categories = get_all_categories()

    lines: list[str] = [
        "Tu es un parseur de recettes. À partir du texte brut extrait d'un fichier de",
        "recette, retourne un objet JSON avec exactement les champs décrits ci-dessous.",
        "",
        "=== Familles d'étiquettes et étiquettes existantes ===",
        "",
    ]

    for family_key, display_name in FAMILY_DISPLAY_NAMES.items():
        tags = existing_tags.get(family_key, [])
        lines.append(f'**{display_name}** (clé: "{family_key}") :')

        if family_key == "origin":
            roots = [t for t in tags if not t.get("parent_name")]
            for root in roots:
                children = [t for t in tags if t.get("parent_name") == root["name"]]
                if children:
                    child_names = ", ".join(str(t["name"]) for t in children)
                    lines.append(f"  - {root['display_name']} > {child_names}")
                    for child in children:
                        grandchildren = [t for t in tags if t.get("parent_name") == child["name"]]
                        if grandchildren:
                            gc_names = ", ".join(str(t["name"]) for t in grandchildren)
                            lines.append(f"    - {child['display_name']} > {gc_names}")
                else:
                    lines.append(f"  - {root['display_name']}")
        else:
            if tags:
                tag_names = ", ".join(str(t["display_name"]) for t in tags)
                lines.append(f"  - {tag_names}")
            else:
                lines.append("  - (aucune étiquette existante)")
        lines.append("")

    lines.append("=== Catégories disponibles ===")
    lines.append("")
    cat_list = ", ".join(str(c["display_name"]) for c in categories)
    lines.append(f"  {cat_list}")
    lines.append("")

    lines.extend(
        [
            "=== Instructions ===",
            "",
            'Utilise les étiquettes existantes quand possible (utilise la clé, p.ex. "japonais", "braise").',
            "Tu peux créer de nouvelles étiquettes si aucune ne convient.",
            "Pour les étiquettes hiérarchiques (Origine), inclus toujours les étiquettes parentes.",
            "Pour la catégorie, utilise le nom exact d'une des catégories disponibles.",
            "",
            "=== IMPORTANT pour les instructions ===",
            "",
            "Le texte brut contient des paragraphes séparés par des sauts de ligne.",
            "Préserve ces sauts de ligne dans le champ 'instructions' en utilisant '\\n' entre chaque étape.",
            "NE mets PAS tout dans un seul paragraphe. Chaque étape doit être séparée par '\\n'.",
            'Exemple: "Étape 1.\\nÉtape 2.\\nÉtape 3."',
            "",
            "=== IMPORTANT pour les ingrédients ===",
            "",
            "Chaque ingrédient est un objet avec les champs 'food', 'quantity_min', 'quantity_max' et 'unit'.",
            "",
            "'food' : l'aliment seul, sans quantité ni unité, avec sa préparation ou ses qualificatifs",
            '(ex: "oignon rouge, haché finement", "boeuf haché").',
            "",
            "'quantity_min' et 'quantity_max' : des nombres décimaux (0.5 pour 1/2, 1.5 pour 1 1/2).",
            "Si l'ingrédient a une plage (ex: \"1 à 2 tasses\"), mets le minimum dans 'quantity_min' et le",
            "maximum dans 'quantity_max'. S'il n'a qu'une seule quantité, mets-la dans 'quantity_min' et null",
            "dans 'quantity_max'. S'il n'a pas de quantité (ex: \"sel au goût\"), mets null dans les deux.",
            "",
            "'unit' : l'unité de mesure, au singulier. Utilise une de ces unités canoniques quand",
            'possible : "g", "kg", "oz", "lb", "ml", "l", "tasse", "c. à soupe", "c. à thé", "oz liquide".',
            'Si l\'unité n\'est pas convertible (ex: "pincée", "gousse", "boîte", "tranche", "botte"),',
            "garde l'unité du texte original. null s'il n'y a pas d'unité.",
            'Si le texte donne la même quantité dans deux systèmes (ex: "450 g / 1 lb"), garde la première.',
            "",
            "=== IMPORTANT pour les portions ===",
            "",
            "'servings' : le nombre de portions que la recette produit, SEULEMENT si le texte le",
            'mentionne explicitement (ex: "pour 4 personnes", "4 portions", "serves 4",',
            '"yield: 4"). Si le texte ne mentionne PAS explicitement un nombre de portions,',
            "retourne null. N'ESTIME PAS et NE DEVINE PAS le nombre de portions à partir des",
            "quantités : retourne null plutôt que d'inventer une valeur.",
            "La valeur doit être un nombre, pas du texte.",
            "",
            "=== Format de sortie JSON ===",
            "",
            "{",
            '  "title": "Nom de la recette",',
            '  "description": "Résumé en une ou deux phrases",',
            '  "servings": 4,',
            '  "ingredients": [',
            '    {"food": "farine", "quantity_min": 1.5, "quantity_max": 2, "unit": "tasse"},',
            '    {"food": "oeufs", "quantity_min": 2, "quantity_max": null, "unit": null},',
            '    {"food": "sel au goût", "quantity_min": null, "quantity_max": null, "unit": null}',
            "  ],",
            '  "instructions": "Étape 1.\\nÉtape 2.\\nÉtape 3.",',
            '  "category": "plat-principal",',
            '  "tags": {',
            '    "origin": ["asiatique", "japonais"],',
            '    "diet": ["sans-gluten"],',
            '    "protein": ["poulet"],',
            '    "cooking_method": ["braise"]',
            "  },",
            '  "source_url": "https://..." ou null',
            "}",
            "",
            "Pour source_url : si le texte contient une URL vers un site de recettes, extrais-la.",
            "Sinon, retourne null.",
            "",
            "Retourne UNIQUEMENT du JSON valide. Pas de markdown, pas d'explication, pas de blocs de code.",
        ]
    )

    return "\n".join(lines)


def tag_recipe(raw_text: str, default_title: str | None = None) -> dict[str, object]:
    system_prompt = build_system_prompt()

    if _get_provider() == "anthropic":
        response = _get_client().messages.create(
            model=_get_model(),
            max_tokens=4000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": raw_text},
            ],
        )
        raw = response.content[0].text.strip()
    else:
        response = _get_client().chat.completions.create(
            model=_get_model(),
            max_tokens=4000,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text},
            ],
        )
        raw = (response.choices[0].message.content or "").strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}\n\nRaw response:\n{raw}") from None

    title = data.get("title")
    if not title or title == "Recette sans titre":
        data["title"] = default_title or "Recette sans titre"
    else:
        data["title"] = title

    data.setdefault("description", "")
    data["ingredients"] = _normalize_ingredients(data.get("ingredients"))
    data["servings"] = _parse_servings(data.get("servings"))
    data.setdefault("instructions", "")
    data.setdefault("tags", {})
    data.setdefault("category", None)
    data.setdefault("source_url", None)

    if not isinstance(data["tags"], dict):
        data["tags"] = {}

    url = data["source_url"]
    if url and not (isinstance(url, str) and url.startswith("http")):
        data["source_url"] = None

    for family_key in data["tags"]:
        tag_list = data["tags"][family_key]
        if isinstance(tag_list, list):
            data["tags"][family_key] = sorted(
                {t.lower().strip().replace(" ", "-") for t in tag_list if t}
            )
        else:
            data["tags"][family_key] = []

    if isinstance(data.get("category"), str):
        data["category"] = data["category"].lower().strip().replace(" ", "-")
    else:
        data["category"] = None

    return dict(data)


def _clean_unit(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_ingredients(raw: object) -> list[dict[str, object]]:
    """Normalise la liste d'ingrédients retournée par le LLM.

    Accepte des objets {food, quantity_min, quantity_max, unit} (avec quelques
    variantes tolérées comme "name" ou "quantity") et, en repli, des chaînes
    simples stockées telles quelles dans "food".
    """
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            food = item.get("food")
            if not isinstance(food, str) or not food.strip():
                alias = item.get("name")
                food = alias if isinstance(alias, str) else ""

            qmin = parse_quantity(item.get("quantity_min"))
            if qmin is None and "quantity_min" not in item:
                qmin = parse_quantity(item.get("quantity"))

            entry: dict[str, object] = {
                "food": food.strip(),
                "quantity_min": qmin,
                "quantity_max": parse_quantity(item.get("quantity_max")),
                "unit": _clean_unit(item.get("unit")),
            }
            if entry["food"] or entry["quantity_min"] is not None:
                normalized.append(entry)
        elif isinstance(item, str) and item.strip():
            normalized.append(
                {"food": item.strip(), "quantity_min": None, "quantity_max": None, "unit": None}
            )
    return normalized


def _parse_servings(value: object) -> int | float | None:
    parsed = parse_quantity(value)
    if parsed is None or parsed <= 0:
        return None
    if parsed == int(parsed):
        return int(parsed)
    return parsed
