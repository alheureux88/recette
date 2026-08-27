"""
tagger.py — Send raw recipe text to an LLM (OpenAI or Anthropic) and get back
structured data with tags grouped by family, a category, and a bilingual
title / description / instructions / ingredients payload.
"""

import json
import os
import threading
from typing import Any

from recipes.db import (
    get_all_categories,
    get_existing_tags_for_prompt,
    get_setting,
)
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
    """Modele LLM : override de la Configuration (DB) sinon LLM_MODEL du .env."""
    override = get_setting("llm_model", "").strip()
    if override:
        return override
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


FAMILY_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "origin": {"fr": "Origine", "en": "Origin"},
    "diet": {"fr": "Régime alimentaire", "en": "Diet"},
    "protein": {"fr": "Protéine principale", "en": "Main protein"},
    "cooking_method": {"fr": "Méthode de cuisson", "en": "Cooking method"},
}


def build_system_prompt() -> str:
    existing_tags = get_existing_tags_for_prompt()
    categories = get_all_categories()

    lines: list[str] = [
        "Tu es un parseur de recettes. À partir du texte brut extrait d'un fichier de",
        "recette, retourne un objet JSON avec exactement les champs décrits ci-dessous.",
        "",
        "=== IMPORTANT : sortie bilingue français / anglais ===",
        "",
        "Le texte source peut être en français ou en anglais. Tu dois TOUJOURS retourner",
        "les champs textuels (title, description, ingredients[].food, instructions)",
        "dans les DEUX langues en parallèle, via des wrappers _fr / _en.",
        "Les noms techniques (category, tags, units, quantity_min/max) restent les mêmes",
        "dans les deux langues — seules les chaînes destinées à l'affichage sont dupliquées.",
        "",
        "=== Familles d'étiquettes et étiquettes existantes ===",
        "",
    ]

    for family_key, names in FAMILY_DISPLAY_NAMES.items():
        tags = existing_tags.get(family_key, [])
        lines.append(f'**{names["fr"]} / {names["en"]}** (clé: "{family_key}") :')

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
                lines.append("  - (aucune étiquette existante / no existing tags)")
        lines.append("")

    lines.append("=== Catégories disponibles / Available categories ===")
    lines.append("")
    cat_list = ", ".join(str(c["display_name"]) for c in categories)
    lines.append(f"  {cat_list}")
    lines.append("")

    lines.extend(
        [
            "=== Instructions ===",
            "",
            'Utilise les étiquettes existantes quand possible (utilise la clé, p.ex. "japonais", "braise").',
            "Use existing tag keys whenever possible (e.g. 'japonais', 'braise').",
            "Tu peux créer de nouvelles étiquettes si aucune ne convient.",
            "You can create new tags when none fit.",
            "Pour les étiquettes hiérarchiques (Origine), inclus toujours les étiquettes parentes.",
            "For hierarchical tags (Origin), always include parent tags.",
            "Pour la catégorie, utilise le nom exact d'une des catégories disponibles.",
            "For category, use the exact name of one of the available categories.",
            "",
            "=== IMPORTANT pour les instructions / IMPORTANT for instructions ===",
            "",
            "Le texte brut contient des paragraphes séparés par des sauts de ligne.",
            "Préserve ces sauts de ligne dans le champ 'instructions' (les deux langues)",
            "en utilisant '\\n' entre chaque étape. NE mets PAS tout dans un seul paragraphe.",
            "",
            "=== IMPORTANT pour les ingrédients / IMPORTANT for ingredients ===",
            "",
            "Chaque ingrédient est un objet avec les champs 'food_fr', 'food_en',",
            "'quantity_min', 'quantity_max' et 'unit'.",
            "",
            "'food_fr' : l'aliment en français, sans quantité ni unité, avec sa préparation",
            'ou ses qualificatifs (ex: "oignon rouge, haché finement", "boeuf haché").',
            "'food_en' : la traduction anglaise du même aliment (ex: \"red onion, finely chopped\",",
            '"ground beef"). Si tu n\'es pas sûr, donne une approximation naturelle en anglais.',
            "",
            "'quantity_min' et 'quantity_max' : des nombres décimaux (0.5 pour 1/2, 1.5 pour 1 1/2).",
            "Si l'ingrédient a une plage (ex: \"1 à 2 tasses\"), mets le minimum dans 'quantity_min' et le",
            "maximum dans 'quantity_max'. S'il n'a qu'une seule quantité, mets-la dans 'quantity_min' et null",
            "dans 'quantity_max'. S'il n'a pas de quantité (ex: \"sel au goût\"), mets null dans les deux.",
            "",
            "'unit' : l'unité de mesure, au singulier. Utilise TOUJOURS la clé canonique française",
            'parmi : "g", "kg", "oz", "lb", "ml", "l", "tasse", "c. à soupe", "c. à thé", "oz liquide".',
            "Always return the canonical French key, even if the source text or the",
            "recipe language is English (so 'cup' → 'tasse', 'tbsp' → 'c. à soupe',",
            "'tsp' → 'c. à thé'). The display layer will translate the unit name",
            "into the user's chosen language.",
            "If the unit isn't convertible (e.g. 'pincée', 'gousse', 'boîte', 'tranche', 'botte',",
            "'pinch', 'clove', 'can', 'slice', 'bunch'), keep the original unit from the source text.",
            "null s'il n'y a pas d'unité. Si le texte donne la même quantité dans deux systèmes",
            '(ex: "450 g / 1 lb"), garde la première.',
            "",
            "=== IMPORTANT pour les portions / IMPORTANT for servings ===",
            "",
            "'servings' : le nombre de portions que la recette produit, SEULEMENT si le texte le",
            'mentionne explicitement (ex: "pour 4 personnes", "4 portions", "serves 4",',
            '"yield: 4"). Si le texte ne mentionne PAS explicitement un nombre de portions,',
            "retourne null. N'ESTIME PAS et NE DEVINE PAS le nombre de portions à partir des",
            "quantités : retourne null plutôt que d'inventer une valeur.",
            "The value must be a number, not text.",
            "",
            "=== Format de sortie JSON / JSON output format ===",
            "",
            "{",
            '  "title_fr": "Nom de la recette en français",',
            '  "title_en": "Recipe name in English",',
            '  "description_fr": "Résumé en français",',
            '  "description_en": "Summary in English",',
            '  "servings": 4,',
            '  "ingredients": [',
            "    {",
            '      "food_fr": "farine",',
            '      "food_en": "flour",',
            '      "quantity_min": 1.5,',
            '      "quantity_max": 2,',
            '      "unit": "tasse"',
            "    },",
            "    {",
            '      "food_fr": "oeufs",',
            '      "food_en": "eggs",',
            '      "quantity_min": 2,',
            '      "quantity_max": null,',
            '      "unit": null',
            "    },",
            "    {",
            '      "food_fr": "sel au goût",',
            '      "food_en": "salt to taste",',
            '      "quantity_min": null,',
            '      "quantity_max": null,',
            '      "unit": null',
            "    }",
            "  ],",
            '  "instructions_fr": "Étape 1.\\nÉtape 2.\\nÉtape 3.",',
            '  "instructions_en": "Step 1.\\nStep 2.\\nStep 3.",',
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
            "Return ONLY valid JSON. No markdown, no explanations, no code blocks.",
        ]
    )

    return "\n".join(lines)


def tag_recipe(raw_text: str, default_title: str | None = None) -> dict[str, object]:
    """Send the raw recipe text to the LLM and return a normalized payload.

    Returns a dict with the following shape:

        {
            "lang_fr": {"title": ..., "description": ..., "instructions": ...,
                        "ingredients": [...]},
            "lang_en": { ... },
            "tags": {"family": [names]},
            "category": "plat-principal" | None,
            "source_url": "https://..." | None,
            "servings": 4 | None,
        }
    """
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

    payload_fr, payload_en = _extract_bilingual_payload(data, default_title)
    ingredients_fr = _normalize_ingredients(data.get("ingredients"), "fr")
    ingredients_en = _normalize_ingredients(data.get("ingredients"), "en")
    payload_fr["ingredients"] = ingredients_fr
    payload_en["ingredients"] = ingredients_en

    tags = data.get("tags") if isinstance(data.get("tags"), dict) else {}
    tags = {str(k): v for k, v in tags.items()}
    for family_key in list(tags.keys()):
        tag_list = tags[family_key]
        if isinstance(tag_list, list):
            tags[family_key] = sorted(
                {str(t).lower().strip().replace(" ", "-") for t in tag_list if t}
            )
        else:
            tags[family_key] = []

    category = data.get("category")
    category = category.lower().strip().replace(" ", "-") if isinstance(category, str) else None

    source_url = data.get("source_url")
    if source_url and not (isinstance(source_url, str) and source_url.startswith("http")):
        source_url = None

    return {
        "lang_fr": payload_fr,
        "lang_en": payload_en,
        "tags": tags,
        "category": category,
        "source_url": source_url,
        "servings": _parse_servings(data.get("servings")),
    }


def _extract_bilingual_payload(
    data: dict[str, object], default_title: str | None
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the (fr, en) translation payloads from the LLM response.

    Accepts the new bilingual shape (`title_fr` / `title_en` / ...) and
    gracefully falls back to a single-language shape for older prompts or
    tests by mirroring the value in both languages.
    """
    title_fr = _coerce_str(data.get("title_fr") or data.get("title"))
    title_en = _coerce_str(data.get("title_en") or data.get("title"))
    if not title_fr or title_fr == "Recette sans titre":
        title_fr = default_title or "Recette sans titre"
    if not title_en or title_en == "Recette sans titre":
        title_en = default_title or title_fr or "Untitled recipe"

    description_fr = _coerce_str(data.get("description_fr") or data.get("description"))
    description_en = _coerce_str(data.get("description_en") or data.get("description"))

    instructions_fr = _coerce_str(data.get("instructions_fr") or data.get("instructions"))
    instructions_en = _coerce_str(data.get("instructions_en") or data.get("instructions"))

    return (
        {
            "title": title_fr,
            "description": description_fr,
            "instructions": instructions_fr,
        },
        {
            "title": title_en,
            "description": description_en,
            "instructions": instructions_en,
        },
    )


def _coerce_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _clean_unit(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_ingredients(raw: object, lang: str) -> list[dict[str, object]]:
    """Normalise la liste d'ingrédients retournée par le LLM.

    Chaque entrée conserve `food_fr` et `food_en` quand le LLM les fournit,
    sinon elle rétrograde vers l'ancien champ `food` (utilisé par quelques
    tests unitaires et recettes legacy). Pour la couche d'affichage, la
    langue souhaitée est aplatie dans `food` lors de l'écriture en DB.
    """
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, object]] = []
    food_key = f"food_{lang}"
    for item in raw:
        if isinstance(item, dict):
            food = item.get(food_key)
            if not isinstance(food, str) or not food.strip():
                # Repli : champ bilingue manquant pour cette langue → on prend
                # l'autre langue, ou l'ancien champ "food" simple.
                fallback_key = "food_en" if lang == "fr" else "food_fr"
                food = item.get(fallback_key)
                if not isinstance(food, str) or not food.strip():
                    food = item.get("food")
                    if not isinstance(food, str):
                        food = item.get("name")
                        food = food if isinstance(food, str) else ""

            qmin = parse_quantity(item.get("quantity_min"))
            if qmin is None and "quantity_min" not in item:
                qmin = parse_quantity(item.get("quantity"))

            entry: dict[str, object] = {
                "food": food.strip() if isinstance(food, str) else "",
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
