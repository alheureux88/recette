"""
tagger.py — Send raw recipe text to an LLM (OpenAI or Anthropic) and get back
structured data with tags grouped by family, a category, and a bilingual
title / description / instructions / ingredients payload.
"""

import json
import logging
import os
import threading
import uuid
from typing import Any

from recipes.features.shopping.services import get_shopping_departments
from recipes.shared.db import (
    get_all_categories,
    get_existing_tags_for_prompt,
    get_setting,
)
from recipes.shared.units import parse_quantity

log = logging.getLogger(__name__)

_client: Any = None
_client_lock = threading.Lock()
_session_id: str = str(uuid.uuid4())


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

            _client = Anthropic(
                api_key=api_key,
                default_headers={
                    "User-Agent": "recettes-merizzi/1.0",
                    "x-opencode-session": _session_id,
                },
            )
        else:
            from openai import OpenAI

            base_url = os.environ.get("LLM_BASE_URL")
            default_headers = {
                "User-Agent": "recettes-merizzi/1.0",
                "x-opencode-session": _session_id,
            }
            if base_url:
                _client = OpenAI(
                    api_key=api_key, base_url=base_url, default_headers=default_headers
                )
            else:
                _client = OpenAI(api_key=api_key, default_headers=default_headers)

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
        "=== IMPORTANT : sortie bilingue ===",
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

    try:
        departments = get_shopping_departments("fr")
        if departments:
            lines.append("=== Départements d'épicerie ===")
            lines.append("")
            lines.append(
                "Pour chaque ingrédient, indique le département d'épicerie où on peut le trouver."
            )
            lines.append(
                "IMPORTANT: Utilise UNIQUEMENT la clé (name) d'un des départements suivants. Ne crée JAMAIS de nouveau département:"
            )
            lines.append("")
            for dept in departments:
                lines.append(f'  - "{dept["name"]}" ({dept["display_name"]})')
            lines.append("")
    except Exception:
        pass

    lines.extend(
        [
            "=== Instructions ===",
            "",
            'Utilise les étiquettes existantes quand possible (utilise la clé, p.ex. "japonais", "braise").',
            "Tu peux créer de nouvelles étiquettes si aucune ne convient.",
            "Pour les étiquettes hiérarchiques (Origine), inclus toujours les étiquettes parentes.",
            "Pour la catégorie, utilise le nom exact d'une des catégories disponibles.",
            "",
            "=== IMPORTANT pour les étapes ===",
            "",
            "Les étapes de la recette doivent être retournées dans un tableau 'steps_fr' et 'steps_en'.",
            "Chaque étape est un objet avec 'text' (le texte de l'étape) et 'timer_seconds' (entier ou null).",
            "",
            "Si une étape mentionne un temps d'attente, de cuisson, de repos, de marinade, etc.",
            "(ex: 'cuire 5 minutes', 'laisser reposer 10 min'), mets la durée",
            "en SECONDES dans 'timer_seconds'. Sinon, mets null.",
            "",
            "=== IMPORTANT pour les ingrédients ===",
            "",
            "Chaque ingrédient est un objet avec les champs 'food_fr', 'food_en',",
            "'quantity_min', 'quantity_max' et 'unit'.",
            "",
            "'food_fr' : l'aliment en français, sans quantité ni unité, avec sa préparation",
            'ou ses qualificatifs (ex: "oignon rouge, haché finement", "boeuf haché").',
            "'food_en' : la traduction anglaise du même aliment.",
            "",
            "'quantity_min' et 'quantity_max' : des nombres décimaux (0.5 pour 1/2, 1.5 pour 1 1/2).",
            "Si l'ingrédient a une plage (ex: \"1 à 2 tasses\"), mets le minimum dans 'quantity_min' et le",
            "maximum dans 'quantity_max'. S'il n'a qu'une seule quantité, mets-la dans 'quantity_min' et null",
            "dans 'quantity_max'. S'il n'a pas de quantité (ex: \"sel au goût\"), mets null dans les deux.",
            "",
            "'unit' : l'unité de mesure, au singulier. Utilise TOUJOURS la clé canonique française",
            'parmi : "g", "kg", "oz", "lb", "ml", "l", "tasse", "c. à soupe", "c. à thé", "oz liquide".',
            "Si l'unité n'est pas convertible (ex: 'pincée', 'gousse', 'boîte', 'tranche'), garde l'unité originale.",
            "null s'il n'y a pas d'unité. Si le texte donne la même quantité dans deux systèmes",
            '(ex: "450 g / 1 lb"), garde la première.',
            "",
            "=== IMPORTANT pour les portions ===",
            "",
            "'servings' : le nombre de portions que la recette produit, SEULEMENT si le texte le",
            'mentionne explicitement (ex: "pour 4 personnes", "4 portions"). Si le texte ne mentionne PAS',
            "explicitement un nombre de portions, retourne null. N'ESTIME PAS et NE DEVINE PAS le nombre",
            "de portions à partir des quantités : retourne null plutôt que d'inventer une valeur.",
            "",
            "=== Format de sortie JSON ===",
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
            '      "unit": "tasse",',
            '      "department": "epicerie"',
            "    }",
            "  ],",
            '  "steps_fr": [',
            '    {"text": "Étape 1.", "timer_seconds": null},',
            '    {"text": "Cuire 5 minutes.", "timer_seconds": 300}',
            "  ],",
            '  "steps_en": [',
            '    {"text": "Step 1.", "timer_seconds": null},',
            '    {"text": "Cook for 5 minutes.", "timer_seconds": 300}',
            "  ],",
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
            "Pour source_url : si le texte contient une URL vers un site de recettes, extrais-la. Sinon, retourne null.",
            "",
            "Retourne UNIQUEMENT du JSON valide. Pas de markdown, pas d'explication, pas de blocs de code.",
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
    log.debug("System prompt length: %d chars", len(system_prompt))

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            if _get_provider() == "anthropic":
                model = _get_model()
                log.debug("Calling Anthropic API with model: %s", model)
                response = _get_client().messages.create(
                    model=model,
                    max_tokens=16000,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": raw_text},
                    ],
                )
                log.debug("Anthropic full response: %s", response)
                log.debug("Anthropic response content: %s", response.content)
                log.debug(
                    "Anthropic response stop_reason: %s", getattr(response, "stop_reason", None)
                )
                log.debug("Anthropic response usage: %s", getattr(response, "usage", None))
                raw = response.content[0].text.strip() if response.content else ""
            else:
                model = _get_model()
                log.debug("Calling OpenAI API with model: %s", model)
                response = _get_client().chat.completions.create(
                    model=model,
                    max_tokens=16000,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": raw_text},
                    ],
                )
                log.debug("OpenAI full response: %s", response)
                log.debug("OpenAI response choices: %s", response.choices)
                if response.choices:
                    log.debug("First choice: %s", response.choices[0])
                    log.debug("First choice message: %s", response.choices[0].message)
                    log.debug("First choice finish_reason: %s", response.choices[0].finish_reason)
                log.debug("OpenAI response usage: %s", getattr(response, "usage", None))
                if not response.choices or not response.choices[0].message:
                    raise ValueError("LLM returned no choices or message")
                raw = (response.choices[0].message.content or "").strip()

            if not raw:
                log.warning(
                    "LLM returned empty response on attempt %d/%d", attempt + 1, max_retries
                )
                if attempt < max_retries - 1:
                    import time

                    time.sleep(1 * (attempt + 1))
                    continue
                log.error(
                    "LLM returned empty response for recipe text (first 100 chars): %s",
                    raw_text[:100],
                )
                raise ValueError("LLM returned empty response")

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"LLM returned invalid JSON: {e}\n\nRaw response:\n{raw}"
                ) from None

            payload_fr, payload_en = _extract_bilingual_payload(data, default_title)
            ingredients_fr = _normalize_ingredients(data.get("ingredients"), "fr")
            ingredients_en = _normalize_ingredients(data.get("ingredients"), "en")
            payload_fr["ingredients"] = ingredients_fr
            payload_en["ingredients"] = ingredients_en

            steps_fr = _normalize_steps(data.get("steps_fr"), "fr")
            steps_en = _normalize_steps(data.get("steps_en"), "en")
            payload_fr["steps"] = steps_fr
            payload_en["steps"] = steps_en

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
            category = (
                category.lower().strip().replace(" ", "-") if isinstance(category, str) else None
            )

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

        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                log.warning("LLM call failed on attempt %d/%d: %s", attempt + 1, max_retries, e)
                import time

                time.sleep(1 * (attempt + 1))
                continue
            raise

    if last_error is not None:
        raise last_error
    raise ValueError("LLM call failed after all retries")


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

    return (
        {
            "title": title_fr,
            "description": description_fr,
        },
        {
            "title": title_en,
            "description": description_en,
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
                "department": _clean_department(item.get("department")),
            }
            if entry["food"] or entry["quantity_min"] is not None:
                normalized.append(entry)
        elif isinstance(item, str) and item.strip():
            normalized.append(
                {
                    "food": item.strip(),
                    "quantity_min": None,
                    "quantity_max": None,
                    "unit": None,
                    "department": "autre",
                }
            )
    return normalized


def _parse_servings(value: object) -> int | float | None:
    parsed = parse_quantity(value)
    if parsed is None or parsed <= 0:
        return None
    if parsed == int(parsed):
        return int(parsed)
    return parsed


def _normalize_steps(raw: object, lang: str) -> list[dict[str, object]]:
    """Normalize the steps array from the LLM response.

    Each step is an object with 'text' (string) and 'timer_seconds' (int or null).
    """
    if not isinstance(raw, list):
        return []
    result: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            timer = item.get("timer_seconds")
            timer_seconds: int | None = None
            if isinstance(timer, (int, float)) and timer > 0:
                timer_seconds = int(timer)
            result.append({"text": text.strip(), "timer_seconds": timer_seconds})
    return result


def _clean_department(value: object) -> str:
    """Normalize a department key from the LLM response."""
    if isinstance(value, str) and value.strip():
        return value.strip().lower().replace(" ", "-")
    return "autre"


def classify_ingredients(ingredients: list[str], lang: str = "fr") -> list[str]:
    """Ask the LLM to classify a list of ingredient names into departments.

    Returns a list of department keys (one per ingredient, same order).
    """
    if not ingredients:
        return []

    departments = get_shopping_departments(lang)
    dept_list = ", ".join(f'"{d["name"]}"' for d in departments)

    prompt = (
        "Tu es un classificateur d'ingrédients d'épicerie. "
        "Pour chaque ingrédient donné, retourne le département d'épicerie "
        "où on peut le trouver.\n\n"
        "You are a grocery ingredient classifier. "
        "For each given ingredient, return the grocery department "
        "where it can be found.\n\n"
        f"Départements disponibles / Available departments: {dept_list}\n\n"
        "Retourne UNIQUEMENT un tableau JSON de clés de département, "
        "dans le même ordre que les ingrédients fournis.\n"
        "Return ONLY a JSON array of department keys, "
        "in the same order as the provided ingredients.\n"
        "Pas de markdown, pas d'explication.\n"
        "No markdown, no explanation.\n\n"
        "Exemple / Example:\n"
        'Input: ["farine", "lait", "poulet"]\n'
        'Output: ["produits-secs", "produits-laitiers", "boucherie"]\n\n'
        f"Input: {json.dumps(ingredients, ensure_ascii=False)}\n"
        "Output:"
    )

    if _get_provider() == "anthropic":
        response = _get_client().messages.create(
            model=_get_model(),
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    else:
        response = _get_client().chat.completions.create(
            model=_get_model(),
            max_tokens=1000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = (response.choices[0].message.content or "").strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ["autre"] * len(ingredients)

    if not isinstance(data, list):
        return ["autre"] * len(ingredients)

    valid_keys = {d["name"] for d in departments}
    result: list[str] = []
    for item in data:
        key = item.strip().lower().replace(" ", "-") if isinstance(item, str) else "autre"
        result.append(key if key in valid_keys else "autre")

    while len(result) < len(ingredients):
        result.append("autre")

    return result[: len(ingredients)]
